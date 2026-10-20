"""计分：连击倍率、3 秒清零、生存分、满血清波。

全部不依赖引擎 —— `GameData` 和 `combo_multiplier` 都是纯 Python，
`ScoreSystem` 只多了一个 `state.in_state()`，用一个假状态机就能跑。

**这些边界必须靠测试钉住**，因为它们全都是"差一点就看起来正常"的数值：
倍率封顶差一层、生存分用 `int(dt)` 恒为 0、连击计时用 `>=` 而不是 `>`，
在画面上几乎看不出来，但分数会系统性偏掉。
"""
import pytest

from src.core import config
from src.core.game_data import GameData, combo_multiplier, set_data
from src.core.game_state import State
from src.systems.score import ScoreSystem


class FakeState:
    def __init__(self, state=State.PLAYING) -> None:
        self.state = state

    def in_state(self, *states) -> bool:
        return self.state in states


class FakeEnemy:
    def __init__(self, score: int) -> None:
        from types import SimpleNamespace
        self.data = SimpleNamespace(score=score)


class FakePlayer:
    def __init__(self, hp=100.0, max_hp=100.0, alive=True) -> None:
        self.hp = hp
        self.max_hp = max_hp
        self.alive = alive


@pytest.fixture(autouse=True)
def clean_shared(tmp_path, monkeypatch):
    """ScoreSystem 默认拿模块级共享实例，测试之间必须隔离。

    同时把 `SAVE_FILE` 指到 tmp：`GameData()` 构造时会读一次存档，
    不隔离的话测试结果会依赖用户 %APPDATA% 里的真实最高分。
    """
    monkeypatch.setattr(config, "SAVE_FILE", str(tmp_path / "save.json"))
    set_data(None)
    yield
    set_data(None)


# ---------------------------------------------------------------- 纯函数

def test_combo_multiplier_boundaries():
    assert combo_multiplier(0) == pytest.approx(1.0)
    assert combo_multiplier(1) == pytest.approx(1.0)
    assert combo_multiplier(2) == pytest.approx(1.1)
    assert combo_multiplier(5) == pytest.approx(1.4)
    # 11 连开始封顶 2.0
    assert combo_multiplier(11) == pytest.approx(2.0)
    assert combo_multiplier(12) == pytest.approx(2.0)
    assert combo_multiplier(999) == pytest.approx(2.0)


# ---------------------------------------------------------------- 连击计时

def test_combo_clears_after_three_seconds():
    data = GameData()
    data.reset(keep_best=False)
    data.push_combo()
    data.push_combo()
    assert data.combo == 2
    assert data.combo_timer == pytest.approx(config.COMBO_TIMEOUT)

    # 2.9 秒时还在
    assert data.tick_combo(2.9) is False
    assert data.combo == 2

    # 再 0.2 秒跨过 3 秒：清零，并且只报一次
    assert data.tick_combo(0.2) is True
    assert data.combo == 0
    assert data.combo_timer == 0.0
    # 已经清零后再 tick 不能重复报
    assert data.tick_combo(1.0) is False


def test_kill_refreshes_combo_timer():
    data = GameData()
    data.reset(keep_best=False)
    data.push_combo()
    data.tick_combo(2.0)
    assert data.combo_timer == pytest.approx(1.0)
    data.push_combo()          # 第二次击杀把倒计时拉回满
    assert data.combo == 2
    assert data.combo_timer == pytest.approx(config.COMBO_TIMEOUT)


# ---------------------------------------------------------------- 击杀得分

def test_kill_score_uses_multiplier():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)

    # 第 1 杀：1.0 倍
    assert score.on_enemy_died(enemy=FakeEnemy(100)) == 100
    assert data.score == 100
    assert data.combo == 1

    # 第 2 杀：1.1 倍，向下取整 110
    assert score.on_enemy_died(enemy=FakeEnemy(100)) == 110
    assert data.score == 210
    assert data.kills == 2


def test_kill_score_is_floored_not_rounded():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)
    score.on_enemy_died(enemy=FakeEnemy(100))      # combo=1 → 1.0
    # combo=2 → 1.1 倍，35 * 1.1 = 38.5，必须向下取整成 38
    assert score.on_enemy_died(enemy=FakeEnemy(35)) == 38


def test_enemy_without_data_scores_zero_but_still_counts_combo():
    """靶子之类没有 data 的实体会走这条路径，不能抛异常。"""
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)
    assert score.on_enemy_died(enemy=None) == 0
    assert data.combo == 1
    assert data.kills == 1


# ---------------------------------------------------------------- 生存分

def test_survival_score_accumulates_by_whole_second():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)

    # 60 帧 × 1/60 秒 = 1.0 秒 → 正好 +5
    for _ in range(60):
        score.update(1.0 / 60.0)
    assert data.score == config.SCORE_PER_SECOND

    # 再来 2 秒
    for _ in range(120):
        score.update(1.0 / 60.0)
    assert data.score == config.SCORE_PER_SECOND * 3


def test_survival_score_works_with_odd_dt():
    """dt 略小于 1/60 时不能因为累计误差而永远加不上去。"""
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)

    for _ in range(70):        # 70 × 0.015s = 1.05s
        score.update(0.015)
    assert data.score == config.SCORE_PER_SECOND


def test_no_survival_score_when_not_playing():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(State.PAUSED), player=FakePlayer(), data=data)
    for _ in range(120):
        score.update(1.0 / 60.0)
    assert data.score == 0
    assert data.time_alive == 0.0


# ---------------------------------------------------------------- 满血清波

def test_full_hp_wave_clear_bonus():
    data = GameData()
    data.reset(keep_best=False)
    player = FakePlayer(hp=100.0, max_hp=100.0)
    score = ScoreSystem(FakeState(), player=player, data=data)

    score.on_wave_cleared(wave=1)
    assert data.score == config.FULL_HP_WAVE_BONUS


def test_wave_clear_bonus_needs_full_hp():
    data = GameData()
    data.reset(keep_best=False)
    player = FakePlayer(hp=99.0, max_hp=100.0)
    score = ScoreSystem(FakeState(), player=player, data=data)

    score.on_wave_cleared(wave=2)
    assert data.score == 0


def test_wave_clear_bonus_skipped_when_dead():
    data = GameData()
    data.reset(keep_best=False)
    player = FakePlayer(hp=0.0, max_hp=100.0, alive=False)
    score = ScoreSystem(FakeState(), player=player, data=data)
    score.on_wave_cleared(wave=2)
    assert data.score == 0


# ---------------------------------------------------------------- 宝石 / 波次记录

def test_gem_pickup_adds_score():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)
    score.on_pickup(kind="gem")
    assert data.score == config.GEM_SCORE
    # 其他道具不加分
    score.on_pickup(kind="health")
    assert data.score == config.GEM_SCORE


def test_wave_number_never_decreases():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)
    score.on_wave_started(wave=5)
    score.on_wave_started(wave=3)
    assert data.wave == 5


# ---------------------------------------------------------------- reset

def test_reset_keeps_best_but_clears_run():
    data = GameData()
    data.best_score = 9000
    data.best_wave = 14
    data.score = 1234
    data.combo = 7
    data.kills = 40
    data.wave = 9

    data.reset()

    assert data.score == 0
    assert data.combo == 0
    assert data.combo_timer == 0.0
    assert data.kills == 0
    assert data.wave == 0
    assert data.time_alive == 0.0
    # 最高分是跨局数据，不能清
    assert data.best_score >= 9000
    assert data.best_wave >= 14


def test_score_system_reset_clears_counters():
    data = GameData()
    data.reset(keep_best=False)
    score = ScoreSystem(FakeState(), player=FakePlayer(), data=data)
    score.on_enemy_died(enemy=FakeEnemy(100))
    for _ in range(60):
        score.update(1.0 / 60.0)
    assert data.score > 0

    score.reset()
    assert data.score == 0
    assert data.combo == 0
    assert score.combo_resets == 0
