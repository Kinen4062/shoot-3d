"""程序化生成 M5 的音效（不依赖任何外部素材）。

**为什么自己合成**

规划书 §5.7 只给了文件名（`assets/audio/sfx_shoot.wav`），但项目里没有这些文件。
缺资源时有两条路：一是让音效系统静默降级（那就等于 M5 没做音效，验收不了），
二是去找免费素材（引入版权与体积问题）。这里走第三条：用标准库 `wave`
合成几个可辨识的短音，规则是「**能听出是什么事**」而不是「好听」。

生成的音效与事件一一对应（`src/systems/audio.py` 里同名）：
    shoot / shotgun / hit / kill / hurt / shield / pickup / reload
    / click / wave / gameover

`shoot` 与 `shotgun` 是**两个独立音效**，不是同一段的音量差异：霰弹枪
0.5 秒一发、步枪 0.1 秒一发，共用一段"砰"时换枪这件事在听感上完全消失
（只剩 HUD 上的数字变了）。所以霰弹的低频更重、低通更闷、尾巴更长，
连射时两发之间还能各自收住。

用法：
    python tools/gen_sfx.py          # 已存在则跳过
    python tools/gen_sfx.py --force  # 全部重新生成

时长合计约 2 秒，全部 22050Hz 16-bit 单声道，总体积 < 100KB。
"""
from __future__ import annotations

import math
import os
import random
import struct
import sys
import wave

SAMPLE_RATE = 22050
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets", "audio")


# ---------------------------------------------------------------- 合成基元

def tone(duration: float, f_start: float, f_end: float | None = None,
         amp: float = 0.6, decay: float = 6.0, shape: str = "sine",
         attack: float = 0.004) -> list[float]:
    """一段带指数衰减的单音，可以做频率滑移（f_start -> f_end）。"""
    n = max(1, int(SAMPLE_RATE * duration))
    end = f_start if f_end is None else f_end
    out: list[float] = []
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        u = t / duration
        freq = f_start + (end - f_start) * u
        phase += math.tau * freq / SAMPLE_RATE
        value = math.sin(phase) if shape == "sine" else (1.0 if math.sin(phase) >= 0 else -1.0)
        env = math.exp(-decay * u)
        if t < attack:
            env *= t / attack        # 起音淡入，否则开头会有"啪"的爆音
        out.append(value * amp * env)
    return out


def noise(duration: float, amp: float = 0.5, decay: float = 8.0,
          lowpass: float = 0.5, seed: int = 20261019) -> list[float]:
    """一段带衰减的噪声，lowpass 越小越闷（0.5 大约是"咔"的质感）。"""
    rng = random.Random(seed)
    n = max(1, int(SAMPLE_RATE * duration))
    out: list[float] = []
    prev = 0.0
    for i in range(n):
        u = i / n
        prev += lowpass * (rng.uniform(-1.0, 1.0) - prev)
        out.append(prev * amp * math.exp(-decay * u))
    return out


def silence(duration: float) -> list[float]:
    return [0.0] * max(1, int(SAMPLE_RATE * duration))


def mix(*tracks: list[float]) -> list[float]:
    """叠加若干轨（长度取最长，短的补零），最后按峰值归一化防止削顶。"""
    length = max(len(t) for t in tracks)
    out = [0.0] * length
    for track in tracks:
        for i, v in enumerate(track):
            out[i] += v
    peak = max(abs(v) for v in out) or 1.0
    if peak > 0.98:
        out = [v * 0.98 / peak for v in out]
    return out


def concat(*tracks: list[float]) -> list[float]:
    out: list[float] = []
    for track in tracks:
        out.extend(track)
    return out


# ---------------------------------------------------------------- 音效定义

def build() -> dict[str, list[float]]:
    click = mix(noise(0.02, 0.4, 40, 0.9), tone(0.02, 2200, 1800, 0.2, 40))

    return {
        # 开枪：噪声爆点 + 低频"砰"
        "shoot": mix(noise(0.09, 0.5, 14, 0.55), tone(0.09, 170, 60, 0.5, 10)),
        # 霰弹枪：同一条思路但整个往下压一档、尾巴拉长
        # （低通 0.55 -> 0.28 更闷，基频 170 -> 105，衰减 10 -> 5.5）。
        # 起手那 0.04 秒的高频是"击锤/炮口"的冲击，让两把枪在连射里也分得开。
        "shotgun": mix(noise(0.20, 0.62, 8, 0.28),
                       tone(0.24, 105, 44, 0.58, 5.5),
                       tone(0.04, 260, 190, 0.28, 22)),
        # 命中敌人：短促的高频"嗒"，要有"打到了"的清脆感
        "hit": mix(tone(0.05, 1500, 950, 0.45, 18), noise(0.03, 0.22, 24, 0.85)),
        # 击杀：两个下行音，和命中区分开
        "kill": mix(tone(0.22, 900, 620, 0.42, 7), tone(0.22, 1350, 930, 0.18, 9)),
        # 受伤：闷、低、带方波的不适感
        "hurt": mix(tone(0.30, 120, 68, 0.5, 6, shape="square"),
                    noise(0.14, 0.2, 14, 0.3)),
        # 护盾：金属"叮"
        "shield": mix(tone(0.20, 1800, 1500, 0.35, 10), tone(0.20, 2700, 2400, 0.16, 14)),
        # 拾取：上行
        "pickup": tone(0.18, 660, 1320, 0.42, 6),
        # 换弹：两下机械声
        "reload": concat(click, silence(0.07), click),
        # 按钮：极短
        "click": tone(0.035, 2400, 2000, 0.3, 40),
        # 新一波：三音上行
        "wave": concat(tone(0.12, 523, None, 0.35, 8),
                       tone(0.12, 659, None, 0.35, 8),
                       tone(0.20, 784, None, 0.32, 6)),
        # 游戏结束：长下滑
        "gameover": mix(tone(0.65, 440, 100, 0.45, 4.5), noise(0.3, 0.12, 6, 0.2)),
    }


def write_wav(path: str, samples: list[float]) -> int:
    with wave.open(path, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, int(v * 32767.0))))
            for v in samples
        )
        fh.writeframes(frames)
    return os.path.getsize(path)


def main(argv: list[str]) -> int:
    force = "--force" in argv
    os.makedirs(OUT_DIR, exist_ok=True)
    total = 0
    for name, samples in build().items():
        path = os.path.join(OUT_DIR, f"{name}.wav")
        if os.path.exists(path) and not force:
            print(f"  跳过 {name}.wav（已存在，用 --force 重新生成）")
            total += os.path.getsize(path)
            continue
        size = write_wav(path, samples)
        total += size
        print(f"  {name:9s} {len(samples) / SAMPLE_RATE:5.2f}s  {size / 1024:6.1f} KB")
    print(f"[gen_sfx] 输出目录 {OUT_DIR}")
    print(f"[gen_sfx] 共 {len(build())} 个文件，合计 {total / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
