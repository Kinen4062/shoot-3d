"""护盾外壳（玩家身上的半透明罩子）的回归约束。

为什么这层视觉值得单独写测试：护盾是**一层随时会碎的保险**，
而它唯一的表现就是这个罩子。如果哪天改玩家模型、改父级缩放、
或者 `tick_shield_aura` 忘了被调用，表现是"捡了护盾看不出区别" ——
不报错、不掉帧，只是玩家以为自己没盾，于是不敢贴脸打。

四条硬约束：
1. 没盾时**必须**不可见（否则重开后旧罩子会留在身上）；
2. 层数越多越亮，且 alpha 落在不会糊住视线的区间；
3. **绝对不能挂 collider** —— 挂了之后射线会停在自己的罩子上，
   表现是"贴着敌人开枪却没伤害"，这是 M2 修过一次的老坑；
4. `reset()` 要把罩子收干净。
"""
import pytest

from src.core import config
from src.entities.player import Player


@pytest.fixture
def player():
    p = Player()
    yield p
    from ursina import destroy
    destroy(p)


def tick(player, frames=1, dt=1 / 60):
    for _ in range(frames):
        player.tick_shield_aura(dt)


def alpha(player):
    """当前罩子的不透明度，换算回 0~255 的字节。"""
    return round(player.shield_aura.color[3] * 255)


def test_no_shield_means_invisible(player):
    tick(player, 5)
    assert player.shield_aura.visible is False
    assert player.shield_charges == 0


def test_one_shield_shows_aura(player):
    player.add_shield()
    tick(player)
    assert player.shield_aura.visible is True
    base = config.SHIELD_AURA_ALPHA * 255
    # 呼吸会让它偏离基准 ±SHIELD_AURA_PULSE；另外 alpha 量化到 1/64（步长 4）
    assert abs(alpha(player) - base) <= config.SHIELD_AURA_PULSE * 255 + 5


def peak_alpha(player, frames=90):
    """跑满一个呼吸周期（0.9Hz ≈ 67 帧）取最大值。

    不能只 tick 一帧就比：呼吸相位不同，同一层数会量出不同字节。
    """
    best = 0
    for _ in range(frames):
        player.tick_shield_aura(1 / 60)
        best = max(best, alpha(player))
    return best


def test_more_charges_are_brighter(player):
    player.add_shield()
    one = peak_alpha(player)

    player.add_shield()
    two = peak_alpha(player)

    player.add_shield()
    three = peak_alpha(player)

    assert one < two < three, f"层数增加必须变亮：{one} / {two} / {three}"
    # 上限：3 层也不能亮到糊住视线（0.35 是"能看清人形"的上限）
    assert three <= int(0.35 * 255)


def test_absorb_reduces_brightness_then_hides(player):
    player.add_shield()
    player.add_shield()
    tick(player, 30)

    assert player.take_damage(1.0) is False, "护盾吸收时不该掉血"
    assert player.shield_charges == 1
    tick(player, 30)
    assert player.shield_aura.visible is True

    assert player.take_damage(1.0) is False
    assert player.shield_charges == 0
    tick(player)
    assert player.shield_aura.visible is False, "盾碎之后罩子必须消失"


def test_aura_has_no_collider(player):
    """挂了 collider 就会挡自己的子弹（M2 修过的坑）。"""
    player.add_shield()
    tick(player)
    assert player.shield_aura.collider is None


def test_dead_player_hides_aura(player):
    player.add_shield()
    tick(player)
    assert player.shield_aura.visible is True

    player.take_damage(1.0)          # 先把盾消耗掉，否则致命伤害会被盾吃掉
    assert player.shield_charges == 0
    player.take_damage(9999.0)
    assert player.alive is False
    tick(player)
    assert player.shield_aura.visible is False


def test_aura_hides_with_player():
    """相机贴墙时 camera_rig 会把玩家藏掉；罩子是子实体，必须一起消失。

    否则贴墙拉近时会出现"人不见了，剩一个球悬在半空"。
    """
    player = Player()
    try:
        player.add_shield()
        player.tick_shield_aura(1 / 60)
        assert player.shield_aura.is_hidden() is False

        player.visible = False
        assert player.shield_aura.is_hidden() is True

        player.visible = True
        assert player.shield_aura.is_hidden() is False
    finally:
        from ursina import destroy
        destroy(player)


def test_controller_drives_aura_every_frame():
    """`PlayerController.update` 必须每帧驱动罩子。

    漏了这一行不会报错，只会表现为"有盾但身上什么都没有" ——
    所以用假玩家钉住接线，不需要真引擎。
    """
    from types import SimpleNamespace

    from src.systems.player_control import PlayerController

    calls: list[float] = []
    player = SimpleNamespace(
        health=SimpleNamespace(tick=lambda dt: None),
        tick_buffs=lambda dt: None,
        tick_shield_aura=lambda dt: calls.append(dt),
        alive=True,
        speed_mult=1.0,
    )
    state = SimpleNamespace(in_state=lambda s: False)      # 不进 PLAYING，update 会提前返回
    controller = PlayerController(player, SimpleNamespace(yaw=0.0, aiming=False),
                                  SimpleNamespace(), state)

    controller.update(1 / 60)
    controller.update(1 / 60)
    assert calls == [pytest.approx(1 / 60), pytest.approx(1 / 60)]


def test_reset_clears_aura(player):
    player.add_shield()
    player.grant_speed_boost(1.5, 5.0)
    tick(player)
    assert player.shield_aura.visible is True

    player.reset()
    assert player.shield_aura.visible is False
    assert player.shield_charges == 0
    tick(player)
    assert player.shield_aura.visible is False


def test_alpha_is_not_written_every_frame(player):
    """只在量化后的字节变化时才写 color。

    每帧无条件写 color 会重建 Vec4 并让 Panda 重排透明队列，
    这里用 120 帧（2 秒）断言写入次数明显少于帧数。
    """
    player.add_shield()
    tick(player)

    writes = 0
    prop = type(player.shield_aura).color
    original_setter = prop.fset

    def counting_setter(self, value):
        nonlocal writes
        writes += 1
        original_setter(self, value)

    type(player.shield_aura).color = property(prop.fget, counting_setter)
    try:
        tick(player, 120)
    finally:
        type(player.shield_aura).color = prop

    assert writes < 60, f"120 帧写了 {writes} 次颜色，说明少了量化判断"
