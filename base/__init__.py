"""窗口自动化与任务控制相关模块。"""

from base.Base import WindowAutomation
from base.Controller import TaskController
from base.click_probe import ClickProbeResult, probe_click_backend
from base import ocr

__all__ = [
    "WindowAutomation",
    "TaskController",
    "ClickProbeResult",
    "probe_click_backend",
    "ocr",
]
