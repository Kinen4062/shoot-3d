import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ursina import Ursina, mouse  # noqa: E402

app = Ursina(window_type="none")
type(mouse).locked = property(lambda self: False, lambda self, v: None)

import main                             # noqa: E402
from src.core.game_state import State   # noqa: E402

world = main.build_world()
world["state"].set(State.PLAYING)
world["rig"].update(1 / 60)

popup = world["score_popup"]
player = world["player"]
from ursina import camera, window
camera.fov = 40.0
print("fov =", camera.fov, " aspect =", round(window.aspect_ratio, 3))
print("player ui =", popup._project(player.world_position))
print("spawn =", popup.on_score_gained(amount=120, position=player.world_position, combo=3))
live = [s for s in popup._slots if s["live"]]
print("slot text =", live[0]["label"].text, " visible =", live[0]["label"].visible,
      " pos =", tuple(round(v, 3) for v in live[0]["label"].position))
for _ in range(60):
    popup.update(1 / 60)
print("after 1.0s live =", sum(1 for s in popup._slots if s["live"]))
# 相机正后方：不该飘
print("behind =", popup._project((player.x, player.y, player.z - 30)))
