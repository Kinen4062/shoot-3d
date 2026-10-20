"""音效系统（规划书 §5.7）：对象池 + 去重 + 随机音高 + 三档音量 + 缺文件降级。

**四条规则，每条都对应一个具体的听感问题**

1. **对象池**（`AUDIO_POOL_SIZE=4`）：步枪 10 发/秒，同一个 `Audio` 实例
   会被下一发打断（Panda 的 `set_time(0)` 直接重置），听感是"每发都没响完"。
   轮转 4 个实例后，前几发能自然叠在一起。
2. **去重**（`AUDIO_DEDUPE_MS=40`）：同一帧里可能触发多次（霰弹未来会有多颗弹丸、
   命中与击杀相邻触发）。40ms 内的重复直接丢掉，否则实例全被抢占，反而更闷。
3. **随机音高**（±5%）：固定音高听起来像"在放同一段录音"。
4. **缺文件静默降级**：`assets/audio/*.wav` 缺了不能崩、也不能每帧刷 warning，
   只在第一次访问时记一条，之后彻底跳过。
   生成音效用 `python tools/gen_sfx.py`（纯标准库合成，不需要外部素材）。

**为什么自己算音量，不用 `Audio.volume_multiplier`**

那是 ursina 的**全局类属性**，改它会影响所有 `Audio`，而且只在 setter 执行时生效 ——
音量按钮改完，已经在播的实例音量不会跟着变。这里每次播放直接算
`base × scale × master × sfx`，没有隐式全局状态，也能脱离引擎单测。

**为什么可以脱离引擎测**

`clip_factory` 是给测试用的注入点：传了它就不碰 ursina、不查文件，
直接生成假实例。引擎相关的部分被压缩到 `_load_clip()` 这一个函数里。
"""
from __future__ import annotations

import os
import random

from src.core import config


def clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else (1.0 if value > 1.0 else value)


def mix_volume(base: float, scale: float, master: float, sfx: float) -> float:
    """把一次播放的最终音量算出来。抽成纯函数，测试直接对它断言。"""
    return clamp01(base * scale * master * sfx)


def bgm_volume(master: float, bgm: float) -> float:
    """BGM 音量 = master × bgm。和音效分开算：音乐不该跟着 sfx 走。

    音乐是"底色"，音效是"事件" —— 玩家的"太吵了"通常是嫌音效，
    把音乐一起压下去就变成"什么都没声"，所以两条总线必须独立。
    """
    return clamp01(master * bgm)


def should_play(now_s: float, last_s: float | None, dedupe_ms: float) -> bool:
    """去重判定：距离上次播放不足 dedupe_ms 就丢弃。"""
    if last_s is None:
        return True
    return (now_s - last_s) * 1000.0 >= dedupe_ms


class AudioSystem:
    def __init__(
        self,
        enabled: bool | None = None,
        audio_dir: str | None = None,
        clip_factory=None,
        bgm_factory=None,
        seed: int = 20261019,
    ) -> None:
        self.enabled = config.AUDIO_ENABLED if enabled is None else enabled
        self.master = config.VOLUME_MASTER
        self.sfx = config.VOLUME_SFX
        self.bgm = config.VOLUME_BGM
        self.audio_dir = audio_dir or config.resource_path(config.AUDIO_DIR)

        self._factory = clip_factory       # 测试注入；生产环境为 None
        self._bgm_factory = bgm_factory    # BGM 的注入点（接口是 AudioSound 那套）
        self._pools: dict[str, list] = {}
        self._next: dict[str, int] = {}
        self._last: dict[str, float] = {}
        self._rng = random.Random(seed)
        self._now = 0.0

        # BGM：单实例（不是池）。音乐同时只该有一份，轮转没有意义。
        self._bgm = None
        self._bgm_failed = False
        self._bgm_time: float | None = None
        self.bgm_state = "idle"            # idle / playing / paused / missing

        # 统计（覆盖层显示，验收时能看出"到底播了没有"）
        self.plays = 0
        self.suppressed = 0       # 被去重吃掉的次数
        self.missing_skips = 0    # 因为没文件而跳过的次数
        self.muted_skips = 0      # 因为关了音效而跳过的次数
        self.missing: set[str] = set()

    # ---------------------------------------------------------------- 装载
    def _load_clip(self, name: str, path: str):
        """装一个 Audio 实例。

        **必须传 `Path` 而不是 str**。ursina 的 `Audio.clip_setter` 对 str
        的处理是"当成资源名去 asset_folder 里 glob"（`**/{name}`），
        传绝对路径会 glob 不到并静默把 clip 设成 None ——
        表现是"文件明明存在、没有报错、但一点声音都没有"。
        传 `Path` 才会走 `_audio_manager.getSound(Filename.fromOsSpecific(...))`。
        """
        from pathlib import Path

        from ursina import Audio
        return Audio(Path(path), autoplay=False, volume=1.0)

    def _ensure_pool(self, name: str) -> list | None:
        pool = self._pools.get(name)
        if pool is not None:
            return pool
        if name in self.missing:
            return None

        if self._factory is not None:
            pool = [self._factory(name) for _ in range(config.AUDIO_POOL_SIZE)]
        else:
            path = os.path.join(self.audio_dir, f"{name}.wav")
            if not os.path.exists(path):
                # 只提示一次。每帧提示会把控制台刷满，反而看不到真正的报错。
                self.missing.add(name)
                print(f"[M5] 缺音效 {name}.wav —— 静默跳过（python tools/gen_sfx.py 可生成）")
                return None
            try:
                pool = [self._load_clip(name, path) for _ in range(config.AUDIO_POOL_SIZE)]
            except Exception as exc:      # noqa: BLE001 - 声卡/解码器异常不能让游戏起不来
                self.missing.add(name)
                print(f"[M5] 音效 {name}.wav 装载失败（{type(exc).__name__}: {exc}），跳过")
                return None

        self._pools[name] = pool
        self._next[name] = 0
        return pool

    # ---------------------------------------------------------------- 播放
    def play(self, name: str, volume_scale: float = 1.0) -> bool:
        """播一次音效。返回 False 表示这次没发声（关闭 / 缺文件 / 被去重）。"""
        if not self.enabled:
            self.muted_skips += 1
            return False

        pool = self._ensure_pool(name)
        if not pool:
            self.missing_skips += 1
            return False

        last = self._last.get(name)
        if not should_play(self._now, last, config.AUDIO_DEDUPE_MS):
            self.suppressed += 1
            return False

        self._last[name] = self._now
        index = self._next.get(name, 0)
        self._next[name] = (index + 1) % len(pool)
        clip = pool[index]

        base = config.SOUND_VOLUMES.get(name, 0.6)
        try:
            clip.pitch = self._rng.uniform(config.AUDIO_PITCH_MIN, config.AUDIO_PITCH_MAX)
            clip.volume = mix_volume(base, volume_scale, self.master, self.sfx)
            clip.play()
        except Exception as exc:      # noqa: BLE001 - 音频驱动异常不能拖垮游戏主循环
            print(f"[M5] 播放 {name} 失败：{type(exc).__name__}: {exc}")
            self.missing.add(name)
            return False
        self.plays += 1
        return True

    def reset(self) -> None:
        """重开一局：只清去重窗口。

        不清 `_pools` —— 重开时重新加载 10×4 个 clip 会让第一发子弹卡顿一下。
        也不清统计：`plays` 是累计量，验收时想看的是"整个过程播了多少次"。
        **更不碰 BGM**：重开一局不该让音乐从头开始，那是"换了一首歌"的听感。
        """
        self._last.clear()

    # ---------------------------------------------------------------- BGM
    def _ensure_bgm(self):
        """装载背景音乐。失败只记一次，之后彻底跳过（和音效同一套降级规则）。"""
        if self._bgm is not None or self._bgm_failed:
            return self._bgm

        if self._bgm_factory is not None:
            try:
                self._bgm = self._bgm_factory("bgm")
                return self._bgm
            except Exception as exc:  # noqa: BLE001
                self._bgm_failed = True
                print(f"[M6] 背景音乐装载失败（{type(exc).__name__}: {exc}），跳过")
                return None

        path = config.resource_path(config.BGM_FILE)
        if not os.path.exists(path):
            self._bgm_failed = True
            self.bgm_state = "missing"
            print(f"[M6] 缺背景音乐 {config.BGM_FILE} —— 静默跳过")
            return None

        try:
            self._bgm = self._load_bgm(path)
        except Exception as exc:      # noqa: BLE001 - 解码器/声卡异常不能拦着游戏起不来
            self._bgm_failed = True
            self.bgm_state = "missing"
            print(f"[M6] 背景音乐装载失败（{type(exc).__name__}: {exc}），跳过")
            return None
        return self._bgm

    def _load_bgm(self, path: str):
        """装载 BGM，**绕开 ursina 的 `Audio` 封装**，直接用 Panda 的音频对象。

        三个理由，每条都是实测撞出来的：

        1. **流式播放**：`base.loadMusic` 是 SMStream；ursina 的 `Audio` 走
           `getSound`（整段解码进内存）。3 分钟的曲子全解码约 30MB，没必要。
        2. **`Audio.pause()/resume()` 对 `Path` 构造的实例是坏的**：
           `resume()` 里执行 `self.clip = self.name`，而 `clip_setter` 走 Path
           分支时**不会设置 `self.name`**，于是 resume 把 clip 换成了空字符串 ——
           表现是"暂停一次之后音乐永久消失"。
        3. **音量**：`Audio.volume_setter` 会再乘一个类属性
           `Audio.volume_multiplier`（默认 0.5），自己算的音量会被悄悄折半。
           直接操作 AudioSound 拿到的是准确值。

        Panda 的 AudioSound 接口是 camelCase，测试里的假对象照着实现即可。
        """
        from panda3d.core import Filename
        from ursina import application

        base = getattr(application, "base", None)
        if base is None:
            raise RuntimeError("没有 Panda base（无窗口环境）")
        sound = base.loadMusic(Filename.fromOsSpecific(path))
        if sound is None:
            raise RuntimeError("loadMusic 返回 None")
        sound.setVolume(bgm_volume(self.master, self.bgm))
        return sound

    def start_bgm(self) -> bool:
        """开始播放 BGM（幂等）。返回 False 表示没放（关闭 / 缺文件）。"""
        if not self.enabled:
            return False
        clip = self._ensure_bgm()
        if clip is None:
            return False
        if self.bgm_state == "playing":
            return True

        try:
            if self.bgm_state == "paused" and self._bgm_time is not None:
                clip.setTime(self._bgm_time)
            # 循环在这里设而不是只在 `_load_bgm` 里设：测试注入的假音源
            # 不走 `_load_bgm`，循环开关必须是"每次开播都确认一遍"的幂等操作。
            clip.setLoop(True)
            clip.setVolume(bgm_volume(self.master, self.bgm))
            clip.play()
        except Exception as exc:      # noqa: BLE001
            self._bgm_failed = True
            self.bgm_state = "missing"
            print(f"[M6] 播放背景音乐失败（{type(exc).__name__}: {exc}），跳过")
            return False
        self.bgm_state = "playing"
        return True

    def pause_bgm(self) -> None:
        """暂停（记住位置）。切到 PAUSED 时调用。"""
        if self._bgm is None or self.bgm_state != "playing":
            return
        try:
            self._bgm_time = float(self._bgm.getTime())
            self._bgm.stop()
        except Exception:             # noqa: BLE001
            self._bgm_time = None
        self.bgm_state = "paused"

    def on_state_changed(self, new=None, **_kwargs) -> None:
        """跟着状态机走：暂停时停音乐，其它状态恢复。

        菜单 / 对局 / 结算都让它一直放着 —— 这是一首歌，不是三首。
        真正的"静音"由 `enabled` 和音量档位负责，不用停播来表达。
        """
        if new is None:
            return
        name = new if isinstance(new, str) else getattr(new, "name", "")
        if name == "PAUSED":
            self.pause_bgm()
        else:
            self.start_bgm()

    def _apply_bgm_volume(self) -> None:
        if self._bgm is None:
            return
        try:
            self._bgm.setVolume(bgm_volume(self.master, self.bgm))
        except Exception:             # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 音量
    @property
    def volume_percent(self) -> int:
        return int(round(self.master * 100))

    def set_master(self, value: float) -> int:
        self.master = clamp01(value)
        self._apply_bgm_volume()      # 音量按钮要立刻作用到正在播的音乐上
        return self.volume_percent

    def cycle_volume(self) -> int:
        """按 `VOLUME_STEPS` 循环切换主音量，返回新的百分比（给按钮显示）。"""
        steps = config.VOLUME_STEPS
        current = self.volume_percent
        percents = [int(round(s * 100)) for s in steps]
        # 找"当前值之后"的下一档；找不到（手工改过常量）就回到第一档。
        for pct in percents:
            if pct > current:
                self.set_master(pct / 100.0)
                return self.volume_percent
        self.set_master(percents[0] / 100.0)
        return self.volume_percent

    # ---------------------------------------------------------------- 每帧
    def update(self, dt: float) -> None:
        # 用自己累计的时钟而不是 time.time()：暂停时主循环不给 dt，
        # 去重窗口会跟着冻住，节奏和游戏逻辑保持一致。
        self._now += dt

    # ---------------------------------------------------------------- 事件
    def on_shot(self, **_kwargs) -> None:
        self.play("shoot")

    def on_hit(self, **_kwargs) -> None:
        self.play("hit")

    def on_kill(self, **_kwargs) -> None:
        self.play("kill")

    def on_hurt(self, **_kwargs) -> None:
        self.play("hurt")

    def on_shield(self, **_kwargs) -> None:
        self.play("shield")

    def on_pickup(self, **_kwargs) -> None:
        self.play("pickup")

    def on_reload(self, **_kwargs) -> None:
        self.play("reload")

    def on_wave(self, **_kwargs) -> None:
        self.play("wave")

    def on_game_over(self, **_kwargs) -> None:
        self.play("gameover")

    def on_click(self, **_kwargs) -> None:
        self.play("click")
