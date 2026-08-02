"""
程序入口：DPI 感知、工作目录，然后启动图形界面。
打包为 exe 后须先启用 DPI 感知，否则高分屏点击会偏移。
运行日志仅在 UI 展示，不落盘。
"""

import ctypes
import logging
import os


def _enable_dpi_awareness():
    """
    启用 Per-Monitor DPI 感知，保证截图坐标与点击坐标一致。
    必须在创建任何窗口之前调用。
    """
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _prepare_workdir():
    """将工作目录切到程序根目录，便于相对路径与资源定位。"""
    from base.paths import app_dir

    root = app_dir()
    try:
        os.chdir(root)
    except OSError:
        pass
    return root


def main():
    """初始化环境后打开 UI。"""
    _enable_dpi_awareness()
    _prepare_workdir()

    # 运行日志仅在 UI 滚动区展示，不写 app.log 等磁盘文件
    logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])

    # 初始化本地中文 OCR（便携 tesseract/ 或系统安装）；详情由 UI 展示
    try:
        from base import ocr as ocr_mod

        ocr_mod.configure_tesseract()
    except Exception:
        pass

    import ui

    ui.main()


if __name__ == "__main__":
    main()
