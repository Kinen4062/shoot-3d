"""全局常量与路径工具。

规则：任何模块都不允许再写魔法数字，一律从这里引用。
数值随开发会调整，集中在这里改一处就够。
"""
from __future__ import annotations

import os
import sys

# ---------- 窗口 ----------
WINDOW_TITLE = "MINI TPS"
WINDOW_SIZE = (1280, 720)
VSYNC = True
# 引擎自带的编辑器 UI（坐标轴、FPS、控制台）。M0 关掉，避免抢 F1/F2 等按键。
DEVELOPMENT_MODE = False

# 窗口图标，路径相对项目根（传给 Ursina 前要过 resource_path）。
# 注意：**不要**把这个字符串直接塞给 Ursina(icon=...)，否则 Windows 窗口层
# 即使文件存在也会打 "Could not find icon filename" 然后退回默认图标。
# 正确传法见 main.window_icon()（用 Filename.fromOsSpecific 包一层）。
ICON = "assets/textures/icon.ico"

# 窗口背景色，0~255 整数。
# 必须用 color.rgb32() 转换：ursina 的 color.rgb 是 rgba 的别名，只收 0~1，
# 传 16/17/22 会被截断成 1.0，整个窗口变成纯白（白底白字，看起来像文字丢了）。
BG_COLOR_RGB = (16, 17, 22)

# ---------- 时间 ----------
# 单帧最大逻辑步长（秒）。切出去再切回来时引擎会给出一个巨大的 time.dt，
# 不夹住的话一帧就走完几十秒，玩家直接穿墙飞到地图外。
MAX_FRAME_DT = 0.05

# ---------- 玩家 ----------
PLAYER_MAX_HP = 100.0
PLAYER_SPEED = 6.0
SPRINT_MULT = 1.6
DODGE_SPEED = 14.0
DODGE_TIME = 0.18
I_FRAME_TIME = 0.5
PLAYER_HEIGHT = 1.8
# 玩家实体的水平尺寸（碰撞盒宽/深）。M6 之后它已经**不是**外观尺寸了：
# 外观由 entities/player_body.py 用 26 个零件拼出来，这个值只决定碰撞体。
PLAYER_WIDTH = 0.7
PLAYER_RADIUS = 0.45      # 水平方向用于推开的半径

# ---------- 相机 ----------
MOUSE_SENS = 40.0
CAM_DIST = 6.0
# 瞄准时相机拉近。不能太近：2.0 米左右角色还只占画面一角，
# 再近就会糊住半个屏幕，反而看不见目标。
CAM_DIST_AIM = 2.0
CAM_HEIGHT = 1.7          # pivot 高于**脚底**的高度（玩家原点在身体中心，见 eye_offset）
# 过肩偏移：相机沿自身 right 方向偏出去多少米。
# 没有它，pivot 在眼睛高度，角色的头正好压在准星上，什么都看不见。
# 有了它，角色偏到画面左下，准星前方永远是空的。
CAM_SHOULDER = 0.55
# 瞄准时肩偏移必须**变大**，不能变小。
# 相机拉近到 2.0 米后，角色在画面里占比明显变大；偏移一小，
# 角色就挤在准星旁边，靶子中弹闪白正好被自己的身体挡住 ——
# 表现是「明明瞄准了、打上去却没反应」，其实是看不见命中反馈。
CAM_SHOULDER_AIM = 0.75

# 相机与遮挡面之间保留的余量，必须大于相机近裁剪面（camera.clip_plane_near = 0.1），
# 否则墙会切进近平面，画面出现「半堵墙糊在镜头上」。
CAM_COLLIDE_MARGIN = 0.35
# 相机离头这么近时隐藏角色模型，否则会从模型内部看出去，满屏青色。
CAM_HIDE_PLAYER_DIST = 1.1
# 相机碰撞打三条射线（中/左/右）。单条射线会从墙角、柱子边缘的缝隙里漏过去。
CAM_COLLIDE_SPREAD = 0.30
CAM_PITCH_MIN = -80.0
CAM_PITCH_MAX = 60.0

# ---------- 场地 ----------
ARENA_HALF = 28
WALL_HEIGHT = 3.0
WALL_THICKNESS = 1.0
# 墙的内表面。玩家能站到的最远处要按这个算，不能按 ARENA_HALF 算，
# 否则会站进墙里半个身位（墙是有厚度的）。
ARENA_INNER = ARENA_HALF - WALL_THICKNESS / 2
GROUND_THICKNESS = 1.0
SPAWN_RING = (18, 26)
MAX_ENEMIES = 30
ENEMY_SPAWN_COOLDOWN = 0.8

# ---------- 武器 ----------
WEAPON_FILE = "data/weapons.json"
DEFAULT_WEAPON = "rifle"
# 腰射散布来自 weapons.json，单位是度（整锥半角）。
# 右键瞄准时**严格归零**：M2 的验收标准是「准星压住就必中」，
# 留任何系数都做不到。所以这里没有 AIM_SPREAD_MULT 这类缩放常量，
# 是否瞄准直接决定 apply_spread 收到的是 0 还是配置值。
RECOIL_DEG = 0.55            # 每发镜头抬升角度（度）
RECOIL_RECOVER_RATE = 14.0   # 回弹速率，越大回得越快
MUZZLE_FLASH_TIME = 0.05
AIM_FOV = 32.0
# 排查开关：设成 True 后连腰射也零散布。
# 作用是把两类问题分开 —— 若开了它仍然偏，问题在弹道方向或准星；
# 若开了它就准，问题就是散布数值本身。
SPREAD_DISABLED = False
# 没造成伤害时在控制台留一行现场（打中了什么/多远/是否瞄准）。
# 定位「瞄准了却打不中」时开着，正常玩可以关掉。
SHOT_MISS_LOG = True
HIP_FOV = 40.0

# ---------- 霰弹枪 / 多武器 ----------
# 没拿在手上的枪自动换弹，但只按这个倍率走。
# 0.5 = 后台慢一半。"后台换弹"是为了消掉"换个枪还得从头装"的惩罚，
# "慢一半"是为了保留"现在换回来会卡一下"的取舍 —— 免费的话
# 玩家就没有任何理由在打空弹匣后留在手里等换弹了。
BACKGROUND_RELOAD_SCALE = 0.5
# 霰弹枪瞄准时的散布倍率。**不能归零**：10 颗弹丸全走同一条射线等于
# 一把精度无限、单发 200 的狙击枪，比步枪还强，
# 距离分段（近距离无衰减 / 远距离散射）的设计会整个失效。
SHOTGUN_AIM_MULT = 0.6

# ---------- 局内成长 ----------
# 每 5 波，**单颗弹丸**伤害 +1（第 5 波起 +1，第 30 波 +6，BOSS 层 +6）。
# 只加伤害不加射速：射速决定"手感节奏"，改它会把所有武器的手感一起搅乱；
# 伤害是纯数值，玩家感受到的是"怪变脆了"，正是要解决的那种"打半天"。
DAMAGE_BONUS_STEP = 5
# 第 20 波起换大弹匣（容量写在 weapons.json 的 late_mag_size）。
# 挑第 20 波是因为它是"通关前的最后一段"，也是敌人血量最厚的阶段：
# 弹匣不涨的话，后期一半时间花在换弹上，与其说是紧张不如说是烦。
MAG_UPGRADE_WAVE = 20

# 枪模型挂在角色右侧，纯视觉，不参与任何判定
# 右移量要大于护肩外沿（0.44），不然第三人称视角里枪被自己的肩甲挡住，
# 玩家的第一反应是"枪呢？"。0.46 让枪管从肩甲外侧露出来。
# 枪的挂载点（相对玩家**身体中心**，单位米）。
# RIGHT 必须大于护肩外沿（0.44）+ 枪半宽（0.065）≈ 0.505，否则第三人称
# 从背后看，枪会被自己的肩甲整个吃掉 —— 玩家会以为"我没拿枪"。
GUN_OFFSET_RIGHT = 0.52
# UP 对着右手握把的高度（hand_r 的局部 y 就是 0.30）。
GUN_OFFSET_UP = 0.30
# 往回收一点：枪身长 0.75 米，挂太靠前的话枪托会穿过胸甲。
GUN_OFFSET_FORWARD = 0.34
GUN_MUZZLE_FORWARD = 0.55

# ---------- 子弹拖尾（纯视觉）----------
TRACER_LIFE = 0.05
TRACER_WIDTH = 0.03
TRACER_POOL_SIZE = 64

# ---------- 敌人 ----------
HIT_FLASH_TIME = 0.07        # 受击闪白时长
DEATH_ANIM_TIME = 0.18       # 死亡缩放时长
ENEMY_SCALE = (0.9, 1.8, 0.9)
ENEMY_COLOR_RGB = (220, 70, 60)

# ---------- M2 静态靶子 ----------
TARGET_HP = 60.0
TARGET_RESPAWN = 3.0         # 0 = 不重生；M2 留靶子方便反复验收
TARGET_POSITIONS = ((0.0, 10.0), (-6.0, 14.0), (6.0, 16.0), (-12.0, 8.0))

# ---------- M3 敌人 AI ----------
ENEMY_FILE = "data/enemies.json"
WAVE_FILE = "data/waves.json"

# 感知节流（规划书 §5.3 的硬要求）。
# 每帧对 30 个敌人各打一次带遮挡的射线 = 1800 次/秒，引擎级射线撑不住。
# 8Hz 意味着每个敌人每 7~8 帧才判一次视线，玩家完全感知不到延迟。
PERCEPTION_HZ = 8.0
VIEW_HALF_ANGLE = 60.0       # 视锥半角（度），120° 总视野
LOSE_TARGET_TIME = 3.0       # 连续看不见多久判定丢失
# 听觉：玩家开枪时广播一个噪声点，半径内的敌人会被惊动。
NOISE_RADIUS = 30.0

# 转向速度（度/秒）。不给上限的话敌人会瞬间甩头，看起来像贴图在转。
ENEMY_TURN_RATE = 540.0

# 攻击落点容差：前摇结束时玩家可能刚好退开一点，
# 允许这么宽的误差仍判定命中，否则近战几乎永远打不到人。
ATTACK_RANGE_TOLERANCE = 0.4
# 前摇期间的蓄力缩放比例（视觉预警，玩家能看见「它要打了」）。
ATTACK_TELEGRAPH_SCALE = 0.15

# 敌人之间的最小间隙。分离力用平方距离比较，避免开方与新建 Vec3。
SEPARATION_GAP = 0.05

# 远程敌人弹丸
PROJECTILE_POOL_SIZE = 64
PROJECTILE_LIFE = 3.0
PROJECTILE_RADIUS = 0.12

# ---------- M3 玩家受伤反馈 ----------
DODGE_COOLDOWN = 0.9         # 两次闪避之间的最小间隔
DAMAGE_FLASH_TIME = 0.28     # 屏幕红闪时长
DAMAGE_FLASH_ALPHA = 0.32    # 红闪最大不透明度
SHAKE_ON_HIT = 1.8           # 受击相机抖动强度（度）
SHAKE_MAX = 4.0
SHAKE_DECAY = 9.0

# ---------- 准星 ----------
CROSSHAIR_BASE_GAP = 0.010
# 开火扩散只是视觉反馈，不改变实际散布角（实际值在 weapons.json）。
# 之前是 0.030，每发 +0.32，连射时十字会张得很大，看起来像霰弹枪，
# 而实测弹着点只有 0.2 米 —— 手感和数值完全脱节，所以收回一半。
CROSSHAIR_BLOOM_GAP = 0.016
CROSSHAIR_BLOOM_PER_SHOT = 0.10
CROSSHAIR_BLOOM_DECAY = 9.0
CROSSHAIR_BAR = (0.0016, 0.009)
CROSSHAIR_HITMARK_TIME = 0.12

# ---------- M4 拾取道具（规划书 §5.5）----------
# 道具用两个池（立方体 / 球体）。不共用一个池、运行时改 model：
# Entity 的 model setter 会走一遍模型查找与节点重建，虽然命中缓存，
# 但道具是高频 acquire 的对象，分池能让这条路完全恒定为「只改颜色和缩放」。
PICKUP_POOL_CUBE = 14
PICKUP_POOL_SPHERE = 10

PICKUP_LIFETIME = 30.0       # 道具多久后消失。规划书没写，但不加会把池耗光
PICKUP_BLINK_TIME = 5.0      # 最后 5 秒开始闪烁提示
PICKUP_BLINK_HZ = 6.0
PICKUP_RADIUS = 1.5          # 拾取判定：水平距离小于这个就捡起来
PICKUP_FLOAT_AMP = 0.10      # 上下浮动幅度（米）
PICKUP_FLOAT_HZ = 0.8
PICKUP_SPIN_RATE = 110.0     # 自转角速度（度/秒）
PICKUP_BASE_HEIGHT = 0.72    # 道具悬浮高度（相对地面）
PICKUP_DROP_CHANCE = 0.30    # 敌人死亡掉落概率（规划书 §5.5）

# 效果数值
HEALTH_PACK_AMOUNT = 30.0
SPEED_BOOST_MULT = 1.5
SPEED_BOOST_TIME = 10.0
GEM_SCORE = 250
# 规划书写「护盾吸收一次伤害」。这里允许叠到 3 层：
# 只允许 1 层的话，玩家已经有盾时再捡一个会毫无反馈，像道具坏了。
SHIELD_MAX_CHARGES = 3

# 护盾外壳（玩家身上那层半透明罩子）。
# 为什么需要它：护盾会在 0.5 秒内被一发打碎，只在覆盖层显示 `shield=2`
# 等于没做 —— 枪战里没人会去读左上角。层数必须能在余光里看出来。
SHIELD_AURA_RGB = (110, 235, 235)
SHIELD_AURA_ALPHA = 0.16     # 1 层时的基准不透明度
SHIELD_AURA_ALPHA_STEP = 0.05  # 每多一层加多少（3 层约 0.26）
SHIELD_AURA_PULSE = 0.045    # 呼吸幅度
SHIELD_AURA_PULSE_HZ = 0.9   # 呼吸频率

# ---------- M4 计分（规划书 §5.6）----------
COMBO_TIMEOUT = 3.0          # 连击多久不刷新就清零
COMBO_STEP = 0.1             # 每层连击的加成
COMBO_MAX_BONUS = 10         # 最多叠 10 层，即最高 2.0 倍
SCORE_PER_SECOND = 5         # 生存加分
FULL_HP_WAVE_BONUS = 500     # 满血清空一波的奖励

# ---------- M5 UI ----------
# 菜单里镜头缓慢环绕的角度速度（度/秒）。
# 0 = 静止。慢一点更像"看板"，快了会晕。
MENU_ORBIT_DEG = 6.0
# 面板淡入淡出时长（秒）。0 就是硬切，观感很廉价。
MENU_FADE_TIME = 0.18

# 菜单背景镜头的取景。比游戏内（CAM_DIST 6.0m）更远：
# 6 米时玩家那个 0.7×1.8 的方块会占掉画面左侧三分之一，
# 看起来像"贴着一张蓝色卡片"，不像在看竞技场。
# 俯角保持和游戏内一致的 12°：再压低（8°）就只剩天空和墙，
# 再抬高（18°）画面几乎全是地板，两种都看不出"这是竞技场"。
MENU_CAM_DIST = 9.0
MENU_CAM_PITCH = 12.0

# ---------- M5 音效（规划书 §5.7）----------
AUDIO_ENABLED = True
AUDIO_DIR = "assets/audio"
# 背景音乐。3.0MB 的 ogg，用流式播放（见 AudioSystem.start_bgm）。
# 文件缺失时和音效一样静默降级，不报错不崩。
BGM_FILE = "assets/audio/bgm.ogg"
# 三档音量：master（总）× sfx（音效）/ bgm（音乐）。
VOLUME_MASTER = 0.80
VOLUME_SFX = 0.90
# BGM 比音效低不少，原因是两条链路不对称：
# 音效走 ursina 的 Audio，volume 会再乘一个全局 0.5（Audio.volume_multiplier）；
# BGM 走 Panda 的 AudioSound（流式、音量准确，见 AudioSystem._load_bgm）。
# 实测平衡点：0.15 × 0.80 ≈ 0.12，大约是枪声（≈0.20）的六成，压得住又不抢戏。
VOLUME_BGM = 0.15
# 音量按钮的档位（主菜单 / 暂停面板循环切换）
VOLUME_STEPS = (0.0, 0.25, 0.50, 0.75, 1.00)
# 同一音效的实例数。步枪 10 发/秒，4 个足够轮转；1 个会被自己打断。
AUDIO_POOL_SIZE = 4
# 去重窗口（毫秒）。连射时两次触发间隔可能只有 1 帧，全放会互相抢占实例，
# 听感反而更闷；40ms 是"听得出连射但不会糊"的经验值。
AUDIO_DEDUPE_MS = 40.0
# 随机音高范围。固定音高会让人觉得"像机器在放同一段录音"。
AUDIO_PITCH_MIN = 0.95
AUDIO_PITCH_MAX = 1.05
# 每种音效的基准音量。受伤/击杀要比环境音更显眼。
SOUND_VOLUMES = {
    "shoot": 0.55,
    "shotgun": 0.72,
    "hit": 0.45,
    "kill": 0.65,
    "hurt": 0.85,
    "shield": 0.60,
    "pickup": 0.55,
    "reload": 0.50,
    "click": 0.35,
    "wave": 0.60,
    "gameover": 0.75,
}
# 每把枪的开火音效（键是 `WeaponData.name`）。没写到的回落到 `DEFAULT_SHOT_SFX`。
#
# 为什么霰弹枪要单独一条：它和步枪共用一段"砰"时，0.5 秒一发的连射和步枪
# 0.1 秒一发的连发在听觉上完全分不开 —— 换枪之后玩家唯一的反馈只剩 HUD 上的
# 数字在变。专属音效的差异是**低频更重、尾巴更长**（见 tools/gen_sfx.py），
# 这样"我拿的是霰弹枪"这件事不用看屏幕也知道。
#
# 名字必须同时出现在 SOUND_VOLUMES 里：音频系统拿它当白名单，
# 写错（比如少个字母）会退回步枪枪声，而不是变成一枪没声。
WEAPON_SHOT_SFX = {"shotgun": "shotgun"}
DEFAULT_SHOT_SFX = "shoot"

# ---------- M6 击杀飘字（规划书 §5.7）----------
# 同时最多 12 个。一波 8 只怪、每只 0.9 秒，峰值就在 12 附近；
# 再多也只是对象池里躺着，不占渲染。
POPUP_COUNT = 12
POPUP_LIFE = 0.9          # 一个飘字活多久（秒）
POPUP_RISE = 0.075        # 全程上升多少（ui 单位，1.0 = 整屏高）
POPUP_FONT = 1.35

# ---------- M6 帧率显示 ----------
FPS_SHOW = True
FPS_REFRESH = 0.5         # 刷新间隔（秒）。滑动平均问得再勤也不会更准。
FPS_GOOD = 55             # ≥ 这个值显示绿色
FPS_MIN_OK = 40           # ≥ 这个值黄色，低于它红色（规划书 §M6：低于 40 要优化）

# ---------- M6 画面观感（规划书 §M6 第 8 条）----------
# 方块光照开关。关掉就退回 ursina 默认的纯色平涂，方便对比「是不是光照的锅」。
LIT_SHADER = True
# 光照参数：世界法线点乘两个方向，再按权重混一个底光。
# 三个权重之和 + 底光 = 1.15（超过 1 是故意的：最亮面 0.95，
#   ursina 自带的 basic_lighting 最亮面只有 0.85，整体会暗一档）。
# 最暗面 0.64 —— 必须留在 0.6 以上：暗色场景里再暗一档，柱子背面就黑成一团。
LIGHT_KEY_DIR = (-0.72, 0.62, 0.31)    # 主光：左上前方
LIGHT_FILL_DIR = (0.35, 0.42, -0.84)   # 补光：右后上方，专门照亮背面
LIGHT_AMBIENT = 0.56                   # 底光
LIGHT_KEY_WEIGHT = 0.32
LIGHT_FILL_WEIGHT = 0.16
LIGHT_GAIN = 1.15

# 地面网格贴图。1 格 = GROUND_GRID_CELL 米。
# 没有网格时跑动完全没有速度感（这也是当初加柱子的原因之一）；
# 网格比柱子更直接：脚下一格一格地过，快慢立刻能看出来。
GROUND_GRID_TEXTURE = "assets/textures/grid.png"
GROUND_GRID_CELL = 2.0

# ---- M6 自制模型（留空 = 用代码拼的方块外观）----
# 填一个 **相对项目根** 的路径就会替换掉代码拼出来的外观，例如
#     PLAYER_MODEL = "assets/models/player.glb"
# 注意三件事（详见 docs/M6_建模指南.md）：
#   1. 加载器只认 asset_folder 下的相对名，**不能**包 resource_path
#      （绝对路径会 glob 不到，只打一行 warning 然后画不出来）；
#   2. 1 单位 = 1 米，正面朝 +Z，原点在身体中心；
#   3. 只改这里，别去动实体的 scale —— 碰撞盒、相机贴墙、reset 全按它算。
PLAYER_MODEL = ""
ENEMY_MODEL = ""
GUN_MODEL = ""

# 按敌人类型分别指定模型，键 = `data/enemies.json` 里的类型名
# （grunt / runner / tank / shooter）。没写到的类型回落到 ENEMY_MODEL，
# 两个都空就是方块。例如只换坦克：
#     ENEMY_MODELS = {"tank": "assets/models/tank.glb"}
#
# **重要约定**：四种敌人的体型是靠 `data.scale` 非等比拉伸做出来的
# （grunt 0.9×1.8×0.9、runner 0.7×1.5×0.7、tank 1.3×2.4×1.3、
#  shooter 0.8×1.7×0.8），所以自制模型要按 **1.8 米的标准身材**建，
# 不要照最终体型建 —— 否则 tank 的模型再乘 1.33 会变成 3.2 米高。
ENEMY_MODELS: dict[str, str] = {}

# 自制玩家模型的整体缩放。按米建的模型填 1.0；如果导出工具的单位不是米
# （Blockbench 的 1 单位不是 1 米），先量一下真高再填 1/实测高度×1.8 之类的值。
PLAYER_MODEL_SCALE = 1.0

# ---- M6 玩家外观 ----
# 配色从「一根蓝柱子」改成「深色装甲 + 青色发光件」：
# 主色压到中明度，让面罩/胸口灯成为唯一的高饱和点，
# 6 米外一眼能看到「人在哪儿、朝哪儿」，又不会像荧光棒。
PLAYER_ARMOR_DARK_RGB = (36, 44, 58)     # 关节、内衬、背包
PLAYER_ARMOR_RGB = (74, 98, 132)         # 胸甲、头盔主体
PLAYER_ARMOR_LIGHT_RGB = (120, 146, 178)  # 护肩、面甲边框
PLAYER_VISOR_RGB = (100, 235, 255)       # 面罩（朝向标识）
PLAYER_ACCENT_RGB = (70, 190, 235)       # 胸口核心、背包灯条
PLAYER_BOOT_RGB = (26, 30, 40)           # 靴子、手套
# 腿和上臂用的中间调。不能直接用 DARK：两根腿并在一起时它们会糊成一整块黑，
# 截图里看起来像"没有腿"。
PLAYER_ARMOR_LIMB_RGB = (56, 70, 92)

# 脚下的接触阴影。没有它角色像浮在半空 —— 第三人称尤其明显。
PLAYER_SHADOW_RADIUS = 0.46
PLAYER_SHADOW_ALPHA = 0.36
ENEMY_SHADOW_ALPHA = 0.34

# ---- M6 敌人外观 ----
# 敌人脸上的发光眼条：让它看起来是"有朝前的生物"而不是一个会动的箱子。
# 颜色按每种敌人的本体色提亮，具体在 enemy.py 里算。
ENEMY_EYE_LIGHTEN = 0.55

# ---------- 调试 ----------
DEBUG_OVERLAY = True

# 覆盖层刷新间隔（秒）。每帧重建文本会白白吃 CPU。
DEBUG_REFRESH_INTERVAL = 0.25

# 开发期调试热键（下一波 / 清场 / 无敌）：`MINITPS_DEV=1 python main.py` 打开。
#
# 默认关掉，不是"懒得做开关"：这三个键一旦钉在正式版里，玩家一次误触就能
# 跳过一整波、或者带着无敌打完 30 波 —— 那已经不是这个游戏的难度了。
# 环境变量的写法和 `MINITPS_LOG` 保持一致：认 1/true/yes/on，其余一律当关闭。
DEVELOPMENT_TOOLS = os.environ.get("MINITPS_DEV", "").strip().lower() in (
    "1", "true", "yes", "on",
)


def resource_path(rel: str) -> str:
    """把「相对项目根」的路径转成绝对路径。

    PyInstaller 打包后资源被解到 sys._MEIPASS，开发期则是本文件上溯三层
    （src/core/config.py -> src/core -> src -> 项目根）。
    所有 assets/ 与 data/ 的读写都必须经过这里，漏一个打包后就白屏。
    """
    base = getattr(
        sys,
        "_MEIPASS",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    return os.path.join(base, rel)


# 存档不能写程序目录：装到 Program Files 后没有写权限会直接崩。
SAVE_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "MiniTPS")
SAVE_FILE = os.path.join(SAVE_DIR, "save.json")
