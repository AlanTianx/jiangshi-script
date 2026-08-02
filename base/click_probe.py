"""
点击能力探测：判断当前环境是否支持「窗口内不移动鼠标」的消息点击，
并在多种点击后端中选出最合适的一种。
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass, field

import win32api
import win32con
import win32gui

logger = logging.getLogger(__name__)

# ---- 仅用于探测的 SendInput 结构（与 Base 保持一致）----
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("mi", _MOUSEINPUT))


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT

# 点击后端标识
METHOD_MESSAGE = "message"  # Post/SendMessage，不移动物理鼠标
METHOD_SENDINPUT = "sendinput"  # SendInput 真鼠标（可恢复光标）
METHOD_MOUSE_EVENT = "mouse_event"  # mouse_event 回退

METHOD_LABELS = {
    METHOD_MESSAGE: "消息点击（不移动鼠标）",
    METHOD_SENDINPUT: "SendInput 真鼠标",
    METHOD_MOUSE_EVENT: "mouse_event 真鼠标",
}

# 这类窗口通常忽略合成鼠标消息，应直接走真鼠标
MESSAGE_UNFRIENDLY_CLASS_PREFIXES = (
    "Chrome_WidgetWin",
    "Chrome_RenderWidget",
    "Intermediate D3D",
    "CEF-",
    "UnityWndClass",
    "SDL_app",
    "GLFW",
    "HwndWrapper",  # WPF 部分场景
)


@dataclass
class ClickProbeResult:
    """一次探测的结论。"""

    method: str
    reason: str
    message_ok: bool = False
    sendinput_ok: bool = False
    mouse_event_ok: bool = False
    target_classes: list[str] = field(default_factory=list)

    @property
    def method_label(self) -> str:
        return METHOD_LABELS.get(self.method, self.method)


def collect_window_classes(hwnd, max_depth: int = 12) -> list[str]:
    """收集根窗口及若干层子窗口的类名，用于判断是否自绘/WebView。"""
    classes: list[str] = []
    if not hwnd or not win32gui.IsWindow(hwnd):
        return classes

    def add(h):
        try:
            name = win32gui.GetClassName(h) or ""
        except Exception:
            name = ""
        if name and name not in classes:
            classes.append(name)

    add(hwnd)
    current = [hwnd]
    for _ in range(max_depth):
        nxt = []
        for parent in current:

            def _enum(child, acc):
                acc.append(child)
                return True

            children = []
            try:
                win32gui.EnumChildWindows(parent, _enum, children)
            except Exception:
                children = []
            for child in children[:20]:
                add(child)
                nxt.append(child)
        current = nxt
        if not current:
            break
    return classes


def is_message_unfriendly(classes: list[str]) -> tuple[bool, str]:
    """窗口类是否通常不响应 PostMessage 鼠标消息。"""
    for cls in classes:
        for prefix in MESSAGE_UNFRIENDLY_CLASS_PREFIXES:
            if cls.startswith(prefix) or prefix in cls:
                return True, f"窗口类「{cls}」通常忽略消息点击"
    return False, ""


def probe_system_message_click() -> bool:
    """
    用隐藏测试窗口验证：本进程是否能通过 PostMessage 收到点击（不移动鼠标）。
    这只证明系统/本进程消息路径可用；目标游戏是否接受需另判。
    """
    received = {"down": False}
    class_name = f"JiangshiClickProbe_{int(time.time() * 1000)}"
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = _make_probe_wndproc(received)
    wc.lpszClassName = class_name
    wc.hInstance = win32api.GetModuleHandle(None)
    try:
        atom = win32gui.RegisterClass(wc)
    except win32gui.error:
        # 类名冲突时仍尝试继续
        atom = class_name

    hwnd = None
    try:
        hwnd = win32gui.CreateWindowEx(
            0,
            atom,
            "click-probe",
            win32con.WS_POPUP,
            0,
            0,
            40,
            40,
            0,
            0,
            wc.hInstance,
            None,
        )
        lparam = win32api.MAKELONG(10, 10)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)
        # 泵送本线程消息，等待测试窗处理
        deadline = time.time() + 0.5
        while time.time() < deadline and not received["down"]:
            win32gui.PumpWaitingMessages()
            time.sleep(0.01)
        return bool(received["down"])
    except Exception as exc:
        logger.warning("系统消息点击探测失败: %s", exc)
        return False
    finally:
        if hwnd:
            try:
                win32gui.DestroyWindow(hwnd)
            except Exception:
                pass
        try:
            win32gui.UnregisterClass(class_name, wc.hInstance)
        except Exception:
            pass


def _make_probe_wndproc(received: dict):
    def wndproc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_LBUTTONDOWN:
            received["down"] = True
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    return wndproc


def probe_target_message_deliverable(hwnd, prefer_message: bool = True) -> tuple[bool, str]:
    """
    检测能否向目标窗口投递消息（权限/完整性）。
    prefer_message=True 时：即使窗口类偏自绘/WebView，仍优先尝试消息点击
    （满足「窗口内不移动鼠标」；若游戏不响应可再手动切真鼠标）。
    """
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False, "目标窗口无效"

    classes = collect_window_classes(hwnd)
    unfriendly, why = is_message_unfriendly(classes)

    try:
        # PostMessage 失败常见于 UIPI（完整性级别不够）
        ok = win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        if not ok:
            if prefer_message:
                # 仍允许尝试鼠标消息：部分进程拦 WM_NULL 但不拦 WM_LBUTTON*
                return True, (
                    "WM_NULL 投递失败（可能权限/UIPI），仍优先窗口内点击；"
                    "若无效果请切换「真鼠标」或用管理员运行助手"
                )
            return False, "无法向目标窗口投递消息（权限/UIPI）"
    except Exception as exc:
        if prefer_message:
            return True, f"投递探测异常仍尝试窗口内点击: {exc}"
        return False, f"投递消息异常: {exc}"

    if unfriendly and not prefer_message:
        return False, why
    if unfriendly:
        return True, f"将使用窗口内点击（提示：{why}，无效时请改用真鼠标）"
    return True, "目标窗口可投递消息，使用窗口内点击"


def probe_sendinput() -> bool:
    """探测 SendInput 是否可用（对当前位置做一次极短点击注入）。"""
    try:
        x, y = win32api.GetCursorPos()
        vx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        vy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        vw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        vh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        if vw <= 1 or vh <= 1:
            vx = vy = 0
            vw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            vh = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        abs_x = int((x - vx) * 65535 / (vw - 1))
        abs_y = int((y - vy) * 65535 / (vh - 1))
        flags = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
        inputs = (_INPUT * 3)()
        inputs[0].type = _INPUT_MOUSE
        inputs[0].mi = _MOUSEINPUT(abs_x, abs_y, 0, flags, 0, 0)
        inputs[1].type = _INPUT_MOUSE
        inputs[1].mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, 0)
        inputs[2].type = _INPUT_MOUSE
        inputs[2].mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, 0)
        # 只验证“移动”事件能否发出，避免误点桌面图标
        sent = _user32.SendInput(1, ctypes.byref(inputs[0]), ctypes.sizeof(_INPUT))
        win32api.SetCursorPos((x, y))
        return sent == 1
    except Exception as exc:
        logger.warning("SendInput 探测失败: %s", exc)
        return False


def probe_mouse_event() -> bool:
    """探测 mouse_event 是否可调用（不改变最终光标位置）。"""
    try:
        pos = win32api.GetCursorPos()
        win32api.mouse_event(win32con.MOUSEEVENTF_MOVE, 0, 0, 0, 0)
        win32api.SetCursorPos(pos)
        return True
    except Exception as exc:
        logger.warning("mouse_event 探测失败: %s", exc)
        return False


def probe_click_backend(target_hwnd=None, prefer_message: bool = True) -> ClickProbeResult:
    """
    综合探测并选择点击后端：
    1) 默认优先 message（窗口内点击、不移动鼠标）
    2) 仅当消息路径不可用时，才改用 SendInput / mouse_event
    """
    system_message_ok = probe_system_message_click()
    sendinput_ok = probe_sendinput()
    mouse_event_ok = probe_mouse_event()

    classes: list[str] = []
    target_message_ok = False
    target_reason = "尚未连接游戏窗口"

    if target_hwnd:
        classes = collect_window_classes(target_hwnd)
        target_message_ok, target_reason = probe_target_message_deliverable(
            target_hwnd, prefer_message=prefer_message
        )

    # 已连窗：必须系统 + 目标都适合才用 message
    # 未连窗：系统消息路径可用则先暂选 message，连窗后再复核
    if target_hwnd:
        message_ok = bool(system_message_ok and target_message_ok)
    else:
        message_ok = bool(system_message_ok)

    if message_ok:
        if target_hwnd:
            reason = f"支持无鼠标位移点击：{target_reason}"
        else:
            reason = "系统支持消息点击（不移动鼠标）；连接游戏窗口后将复核目标是否接受"
        return ClickProbeResult(
            method=METHOD_MESSAGE,
            reason=reason,
            message_ok=True,
            sendinput_ok=sendinput_ok,
            mouse_event_ok=mouse_event_ok,
            target_classes=classes,
        )

    if not system_message_ok:
        deny = "系统侧消息点击探测未通过"
    else:
        deny = target_reason

    if sendinput_ok:
        return ClickProbeResult(
            method=METHOD_SENDINPUT,
            reason=f"不使用消息点击（{deny}），改用 SendInput",
            message_ok=False,
            sendinput_ok=True,
            mouse_event_ok=mouse_event_ok,
            target_classes=classes,
        )

    if mouse_event_ok:
        return ClickProbeResult(
            method=METHOD_MOUSE_EVENT,
            reason=f"SendInput 不可用，回退 mouse_event（{deny}）",
            message_ok=False,
            sendinput_ok=False,
            mouse_event_ok=True,
            target_classes=classes,
        )

    return ClickProbeResult(
        method=METHOD_SENDINPUT,
        reason="未检测到可用点击后端，将尝试 SendInput",
        message_ok=False,
        sendinput_ok=False,
        mouse_event_ok=False,
        target_classes=classes,
    )
