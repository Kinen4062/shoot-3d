"""MINI TPS 唯一入口。

M5 阶段：主菜单 / 暂停 / 结算 + 正式 HUD + 音效。

操作：
    菜单里用鼠标点 START → 进入 PLAYING
    WASD 移动 / SHIFT 疾跑 / SPACE 闪避（带无敌帧）/ 鼠标转视角
    左键开枪 / 右键瞄准 / R 换弹 / 1-2 切枪 / ESC 暂停

运行：直接运行本文件（PyCharm 里 Ctrl+Shift+F10 / 右键 Run 'main'）。
音效资源用 `python tools/gen_sfx.py` 生成（已生成过就不用再跑）。
"""
from __future__ import annotations

from pathlib import Path

from panda3d.core import Filename
from ursina import Ursina, application, color, window

from src.core import config
from src.core.clock import GameClock
from src.core.game_data import get_data
from src.core.game_state import GameLoop, GameStateMachine, State
from src.core.input_map import InputMap
from src.core.session import RunSession
from src.entities.arena import Arena
from src.entities.gun import Gun
from src.entities.player import Player
from src.systems.audio import AudioSystem
from src.systems.camera_rig import CameraRig
from src.systems.focus_watch import FocusWatch
from src.systems.enemy_system import EnemySystem
from src.systems.pickup_system import PickupSystem
from src.systems.player_control import PlayerController
from src.systems.projectile_system import ProjectileSystem
from src.systems.score import ScoreSystem
from src.systems.tracer import TracerSystem
from src.systems.wave import WaveSystem
from src.systems.weapon import WeaponSystem
from src.ui.combat_hud import CombatHud
from src.ui.score_popup import ScorePopup
from src.ui.crosshair import Crosshair
from src.ui.damage_flash import DamageFlash
from src.ui.debug_overlay import DebugOverlay
from src.ui.fps_counter import FpsCounter
from src.ui.menus import GameOverPanel, MainMenu, PauseMenu, quit_game
from src.ui.score_hud import ScoreHud
from src.ui.wave_banner import WaveBanner


def window_icon() -> Filename:
    """窗口图标的正确传法。

    **为什么不能直接传字符串路径**（实测 ursina 8.3 + panda3d 1.10）：
    `Ursina(icon="C:/.../icon.ico")` 会一路走到 `WindowProperties.setIconFilename()`，
    **即使文件真的存在**，Windows 窗口层仍会打

        :display:windisplay(warning): Could not find icon filename C:\\...\\icon.ico

    然后退回 Panda3D 的默认图标。原因是字符串被隐式转成 `Filename` 时
    走的是「文本路径」通道，窗口层拿它去 stat 会失败。
    换成 `Filename.fromOsSpecific()` 显式构造（内部是 `/c/Users/...` 形式）
    就一切正常 —— 见 `tools/_probe_icon.py` 的四组对照。

    文件真的缺失时照样会打 warning，但那时是**如实**报错，不是误报。
    注意别用空字符串或空 `Filename()` 当兜底：panda 会把「当前目录」
    当图标去加载，打出另一条更难理解的 warning。
    """
    return Filename.fromOsSpecific(config.resource_path(config.ICON))


def build_app() -> Ursina:
    app = Ursina(
        title=config.WINDOW_TITLE,
        icon=window_icon(),
        size=config.WINDOW_SIZE,
        vsync=config.VSYNC,
        development_mode=config.DEVELOPMENT_MODE,
        show_ursina_splash=False,
        borderless=False,
    )
    # 颜色统一走 config，且必须用 rgb32（0~255）。
    # 注意：color.rgb 是 rgba 的别名，只收 0~1，传 16/17/22 会被截断成 1.0 -> 纯白。
    window.color = color.rgb32(*config.BG_COLOR_RGB)
    # 默认退出快捷键是 alt+f4 / ctrl+shift+alt+q，开发期容易误触。
    window.exit_button.enabled = False

    build_world()
    return app


def anchor_asset_folder() -> None:
    """把 ursina 的 asset_folder 锚到项目根（打包后是 `sys._MEIPASS`）。

    **为什么必须显式设**：ursina 默认取「入口脚本所在目录」。
    开发期从项目根启动正好是对的，但有三个场景会错：
    - 从别的目录启动（`python C:\\...\\main.py`）；
    - 探针脚本在 `tools/` 下，asset_folder 变成 `tools/`；
    - 打包后入口是 exe，而资源在 `_MEIPASS`。

    一旦错位，`model="assets/models/xxx.glb"` 和 `texture="..."` 都会静默失败
    （只打一行 warning，画面变成纯色或干脆不画），这是最难查的一类问题。
    贴图我们已经在调用处绕开了（传绝对路径构造 Texture），
    但模型只能走这条路 —— `load_model` 同样只认 asset_folder 下的相对名，
    传绝对路径会 glob 不到（见 `docs/M6_建模指南.md`）。
    """
    application.asset_folder = Path(config.resource_path(""))


def build_world() -> dict:
    """装配整个世界，返回一个包含所有系统的字典。

    **为什么从 build_app 里拆出来**：探针（`tools/m5_probe.py`）需要用
    **完全相同的接线**跑一遍"菜单 → 对局 → 死亡 → 重开"。
    如果探针自己复制一份装配代码，它验证的就不是生产路径了。
    """
    anchor_asset_folder()
    clock = GameClock()
    game_state = GameStateMachine(clock)

    arena = Arena()
    player = Player()
    camera_rig = CameraRig(player, game_state)

    # 顺序说明：敌人系统需要玩家（打谁）和弹丸系统（远程兵怎么打），
    # 而弹丸系统又需要敌人列表（子弹不能打自己人）—— 所以先建敌人系统，
    # 再把弹丸系统塞回去。
    enemies = EnemySystem(arena=arena, player=player)
    projectiles = ProjectileSystem(player, enemies)
    enemies.projectiles = projectiles

    gun = Gun()
    tracer = TracerSystem()
    crosshair = Crosshair(game_state)
    weapon = WeaponSystem(player, camera_rig, game_state, enemies, tracer, crosshair, gun)
    controller = PlayerController(player, camera_rig, arena, game_state, weapon=weapon)
    waves = WaveSystem(enemies, arena, camera_rig, game_state)

    # ---- M4：计分与道具 ----
    # 两者共用同一份 GameData（唯一真相源）。HUD 也读它，
    # 所以分数不会出现"HUD 显示的和结算界面不一致"。
    data = get_data()
    score = ScoreSystem(game_state, player=player, data=data)
    pickups = PickupSystem(player, weapon)
    hud = ScoreHud(data, state=game_state)
    combat_hud = CombatHud(game_state, player, weapon, waves, enemies)
    # 击杀飘字。位置由 `score_gained` 事件带出来，不自己轮询计分系统。
    score_popup = ScorePopup(game_state)
    # 失焦自动暂停。放在 ui_systems 里：它要按真实时间轮询，且不受 clock.paused 影响。
    focus_watch = FocusWatch(game_state)
    # 帧率读数：右上角，BEST 下面。ursina 8 没有可用的内置 fps 显示。
    fps_counter = FpsCounter(game_state)

    # ---- M5：音效 ----
    # 资源是 `tools/gen_sfx.py` 程序化合成的；缺文件时系统只记一条日志，
    # 之后彻底跳过，不会每帧刷 warning，也不会崩。
    audio = AudioSystem()

    # ---- M5：界面 ----
    # 按钮回调在这里注入，界面层不认识 session/state 之外的任何东西。
    game_over = GameOverPanel(
        on_restart=lambda: session.start_run(),
        on_menu=lambda: session.return_to_menu(),
        audio=audio,
    )
    pause_menu = PauseMenu(
        on_resume=game_state.toggle_pause,
        on_menu=lambda: session.return_to_menu(),
        audio=audio,
        audio_system=audio,
    )
    main_menu = MainMenu(
        on_start=lambda: session.start_run(),
        on_quit=quit_game,
        audio=audio,
        data=data,
    )

    overlay = DebugOverlay(game_state, clock)
    overlay.watch = player       # 覆盖层显示玩家坐标 / 血量
    overlay.rig = camera_rig      # 覆盖层显示相机距离，用来确认贴墙时确实被拉近了
    overlay.weapon = weapon
    overlay.enemies = enemies
    overlay.waves = waves
    overlay.controller = controller
    overlay.score = score
    overlay.pickups = pickups
    overlay.audio = audio

    damage_flash = DamageFlash()
    wave_banner = WaveBanner()

    # ---- 事件接线 + 重开契约（全部集中在 session 里）----
    session = RunSession(
        state=game_state,
        player=player,
        controller=controller,
        rig=camera_rig,
        weapon=weapon,
        enemies=enemies,
        projectiles=projectiles,
        pickups=pickups,
        waves=waves,
        score=score,
        tracer=tracer,
        overlay=overlay,
        crosshair=crosshair,
        damage_flash=damage_flash,
        wave_banner=wave_banner,
        hud=hud,
        combat_hud=combat_hud,
        audio=audio,
        main_menu=main_menu,
        pause_menu=pause_menu,
        game_over=game_over,
        score_popup=score_popup,
    )
    session.register_listeners()
    # 状态机在构造时就已经是 MENU 了，那时 session 还不存在，
    # 所以开局要手动同步一次，否则主菜单不会显示（黑屏 + 只有调试覆盖层）。
    session.sync_ui(State.MENU)

    # ---- M6：背景音乐 ----
    # 开局就播（菜单里也该有音乐）。`start_bgm` 幂等，缺文件只返回 False 不抛异常。
    # 暂停/恢复由 `session.on_state_changed` 驱动，这里只负责"开始"。
    if audio.start_bgm():
        print(f"[M6] bgm on  {config.BGM_FILE}")
    else:
        print(f"[M6] bgm off  （state={audio.bgm_state}）")

    # systems 的顺序就是执行顺序：
    # 玩家移动 -> 相机跟上 -> 用最新相机姿态结算射线 -> 敌人 AI/攻击
    # -> 弹丸推进 -> 道具拾取 -> 波次补充 -> 计分 -> 拖尾计时。
    # 敌人放在武器之后：玩家这一帧打死的敌人，不该在同一帧还能反击。
    # 计分放在波次之后：满血清波的加分要用"本帧结算完"的血量判断。
    loop = GameLoop(
        systems=[controller, camera_rig, weapon, enemies, projectiles,
                 pickups, waves, score, tracer],
        ui_systems=[overlay, crosshair, damage_flash, wave_banner, hud,
                    combat_hud, score_popup, focus_watch, fps_counter, audio,
                    main_menu, pause_menu, game_over],
        clock=clock,
        state=game_state,
    )
    overlay.loop = loop

    keys = InputMap(game_state, weapon, controller)
    keys.install()
    overlay.input_map = keys

    print(
        f"[M5] state={game_state.state.name}  "
        f"logic_systems={len(loop.systems)}  ui_systems={len(loop.ui_systems)}  "
        f"types={list(enemies.types)}  waves={len(waves.table.waves)}  "
        f"listeners={session.listener_count}  best={data.best_score}  "
        f"audio={'on' if audio.enabled else 'off'}"
    )
    return {
        "clock": clock,
        "state": game_state,
        "arena": arena,
        "player": player,
        "rig": camera_rig,
        "enemies": enemies,
        "projectiles": projectiles,
        "weapon": weapon,
        "controller": controller,
        "waves": waves,
        "data": data,
        "score": score,
        "pickups": pickups,
        "hud": hud,
        "combat_hud": combat_hud,
        "score_popup": score_popup,
        "focus_watch": focus_watch,
        "fps_counter": fps_counter,
        "crosshair": crosshair,
        "audio": audio,
        "main_menu": main_menu,
        "pause_menu": pause_menu,
        "game_over": game_over,
        "session": session,
        "loop": loop,
        "overlay": overlay,
        "keys": keys,
    }


if __name__ == "__main__":
    build_app().run()
