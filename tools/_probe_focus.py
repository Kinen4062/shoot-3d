from panda3d.core import GraphicsWindow, WindowProperties  # noqa: E402

print("A:", [n for n in dir(GraphicsWindow) if "ocus" in n or "vent" in n])
print("B:", [n for n in dir(WindowProperties) if "inimiz" in n or "oreground" in n or "ctive" in n or "Open" in n])
