# MINI TPS

一个用 Ursina 写的迷你第三人称射击游戏。目标是**做完整、能玩、能打包**，
不是做多。

## 环境

- Python **3.12+**（Ursina 8.x 的 `Requires-Python` 是 `>=3.12`，3.10/3.11 装不上）
- Ursina **8.3.x** + Panda3D 1.10.x

```powershell
# 项目里的 .venv 已经建好，重新安装依赖：
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```

## 测试

```powershell
python -m pytest
```

M0 的底座（时钟夹取、暂停、状态循环、事件隔离、延迟队列、目录完整性）、
M1 的方向换算与相机安全距离、M2 的射速/弹药/换弹/散布/瞄准归零/屏幕中心射线、
M3 的视锥与听觉、M4 的存档清洗与重开契约、M5 的音效去重与菜单流转、
M6 的飘字投影与失焦判定、资源路径、光照参数与窗口图标类型，
已固化成 **385 项**测试，不启动窗口就能跑，改完代码先跑一遍再开游戏。

端到端还有几个探针（同样不开窗口，用 `window_type='none'` 走真实接线）：

```powershell
python tools/m5_probe.py     # 57 项：菜单→开局→暂停→死亡→重开→回菜单→音效
python tools/m3_probe.py     # 22 项：AI / 波次 / 远程兵
python tools/aim_probe.py    # 4 项：屏幕中心射线
```

音效和贴图都是程序化生成的，被删了可以重新生成（幂等）：

```powershell
python tools/gen_sfx.py        # 10 个 wav
python tools/gen_textures.py   # 地面 2 米网格贴图
```

外观类改动没法用断言验收（"头太大"不会让测试失败），所以另外几个探针：

```powershell
python tools/_probe_look.py    # 离屏渲染四张图到 tools/_shots/：游戏内 / 正面近景 / 场地全景 / 四种敌人列队
python tools/export_blockout.py # 导出白模 OBJ + 正交四视图（进 Blender 的起点）
python tools/_probe_glb.py     # 自造一个 1x2x1 的 glb，量出引擎的坐标与缩放行为
python tools/_probe_icon.py abs  # 窗口图标四种传法对照（abs/rel/fname/none），要开真窗口
```

**窗口图标别传字符串**：`Ursina(icon="C:/.../icon.ico")` 即使文件存在，
Windows 窗口层也会打 `Could not find icon filename` 并退回默认图标。
生产代码走 `main.window_icon()`，内部用 `Filename.fromOsSpecific()` 包一层。

## 当前进度

- [x] **M0 骨架**：单主循环 / GameClock / 状态机 / 事件总线 / 输入接管 / 调试覆盖层
- [x] **M1 场地与移动**：地面 + 边界墙 + 柱子 / WASD 移动 + 疾跑 / 第三人称相机 + 相机碰撞
- [x] **M2 射击与命中**：weapons.json / hitscan + 拖尾池 / 弹药换弹 / 靶子受击闪白 / 准星
- [x] **M3 敌人 AI 与波次**：状态机 / 视锥 + 听觉 / 近战 + 远程兵 / 生成点规则 / 分离力
- [x] **M4 道具与计分**：5 种道具 + 掉落表 / 连击倍率 / 最高分持久化 / 重开契约
- [x] **M5 UI 与音效**：主菜单 / 暂停 / 结算 / 正式 HUD / 音效池 + 三档音量
- [x] **M6 打磨**：BGM / 击杀飘字 / 失焦暂停 / 资源路径审计 / FPS 显示 / 方块光照 + 玩家外观重做
      （**待实机验收**；打包按你的要求暂不做）

## 操作

开局停在主菜单，**鼠标点 START**（或按 `TAB` 循环状态机，调试用）。

对局中：`WASD` 移动、`Shift` 疾跑、`Space` 闪避、鼠标转视角、
左键开火、右键瞄准、`R` 换弹、`1/2` 切枪、`ESC` 暂停。
暂停面板里 `VOLUME` 按钮循环 5 档音量，`MAIN MENU` 回主菜单。

M0 验收：窗口里只有左上角的调试覆盖层。
按 `TAB` 在 `MENU → PLAYING → PAUSED → GAME_OVER → MENU` 之间循环；
按 `ESC` 在 `PLAYING / PAUSED` 之间切换（再按一次必须能恢复）。

M1 验收：能自由跑动且不抖；跑到边缘被墙挡住（`pos` 停在 ±27.05，因为墙有 1 米厚度）；
贴着墙或柱子转镜头，`camera` 那行的 `dist` 会从 6.00 掉到 **0.10 左右**、
`body` 变成 `off`（角色模型自动隐藏，避免从模型内部看出去），画面始终看不到墙内部。
`dist` 在正常贴墙时不应掉到 0.00 —— 那是侧向射线把偏移量重复扣了一次，已修。
相机是**过肩视角**（`CAM_SHOULDER = 0.55`）：角色偏在画面左下，准星正前方是空的；
挂点在眼高 1.70 米（不是头顶以上的 2.60 米），俯视感没那么强。

M2 验收：四个靶子在出生点前方（`targets: 4 alive`）。
`gun` 行显示弹匣、`HIP/AIM/RELOAD` 和**当前真实散布角**（瞄准时应为 `0.00deg`）；
按住右键立刻变 `AIM`、镜头拉近、肩偏移从 0.55 收到 0.28，准星压住靶子必中；
腰射散布：手枪 0.25°、步枪 0.5°（15 米内偏差小于靶子半宽的三分之一）；
`shots` 行的 `last=` / `dmg=` 是命中诊断：`last` 是这一发打到的对象名，
`dmg=Y` 才表示真的结算了伤害。**准星压住靶子却打不中时，先看这两个值**：
`last=target_dummy dmg=Y` 说明判定没问题，问题在观感；
`last=wall_n` / `last=ground` 说明射线向上或向下偏；
`last=target_dummy dmg=N` 说明打到了但没认成敌人。
`input` 行显示最近一次按键与 `L/R` 状态 —— 右键没反应时先看这行；
靶子中弹闪白、4 发步枪击杀、3.5 秒后原地复活；
连续按住左键时 `fps` 不应低于 M1 太多（拖尾走池，不创建新 Entity）。

M5 验收：主菜单（`START` / `QUIT` + 两行操作说明 + 背景镜头缓慢环绕 6°/秒）→
打完一波（左下血条三色分档 + 3 个护盾格、右下弹药和换弹条、顶部 `WAVE n · n LEFT`、
右上 `SCORE / BEST`）→ `ESC` 暂停（`RESUME` / `VOLUME` 五档 / `MAIN MENU`）→
死亡（慢镜头 + `YOU DIED` 四行数据，破纪录多一行 `NEW RECORD`）→
`RESTART`（控制台 `[M5] run reset (restarts=1, listeners=27)`，**波次回到 1 而不是 2**）→
`MAIN MENU`。全程控制台不应出现 Traceback 或 `缺音效 / 装载失败`。

M6 验收：主菜单就有 BGM（音量偏小是故意的，压枪声）；玩家是深色装甲 + 青色面罩 + 护肩，
脚下有一圈接触阴影；地面网格跑起来一格一格地过；击杀点飘 `+120` 上浮淡出（连杀变青色）；
**Alt+Tab 切出去再切回来应该已经暂停**（且不自动恢复）；右上角 FPS ≥55 绿 / 40~54 黄 / <40 红。

逐条清单和踩过的坑见：

- `M3_验收报告.md` — 敌人 AI 与波次
- `M4_验收清单.md` — 道具、计分、护盾外壳
- `M5_验收清单.md` — 界面、音效、菜单流转
- `M6_验收清单.md` — 观感、飘字、BGM、失焦暂停、帧率
- `M6_建模指南.md` — 想自己建模型换掉这堆方块，技术流程从这里开始
- `M6_形象设计建议.md` — 「不知道该做成什么样」看这份：配色约束、玩家三个方向、敌人共用模型的坑、三视图的正确用法

### 弹道方向为什么不用 `camera.forward`

`Entity.forward` 取的是节点局部 +Z，而"屏幕正中心是哪个方向"由投影用的 lens 定义。
两者是否同向取决于引擎内部的相机朝向约定，一旦不一致，弹道会整体偏离准星，
而且不报任何错。所以 `src/core/screen_ray.py` 直接对 lens 做反投影：
取屏幕中心在近/远裁剪面上的两个点，把它们的差向量从相机局部空间变换到世界空间。
屏幕中心就是准星圆心，因此这条射线与准星严格共线，fov、相机距离、肩偏移都影响不到它。

第一次开火时控制台会打印一行 `[aim] lens中心 与 camera.forward 夹角=...deg`，
这是可复核的现场证据：0 表示两者一致，非 0 表示必须走 lens。

## 目录约定

```
main.py              唯一入口
src/core/            时钟、状态机、事件总线、输入、配置
src/entities/        玩家、敌人、子弹、道具、场地（player_body / enemy_body 是外观）
src/systems/         武器、AI、感知、生成器、计分、音效
src/ui/              HUD、菜单、调试覆盖层
assets/              模型、字体、贴图、音频
data/                数值配置 json
tests/               单元测试
```

## 三条不能破的规矩

1. **只有 `GameLoop` 一个 Entity 实现 `update()`**。其余系统都是普通对象，
   由主循环按显式顺序调用。
2. **只有 `GameClock` 能读 `time.dt`**。暂停用 `clock.paused`，
   绝对不要用 `application.paused`（它会把全局输入回调一起关掉）。
3. **不要在业务代码里写按键字符串**，一律引用 `src/core/input_map.py` 的常量。
