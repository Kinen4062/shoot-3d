"""音效系统（`src/systems/audio.py`）的回归约束。

音效是最容易被"看起来没问题"掩盖的一块：没有声音时你无法判断
是「文件缺失」还是「事件没触发」还是「音量是 0」。

所以这里的测试分成两组：
1. **纯函数**：音量换算、去重判定 —— 边界写错时听感是"偶尔少一声"，很难察觉；
2. **注入假 clip**：池轮转 / 随机音高 / 去重窗口 / 音量档位，
   全部不依赖 ursina，也不碰用户真实的 `assets/audio/`。

`clip_factory` 就是为了这一条存在的：生产环境走 `_load_clip()`（真 Audio），
测试里换成假对象，逻辑完全一样。
"""
from __future__ import annotations

import pytest

from src.core import config
from src.systems.audio import AudioSystem, clamp01, mix_volume, should_play


class FakeClip:
    """够用的假 Audio：只记"播了几次"和最后设置的音高/音量。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.pitch = 1.0
        self.volume = 1.0
        self.played = 0

    def play(self) -> None:
        self.played += 1


def make(factory_clips: list[FakeClip] | None = None, **kwargs) -> AudioSystem:
    """造一个用假 clip 的 AudioSystem，并把造出来的 clip 收集起来。"""
    made: list[FakeClip] = factory_clips if factory_clips is not None else []

    def factory(name: str) -> FakeClip:
        clip = FakeClip(name)
        made.append(clip)
        return clip

    audio = AudioSystem(clip_factory=factory, **kwargs)
    audio.made = made          # type: ignore[attr-defined]
    return audio


# ---------------------------------------------------------------- 纯函数

def test_clamp01():
    assert clamp01(-1.0) == 0.0
    assert clamp01(0.5) == 0.5
    assert clamp01(3.0) == 1.0


def test_mix_volume_multiplies_all_three():
    assert mix_volume(0.5, 1.0, 0.8, 0.9) == pytest.approx(0.36)
    assert mix_volume(0.5, 0.5, 1.0, 1.0) == pytest.approx(0.25)
    # 超过 1 要夹住，否则 Panda 的 setVolume 会爆音
    assert mix_volume(1.0, 1.0, 1.0, 1.0) == 1.0


def test_should_play_respects_dedupe_window():
    assert should_play(0.0, None, 40) is True
    assert should_play(0.010, 0.0, 40) is False
    assert should_play(0.039, 0.0, 40) is False
    assert should_play(0.040, 0.0, 40) is True


# ---------------------------------------------------------------- 缺文件降级

def test_missing_file_is_silent(tmp_path):
    """文件不存在时不能抛异常，也不能反复提示。"""
    audio = AudioSystem(audio_dir=str(tmp_path))
    assert audio.play("shoot") is False
    assert "shoot" in audio.missing
    # 第二次必须走缓存分支，不再查盘、不再打印
    assert audio.play("shoot") is False
    assert audio.missing_skips == 2
    assert audio.plays == 0


def test_disabled_audio_does_nothing():
    audio = make(enabled=False)
    assert audio.play("shoot") is False
    assert audio.muted_skips == 1
    assert audio.made == []


# ---------------------------------------------------------------- 池与去重

def test_pool_round_robin():
    audio = make()
    for _ in range(config.AUDIO_POOL_SIZE):
        assert audio.play("shoot") is True
        audio.update(0.2)          # 越过去重窗口

    clips = [c for c in audio.made if c.name == "shoot"]
    assert len(clips) == config.AUDIO_POOL_SIZE
    assert all(c.played == 1 for c in clips), "第一轮应当每个实例各播一次"

    assert audio.play("shoot") is True
    audio.update(0.2)
    assert clips[0].played == 2, "第 5 次应当回到第 0 个实例"


def test_dedupe_suppresses_rapid_repeats():
    audio = make()
    assert audio.play("shoot") is True
    audio.update(0.02)
    assert audio.play("shoot") is False
    assert audio.suppressed == 1
    audio.update(0.03)             # 累计 50ms > 40ms
    assert audio.play("shoot") is True


def test_reset_clears_dedupe_window():
    audio = make()
    audio.play("shoot")
    audio.reset()
    assert audio.play("shoot") is True, "重开后第一枪不该被上一局的去重吃掉"


def test_pitch_stays_in_range():
    audio = make()
    for _ in range(40):
        audio.play("shoot")
        audio.update(0.2)
    pitches = [c.pitch for c in audio.made if c.name == "shoot"]
    assert pitches, "至少播过一次"
    assert all(config.AUDIO_PITCH_MIN <= p <= config.AUDIO_PITCH_MAX for p in pitches)
    assert len(set(pitches)) > 1, "音高必须随机，不能恒定"


# ---------------------------------------------------------------- 音量

def test_volume_uses_master_and_sfx():
    audio = make()
    audio.set_master(0.5)
    audio.sfx = 1.0
    audio.play("shoot")
    clip = audio.made[0]
    assert clip.volume == pytest.approx(config.SOUND_VOLUMES["shoot"] * 0.5)


def test_cycle_volume_walks_steps_and_wraps():
    audio = make()
    audio.set_master(0.80)
    assert audio.volume_percent == 80
    assert audio.cycle_volume() == 100
    assert audio.cycle_volume() == 0          # 到顶后回到静音
    assert audio.cycle_volume() == 25


def test_zero_volume_still_plays():
    """音量 0 也要照常走播放流程：玩家把音量调回来时不该"丢音效"。"""
    audio = make()
    audio.set_master(0.0)
    assert audio.play("shoot") is True
    assert audio.made[0].volume == 0.0


# ---------------------------------------------------------------- 事件胶水

def test_event_handlers_map_to_sound_names():
    audio = make()
    for handler, expected in (
        ("on_shot", "shoot"),
        ("on_hit", "hit"),
        ("on_kill", "kill"),
        ("on_hurt", "hurt"),
        ("on_shield", "shield"),
        ("on_pickup", "pickup"),
        ("on_reload", "reload"),
        ("on_wave", "wave"),
        ("on_game_over", "gameover"),
        ("on_click", "click"),
    ):
        getattr(audio, handler)()
        audio.update(0.2)          # 错过窗口，避免被去重吞掉
    names = {c.name for c in audio.made}
    assert names == {
        "shoot", "hit", "kill", "hurt", "shield",
        "pickup", "reload", "wave", "gameover", "click",
    }


# ---------------------------------------------------------------- BGM

class FakeSound:
    """假 AudioSound（Panda 的 camelCase 接口）。只记状态，不碰声卡。"""

    def __init__(self) -> None:
        self.volume = 0.0
        self.loop = False
        self.played = 0
        self.stopped = 0
        self._t = 0.0

    def setLoop(self, value):        # noqa: N802 - 对齐 Panda 的接口名
        self.loop = bool(value)

    def setVolume(self, value):      # noqa: N802
        self.volume = value

    def play(self):
        self.played += 1

    def stop(self):
        self.stopped += 1

    def getTime(self):               # noqa: N802
        return self._t

    def setTime(self, value):        # noqa: N802
        self._t = value


def make_bgm(**kwargs) -> tuple[AudioSystem, FakeSound]:
    sound = FakeSound()
    audio = AudioSystem(clip_factory=lambda name: FakeClip(name),
                        bgm_factory=lambda name: sound, **kwargs)
    return audio, sound


def test_bgm_volume_is_master_times_bgm_only():
    """音乐走 master × bgm，**不乘 sfx** —— 否则压音效会连音乐一起压没。"""
    from src.systems.audio import bgm_volume
    assert bgm_volume(1.0, 0.5) == pytest.approx(0.5)
    assert bgm_volume(0.8, 0.15) == pytest.approx(0.12)
    assert bgm_volume(2.0, 2.0) == 1.0        # 夹住，不然会爆音


def test_bgm_starts_loops_and_is_idempotent():
    audio, sound = make_bgm()
    assert audio.start_bgm() is True
    assert sound.played == 1
    assert sound.loop is True                 # 循环必须交给 Panda，不能自己检测重播
    assert sound.volume == pytest.approx(config.VOLUME_MASTER * config.VOLUME_BGM)

    assert audio.start_bgm() is True          # 再调一次
    assert sound.played == 1, "start_bgm 必须幂等，否则每帧都会叠加一份音乐"
    assert audio.bgm_state == "playing"


def test_bgm_pause_remembers_position():
    audio, sound = make_bgm()
    audio.start_bgm()
    sound._t = 42.5
    audio.pause_bgm()
    assert audio.bgm_state == "paused"
    assert sound.stopped == 1

    audio.start_bgm()                          # 恢复
    assert audio.bgm_state == "playing"
    assert sound._t == pytest.approx(42.5), "恢复时必须 seek 回暂停点，不能从头开始"
    assert sound.played == 2


def test_bgm_ignores_pause_when_not_playing():
    """没在播的时候暂停不能把状态改成 paused，否则下一次 resume 会带一个 0 秒 seek。"""
    audio, sound = make_bgm()
    audio.pause_bgm()
    assert audio.bgm_state == "idle"
    assert sound.stopped == 0


def test_bgm_volume_button_applies_immediately():
    audio, sound = make_bgm()
    audio.start_bgm()
    audio.cycle_volume()
    assert sound.volume == pytest.approx(audio.master * config.VOLUME_BGM), \
        "改音量后正在播的音乐必须立刻变，不能等下一次播放"


def test_bgm_state_changed_pauses_and_resumes():
    from src.core.game_state import State
    audio, sound = make_bgm()
    audio.start_bgm()
    audio.on_state_changed(State.PAUSED)
    assert audio.bgm_state == "paused"
    audio.on_state_changed(State.PLAYING)
    assert audio.bgm_state == "playing"


def test_bgm_missing_file_degrades_silently(monkeypatch):
    """BGM 文件没了不能崩、不能抛异常，只把状态标成 missing 并返回 False。"""
    monkeypatch.setattr(config, "BGM_FILE", "assets/audio/__nope__.ogg")
    audio = AudioSystem(clip_factory=lambda name: FakeClip(name))
    assert audio.start_bgm() is False
    assert audio.bgm_state == "missing"
    assert audio._bgm_failed is True


def test_bgm_respects_disabled():
    audio, sound = make_bgm(enabled=False)
    assert audio.start_bgm() is False
    assert sound.played == 0
