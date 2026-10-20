"""M1 回归：贴墙时相机必须被拉近，且永远不进墙。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panda3d.core import LensNode, PerspectiveLens
from ursina import Ursina, Vec3, camera, mouse
app = Ursina(window_type="none")
camera.lens = PerspectiveLens(); camera.lens_node = LensNode("ln", camera.lens); camera._cam = camera
type(mouse).locked = property(lambda self: False, lambda self, v: None)

from src.core import config
from src.core.clock import GameClock
from src.core.game_state import GameStateMachine, State
from src.entities.arena import Arena
from src.entities.player import Player
from src.systems.camera_rig import CameraRig

clock = GameClock(); state = GameStateMachine(clock)
arena = Arena(); player = Player(); rig = CameraRig(player, state)
state.set(State.PLAYING)

def settle(frames=40):
    for _ in range(frames):
        rig.update(1/60)
    return rig.distance, Vec3(camera.world_position)

# 贴着北墙（内表面 z = 27.5），镜头转到背后朝墙
player.position = (0, config.PLAYER_HEIGHT/2, config.ARENA_INNER - config.PLAYER_RADIUS)
rig.yaw = 180.0
d, cam = settle()
print(f"背朝北墙: dist={d:5.2f}  camera_z={cam.z:7.3f}  墙内表面=27.500  穿过墙? {cam.z > 27.5}")

rig.yaw = 0.0
d, cam = settle()
print(f"面朝北墙: dist={d:5.2f}  camera_z={cam.z:7.3f}")

# 场地中央，视野开阔
player.position = (0, config.PLAYER_HEIGHT/2, 0)
rig.pitch = 12.0
d, cam = settle()
print(f"场地中央: dist={d:5.2f}（应接近 {config.CAM_DIST}）")
