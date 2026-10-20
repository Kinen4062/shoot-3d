"""自建射线检测（替代 `ursina.raycast`）。

**为什么不用 ursina 的 raycast**

它的碰撞体挂在一个靠 `look_at()` 转向的可移动节点上，实测连续两次
参数完全相同的调用会得到不同的命中结果 —— 第一次的射线用的是上一次调用
留下的位置（起点 x 差了近 3 米），于是「准星压在靶子上，子弹却打中
身后 26 米的地面/墙」。它还按 `get_t()` 排序，而实测多个命中体的 t
全是同一个值，排序形同虚设，最终取谁由队列插入顺序决定。
这类问题不会报错，只会表现为「散布很奇怪、瞄准了打不中、伤害被吞」。

**这里的做法**

- `CollisionRay` 直接在工作空间里设 origin / direction，节点恒定为单位变换，
  不依赖任何节点旋转是否已刷新；
- 每次遍历前 `clear()` 队列，绝不跨调用残留；
- 自己算 `(命中点 - 起点)` 的长度排序取最近，不信任 `get_t()`；
- 距离与 ignore 在本地过滤，ignore 用**身份比较**，不会误伤同名实体。

坐标约定：`origin` / `direction` 都是世界坐标（与 `Entity.world_position`、
`camera.world_position` 同一套）。原因是 ray 节点挂在 `scene` 上，
而 `scene` 的变换是单位矩阵。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from panda3d.core import (
    BitMask32,
    CollisionHandlerQueue,
    CollisionNode,
    CollisionRay,
    CollisionTraverser,
    Point3,
)
from ursina import Vec3, scene

# ---- 一把全局复用的射线（无状态：每次调用都重设 origin/direction）----
_ray = CollisionRay()
_ray.setOrigin(Point3(0.0, 0.0, 0.0))
_ray.setDirection(Vec3(0.0, 0.0, 1.0))

_node = CollisionNode("hitscan_ray")
_node.set_into_collide_mask(BitMask32(0))    # 射线自己不进碰撞集合，不会被别的射线打到
_node.addSolid(_ray)

_ray_np = scene.attach_new_node(_node)       # 保持单位变换：射线坐标 == 世界坐标
_queue = CollisionHandlerQueue()
_traverser = CollisionTraverser("hitscan")
_traverser.addCollider(_ray_np, _queue)


@dataclass
class RayHit:
    hit: bool = False
    entity: object | None = None
    world_point: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))
    world_normal: Vec3 = field(default_factory=lambda: Vec3(0.0, 0.0, 0.0))
    distance: float = 0.0


def raycast(origin, direction, distance: float = 9999.0, ignore=()) -> RayHit:
    """从 origin 沿 direction 打一条射线，返回最近的命中。

    ignore 里的实体按**身份**跳过（`is`），不受 `==` 实现影响。
    超出 distance 的命中视为没打中。
    """
    d = Vec3(*direction)
    if d.length() < 1e-9:
        return RayHit()
    d.normalize()

    _ray.setOrigin(Point3(origin[0], origin[1], origin[2]))
    _ray.setDirection(d)
    _queue.clear_entries()          # 绝不跨调用残留上一次的命中
    _traverser.traverse(scene)

    best: RayHit | None = None
    for i in range(_queue.get_num_entries()):
        entry = _queue.get_entry(i)
        entity_np = entry.get_into_node_path().get_parent()
        entity = entity_np.getPythonTag("Entity") if entity_np is not None else None
        if entity is None:
            continue
        if any(entity is ig for ig in ignore):
            continue

        point = Vec3(*entry.get_surface_point(_ray_np))
        dist = (point - Vec3(origin)).length()
        if dist > distance:
            continue
        if best is not None and dist >= best.distance:
            continue

        best = RayHit(
            hit=True,
            entity=entity,
            world_point=point,
            world_normal=Vec3(*entry.get_surface_normal(_ray_np)).normalized(),
            distance=dist,
        )

    return best if best is not None else RayHit()
