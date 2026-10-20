"""窗口图标的四种传法对照实验（要开真窗口，几秒钟自动关）。

**用途**：Ursina 的 `icon=` 参数最终落到 `WindowProperties.setIconFilename()`，
在 Windows 上对「字符串路径」的解析有坑 —— 文件明明存在，仍会打

    :display:windisplay(warning): Could not find icon filename C:\\...\\icon.ico

然后退回 Panda3D 默认图标。这个脚本把四种传法各跑一遍，用来复核结论：

    python tools/_probe_icon.py abs      # 字符串绝对路径      -> 报 warning（坏）
    python tools/_probe_icon.py rel      # 字符串相对路径      -> 报 warning（坏）
    python tools/_probe_icon.py fname    # Filename.fromOsSpecific -> 干净（正确）
    python tools/_probe_icon.py none     # 空串 -> 把 CWD 当图标去加载，更怪的 warning

生产代码走 `main.window_icon()`，即 `fname` 那一路。
结论钉在 `tests/test_visuals.py::test_window_icon_is_os_specific_filename`。
"""
import os
import sys

from panda3d.core import Filename
from ursina import Ursina

mode = sys.argv[1]
abs_path = os.path.abspath("assets/textures/icon.ico")
icon = {
    "abs": abs_path,
    "fname": Filename.fromOsSpecific(abs_path),
    "rel": "assets/textures/icon.ico",
    "none": "",
}[mode]

app = Ursina(icon=icon, title=f"probe: {mode}", window_type="onscreen")
app.taskMgr.doMethodLater(0.6, lambda t: app.userExit(), "quit")
app.run()
print(f"=== mode={mode} finished ===")
