"""
运行路径工具：开发目录与 PyInstaller 打包后的资源定位。
"""

import os
import sys


def app_dir() -> str:
    """
    返回程序根目录。
    - 开发：项目根目录
    - 打包：exe 所在目录
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """
    解析资源文件路径（如 image/xxx.png）。
    查找顺序：exe 旁 → exe/_internal（PyInstaller 6 onedir）→ _MEIPASS。
    """
    rel = os.path.join(*parts) if parts else ""
    root = app_dir()
    candidates = [
        os.path.join(root, rel),
        os.path.join(root, "_internal", rel),
    ]

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, rel))

    for path in candidates:
        if os.path.exists(path):
            return os.path.normpath(path)
    return os.path.normpath(candidates[0])
