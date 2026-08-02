"""
窗口自动化基础能力：查找/激活窗口、截图、模板匹配、点击。
依赖 Windows API（pywin32）与 OpenCV 图像匹配。

点击后端由 click_probe 在初始化/连窗时探测：
优先 message（不移动鼠标），不支持则自动改用 sendinput / mouse_event。
"""

import ctypes
import logging
import os
import time
from ctypes import wintypes

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab

from base.click_probe import (
    METHOD_MESSAGE,
    METHOD_MOUSE_EVENT,
    METHOD_SENDINPUT,
    ClickProbeResult,
    probe_click_backend,
)

logger = logging.getLogger(__name__)

# ---- SendInput（64 位兼容）----
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    )


class _INPUT(ctypes.Structure):
    # type 后由 ctypes 自动按对齐插入填充，适配 Win64 INPUT 布局
    _fields_ = (
        ("type", wintypes.DWORD),
        ("mi", _MOUSEINPUT),
    )


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_user32.SendInput.restype = wintypes.UINT


class WindowAutomation:
    """封装游戏窗口操作与图像识别。"""

    # 默认直接窗口内消息点击（不移动物理鼠标）；可改为 sendinput / mouse_event / auto
    click_method = METHOD_MESSAGE
    # True=探测也优先 message，不因 Chrome/WebView 自动改真鼠标
    prefer_message_click = True
    # 探测选定的实际后端（由 init_click_backend 写入）
    active_click_method = METHOD_MESSAGE
    click_probe_result: ClickProbeResult | None = None
    # 真鼠标点击后是否恢复光标原位置
    restore_cursor = True
    # 每次按钮点击后等待 UI 响应再继续下一步（秒）；过短可能页面未切完
    post_click_delay = 0.5
    # 可选：返回 True 时中断 post_click_delay（用于点「停止」立刻生效）
    stop_check = None
    # 灰度模板缓存：避免每次 find_image 都 imread
    _template_gray_cache: dict = {}

    def set_click_method(self, method: str):
        """手动切换点击方式：message / sendinput / mouse_event / auto。"""
        method = (method or METHOD_MESSAGE).lower()
        self.click_method = method
        if method == "auto":
            self.prefer_message_click = True
            self.active_click_method = (
                self.click_probe_result.method
                if self.click_probe_result
                else METHOD_MESSAGE
            )
        else:
            self.prefer_message_click = method == METHOD_MESSAGE
            self.active_click_method = method
        logger.info("手动切换点击方式: %s", self.active_click_method or method)

    def init_click_backend(self, target_hwnd=None) -> ClickProbeResult:
        """
        初始化/刷新点击后端：默认锁定窗口内消息点击（不移动鼠标）。
        仅当用户已手动选真鼠标时，才采用 SendInput 等探测结果。
        """
        result = probe_click_backend(
            target_hwnd, prefer_message=self.prefer_message_click
        )
        self.click_probe_result = result

        if self.click_method in (METHOD_SENDINPUT, METHOD_MOUSE_EVENT):
            # 尊重用户手动选择的真鼠标模式
            self.active_click_method = self.click_method
        elif self.prefer_message_click:
            # 强制窗口内点击，避免再被自动切去 SendInput
            self.active_click_method = METHOD_MESSAGE
            self.click_method = METHOD_MESSAGE
        else:
            self.active_click_method = result.method

        logger.info(
            "点击后端=%s — %s",
            self.active_click_method,
            result.reason,
        )
        return result

    def find_window(self, window_title=None):
        """
        按标题查找可见的顶层窗口（排除本进程窗口，避免匹配到助手自身 UI）。
        优先返回标题完全相等的窗口，否则返回标题包含关键字的第一个窗口。
        """
        if not window_title:
            return None

        self_pid = os.getpid()
        exact_hits = []
        partial_hits = []

        def callback(hwnd, _extra):
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == self_pid:
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title:
                return
            # 兼容旧版助手标题，避免把助手 UI 当成游戏窗口
            if "自动化助手" in title:
                return
            if title == window_title:
                exact_hits.append(hwnd)
            elif window_title in title:
                partial_hits.append(hwnd)

        win32gui.EnumWindows(callback, None)
        if exact_hits:
            return exact_hits[0]
        return partial_hits[0] if partial_hits else None

    def activate_window(self, hwnd):
        """
        恢复窗口（若最小化），并强制置于桌面最上层前台。
        使用临时 TOPMOST + AttachThreadInput，绕过 Windows 前台限制。
        """
        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"无效窗口句柄: {hwnd}")

        # 恢复最小化 / 确保可见
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

        # 临时置顶，确保压过其他窗口的 Z 序
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)

        # 绑定线程输入后设置前台，降低 SetForegroundWindow 被系统拒绝的概率
        foreground = win32gui.GetForegroundWindow()
        current_tid = win32api.GetCurrentThreadId()
        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        foreground_tid = 0
        if foreground:
            foreground_tid, _ = win32process.GetWindowThreadProcessId(foreground)

        attached_fg = False
        attached_target = False
        try:
            if foreground_tid and foreground_tid != current_tid:
                attached_fg = bool(
                    win32process.AttachThreadInput(current_tid, foreground_tid, True)
                )
            if target_tid and target_tid != current_tid:
                attached_target = bool(
                    win32process.AttachThreadInput(current_tid, target_tid, True)
                )

            win32gui.BringWindowToTop(hwnd)
            for action, fn in (
                ("SetForegroundWindow", lambda: win32gui.SetForegroundWindow(hwnd)),
                ("SetActiveWindow", lambda: win32gui.SetActiveWindow(hwnd)),
                ("SetFocus", lambda: win32gui.SetFocus(hwnd)),
            ):
                try:
                    fn()
                except Exception as exc:
                    # 跨进程时部分 API 会被系统拒绝，其余步骤仍可把窗口抬到最前
                    logger.debug("%s 失败: %s", action, exc)
        finally:
            if attached_target:
                win32process.AttachThreadInput(current_tid, target_tid, False)
            if attached_fg:
                win32process.AttachThreadInput(current_tid, foreground_tid, False)

        # 取消永久置顶属性，但保留当前最前 Z 序（避免一直压住其它窗口）
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        win32gui.BringWindowToTop(hwnd)
        time.sleep(0.2)

    def is_window_alive(self, hwnd) -> bool:
        """窗口句柄是否仍有效。"""
        try:
            return bool(hwnd) and bool(win32gui.IsWindow(hwnd))
        except Exception:
            return False

    def get_window_rect(self, hwnd):
        """返回窗口屏幕坐标与尺寸字典：left/top/right/bottom/width/height。"""
        if not self.is_window_alive(hwnd):
            raise ValueError(f"无效窗口句柄: {hwnd}")
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return {
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
        }

    def set_window_size(self, hwnd, width, height):
        """
        保持左上角不变，调整窗口外框宽高；成功返回 True。
        部分游戏/模拟器有最小尺寸限制，可能无法精确到目标值。
        """
        try:
            # 先显示再改尺寸，避免对最小化窗口设置无效
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            left, top, _, _ = win32gui.GetWindowRect(hwnd)
            win32gui.MoveWindow(hwnd, left, top, width, height, True)

            # 再尝试 SetWindowPos，提高部分宿主窗口的成功率
            flags = (
                win32con.SWP_NOZORDER
                | win32con.SWP_NOACTIVATE
                | win32con.SWP_SHOWWINDOW
            )
            win32gui.SetWindowPos(hwnd, 0, left, top, width, height, flags)

            rect = self.get_window_rect(hwnd)
            ok = rect["width"] == width and rect["height"] == height
            if not ok:
                logger.warning(
                    "窗口尺寸未完全生效: 期望=%sx%s 实际=%sx%s",
                    width,
                    height,
                    rect["width"],
                    rect["height"],
                )
            return ok
        except Exception as exc:
            logger.error("设置窗口大小失败: %s", exc)
            return False

    def prepare_game_window(self, hwnd, width, height):
        """
        连接游戏窗口的标准流程：设置尺寸 → 激活并置于桌面最上层。
        返回尺寸是否按预期生效。
        """
        sized = self.set_window_size(hwnd, width, height)
        self.activate_window(hwnd)
        return sized

    def capture_window(self, hwnd=None):
        """截取指定窗口（或全屏），返回 OpenCV BGR 图像；句柄失效时抛 ValueError。"""
        if hwnd:
            if not self.is_window_alive(hwnd):
                raise ValueError(f"目标窗口已关闭或句柄无效: {hwnd}")
            rect = self.get_window_rect(hwnd)
            screenshot = ImageGrab.grab(
                bbox=(rect["left"], rect["top"], rect["right"], rect["bottom"])
            )
        else:
            screenshot = ImageGrab.grab()
        return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)

    def _load_template_gray(self, template_path: str):
        """读取并缓存灰度模板图。"""
        cached = self._template_gray_cache.get(template_path)
        if cached is not None:
            return cached
        template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"模板图像不存在: {template_path}")
        self._template_gray_cache[template_path] = template
        return template

    def find_image_in_window(self, window_image, template_path, threshold=0.8):
        """
        在窗口截图中模板匹配。
        命中返回 {x, y, width, height, confidence}，否则返回 None。
        """
        hits = self.find_all_images_in_window(
            window_image, template_path, threshold=threshold, max_count=1
        )
        return hits[0] if hits else None

    def find_all_images_in_window(
        self,
        window_image,
        template_path,
        threshold=0.8,
        max_count=20,
    ):
        """
        模板多目标匹配（非极大值抑制去重）。
        返回按 confidence 降序的位置列表；无命中返回 []。
        """
        template = self._load_template_gray(template_path)
        window_gray = cv2.cvtColor(window_image, cv2.COLOR_BGR2GRAY)
        height, width = template.shape[:2]
        if (
            window_gray.shape[0] < height
            or window_gray.shape[1] < width
        ):
            return []

        result = cv2.matchTemplate(window_gray, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(result >= float(threshold))
        raw = []
        for y, x in zip(ys.tolist(), xs.tolist()):
            raw.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "width": int(width),
                    "height": int(height),
                    "confidence": float(result[y, x]),
                }
            )
        if not raw:
            return []

        # 按分数从高到低做简易 NMS，避免同一卡片重复命中
        raw.sort(key=lambda h: -h["confidence"])
        min_dy = max(8, int(height * 0.65))
        min_dx = max(8, int(width * 0.65))
        kept = []
        for hit in raw:
            if any(
                abs(hit["y"] - k["y"]) < min_dy and abs(hit["x"] - k["x"]) < min_dx
                for k in kept
            ):
                continue
            kept.append(hit)
            if len(kept) >= max(1, int(max_count)):
                break
        return kept

    def find_text_in_window(
        self,
        window_image,
        target_text,
        language="chi_sim",
        threshold=0.6,
        region=None,
    ):
        """
        使用本地中文 OCR 查找按钮/文字。
        依赖便携或系统 Tesseract + chi_sim；命中返回位置字典，否则 None。
        """
        from base import ocr as ocr_mod

        hit = ocr_mod.find_text(
            window_image,
            target_text,
            language=language,
            threshold=threshold,
            region=region,
        )
        if not hit:
            return None
        return {
            "x": hit.x,
            "y": hit.y,
            "width": hit.width,
            "height": hit.height,
            "text": hit.text,
            "confidence": hit.confidence,
            "center_x": hit.center[0],
            "center_y": hit.center[1],
        }

    def click_text_in_window(
        self,
        hwnd,
        target_text,
        language="chi_sim",
        threshold=0.6,
        region=None,
    ) -> bool:
        """截图并用 OCR 点击指定中文按钮；成功返回 True。"""
        window_image = self.capture_window(hwnd)
        hit = self.find_text_in_window(
            window_image,
            target_text,
            language=language,
            threshold=threshold,
            region=region,
        )
        if not hit:
            return False
        self.click_in_window(hwnd, hit["center_x"], hit["center_y"])
        return True

    def read_text_in_window(
        self, window_image, region=None, digits_only: bool = False
    ) -> str:
        """OCR 读取窗口或子区域全文（去空白），失败返回空串。"""
        from base import ocr as ocr_mod

        return (
            ocr_mod.read_region_text(
                window_image, region=region, digits_only=digits_only
            )
            or ""
        )

    def read_difficulty_digits(self, window_image, region=None) -> list[str]:
        """专用于环难度数字条，返回多个 OCR 数字候选（加强 10~20）。"""
        from base import ocr as ocr_mod

        return ocr_mod.read_difficulty_digits(window_image, region=region) or []

    def ocr_status(self) -> str:
        """返回 OCR 引擎状态文案。"""
        from base import ocr as ocr_mod

        return ocr_mod.status_text()

    def swipe_in_window(self, hwnd, x1, y1, x2, y2, steps: int = 12, step_delay: float = 0.02):
        """
        在窗口内拖拽滑动（用于好友列表翻页）。
        使用真鼠标轨迹，结束后尽量恢复光标位置。
        """
        import time as _time

        old_pos = win32api.GetCursorPos()
        sx1, sy1 = self._window_offset_to_screen(hwnd, x1, y1)
        sx2, sy2 = self._window_offset_to_screen(hwnd, x2, y2)
        self._ensure_clickable(hwnd)
        try:
            win32api.SetCursorPos((int(sx1), int(sy1)))
            _time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            for i in range(1, max(steps, 2) + 1):
                t = i / steps
                x = int(sx1 + (sx2 - sx1) * t)
                y = int(sy1 + (sy2 - sy1) * t)
                win32api.SetCursorPos((x, y))
                _time.sleep(step_delay)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        finally:
            try:
                win32api.SetCursorPos(old_pos)
            except Exception:
                pass

    def find_button_in_region(self, window_image, region, button_image_path, threshold=0.8):
        """
        在截图的指定子区域内匹配按钮模板。
        region: {x, y, width, height}
        命中额外包含 center_x / center_y。
        """
        button_template = cv2.imread(button_image_path, cv2.IMREAD_GRAYSCALE)
        if button_template is None:
            raise FileNotFoundError(f"按钮模板图像不存在: {button_image_path}")

        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        region_image = window_image[y : y + h, x : x + w]
        region_gray = cv2.cvtColor(region_image, cv2.COLOR_BGR2GRAY)

        result = cv2.matchTemplate(region_gray, button_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            button_x = x + max_loc[0]
            button_y = y + max_loc[1]
            button_w, button_h = button_template.shape[::-1]
            return {
                "x": button_x,
                "y": button_y,
                "width": button_w,
                "height": button_h,
                "confidence": max_val,
                "center_x": button_x + button_w // 2,
                "center_y": button_y + button_h // 2,
            }
        return None

    def click_in_window(self, hwnd, x, y, method=None, hold_ms=40, after_delay=None):
        """
        点击窗口内坐标（相对 GetWindowRect / 截图像素）。

        method:
          - None/"auto"：使用 init_click_backend 探测结果
          - "message"：消息点击，不移动物理鼠标
          - "sendinput"：SendInput 真鼠标
          - "mouse_event"：mouse_event 真鼠标回退

        after_delay: 点击后等待秒数；None 则用 post_click_delay（抢环可传更短间隔）。
        """
        mode = (method or self.click_method or "auto").lower()
        if mode == "auto":
            mode = (self.active_click_method or METHOD_SENDINPUT).lower()

        try:
            screen_x, screen_y = self._window_offset_to_screen(hwnd, x, y)

            if mode == METHOD_MESSAGE:
                # 只点最深子窗 + 根窗（整链/全部 RenderWidget 会极慢）
                chain = self._message_target_chain(hwnd, screen_x, screen_y)
                targets = []
                if chain:
                    targets.append(chain[0])
                if hwnd not in targets:
                    targets.append(hwnd)
                for target in targets:
                    client_x, client_y = win32gui.ScreenToClient(
                        target, (screen_x, screen_y)
                    )
                    self._click_post_message(
                        target, int(client_x), int(client_y), hold_ms=hold_ms
                    )
                return

            # 真鼠标类后端：先保证窗口可点中
            self._ensure_clickable(hwnd)
            screen_x, screen_y = self._window_offset_to_screen(hwnd, x, y)

            if mode == METHOD_MOUSE_EVENT:
                self._click_mouse_event(screen_x, screen_y, hold_ms=hold_ms)
                return

            if mode != METHOD_SENDINPUT:
                logger.warning("未知点击方式 %s，回退 SendInput", mode)
            self._click_sendinput(screen_x, screen_y, hold_ms=hold_ms)
        finally:
            # 等 UI 切换完成后再进行下一步；停止信号到来时可提前结束等待
            if after_delay is None:
                delay = float(getattr(self, "post_click_delay", 0) or 0)
            else:
                delay = float(after_delay or 0)
            if delay > 0:
                deadline = time.time() + delay
                while time.time() < deadline:
                    checker = getattr(self, "stop_check", None)
                    if callable(checker) and checker():
                        break
                    time.sleep(min(0.05, max(0.0, deadline - time.time())))

    def _ensure_clickable(self, hwnd):
        """确保目标窗口可见且位于 Z 序前方，供 SendInput 命中。"""
        if not win32gui.IsWindow(hwnd):
            raise ValueError(f"无效窗口句柄: {hwnd}")
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        else:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
        # 先 TOPMOST 再取消，强制抬到普通窗口最前（不依赖前台权限）
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        win32gui.BringWindowToTop(hwnd)
        time.sleep(0.05)

    def _window_offset_to_screen(self, hwnd, x, y):
        """截图/外框相对坐标 → 屏幕坐标。"""
        left, top, _, _ = win32gui.GetWindowRect(hwnd)
        return left + int(x), top + int(y)

    def _resolve_message_target(self, hwnd, screen_x, screen_y):
        """解析实际接收消息的子窗口及其客户区坐标。"""
        target = self._deepest_child_from_screen(hwnd, screen_x, screen_y)
        client_x, client_y = win32gui.ScreenToClient(target, (screen_x, screen_y))
        return target, int(client_x), int(client_y)

    def _message_target_chain(self, root_hwnd, screen_x, screen_y):
        """
        收集点击点对应的窗口链（根→子），并补充常见渲染子窗。
        返回顺序：最深子窗口优先，避免只点到外壳 HWND。
        """
        chain = []
        current = root_hwnd
        seen = set()
        for _ in range(20):
            if not current or current in seen:
                break
            seen.add(current)
            chain.append(current)
            try:
                client_pt = win32gui.ScreenToClient(current, (screen_x, screen_y))
                if hasattr(win32gui, "RealChildWindowFromPoint"):
                    child = win32gui.RealChildWindowFromPoint(current, client_pt)
                else:
                    child = win32gui.ChildWindowFromPoint(current, client_pt)
            except Exception:
                break
            if not child or child == current:
                break
            current = child

        # 最深优先（不再枚举全部 RenderWidget，避免每次点击扫几十个子窗）
        return list(reversed(chain))

    def _deepest_child_from_screen(self, root_hwnd, screen_x, screen_y):
        """从根窗口起，按屏幕点向下找到最深子窗口。"""
        chain = self._message_target_chain(root_hwnd, screen_x, screen_y)
        return chain[0] if chain else root_hwnd

    @staticmethod
    def _make_mouse_lparam(client_x, client_y):
        """打包 WM_* 鼠标消息的 lParam（客户区坐标）。"""
        return win32api.MAKELONG(client_x & 0xFFFF, client_y & 0xFFFF)

    def _click_post_message(self, hwnd, client_x, client_y, hold_ms=40):
        """向 HWND 发送鼠标消息（不移动物理鼠标）。"""
        lparam = self._make_mouse_lparam(client_x, client_y)
        # Post + Send 双投递，提高不同宿主的接收概率
        for msg, wparam in (
            (win32con.WM_MOUSEMOVE, 0),
            (win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON),
            (win32con.WM_LBUTTONUP, 0),
        ):
            try:
                win32gui.PostMessage(hwnd, msg, wparam, lparam)
            except Exception:
                pass
            try:
                win32gui.SendMessageTimeout(
                    hwnd,
                    msg,
                    wparam,
                    lparam,
                    win32con.SMTO_ABORTIFHUNG,
                    30,
                )
            except Exception:
                try:
                    win32gui.SendMessage(hwnd, msg, wparam, lparam)
                except Exception:
                    pass
            if msg == win32con.WM_LBUTTONDOWN:
                time.sleep(max(hold_ms, 1) / 1000.0)
        logger.debug(
            "窗口内消息点击 hwnd=%s client=(%s,%s)", hwnd, client_x, client_y
        )

    def _click_mouse_event(self, screen_x, screen_y, hold_ms=40):
        """SetCursorPos + mouse_event 单击，并可恢复光标。"""
        screen_x, screen_y = int(screen_x), int(screen_y)
        old_pos = win32api.GetCursorPos() if self.restore_cursor else None
        try:
            win32api.SetCursorPos((screen_x, screen_y))
            time.sleep(0.02)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(max(hold_ms, 1) / 1000.0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            logger.debug("mouse_event 点击 screen=(%s,%s)", screen_x, screen_y)
        finally:
            if old_pos is not None:
                try:
                    win32api.SetCursorPos(old_pos)
                except Exception:
                    pass

    def _click_sendinput(self, screen_x, screen_y, hold_ms=40):
        """
        使用 SendInput 绝对坐标左键单击（常见 Windows 上最稳）。
        失败时回退 mouse_event。默认点击后恢复光标位置。
        """
        screen_x, screen_y = int(screen_x), int(screen_y)
        old_pos = win32api.GetCursorPos() if self.restore_cursor else None

        try:
            if not self._sendinput_absolute_click(screen_x, screen_y, hold_ms):
                # 回退 mouse_event；光标恢复由外层统一处理
                prev = self.restore_cursor
                self.restore_cursor = False
                try:
                    self._click_mouse_event(screen_x, screen_y, hold_ms=hold_ms)
                finally:
                    self.restore_cursor = prev
        finally:
            if old_pos is not None:
                try:
                    win32api.SetCursorPos(old_pos)
                except Exception:
                    pass

    def _sendinput_absolute_click(self, screen_x, screen_y, hold_ms=40) -> bool:
        """SendInput 绝对移动并单击；成功返回 True。"""
        vx = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        vy = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)
        vw = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        vh = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        if vw <= 1 or vh <= 1:
            vx = vy = 0
            vw = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
            vh = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

        abs_x = int((screen_x - vx) * 65535 / (vw - 1))
        abs_y = int((screen_y - vy) * 65535 / (vh - 1))
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK

        events = (
            (abs_x, abs_y, move_flags),
            (0, 0, MOUSEEVENTF_LEFTDOWN),
            (0, 0, MOUSEEVENTF_LEFTUP),
        )
        inputs = (_INPUT * len(events))()
        for i, (dx, dy, flags) in enumerate(events):
            inputs[i].type = INPUT_MOUSE
            inputs[i].mi = _MOUSEINPUT(dx, dy, 0, flags, 0, 0)

        # 先移动，再按下/抬起（中间稍等，提高命中率）
        sent = _user32.SendInput(1, ctypes.byref(inputs[0]), ctypes.sizeof(_INPUT))
        if sent != 1:
            logger.warning("SendInput 移动失败 err=%s", ctypes.get_last_error())
            return False
        time.sleep(0.02)
        inputs[1].mi = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0)
        inputs[2].mi = _MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0)
        down = (_INPUT * 1)(inputs[1])
        up = (_INPUT * 1)(inputs[2])
        if _user32.SendInput(1, down, ctypes.sizeof(_INPUT)) != 1:
            return False
        time.sleep(max(hold_ms, 1) / 1000.0)
        if _user32.SendInput(1, up, ctypes.sizeof(_INPUT)) != 1:
            return False
        logger.debug("SendInput 点击 screen=(%s,%s)", screen_x, screen_y)
        return True
