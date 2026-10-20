"""整局运行时数据：分数 / 连击 / 击杀 / 存活时间。

**为什么必须是一个显式对象，而不是一堆模块级全局变量或类属性**
（规划书 §5.6 专门点名了这一条）：

类属性是"所有实例共享"的，重开时只要漏掉一次赋值，上一局的分数就会
跟着新局一起走。而模块级全局变量在测试里没法隔离 —— 一个测试改了分数，
下一个测试拿到的就是脏值。

所以这里用「模块持有唯一实例 + 显式 reset()」：
- 生产环境：`get_data()` 拿同一个对象；
- 测试：`GameData()` 直接造一个新的，互不干扰。

`reset()` **默认保留 best_score**。最高分是跨局数据，写在这里只是为了避免
HUD 每次都要去读一次文件；它的真相源始终是 `core/save.py`。
"""
from __future__ import annotations

from src.core import config, save


class GameData:
    __slots__ = (
        "best_score",
        "best_wave",
        "combo",
        "combo_timer",
        "kills",
        "score",
        "time_alive",
        "wave",
    )

    def __init__(self) -> None:
        self.score = 0
        self.combo = 0
        self.combo_timer = 0.0
        self.kills = 0
        self.wave = 0
        self.time_alive = 0.0
        self.best_score = 0
        self.best_wave = 0
        self.reset()

    def reset(self, keep_best: bool = True) -> None:
        """新一局开局。`keep_best=False` 会连最高分一起清（只在测试里用）。"""
        best_score = self.best_score if keep_best else 0
        best_wave = self.best_wave if keep_best else 0

        self.score = 0
        self.combo = 0
        self.combo_timer = 0.0
        self.kills = 0
        self.wave = 0
        self.time_alive = 0.0
        self.best_score = best_score
        self.best_wave = best_wave

        if not keep_best:
            return
        # 从存档同步一次最高分。读盘失败时 load_save 自己会兜底成 0，
        # 这里不能再把它覆盖回内存里的旧值 —— 取 max 更安全。
        stored = save.load_save()
        self.best_score = max(self.best_score, stored["best_score"])
        self.best_wave = max(self.best_wave, stored["best_wave"])

    # ---- 连击 ----
    @property
    def combo_multiplier(self) -> float:
        """当前连击倍率。1 连 = 1.0 倍，11 连及以上封顶 2.0 倍。"""
        return combo_multiplier(self.combo)

    def push_combo(self) -> int:
        """击杀时调用：连击 +1 并把倒计时拉满。返回新的连击数。"""
        self.combo += 1
        self.combo_timer = config.COMBO_TIMEOUT
        return self.combo

    def tick_combo(self, dt: float) -> bool:
        """倒计时递减。返回 True 表示这一帧连击刚被清零。"""
        if self.combo <= 0:
            return False
        self.combo_timer -= dt
        if self.combo_timer > 0.0:
            return False
        self.combo = 0
        self.combo_timer = 0.0
        return True


def combo_multiplier(combo: int) -> float:
    """纯函数，方便单测钉住边界（规划书 §5.6 的公式）。"""
    if combo <= 0:
        return 1.0
    return 1.0 + min(combo - 1, config.COMBO_MAX_BONUS) * config.COMBO_STEP


# ---- 唯一实例 ----
_shared: GameData | None = None


def get_data() -> GameData:
    global _shared
    if _shared is None:
        _shared = GameData()
    return _shared


def set_data(data: GameData | None) -> None:
    """测试用：替换/清空共享实例。生产代码不应调用。"""
    global _shared
    _shared = data
