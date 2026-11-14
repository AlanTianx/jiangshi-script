"""
自动化助手主界面：连接游戏窗口 → 单选功能 → 开始/停止运行。
"""

import datetime
import os
import queue
import tempfile
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image, ImageGrab

from base.Base import WindowAutomation
from base.Controller import TaskController

try:
    import windnd
except ImportError:  # 无 windnd 时仍可用粘贴 / 浏览
    windnd = None

# 目标窗口与固定尺寸（模板坐标依赖此分辨率）
WINDOW_TITLE = "向僵尸开炮"
WINDOW_WIDTH = 418
WINDOW_HEIGHT = 803

# UI 轮询间隔（毫秒）
POLL_INTERVAL_MS = 100
# 界面日志固定滚动窗口：最多保留最近 LOG_MAX_LINES 行，超出删最旧
LOG_MAX_LINES = 400

# 颜色
COLOR_WARN = "#f0a500"
COLOR_ERROR = "#e63946"
COLOR_OK = "#52b788"
COLOR_MUTED = "#9aa0a6"
COLOR_ACCENT = "#7aa2f7"
COLOR_START = "#2d6a4f"
COLOR_START_HOVER = "#1b4332"
COLOR_STOP = "#c1121f"
COLOR_STOP_HOVER = "#780000"

# (内部标识, 标题, 说明)
TASK_OPTIONS = (
    ("huan", "打环", "独立串行：被邀请→按最低难度接环→超时离队→结算返回"),
    ("saodang", "扫荡", "独立串行：开局→刷技能→达标退出重开"),
    ("me_invite", "主动邀请", "独立串行：邀指定好友→开战→结算后再邀"),
    (
        "qianghuan",
        "招募抢环",
        "抢卡入队后等开战；5s未进游戏则点消息回招募续抢，开战则等结束后再抢",
    ),
)

# 打环：队长不开局超时离队可选秒数
LOBBY_WAIT_OPTIONS = ("5", "10", "15", "20")
# 打环：最低接受难度 1~20
MIN_DIFFICULTY_OPTIONS = tuple(str(i) for i in range(1, 21))
# 好友名字截图允许的扩展名
FRIEND_IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}

FONT_UI = ("微软雅黑", 13)
FONT_UI_BOLD = ("微软雅黑", 13, "bold")
FONT_BTN = ("微软雅黑", 14, "bold")
FONT_LOG = ("Consolas", 11)


class App(ctk.CTk):
    """主窗口：连接状态 + 单选任务 + 启停控制 + 日志。"""

    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 标题避免包含游戏窗口关键字，防止「查找窗口」误匹配到本程序
        self.title("自动化助手 · 打环/扫荡/邀请/抢环")
        self.geometry("540x900")
        self.minsize(500, 760)

        self.auto = WindowAutomation()
        self.target_window = None
        self.controller = None

        self.task_var = ctk.StringVar(value=TASK_OPTIONS[0][0])
        self.lobby_wait_var = ctk.StringVar(value="10")
        self.min_diff_var = ctk.StringVar(value="1")
        self.friend_name_var = ctk.StringVar(value="")
        self.friend_template_path = ""  # 仅指向本次会话临时文件
        self._friend_temp_path = ""  # 临时 png，退出/清除时删除
        self._friend_preview_image = None  # CTkImage 引用，防止被回收
        self._radio_buttons = []

        self._build_ui()
        self._setup_friend_image_dnd()
        self._sync_controls()
        # 启动后做一次系统级点击能力初始化（不依赖游戏窗口）
        self.after(200, self._init_click_backend)
        self.after(POLL_INTERVAL_MS, self._poll)

    # ---------- 布局 ----------
    def _build_ui(self):
        """组装四块区域：连接 / 选功能 / 启停 / 日志。"""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        pad = {"padx": 14, "pady": (12, 6)}

        self._build_window_section(pad)
        self._build_task_section(pad)
        self._build_control_section(pad)
        self._build_log_section()

        self._log("请先运行游戏并保持窗口可见，再点击「查找并激活」。")
        self._log("默认使用「窗口内点击」（不移动鼠标）；若游戏无反应再切换「真鼠标」。")
        # 显示中文 OCR 状态
        try:
            self._log(self.auto.ocr_status())
        except Exception as exc:
            self._log(f"OCR 状态读取失败: {exc}")

    def _build_window_section(self, pad):
        """游戏窗口连接区 + 点击后端状态。"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=0, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(frame, text="游戏窗口", font=FONT_UI_BOLD).grid(
            row=0, column=0, columnspan=3, padx=12, pady=(12, 4), sticky="w"
        )
        ctk.CTkLabel(frame, text="状态", width=48).grid(
            row=1, column=0, padx=(12, 4), pady=(0, 4), sticky="w"
        )
        self.window_status = ctk.CTkLabel(
            frame, text="未连接", text_color=COLOR_WARN, anchor="w"
        )
        self.window_status.grid(row=1, column=1, padx=4, pady=(0, 4), sticky="ew")
        self.find_btn = ctk.CTkButton(
            frame, text="查找并激活", width=120, command=self._find_window
        )
        self.find_btn.grid(row=1, column=2, padx=(4, 12), pady=(0, 4))

        ctk.CTkLabel(frame, text="点击", width=48).grid(
            row=2, column=0, padx=(12, 4), pady=(0, 4), sticky="w"
        )
        self.click_mode_var = ctk.StringVar(value="message")
        self.click_mode_seg = ctk.CTkSegmentedButton(
            frame,
            values=["窗口内点击", "真鼠标"],
            command=self._on_click_mode_changed,
        )
        self.click_mode_seg.set("窗口内点击")
        self.click_mode_seg.grid(
            row=2, column=1, columnspan=2, padx=(4, 12), pady=(0, 4), sticky="ew"
        )

        self.click_status = ctk.CTkLabel(
            frame, text="默认：窗口内消息点击（不移动鼠标）", text_color=COLOR_MUTED, anchor="w"
        )
        self.click_status.grid(
            row=3, column=0, columnspan=3, padx=12, pady=(0, 12), sticky="ew"
        )

    def _build_task_section(self, pad):
        """功能单选区。"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=1, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(frame, text="选择功能", font=FONT_UI_BOLD).grid(
            row=0, column=0, padx=12, pady=(12, 6), sticky="w"
        )

        for i, (key, title, desc) in enumerate(TASK_OPTIONS, start=1):
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.grid(row=i, column=0, sticky="ew", padx=8, pady=2)
            row.grid_columnconfigure(1, weight=1)

            rb = ctk.CTkRadioButton(
                row,
                text=title,
                variable=self.task_var,
                value=key,
                font=FONT_UI,
                command=self._on_task_changed,
            )
            rb.grid(row=0, column=0, padx=(8, 10), pady=6, sticky="w")
            self._radio_buttons.append(rb)

            ctk.CTkLabel(row, text=desc, text_color=COLOR_MUTED, anchor="w").grid(
                row=0, column=1, padx=(0, 12), pady=6, sticky="w"
            )

        # 打环专用：最低难度 + 等待开战超时秒数
        self.lobby_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.lobby_frame.grid(
            row=len(TASK_OPTIONS) + 1, column=0, sticky="ew", padx=8, pady=(4, 0)
        )
        self.lobby_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.lobby_frame, text="最低难度", width=70).grid(
            row=0, column=0, padx=(8, 4), pady=4, sticky="w"
        )
        self.min_diff_menu = ctk.CTkOptionMenu(
            self.lobby_frame,
            values=list(MIN_DIFFICULTY_OPTIONS),
            variable=self.min_diff_var,
            width=88,
        )
        self.min_diff_menu.set("1")
        self.min_diff_menu.grid(row=0, column=1, padx=(0, 12), pady=4, sticky="w")
        ctk.CTkLabel(
            self.lobby_frame,
            text="只接受「寰球救援-难度」≥ N 的环（OCR 识别）",
            text_color=COLOR_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, padx=(8, 12), pady=(0, 2), sticky="w")

        ctk.CTkLabel(self.lobby_frame, text="超时离队", width=70).grid(
            row=2, column=0, padx=(8, 4), pady=4, sticky="w"
        )
        self.lobby_wait_seg = ctk.CTkSegmentedButton(
            self.lobby_frame,
            values=list(LOBBY_WAIT_OPTIONS),
            variable=self.lobby_wait_var,
        )
        self.lobby_wait_seg.set("10")
        self.lobby_wait_seg.grid(row=2, column=1, padx=(0, 12), pady=4, sticky="ew")
        ctk.CTkLabel(
            self.lobby_frame,
            text="秒内队长未开战则：离开 → 确定离队，继续接新邀请",
            text_color=COLOR_MUTED,
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, padx=(8, 12), pady=(0, 4), sticky="w")

        # 主动邀请专用：指定好友名 / 名字截图
        self.friend_frame = ctk.CTkFrame(frame, fg_color="transparent")
        self.friend_frame.grid(
            row=len(TASK_OPTIONS) + 2, column=0, sticky="ew", padx=8, pady=(4, 0)
        )
        self.friend_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.friend_frame, text="好友名字", width=70).grid(
            row=0, column=0, padx=(8, 4), pady=4, sticky="w"
        )
        self.friend_entry = ctk.CTkEntry(
            self.friend_frame,
            textvariable=self.friend_name_var,
            placeholder_text="输入游戏内好友昵称（OCR 查找）",
        )
        self.friend_entry.grid(row=0, column=1, columnspan=2, padx=(0, 12), pady=4, sticky="ew")

        ctk.CTkLabel(self.friend_frame, text="名字截图", width=70).grid(
            row=1, column=0, padx=(8, 4), pady=(8, 4), sticky="nw"
        )
        # 拖拽 / 粘贴上传区
        self.friend_drop = ctk.CTkFrame(
            self.friend_frame,
            height=72,
            border_width=1,
            border_color=COLOR_MUTED,
            fg_color=("gray90", "gray20"),
        )
        self.friend_drop.grid(
            row=1, column=1, columnspan=2, padx=(0, 12), pady=(4, 4), sticky="ew"
        )
        self.friend_drop.grid_propagate(False)
        self.friend_drop.grid_columnconfigure(0, weight=1)
        self.friend_drop_label = ctk.CTkLabel(
            self.friend_drop,
            text="拖拽图片到此处，或 Ctrl+V / 点「粘贴」",
            text_color=COLOR_MUTED,
            anchor="center",
        )
        self.friend_drop_label.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        # 点击上传区也可粘贴（截图后常见操作）
        self.friend_drop.bind("<Button-1>", lambda _e: self._paste_friend_image())
        self.friend_drop_label.bind("<Button-1>", lambda _e: self._paste_friend_image())
        self.friend_drop.bind("<Control-v>", lambda _e: self._paste_friend_image())
        self.friend_drop_label.bind("<Control-v>", lambda _e: self._paste_friend_image())
        self.bind_all("<Control-v>", self._on_global_paste_friend_image, add="+")

        btn_row = ctk.CTkFrame(self.friend_frame, fg_color="transparent")
        btn_row.grid(row=2, column=1, columnspan=2, padx=(0, 12), pady=(0, 2), sticky="e")
        self.friend_paste_btn = ctk.CTkButton(
            btn_row, text="粘贴", width=64, command=self._paste_friend_image
        )
        self.friend_paste_btn.grid(row=0, column=0, padx=(0, 6))
        self.friend_browse_btn = ctk.CTkButton(
            btn_row, text="浏览", width=64, command=self._pick_friend_image
        )
        self.friend_browse_btn.grid(row=0, column=1, padx=(0, 6))
        self.friend_clear_btn = ctk.CTkButton(
            btn_row, text="清除", width=56, command=self._clear_friend_image
        )
        self.friend_clear_btn.grid(row=0, column=2)

        ctk.CTkLabel(
            self.friend_frame,
            text="可填名字、可临时粘贴/拖拽截图；截图不落盘，关闭或清除即丢弃；都空则一键邀请",
            text_color=COLOR_MUTED,
            anchor="w",
        ).grid(row=3, column=0, columnspan=3, padx=(8, 12), pady=(0, 4), sticky="w")

        self.task_hint = ctk.CTkLabel(frame, text="", text_color=COLOR_ACCENT, anchor="w")
        self.task_hint.grid(
            row=len(TASK_OPTIONS) + 3, column=0, padx=12, pady=(4, 12), sticky="w"
        )
        self._on_task_changed()

    def _build_control_section(self, pad):
        """开始 / 停止与任务状态。"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)

        self.task_status = ctk.CTkLabel(frame, text="当前任务：空闲", anchor="w")
        self.task_status.grid(
            row=0, column=0, columnspan=2, padx=12, pady=(12, 6), sticky="ew"
        )

        self.start_btn = ctk.CTkButton(
            frame,
            text="开始运行",
            height=40,
            font=FONT_BTN,
            fg_color=COLOR_START,
            hover_color=COLOR_START_HOVER,
            command=self._start,
        )
        self.start_btn.grid(row=1, column=0, padx=(12, 6), pady=(0, 12), sticky="ew")

        self.stop_btn = ctk.CTkButton(
            frame,
            text="停止",
            height=40,
            font=FONT_BTN,
            fg_color=COLOR_STOP,
            hover_color=COLOR_STOP_HOVER,
            command=self._stop,
        )
        self.stop_btn.grid(row=1, column=1, padx=(6, 12), pady=(0, 12), sticky="ew")

    def _build_log_section(self):
        """运行日志区。"""
        frame = ctk.CTkFrame(self)
        frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(6, 14))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="运行日志", font=FONT_UI_BOLD).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(
            header, text="清空", width=64, height=28, command=self._clear_log
        ).grid(row=0, column=1, sticky="e")

        self.log_box = ctk.CTkTextbox(
            frame, wrap="word", state="disabled", font=FONT_LOG
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))

    # ---------- 交互 ----------
    def _on_task_changed(self):
        """根据当前单选项刷新提示文案，并显示/隐藏对应设置。"""
        selected = self.task_var.get()
        if selected == "huan":
            self.lobby_frame.grid()
        else:
            self.lobby_frame.grid_remove()

        if selected == "me_invite":
            self.friend_frame.grid()
        else:
            self.friend_frame.grid_remove()

        for key, title, desc in TASK_OPTIONS:
            if key == selected:
                self.task_hint.configure(text=f"已选择：{title} — {desc}")
                return

    def _friend_upload_enabled(self) -> bool:
        """当前是否允许改好友截图（主动邀请且未在跑任务）。"""
        if self.task_var.get() != "me_invite":
            return False
        if self.controller and (
            self.controller.is_running or self.controller.is_stopping
        ):
            return False
        return True

    def _cleanup_friend_temp(self):
        """删除本次会话的临时好友截图文件。"""
        path = (self._friend_temp_path or "").strip()
        self._friend_temp_path = ""
        self.friend_template_path = ""
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def _write_friend_temp_png(self, image: Image.Image) -> str:
        """把图片写入系统临时目录（不写入项目 image/），返回路径。"""
        img = image
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        self._cleanup_friend_temp()
        fd, dest = tempfile.mkstemp(prefix="jiangshi_friend_", suffix=".png")
        os.close(fd)
        img.save(dest, format="PNG")
        self._friend_temp_path = dest
        self.friend_template_path = dest
        return dest

    def _setup_friend_image_dnd(self):
        """注册窗口级拖拽放文件（需 windnd）。"""
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if windnd is None:
            self._log("未安装 windnd：好友截图仍可粘贴/浏览，拖拽不可用（pip install windnd）")
            return

        def _on_drop(files):
            # windnd 回调可能在非 UI 线程/直接消息里，丢回主线程
            self.after(0, lambda: self._handle_friend_drop(files))

        try:
            windnd.hook_dropfiles(self, _on_drop)
        except Exception as exc:
            self._log(f"注册拖拽上传失败: {exc}")

    def _on_close(self):
        """关闭窗口时清理临时好友截图。"""
        self._cleanup_friend_temp()
        self.destroy()

    def _handle_friend_drop(self, files):
        """处理拖入的文件列表（仅本次会话临时使用）。"""
        if not self._friend_upload_enabled():
            return
        paths = []
        for item in files or []:
            if isinstance(item, bytes):
                try:
                    item = item.decode("utf-8")
                except UnicodeDecodeError:
                    item = item.decode("gbk", errors="ignore")
            path = str(item).strip().strip("{}")
            if path:
                paths.append(path)
        if not paths:
            return
        path = paths[0]
        ext = os.path.splitext(path)[1].lower()
        if ext not in FRIEND_IMG_EXTS:
            self._log(f"不支持的图片格式: {ext or '(无)'}")
            return
        self._set_friend_image_from_path(path)

    def _on_global_paste_friend_image(self, event=None):
        """
        全局 Ctrl+V：仅在主动邀请页且焦点不在名字输入框时，尝试粘贴图片。
        输入框内仍走系统默认粘贴文字。
        """
        if not self._friend_upload_enabled():
            return
        focus = self.focus_get()
        # CTkEntry 内部是 tk Entry
        try:
            if focus is not None and str(focus).endswith("!entry"):
                # 名字输入框：不拦截
                if self.friend_entry.winfo_ismapped():
                    return
        except Exception:
            pass
        self._paste_friend_image()
        return "break"

    def _paste_friend_image(self):
        """从剪贴板粘贴图片到临时文件（不写入项目目录）。"""
        if not self._friend_upload_enabled():
            return
        try:
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            self._log(f"读取剪贴板失败: {exc}")
            return

        if data is None:
            self._log("剪贴板里没有图片，请先截图或复制图片文件")
            return

        if isinstance(data, list):
            for path in data:
                if isinstance(path, bytes):
                    path = path.decode("utf-8", errors="ignore")
                path = str(path)
                if os.path.isfile(path) and os.path.splitext(path)[1].lower() in FRIEND_IMG_EXTS:
                    self._set_friend_image_from_path(path)
                    return
            self._log("剪贴板文件不是支持的图片格式")
            return

        if isinstance(data, Image.Image):
            self._set_friend_image_from_pil(data, source_label="剪贴板")
            return

        self._log("剪贴板内容无法识别为图片")

    def _set_friend_image_from_path(self, path: str):
        """从本地路径读取图片，写入临时文件供本次匹配使用。"""
        if not path or not os.path.isfile(path):
            self._log(f"图片不存在: {path}")
            return
        try:
            with Image.open(path) as im:
                self._set_friend_image_from_pil(
                    im.copy(), source_label=os.path.basename(path)
                )
        except Exception as exc:
            self._log(f"读取好友截图失败: {exc}")

    def _set_friend_image_from_pil(self, image: Image.Image, source_label: str = "临时截图"):
        """写入系统临时 png，并刷新预览（不保存到项目 image/）。"""
        try:
            dest = self._write_friend_temp_png(image)
            self._refresh_friend_image_preview(image, label=source_label)
            self._log(f"已设置临时好友截图: {source_label}")
            return dest
        except Exception as exc:
            self._log(f"设置临时好友截图失败: {exc}")
            return None

    def _refresh_friend_image_preview(self, image: Image.Image, label: str = "已选择"):
        """用内存图更新拖拽区预览。"""
        try:
            preview = image.convert("RGB").copy()
            preview.thumbnail((220, 56))
            ctk_img = ctk.CTkImage(
                light_image=preview, dark_image=preview, size=preview.size
            )
            self._friend_preview_image = ctk_img
            self.friend_drop_label.configure(
                image=ctk_img,
                text=f"  {label}（临时）",
                text_color=COLOR_OK,
                compound="left",
            )
            self.friend_drop.configure(border_color=COLOR_OK)
        except Exception:
            self._friend_preview_image = None
            self.friend_drop_label.configure(
                image=None,
                text=f"{label}（临时）",
                text_color=COLOR_OK,
            )

    def _pick_friend_image(self):
        """浏览选择好友名字截图（仅本次临时使用）。"""
        if not self._friend_upload_enabled():
            return
        path = filedialog.askopenfilename(
            title="选择好友名字截图（临时）",
            filetypes=[
                ("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.webp;*.gif"),
                ("全部文件", "*.*"),
            ],
        )
        if not path:
            return
        self._set_friend_image_from_path(path)

    def _clear_friend_image(self):
        """清除临时好友名字截图。"""
        self._cleanup_friend_temp()
        self._friend_preview_image = None
        self.friend_drop_label.configure(
            image=None,
            text="拖拽图片到此处，或 Ctrl+V / 点「粘贴」",
            text_color=COLOR_MUTED,
            compound="center",
        )
        self.friend_drop.configure(border_color=COLOR_MUTED)
        self._log("已清除临时好友截图")

    def _on_click_mode_changed(self, value: str):
        """界面切换：窗口内点击 / 真鼠标。"""
        if value == "真鼠标":
            self.auto.set_click_method("sendinput")
            self.click_status.configure(
                text="真鼠标（SendInput，会短暂移动光标）", text_color=COLOR_WARN
            )
            self._log("已切换为真鼠标点击")
        else:
            self.auto.set_click_method("message")
            self.click_status.configure(
                text="窗口内消息点击（不移动鼠标）", text_color=COLOR_OK
            )
            self._log("已切换为窗口内消息点击")

    def _init_click_backend(self, target_hwnd=None):
        """探测点击环境并刷新提示；默认仍锁定窗口内点击。"""
        self._log("正在初始化点击能力检测…")
        try:
            result = self.auto.init_click_backend(target_hwnd)
        except Exception as exc:
            self.auto.set_click_method("message")
            self.click_status.configure(
                text="检测异常，仍使用窗口内点击", text_color=COLOR_WARN
            )
            self._log(f"点击能力检测异常：{exc}，继续使用窗口内点击")
            return

        active = self.auto.active_click_method or "message"
        if active == "message":
            self.click_mode_seg.set("窗口内点击")
            color = COLOR_OK
            label = "窗口内消息点击（不移动鼠标）"
        else:
            self.click_mode_seg.set("真鼠标")
            color = COLOR_WARN
            label = f"真鼠标 · {result.method_label}"

        self.click_status.configure(text=label, text_color=color)
        self._log(f"当前点击：{label}")
        self._log(f"环境说明：{result.reason}")
        if result.target_classes:
            preview = ", ".join(result.target_classes[:4])
            self._log(f"窗口类：{preview}")

    def _find_window(self):
        """查找游戏窗口 → 设置尺寸 → 激活 → 复核点击后端。"""
        # 运行中禁止重连，避免中断任务线程所持有的窗口句柄
        if self.controller and self.controller.is_running:
            self._log("任务运行中，请先停止再重新连接窗口")
            return

        self._log(f"正在查找窗口：标题包含「{WINDOW_TITLE}」…")
        hwnd = self.auto.find_window(window_title=WINDOW_TITLE)
        if not hwnd:
            self.target_window = None
            self.controller = None
            self.window_status.configure(text="未找到窗口", text_color=COLOR_ERROR)
            self._log("未找到目标窗口，请确认游戏已运行且窗口可见")
            self._sync_controls()
            return

        self.target_window = hwnd
        self._log(f"已找到窗口 HWND={hwnd}，正在设置大小 {WINDOW_WIDTH}x{WINDOW_HEIGHT}…")
        try:
            sized = self.auto.prepare_game_window(hwnd, WINDOW_WIDTH, WINDOW_HEIGHT)
        except Exception as exc:
            self.window_status.configure(text="激活失败", text_color=COLOR_ERROR)
            self._log(f"设置/激活窗口失败：{exc}")
            self.controller = None
            self._sync_controls()
            return

        if sized:
            self._log(f"窗口大小已设置为 {WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        else:
            self._log("窗口大小可能未完全生效，仍将继续激活")

        self._log("窗口已激活并置于桌面最上层")
        # 针对真实游戏窗口复核：不支持消息点击则自动换真鼠标方案
        self._init_click_backend(target_hwnd=hwnd)

        self.controller = TaskController(self.auto, hwnd)
        self.window_status.configure(
            text=f"已连接  HWND={hwnd}", text_color=COLOR_OK
        )
        self._log(f"窗口连接成功：HWND={hwnd}")
        self._sync_controls()

    def _start(self):
        """按当前单选启动对应任务。"""
        if not self.controller:
            self._log("请先查找并激活窗口")
            return

        mode = self.task_var.get()
        if mode == "huan":
            try:
                timeout = int(self.lobby_wait_var.get())
            except ValueError:
                timeout = 10
            try:
                min_diff = int(self.min_diff_var.get())
            except ValueError:
                min_diff = 1
            min_diff = max(1, min(20, min_diff))
            self._log(f"启动打环：最低难度≥{min_diff} 超时离队={timeout}秒")
            self.controller.start_huan(
                lobby_wait_timeout=timeout, min_difficulty=min_diff
            )
        elif mode == "saodang":
            self.controller.start_saodang()
        elif mode == "me_invite":
            name = self.friend_name_var.get().strip()
            template = self.friend_template_path.strip()
            if not name and not template:
                self._log("未填好友名且无截图 → 将使用一键邀请全部好友")
            else:
                self._log(
                    f"指定好友邀请：名字={name or '无'} 截图={'有' if template else '无'}"
                )
            self.controller.start_me_invite(
                friend_name=name, friend_template=template
            )
        elif mode == "qianghuan":
            self._log("启动招募抢环：消息 → 招募 → 点击寰球救援卡片")
            self.controller.start_qianghuan()
        else:
            self._log(f"未知功能：{mode}")
            return
        self._sync_controls()

    def _stop(self):
        """停止当前任务（异步，不阻塞界面）。"""
        if self.controller:
            self.controller.stop()
            self.task_status.configure(
                text="当前任务：正在停止…", text_color=COLOR_MUTED
            )
            self.stop_btn.configure(state="disabled")
        self._sync_controls()

    def _sync_controls(self):
        """按连接/运行状态切换按钮与单选可用性。"""
        connected = self.controller is not None
        running = bool(self.controller and self.controller.is_running)
        stopping = bool(self.controller and self.controller.is_stopping)

        self.start_btn.configure(
            state="normal" if connected and not running else "disabled"
        )
        # 停止中禁用停止键，避免重复点击；运行中可点停止
        self.stop_btn.configure(
            state="normal" if running and not stopping else "disabled"
        )
        self.find_btn.configure(state="disabled" if running else "normal")

        radio_state = "disabled" if running else "normal"
        for rb in self._radio_buttons:
            rb.configure(state=radio_state)
        # 运行中禁止改超时/难度 / 好友设置
        try:
            state = "disabled" if running else "normal"
            self.lobby_wait_seg.configure(state=state)
            self.min_diff_menu.configure(state=state)
        except Exception:
            pass
        try:
            state = "disabled" if running else "normal"
            self.friend_entry.configure(state=state)
            self.friend_paste_btn.configure(state=state)
            self.friend_browse_btn.configure(state=state)
            self.friend_clear_btn.configure(state=state)
        except Exception:
            pass

        if stopping:
            name = self.controller.current_task or "未知"
            self.task_status.configure(
                text=f"当前任务：正在停止 · {name}", text_color=COLOR_MUTED
            )
        elif running:
            name = self.controller.current_task or "未知"
            self.task_status.configure(
                text=f"当前任务：运行中 · {name}", text_color=COLOR_OK
            )
        elif connected:
            self.task_status.configure(
                text="当前任务：空闲（已连接）", text_color=COLOR_MUTED
            )
        else:
            self.task_status.configure(
                text="当前任务：空闲（未连接）", text_color=COLOR_MUTED
            )

    # ---------- 日志 ----------
    def _log(self, msg: str):
        """追加一行带时间戳的界面日志；超过上限时滚动删除最旧行（不落盘）。"""
        if not getattr(self, "log_box", None):
            return
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {msg}\n")
        self._trim_log_box()
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _trim_log_box(self):
        """超过 LOG_MAX_LINES 时只保留最新若干行（滚动窗口）。"""
        try:
            # end-1c 去掉末尾自动换行，行号即当前行数
            last = self.log_box.index("end-1c")
            lines = int(float(str(last).split(".")[0]))
            if lines <= LOG_MAX_LINES:
                return
            cut = lines - LOG_MAX_LINES
            self.log_box.delete("1.0", f"{cut + 1}.0")
        except Exception:
            pass

    def _clear_log(self):
        """清空日志文本框。"""
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _poll(self):
        """定时拉取后台日志并刷新控件状态。"""
        if self.controller:
            while True:
                try:
                    self._log(self.controller.log_queue.get_nowait())
                except queue.Empty:
                    break
        self._sync_controls()
        self.after(POLL_INTERVAL_MS, self._poll)


def main():
    """启动图形界面。"""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
