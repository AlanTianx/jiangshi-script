"""
任务控制器：打环 / 扫荡 / 主动邀请 / 招募抢环 互斥，同一时刻只跑一个；
各功能内部串行执行（无多线程并行子任务），并通过队列向 UI 推送日志。
"""

import queue
import re
import threading
import time

from base.Base import WindowAutomation
from base.paths import resource_path

# 模板图路径（开发/打包后均可定位）
# 打环：别人发来的「副本邀请」入口，点开后才能进被邀请组队界面
IMG_BEI_YAOQING = resource_path("image", "bei-yaoqing.png")  # 被邀请/副本邀请入口
IMG_HUAN = resource_path("image", "huan.png")
IMG_OK = resource_path("image", "ok.png")
IMG_RETURN = resource_path("image", "return.png")
IMG_OPEN_GAME = resource_path("image", "open-game.png")  # 「开始游戏」（扫荡/邀请共用）
IMG_SHUAXIN = resource_path("image", "shuaxin.png")
IMG_TUICHU = resource_path("image", "tuichu.png")
IMG_ME_INVITE = resource_path("image", "me-invite.png")  # 组队界面整块邀请条
IMG_INVITE_PLUS = resource_path("image", "invite-plus.png")  # 邀请条左侧加号（要点这里）
IMG_FRIEND = resource_path("image", "friend.png")  # 底部「好友」页签（推荐页未选中态）
IMG_ALL_INVITE = resource_path("image", "all-invite.png")
IMG_INV_FRIEND = resource_path("image", "inv_friend.png")  # 好友列表里的「邀请」按钮
# 推荐页专有：绿色「发布招募」（好友页没有，用来区分页签）
IMG_PUBLISH_RECRUIT = resource_path("image", "publish-recruit.png")
# 组队等待开战超时离队
IMG_WAIT_START = resource_path("image", "wait-start.png")
IMG_LEAVE = resource_path("image", "leave.png")
IMG_LEAVE_OK = resource_path("image", "leave-ok.png")
# 指定好友名截图（用户可替换）
IMG_FRIEND_NAME = resource_path("image", "friends", "target.png")
# 招募抢环：消息 → 招募 → 寰球救援卡片
IMG_CHAT = resource_path("image", "chat.png")
IMG_ZHAOMU = resource_path("image", "zhaomu.png")
IMG_ZHAOMUPD = resource_path("image", "zhaomupd.png")  # 已在招募频道（选中态）
IMG_QIANGHUAN = resource_path("image", "qianghuan.png")
# 招募橙卡相对标题左上角的大致宽高（点卡片正中，不点「加入」）
QIANGHUAN_CARD_W = 185
QIANGHUAN_CARD_H = 90
# 点卡后短确认入队；仍停在招募列表则立刻续抢（不宜过长，易被抢走）
QIANGHUAN_JOIN_DETECT_SEC = 0.85
QIANGHUAN_JOIN_POLL = 0.1
# 入队后等待开战；超时则离队继续抢环
QIANGHUAN_BATTLE_START_WAIT = 5.0
# 抢环卡点击后延迟（秒），短于全局 post_click_delay
QIANGHUAN_CLICK_DELAY = 0.06

# 好友列表：在名字右侧点击邀请的水平偏移；列表滑动区域
WINDOW_WIDTH_SAFE = 418
FRIEND_INVITE_OFFSET_X = 140
FRIEND_LIST_SCROLL = {
    "x": 210,
    "y_from": 620,
    "y_to": 280,
}
FRIEND_SCROLL_MAX = 10
FRIEND_INVITE_RETRY_WAIT = 8
# 邀请弹层 / 好友页：打开后的确认轮询
INVITE_UI_POLL = 0.2
INVITE_PANEL_TIMEOUT = 5
FRIEND_PAGE_TIMEOUT = 5
# 好友接受邀请后出现「开始游戏」；轮询上限（秒）
START_GAME_POLL = 0.25
START_GAME_WAIT_TIMEOUT = 5
# 开战成功后暂停邀人，等待结算「返回」再继续
BATTLE_END_POLL = 1.2
BATTLE_END_TIMEOUT = 600
# OCR 找到「邀请」文字后，向左偏移点击加号（像素）
INVITE_PLUS_LEFT_OFFSET = 36

# 扫荡：刷新技能若干次后触发退出
SAODANG_REFRESH_LIMIT = 4
# 打环：环图标周围扩展搜索「同意」按钮的区域偏移
HUAN_OK_OFFSET_Y = 55
HUAN_OK_EXTRA_H = 55
HUAN_OK_EXTRA_W = 160
# 扫荡退出：菜单按钮相对窗口坐标
EXIT_MENU_X, EXIT_MENU_Y = 38, 67
# 打环：队长不开局超时离队默认秒数
DEFAULT_LOBBY_WAIT_TIMEOUT = 10
# 打环：最低接受难度（1~20，含）；默认 1 表示不筛选
DEFAULT_MIN_DIFFICULTY = 1
MIN_DIFFICULTY_LO = 1
MIN_DIFFICULTY_HI = 20


class TaskController:
    """打环 / 扫荡 / 主动邀请 / 招募抢环 的启停与执行逻辑。"""

    def __init__(self, auto: WindowAutomation, target_window):
        self.auto = auto
        self.target_window = target_window
        self.stop_event = threading.Event()
        self.task_thread = None
        self.current_task = None
        # UI 通过轮询此队列展示运行日志
        self.log_queue = queue.Queue()
        # 组队界面「等待开始」持续超过该秒数则点离开→确定
        self.lobby_wait_timeout = DEFAULT_LOBBY_WAIT_TIMEOUT
        # 打环：只接受「难度」≥ 该值的环任务
        self.min_difficulty = DEFAULT_MIN_DIFFICULTY
        # 最近一次环难度 OCR 原文（拒绝时打日志用）
        self._last_huan_ocr_text = ""
        # 指定好友打环邀请：名字 和/或 名字截图路径
        self.friend_name = ""
        self.friend_template = ""

    @property
    def is_running(self) -> bool:
        """是否有任务线程仍在执行。"""
        return bool(self.task_thread and self.task_thread.is_alive())

    def log(self, msg: str):
        """推送到 UI 日志队列（界面滚动区展示，不落盘）。"""
        self.log_queue.put(str(msg))

    # ---------- 通用工具 ----------
    def _center(self, loc):
        """模板匹配结果的中心点坐标。"""
        return loc["x"] + loc["width"] // 2, loc["y"] + loc["height"] // 2

    def _click_template(self, template_path, threshold=0.8):
        """截图 → 匹配模板 → 点击中心；找到并点击返回 True。"""
        return self._click_template_rel(
            template_path, threshold=threshold, rel_x=0.5, rel_y=0.5
        )

    def _window_alive(self) -> bool:
        """目标游戏窗口是否仍存在。"""
        return bool(self.auto.is_window_alive(self.target_window))

    def _capture(self):
        """
        安全截图；窗口失效时返回 None（并打日志），其它异常继续抛出。
        """
        if not self._window_alive():
            self.log("目标窗口已关闭或句柄无效，停止当前操作")
            self.stop_event.set()
            return None
        try:
            return self.auto.capture_window(self.target_window)
        except Exception as exc:
            # 含 pywintypes.error(1400) 等句柄失效
            self.log(f"截图失败（窗口可能已关闭）: {exc}")
            self.stop_event.set()
            return None

    def _click_template_rel(
        self, template_path, threshold=0.8, rel_x: float = 0.5, rel_y: float = 0.5
    ):
        """
        截图 → 匹配模板 → 按相对位置点击。
        rel_x/rel_y: 0~1，相对模板矩形（0.2,0.5 表示偏左中心，适合点加号）。
        """
        window_image = self._capture()
        if window_image is None:
            return False
        location = self.auto.find_image_in_window(
            window_image, template_path=template_path, threshold=threshold
        )
        if not location:
            return False
        if not self._window_alive():
            return False
        cx = location["x"] + int(location["width"] * rel_x)
        cy = location["y"] + int(location["height"] * rel_y)
        try:
            self.auto.click_in_window(self.target_window, cx, cy)
        except Exception as exc:
            self.log(f"点击失败（窗口可能已关闭）: {exc}")
            self.stop_event.set()
            return False
        return True

    def _template_visible(self, template_path, threshold=0.7, window_image=None):
        """截图中是否能匹配到模板；找不到模板文件视为未命中。"""
        if window_image is None:
            window_image = self._capture()
            if window_image is None:
                return False
        try:
            return bool(
                self.auto.find_image_in_window(
                    window_image, template_path=template_path, threshold=threshold
                )
            )
        except FileNotFoundError:
            return False

    def _is_invite_panel_open(self, window_image=None) -> bool:
        """
        主动邀请：组队邀请弹层是否已打开（推荐/好友任一页签均可）。
        依据：发布招募 / 好友页签 / 一键邀请 / OCR「组队邀请」等。
        注意：bei-yaoqing.png（副本邀请）属于打环接邀请，不在此判断。
        """
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        if self._template_visible(IMG_PUBLISH_RECRUIT, 0.7, window_image):
            return True
        if self._template_visible(IMG_FRIEND, 0.7, window_image):
            return True
        if self._template_visible(IMG_ALL_INVITE, 0.7, window_image):
            return True
        if self._template_visible(IMG_INV_FRIEND, 0.7, window_image):
            return True
        if self.auto.find_text_in_window(window_image, "组队邀请", threshold=0.5):
            return True
        if self.auto.find_text_in_window(window_image, "发布招募", threshold=0.5):
            return True
        if self.auto.find_text_in_window(window_image, "一键邀请", threshold=0.5):
            return True
        if self.auto.find_text_in_window(window_image, "好友", threshold=0.5):
            return True
        return False

    def _is_recommend_invite_page(self, window_image=None) -> bool:
        """
        是否停在「推荐」页签。
        依据：绿色「发布招募」（好友页没有此按钮）。
        """
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        if self._template_visible(IMG_PUBLISH_RECRUIT, 0.7, window_image):
            return True
        if self.auto.find_text_in_window(window_image, "发布招募", threshold=0.5):
            return True
        return False

    def _has_invite_list_content(self, window_image=None) -> bool:
        """弹层内是否有可邀请列表内容（一键邀请 / 行内邀请）。"""
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        if self._template_visible(IMG_ALL_INVITE, 0.7, window_image):
            return True
        if self._template_visible(IMG_INV_FRIEND, 0.7, window_image):
            return True
        if self.auto.find_text_in_window(window_image, "一键邀请", threshold=0.5):
            return True
        return False

    def _is_friend_invite_page(self, window_image=None) -> bool:
        """
        是否已进入「好友」邀请页。
        推荐页也有「一键邀请」，不能单靠它判断；
        必须：弹层已开 + 没有「发布招募」+ 有列表邀请内容。
        """
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        if not self._is_invite_panel_open(window_image):
            return False
        # 仍有「发布招募」= 还在推荐页，不算好友页
        if self._is_recommend_invite_page(window_image):
            return False
        return self._has_invite_list_content(window_image)

    def _wait_ui(self, predicate, timeout: float, desc: str) -> bool:
        """轮询 predicate 直至为真；超时或停止返回 False。"""
        deadline = time.time() + max(0.5, float(timeout))
        while not self.stop_event.is_set() and time.time() < deadline:
            try:
                if predicate():
                    return True
            except Exception as exc:
                self.log(f"检测「{desc}」异常: {exc}")
            if self._wait(INVITE_UI_POLL):
                return False
        return False

    def _click_invite_entry(self) -> bool:
        """
        点击组队界面「邀请」入口的加号区域（不是文字中心）。
        优先加号模板，其次 me-invite 整块点左侧，再 OCR「邀请」后左偏。
        注意：bei-yaoqing.png（副本邀请）属于打环，不在此处点击。
        """
        # 1) 专用加号模板
        if self._click_template(IMG_INVITE_PLUS, threshold=0.7):
            self.log("已点击邀请加号（plus 模板）")
            return True

        # 2) 整块邀请条：点左侧约 20% 处（加号中心）
        if self._click_template_rel(
            IMG_ME_INVITE, threshold=0.7, rel_x=0.2, rel_y=0.5
        ):
            self.log("已点击邀请加号（me-invite 左侧）")
            return True

        # 3) OCR「邀请」文字，向左偏移到加号
        window_image = self.auto.capture_window(self.target_window)
        hit = self.auto.find_text_in_window(
            window_image, "邀请", threshold=0.5
        )
        if hit:
            cx = max(8, hit["x"] - INVITE_PLUS_LEFT_OFFSET)
            cy = hit.get("center_y", hit["y"] + hit["height"] // 2)
            self.auto.click_in_window(self.target_window, cx, cy)
            self.log(f"已点击邀请加号（OCR 左偏 {INVITE_PLUS_LEFT_OFFSET}px）")
            return True
        return False

    def _open_invite_panel(self) -> bool:
        """点击邀请加号，并确认邀请列表弹层已真正打开。"""
        # 已打开则无需再点
        if self._is_invite_panel_open():
            self.log("邀请列表已处于打开状态")
            return True
        if not self._click_invite_entry():
            return False
        if self._wait_ui(
            self._is_invite_panel_open, INVITE_PANEL_TIMEOUT, "邀请列表"
        ):
            self.log("已确认打开邀请列表")
            return True
        self.log("点击加号后未检测到邀请列表，将重试")
        return False

    def _enter_friend_invite_page(self) -> bool:
        """
        进入好友邀请页：默认先进推荐页，需点底部「好友」页签。
        成功条件：发布招募消失，且仍有一键邀请/行内邀请。
        """
        if self._is_friend_invite_page():
            self.log("已在好友邀请页面")
            return True

        if self._is_recommend_invite_page():
            self.log("当前在推荐页（检测到发布招募），准备点击「好友」页签")

        # 优先点未选中态「好友」页签模板，失败再 OCR
        if not self._click_template_or_text(IMG_FRIEND, "好友", threshold=0.65):
            self.log("未找到「好友」页签")
            return False
        self.log("已点击「好友」页签，等待好友邀请页…")

        if self._wait_ui(
            self._is_friend_invite_page, FRIEND_PAGE_TIMEOUT, "好友邀请页"
        ):
            self.log("已确认进入好友邀请页面（无发布招募）")
            return True
        self.log("点击「好友」后未进入好友邀请页，将重试")
        return False

    def _click_text(self, text: str, threshold: float = 0.6) -> bool:
        """OCR 识别中文按钮并点击；失败返回 False。"""
        try:
            ok = self.auto.click_text_in_window(
                self.target_window, text, threshold=threshold
            )
        except Exception as exc:
            self.log(f"OCR 点击「{text}」异常: {exc}")
            return False
        if ok:
            self.log(f"OCR 已点击「{text}」")
        return ok

    def _click_template_or_text(self, template_path, text: str, threshold=0.75):
        """先模板匹配，失败再用中文 OCR 点击。"""
        if self._click_template(template_path, threshold=threshold):
            return True
        return self._click_text(text, threshold=0.55)

    def _wait(self, seconds):
        """可中断等待；若收到停止信号返回 True。"""
        return self.stop_event.wait(seconds)

    # ---------- 打环 ----------
    def _try_open_bei_yaoqing(self, window_image=None) -> bool:
        """
        在已有截图上找「被邀请 / 副本邀请」并点击（不再二次截图匹配）。
        """
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        try:
            loc = self.auto.find_image_in_window(
                window_image, template_path=IMG_BEI_YAOQING, threshold=0.7
            )
        except FileNotFoundError:
            loc = None
        if loc:
            cx, cy = self._center(loc)
            self.auto.click_in_window(self.target_window, cx, cy)
            self.log("已点击「被邀请 / 副本邀请」入口")
            return True
        hit = self.auto.find_text_in_window(
            window_image, "副本邀请", threshold=0.5
        )
        if hit:
            self.auto.click_in_window(
                self.target_window, hit["center_x"], hit["center_y"]
            )
            self.log("OCR 已点击「副本邀请」入口")
            return True
        return False

    def _normalize_ocr_digits(self, text: str) -> str:
        """OCR 文本规范化：去空白、全角数字转半角。"""
        if not text:
            return ""
        t = str(text).translate(
            str.maketrans("０１２３４５６７８９", "0123456789")
        )
        # 常见 OCR 把 11 识别成 ll / II 等，仅在「难度」后做弱纠正
        t = re.sub(r"\s+", "", t)
        return t

    def _difficulty_from_ocr_text(self, text: str) -> int | None:
        """
        从 OCR 文本解析「难度」后的数字为 int。
        优先从 20 向 1 匹配；用 (?!\\d) 防止「难度1」吃掉「难度11」。
        """
        if not text:
            return None
        t = self._normalize_ocr_digits(text)
        # OCR 常把第二个 1 认成 l/I/|：难度1l → 难度11
        t = re.sub(r"难度1[lI|！]", "难度11", t)
        t = re.sub(r"难度[lI|！]{2}", "难度11", t)
        t = re.sub(
            r"难度([lI|！])(?!\d)",
            lambda m: "难度1",
            t,
        )

        for n in range(MIN_DIFFICULTY_HI, MIN_DIFFICULTY_LO - 1, -1):
            # 不能是更长数字的前缀：难度1 不匹配 难度11
            if re.search(rf"难度{n}(?!\d)", t):
                return int(n)
        m = re.search(r"难度(\d{1,2})(?!\d)", t)
        if m:
            n = int(m.group(1))
            if MIN_DIFFICULTY_LO <= n <= MIN_DIFFICULTY_HI:
                return n
        return None

    def _pick_difficulty_from_ocr_texts(self, texts: list[str]) -> int | None:
        """
        从整行/数字条 OCR 文本选出难度。
        规则：「难度XX」一旦出现立即采用（防 La!| 等噪声投票成 11）。
        """
        from base import ocr as ocr_mod

        # 1) 权威命中：优先「难度10~20」，避免「难度1」截断盖过「难度15」
        best_nandu = None
        for raw in texts:
            n = ocr_mod.parse_explicit_nandu(raw or "")
            if n is None:
                continue
            if 10 <= int(n) <= 20:
                return int(n)
            if best_nandu is None or int(n) > int(best_nandu):
                best_nandu = int(n)
        if best_nandu is not None:
            return int(best_nandu)

        # 2) 无「难度」前缀时，再对含真数字的片段加权投票（10~20）
        weights: dict[int, int] = {}
        first_seen: dict[int, int] = {}
        order = 0
        for raw in texts:
            if not raw or not re.search(r"\d", raw):
                continue
            hits = ocr_mod.extract_difficulty_10_20_from_text(raw)
            if not hits:
                hits = ocr_mod.extract_levels_10_20(raw)
            for val, w in hits:
                weights[val] = weights.get(val, 0) + int(w)
                if val not in first_seen:
                    first_seen[val] = order
                    order += 1
        if weights:
            return int(
                max(weights.keys(), key=lambda v: (weights[v], -first_seen.get(v, 999)))
            )

        # 3) 回退：拼「难度」前缀再解析 1~20
        for raw in texts:
            n = self._difficulty_from_ocr_text(
                raw if "难度" in (raw or "") else f"难度{raw}"
            )
            if n is not None:
                return int(n)
        return None

    def _parse_huan_difficulty(self, window_image, huan_loc) -> int | None:
        """
        纯 OCR 识别「寰球救援-难度XX」（不用数字模板匹配）。
        主路径：含「难度」整行 chi_sim；辅路径：右侧数字条 eng。
        重点保证 10~20。
        """
        from base import ocr as ocr_mod

        self._last_huan_ocr_text = ""
        hx = int(huan_loc["x"])
        hy = int(huan_loc["y"])
        hw = int(huan_loc["width"])
        hh = max(14, int(huan_loc["height"]))

        # 含「难度」+ 两位数（主 ROI）
        line_region = {
            "x": max(0, hx + max(0, hw - 44)),
            "y": max(0, hy - 4),
            "width": min(96, WINDOW_WIDTH_SAFE - max(0, hx + max(0, hw - 44))),
            "height": max(22, hh + 10),
        }
        # 纯数字条（不重叠「难度」，避免橙字掩膜吃进汉字）
        digit_region = {
            "x": min(WINDOW_WIDTH_SAFE - 10, max(0, hx + hw + 1)),
            "y": max(0, hy - 4),
            "width": 44,
            "height": max(22, hh + 10),
        }

        line_texts = ocr_mod.read_difficulty_line_texts(
            window_image, region=line_region
        )
        n = self._pick_difficulty_from_ocr_texts(line_texts)
        digit_strs: list[str] = []
        if n is None or not (10 <= int(n) <= 20):
            digit_strs = self.auto.read_difficulty_digits(
                window_image, region=digit_region
            )
            n2 = self._pick_difficulty_from_ocr_texts(digit_strs)
            # 整行未给出 10~20 时采用数字条；若整行已是 10~20 则保留
            if n is None:
                n = n2
            elif n2 is not None and 10 <= int(n2) <= 20:
                # 两边都有 10~20 时以整行「难度XX」为准（上面已优先）
                pass

        texts_for_log = list(line_texts) + list(digit_strs)
        self._last_huan_ocr_text = " | ".join(texts_for_log) if texts_for_log else ""

        if n is not None:
            self.log(
                f"环难度识别成功：值={n}（int） 方式=OCR "
                f"原文={self._last_huan_ocr_text[:80]!r}"
            )
            return int(n)

        self.log(
            f"环难度识别失败：未匹配难度20~1 "
            f"line_region={line_region} "
            f"OCR原文={self._last_huan_ocr_text[:80]!r}"
        )
        return None

    def _huan_difficulty_ok(self, difficulty: int | None) -> bool:
        """是否满足最低难度：识别失败时仅当最低难度为 1 才放行。"""
        need = max(
            MIN_DIFFICULTY_LO,
            min(MIN_DIFFICULTY_HI, int(self.min_difficulty or DEFAULT_MIN_DIFFICULTY)),
        )
        ocr_raw = getattr(self, "_last_huan_ocr_text", "") or ""
        if difficulty is None:
            if need <= DEFAULT_MIN_DIFFICULTY:
                self.log(
                    f"未识别环难度，最低难度为 1，仍尝试接受；OCR原文={ocr_raw[:60]!r}"
                )
                return True
            self.log(
                f"环难度拒绝：识别难度=None（未识别） 最低要求={need} "
                f"OCR原文={ocr_raw[:60]!r}"
            )
            return False
        if int(difficulty) >= int(need):
            self.log(
                f"环难度接受：识别难度={int(difficulty)} 最低要求={need} "
                f"OCR原文={ocr_raw[:60]!r}"
            )
            return True
        self.log(
            f"环难度拒绝：识别难度={int(difficulty)} 最低要求={need} "
            f"OCR原文={ocr_raw[:60]!r}"
        )
        return False

    def _dismiss_huan_invite(self, window_image=None, huan_loc=None) -> None:
        """难度不足时点击同行「拒绝」，避免卡住同一张邀请卡。"""
        region = None
        if huan_loc:
            region = {
                "x": max(0, int(huan_loc["x"])),
                "y": max(0, int(huan_loc["y"]) - 20),
                "width": max(120, WINDOW_WIDTH_SAFE - int(huan_loc["x"]) - 8),
                "height": max(80, int(huan_loc.get("height", 30)) + 90),
            }
        if window_image is not None and region:
            hit = self.auto.find_text_in_window(
                window_image, "拒绝", threshold=0.5, region=region
            )
            if hit:
                self.auto.click_in_window(
                    self.target_window, hit["center_x"], hit["center_y"]
                )
                self.log("已点击「拒绝」（难度不足）")
                return
        if self._click_text("拒绝", threshold=0.5):
            return

    def _try_accept_huan(self, window_image) -> bool:
        """
        查找环任务；OCR「难度」后数字（20→1）筛选，达标则点「接受」。
        成功进入组队等待返回 True。
        """
        res = self.auto.find_image_in_window(
            window_image, template_path=IMG_HUAN, threshold=0.8
        )
        if not res:
            return False

        self.log("找到环了---- OCR 解析难度（优先 20→1）")
        difficulty = self._parse_huan_difficulty(window_image, res)
        if not self._huan_difficulty_ok(difficulty):
            self._dismiss_huan_invite(window_image, res)
            return False

        # 同行右侧：「接受」在上、「拒绝」在下（ok.png 实为接受按钮）
        region = {
            "x": res["x"],
            "y": max(0, res["y"] - HUAN_OK_OFFSET_Y),
            "width": res["width"] + HUAN_OK_EXTRA_W,
            "height": res["height"] + HUAN_OK_EXTRA_H + 40,
        }
        ok = self.auto.find_button_in_region(
            window_image, region, IMG_OK, threshold=0.75
        )
        if ok:
            self.auto.click_in_window(
                self.target_window, ok["center_x"], ok["center_y"]
            )
            self.log("已点击「接受」（模板）")
            return True

        for label in ("接受", "同意"):
            ocr_hit = self.auto.find_text_in_window(
                window_image, label, threshold=0.5, region=region
            )
            if ocr_hit:
                self.auto.click_in_window(
                    self.target_window,
                    ocr_hit["center_x"],
                    ocr_hit["center_y"],
                )
                self.log(f"已点击「{label}」（OCR）")
                return True
        self.log("未找到「接受」按钮")
        return False

    def _is_lobby_waiting(self, window_image=None) -> bool:
        """是否处于组队等待开战界面（默认仅模板，避免热循环里反复 OCR）。"""
        if window_image is None:
            window_image = self.auto.capture_window(self.target_window)
        wait = self.auto.find_image_in_window(
            window_image, template_path=IMG_WAIT_START, threshold=0.7
        )
        leave = self.auto.find_image_in_window(
            window_image, template_path=IMG_LEAVE, threshold=0.75
        )
        return bool(wait or leave)

    def _leave_team(self):
        """点击「离开」→「确定」，退出当前队伍以便继续接环。"""
        if not self._click_template_or_text(IMG_LEAVE, "离开", threshold=0.75):
            self.log("超时离队：未找到「离开」按钮（模板/OCR）")
            return False
        self.log("超时离队：已点击「离开」")
        if self._wait(0.35):
            return False

        for _ in range(6):
            # 「确定」优先模板；OCR 时避免误点「取消」旁的确定
            if self._click_template_or_text(IMG_LEAVE_OK, "确定", threshold=0.75):
                self.log("超时离队：已点击「确定」，已离队")
                return True
            if self._wait(0.25):
                return False
        self.log("超时离队：未找到「确定」按钮（模板/OCR）")
        return False

    def _run_huan(self):
        """
        串行打环，每轮顺序：
        1) 被邀请 2) 找环并接受 3) 结算返回 4) 组队等待/超时离队。
        点开被邀请后会再截一张图找环；找不到被邀请则同帧直接找环。
        """
        times = 0
        lobby_since = None
        timeout = max(1, int(self.lobby_wait_timeout or DEFAULT_LOBBY_WAIT_TIMEOUT))
        min_diff = max(
            MIN_DIFFICULTY_LO,
            min(MIN_DIFFICULTY_HI, int(self.min_difficulty or DEFAULT_MIN_DIFFICULTY)),
        )
        self.min_difficulty = min_diff
        self.log(
            f"打环开始（串行）：顺序=被邀请→环→返回→组队等待；"
            f"最低难度≥{min_diff}；超时离队={timeout}s"
        )

        while not self.stop_event.is_set():
            try:
                window_image = self.auto.capture_window(self.target_window)
            except Exception as exc:
                self.log(f"打环截图失败: {exc}")
                if self._wait(1):
                    break
                continue

            # 1) 被邀请：找到则点击，再截图找环
            if self._try_open_bei_yaoqing(window_image):
                if self._wait(0.35):
                    break
                try:
                    window_image = self.auto.capture_window(self.target_window)
                except Exception as exc:
                    self.log(f"打开被邀请后截图失败: {exc}")
                    if self._wait(0.25):
                        break
                    continue

            # 2) 找环（难度筛选 → 接受/拒绝）
            if self._try_accept_huan(window_image):
                lobby_since = time.time()
                if self._wait(0.45):
                    break
                continue

            # 3) 结算「返回」
            end_btn = self.auto.find_image_in_window(
                window_image, template_path=IMG_RETURN, threshold=0.8
            )
            if end_btn:
                cx, cy = self._center(end_btn)
                self.auto.click_in_window(self.target_window, cx, cy)
                times += 1
                lobby_since = None
                self.log(f"战斗结束：点击返回 现在已经打环 {times} 次")
                if self._wait(1.0):
                    break
                continue

            # 4) 组队等待：超时离队
            if self._is_lobby_waiting(window_image):
                now = time.time()
                if lobby_since is None:
                    lobby_since = now
                    self.log("检测到组队等待界面，开始计时…")
                elif now - lobby_since >= timeout:
                    self.log(f"等待开战已超过 {timeout} 秒，执行离开→确定")
                    self._leave_team()
                    lobby_since = None
                    if self._wait(1.0):
                        break
                    continue
                if self._wait(0.3):
                    break
                continue

            if lobby_since is not None:
                self.log("已离开等待界面（可能已开战或已离队），计时重置")
                lobby_since = None

            if self._wait(0.3):
                break

    # ---------- 扫荡 ----------
    def _exit_game(self):
        """约 5 级时：点菜单 → 退出 → 确认返回大厅。"""
        self.log("5级退出游戏")
        self.auto.click_in_window(self.target_window, EXIT_MENU_X, EXIT_MENU_Y)
        if self._wait(0.45):
            return

        window_image = self.auto.capture_window(self.target_window)
        res = self.auto.find_image_in_window(
            window_image, template_path=IMG_TUICHU, threshold=0.8
        )
        if not res:
            return

        cx, cy = self._center(res)
        self.auto.click_in_window(self.target_window, cx, cy)
        if self._wait(0.4):
            return

        window_image = self.auto.capture_window(self.target_window)
        back = self.auto.find_image_in_window(
            window_image, template_path=IMG_RETURN, threshold=0.8
        )
        if back:
            bx, by = self._center(back)
            self.auto.click_in_window(self.target_window, bx, by)

    def _run_saodang(self):
        """
        串行扫荡：开局 → 刷技能 → 达标后退出 → 结算返回，循环。
        与打环、主动邀请互斥，功能内部不再开并行子线程。
        """
        success = 0
        refresh_count = 0
        self.log("扫荡开始（串行）")

        while not self.stop_event.is_set():
            # 大厅开局
            if self._click_template(IMG_OPEN_GAME, threshold=0.7):
                refresh_count = 0
                self.log("已点击「开始游戏」")
                if self._wait(1):
                    break
                continue

            # 战斗内刷技能
            if self._click_template(IMG_SHUAXIN, threshold=0.75):
                refresh_count += 1
                self.log(
                    f"刷新技能 {refresh_count}/{SAODANG_REFRESH_LIMIT}"
                )
                if refresh_count >= SAODANG_REFRESH_LIMIT:
                    self._exit_game()
                    success += 1
                    refresh_count = 0
                    self.log(f"退出本局成功，当前扫荡成功次数 {success}")
                    if self._wait(2):
                        break
                    continue
                if self._wait(1):
                    break
                continue

            # 结算返回（若退出流程未点到）
            if self._click_template(IMG_RETURN, threshold=0.75):
                self.log("扫荡：已点击结算返回")
                if self._wait(1.5):
                    break
                continue

            if self._wait(1):
                break

    # ---------- 主动邀请（指定好友打环）----------
    def _find_friend_on_screen(self, window_image):
        """
        在当前画面查找指定好友。
        优先名字截图模板，其次 OCR 名字；返回命中位置字典或 None。
        """
        template = (self.friend_template or "").strip()
        if template:
            try:
                hit = self.auto.find_image_in_window(
                    window_image, template_path=template, threshold=0.72
                )
                if hit:
                    hit["source"] = "template"
                    return hit
            except FileNotFoundError:
                self.log(f"好友截图不存在: {template}")

        name = (self.friend_name or "").strip()
        if name:
            hit = self.auto.find_text_in_window(
                window_image, name, threshold=0.5
            )
            if hit:
                hit["source"] = "ocr"
                return hit
        return None

    def _click_invite_near_friend(self, friend_loc) -> bool:
        """
        点击好友条目上的邀请：
        1) 同行右侧匹配 inv_friend / OCR「邀请」
        2) 否则按名字右侧固定偏移点击
        """
        window_image = self.auto.capture_window(self.target_window)
        row_top = max(0, friend_loc["y"] - 10)
        row_h = max(friend_loc.get("height", 30) + 24, 40)
        row_left = max(0, friend_loc["x"])
        row_w = max(80, WINDOW_WIDTH_SAFE - row_left - 8)
        region = {
            "x": row_left,
            "y": row_top,
            "width": row_w,
            "height": row_h,
        }

        # 好友列表黄色「邀请」按钮模板（限定在该行区域）
        try:
            btn = self.auto.find_button_in_region(
                window_image, region, IMG_INV_FRIEND, threshold=0.7
            )
            if btn:
                self.auto.click_in_window(
                    self.target_window, btn["center_x"], btn["center_y"]
                )
                self.log("已点击好友旁「邀请」按钮（模板）")
                return True
        except FileNotFoundError:
            pass

        invite = self.auto.find_text_in_window(
            window_image, "邀请", threshold=0.5, region=region
        )
        if invite:
            self.auto.click_in_window(
                self.target_window, invite["center_x"], invite["center_y"]
            )
            self.log("已点击好友旁「邀请」按钮（OCR）")
            return True

        cx = friend_loc["x"] + friend_loc.get("width", 40) // 2 + FRIEND_INVITE_OFFSET_X
        cy = friend_loc["y"] + friend_loc.get("height", 20) // 2
        cx = min(max(cx, 20), WINDOW_WIDTH_SAFE - 20)
        self.auto.click_in_window(self.target_window, cx, cy)
        self.log(f"已按偏移点击好友邀请位置 ({cx},{cy})")
        return True

    def _scroll_friend_list(self):
        """好友列表向上滑动，露出更多好友。"""
        cfg = FRIEND_LIST_SCROLL
        self.auto.swipe_in_window(
            self.target_window,
            cfg["x"],
            cfg["y_from"],
            cfg["x"],
            cfg["y_to"],
            steps=14,
            step_delay=0.015,
        )
        self.log("好友列表已滑动，继续查找…")

    def _invite_target_friend_once(self) -> bool:
        """在当前好友列表中查找并邀请目标好友；找不到则滚动重试。"""
        for scroll_i in range(FRIEND_SCROLL_MAX + 1):
            if self.stop_event.is_set():
                return False
            window_image = self.auto.capture_window(self.target_window)
            friend = self._find_friend_on_screen(window_image)
            if friend:
                src = friend.get("source", "?")
                self.log(
                    f"找到目标好友（{src}） at=({friend['x']},{friend['y']})"
                )
                return self._click_invite_near_friend(friend)
            if scroll_i < FRIEND_SCROLL_MAX:
                self._scroll_friend_list()
                if self._wait(0.6):
                    return False
        self.log("未在好友列表中找到目标好友（已滑到底）")
        return False

    def _click_start_game(self) -> bool:
        """点击组队就绪页「开始游戏」：统一用 open-game.png，失败再 OCR。"""
        if self._click_template(IMG_OPEN_GAME, threshold=0.7):
            self.log("已点击「开始游戏」（open-game 模板）")
            return True
        return self._click_text("开始游戏", threshold=0.5)

    def _wait_and_start_game(self, timeout: float | None = None) -> bool:
        """
        好友接受邀请后约 0.5s 会跳到就绪页；轮询「开始游戏」并点击。
        找到并点击返回 True，超时或停止返回 False。
        """
        limit = START_GAME_WAIT_TIMEOUT if timeout is None else float(timeout)
        self.log(f"等待好友入队后的「开始游戏」（最长 {limit:.0f}s）…")
        deadline = time.time() + max(1.0, limit)
        while not self.stop_event.is_set() and time.time() < deadline:
            if self._click_start_game():
                return True
            if self._wait(START_GAME_POLL):
                return False
        self.log("等待「开始游戏」超时，未开局")
        return False

    def _wait_battle_end_and_return(
        self,
        timeout: float | None = None,
        use_ocr: bool = True,
    ) -> bool:
        """
        开战中轮询结算「返回」并点击。
        use_ocr=False 时只用 return.png（招募抢环专用，避免战斗中 OCR 误点）。
        成功点返回为 True；超时或停止为 False。
        """
        limit = BATTLE_END_TIMEOUT if timeout is None else float(timeout)
        self.log(f"已进入战斗，等待结束（最长 {limit:.0f}s）…")
        deadline = time.time() + max(30.0, limit)
        while not self.stop_event.is_set() and time.time() < deadline:
            window_image = self._capture()
            if window_image is None:
                return False
            res = self.auto.find_image_in_window(
                window_image, template_path=IMG_RETURN, threshold=0.78
            )
            if res:
                cx, cy = self._center(res)
                try:
                    self.auto.click_in_window(self.target_window, cx, cy)
                except Exception as exc:
                    self.log(f"点击「返回」失败: {exc}")
                    self.stop_event.set()
                    return False
                self.log("战斗结束：已点击「返回」（模板）")
                if self._wait(1.2):
                    return False
                return True
            # 主动邀请可 OCR；抢环关闭，防止战斗 UI 误点
            if use_ocr and self._click_text("返回", threshold=0.55):
                self.log("战斗结束：已点击「返回」（OCR）")
                if self._wait(1.2):
                    return False
                return True
            if self._wait(BATTLE_END_POLL):
                return False
        self.log("等待战斗结束超时")
        return False

    def _run_me_invite(self):
        """
        串行主动邀请（与打环/扫荡互斥）：
        打开邀请 → 好友页 → 邀指定好友 → 点「开始游戏」；
        开战成功后暂停邀人，等结算返回后再开启下一轮邀请。
        若未配置好友名与截图，则回退为一键邀请。
        """
        name = (self.friend_name or "").strip()
        template = (self.friend_template or "").strip()
        use_target = bool(name or template)

        if use_target:
            self.log(
                f"开始指定好友邀请：名字={name or '未填'} "
                f"截图={'有' if template else '无'}"
            )
        else:
            self.log("未指定好友，使用一键邀请全部好友")

        while not self.stop_event.is_set():
            # 点加号后必须确认弹层打开，再确认进入好友邀请页
            if not self._open_invite_panel():
                if self._wait(0.8):
                    break
                continue

            if not self._enter_friend_invite_page():
                if self._wait(1):
                    break
                continue

            started = False
            if use_target:
                ok = self._invite_target_friend_once()
                if ok:
                    self.log("已向指定好友发送邀请，等待入队后开局")
                    started = self._wait_and_start_game()
                if not started:
                    # 未开战：稍等再重试邀人，避免战斗中误操作
                    if self._wait(FRIEND_INVITE_RETRY_WAIT):
                        break
                    continue
            else:
                # 一键邀请 → 等入队 → 开局
                invited = False
                while not self.stop_event.is_set():
                    if self._click_template(IMG_ALL_INVITE):
                        self.log("一键邀请了好友")
                        invited = True
                        break
                    if self._click_text("一键邀请", threshold=0.5):
                        invited = True
                        break
                    if self._wait(1):
                        break
                if invited:
                    started = self._wait_and_start_game()
                if not started:
                    if self._wait(FRIEND_INVITE_RETRY_WAIT):
                        break
                    continue

            # 开战成功：停止邀请，等战斗结束点返回后再继续
            self._wait_battle_end_and_return()
            if self._wait(0.5):
                break

    # ---------- 招募抢环 ----------
    def _find_bottom_qianghuan_card(self, window_image):
        """
        在招募列表中找出所有「寰球救援-难度」卡片，返回最靠下的一张。
        """
        cards = self.auto.find_all_images_in_window(
            window_image,
            template_path=IMG_QIANGHUAN,
            threshold=0.72,
            max_count=12,
        )
        if not cards:
            return None
        # y 最大 = 列表最下方可见卡片
        return max(cards, key=lambda c: int(c["y"]))

    def _click_qianghuan_card(self, card) -> bool:
        """点击招募环橙卡正中间（以标题模板为锚点，不点「加入」按钮）。"""
        if not self._window_alive():
            self.log("目标窗口已关闭，无法点击招募卡")
            self.stop_event.set()
            return False
        cx = int(card["x"] + QIANGHUAN_CARD_W // 2)
        cy = int(card["y"] + QIANGHUAN_CARD_H // 2)
        cx = min(max(cx, 20), WINDOW_WIDTH_SAFE - 20)
        try:
            self.auto.click_in_window(
                self.target_window,
                cx,
                cy,
                after_delay=QIANGHUAN_CLICK_DELAY,
            )
        except Exception as exc:
            self.log(f"招募卡点击失败（窗口可能已关闭）: {exc}")
            self.stop_event.set()
            return False
        self.log(f"招募卡点击正中 ({cx},{cy})")
        return True

    def _is_zhaomu_channel(self, window_image=None) -> bool:
        """是否已在招募频道（左侧「招募」选中态 zhaomupd.png）。"""
        return bool(
            self._template_visible(IMG_ZHAOMUPD, threshold=0.72, window_image=window_image)
        )

    def _open_zhaomu_channel(self) -> bool:
        """
        未在招募频道时：先点消息 chat.png，再点招募 zhaomu.png。
        任一步成功推进 UI 则返回 True。
        """
        if self._click_template(IMG_CHAT, threshold=0.7):
            self.log("未在招募频道：已点击消息按钮")
            if self._wait(0.45):
                return True
        else:
            self.log("未在招募频道：未找到消息按钮")

        if self._click_template(IMG_ZHAOMU, threshold=0.7):
            self.log("已点击「招募」按钮")
            if self._wait(0.45):
                return True
            return True

        self.log("未找到「招募」按钮")
        return False

    def _wait_start_visible(self, window_image=None) -> bool:
        """是否出现组队「等待开始」（wait-start.png）。"""
        if window_image is None:
            window_image = self._capture()
            if window_image is None:
                return False
        return bool(
            self._template_visible(IMG_WAIT_START, 0.7, window_image)
        )

    def _after_qianghuan_join(self) -> None:
        """
        点卡后短确认入队：
        - 很快仍见招募列表 → 立刻续抢（不等满检测窗）；
        - 出现 wait-start → 再等开战/超时离队；
        - 检测窗内无 wait-start → 马上续抢。
        """
        joined = False
        t0 = time.time()
        deadline = t0 + QIANGHUAN_JOIN_DETECT_SEC
        while not self.stop_event.is_set() and time.time() < deadline:
            img = self._capture()
            if img is None:
                return
            if self._wait_start_visible(img):
                joined = True
                self.log("加入队伍成功（检测到 wait-start）")
                break
            # 点完仍停在招募列表：入队失败，尽快下一枪
            if time.time() - t0 >= 0.12:
                if self._is_zhaomu_channel(img) and self._find_bottom_qianghuan_card(
                    img
                ):
                    return
            if self._wait(QIANGHUAN_JOIN_POLL):
                return
        if not joined:
            return

        # 入队后等开战
        self.log(
            f"等待进入战斗（最长 {QIANGHUAN_BATTLE_START_WAIT:.0f}s，"
            f"超时则离队继续抢环）…"
        )
        deadline = time.time() + QIANGHUAN_BATTLE_START_WAIT
        while not self.stop_event.is_set() and time.time() < deadline:
            img = self._capture()
            if img is None:
                return
            if self._wait_start_visible(img):
                if self._wait(0.25):
                    return
                continue

            if self._is_zhaomu_channel(img) or self._find_bottom_qianghuan_card(img):
                self.log("已离开等待界面且回到招募列表，继续抢环")
                return
            self.log("已进入战斗，等待结束后再继续抢环")
            # 抢环禁用 OCR「返回」，避免战斗中误点后继续狂点消息/招募
            self._wait_battle_end_and_return(use_ocr=False)
            return

        if self.stop_event.is_set():
            return
        self.log(
            f"等待开战超过 {QIANGHUAN_BATTLE_START_WAIT:.0f}s，执行离队后继续抢环"
        )
        self._leave_team()
        if self._wait(0.35):
            return

    def _run_qianghuan(self):
        """
        串行招募抢环：高频点最下方卡片；入队后再走开战/离队逻辑。
        战斗中只等 return.png，不点消息/招募。
        """
        clicks = 0
        self.log(
            "招募抢环开始：快速抢卡→短确认入队→"
            f"{QIANGHUAN_BATTLE_START_WAIT:.0f}s 未开战则离队续抢"
        )

        while not self.stop_event.is_set():
            window_image = self._capture()
            if window_image is None:
                break

            try:
                if self._is_zhaomu_channel(window_image):
                    card = self._find_bottom_qianghuan_card(window_image)
                    if card:
                        if not self._click_qianghuan_card(card):
                            break
                        clicks += 1
                        self.log(
                            f"招募频道内：已点击最下方抢环卡片 "
                            f"（y={card['y']}；第 {clicks} 次）"
                        )
                        self._after_qianghuan_join()
                    else:
                        if self._wait(0.12):
                            break
                    continue

                # 结算页：只点模板返回
                if self._click_template(IMG_RETURN, threshold=0.78):
                    self.log("检测到结算「返回」，已点击，准备回到招募")
                    if self._wait(1.0):
                        break
                    continue

                # 仍在组队等待：交给入队后逻辑，勿点消息
                if self._wait_start_visible(window_image):
                    self.log("仍在等待开战界面，继续等待/超时离队逻辑")
                    self._after_qianghuan_join()
                    continue

                # 消息/招募都找不到 → 多半还在战斗，阻塞等到结算
                chat_ok = self._template_visible(
                    IMG_CHAT, 0.7, window_image
                )
                zhaomu_ok = self._template_visible(
                    IMG_ZHAOMU, 0.7, window_image
                )
                if not chat_ok and not zhaomu_ok:
                    self.log(
                        "未回到大厅（无消息/招募按钮），等待战斗结束…"
                    )
                    self._wait_battle_end_and_return(use_ocr=False)
                    if self._wait(0.3):
                        break
                    continue

                self._open_zhaomu_channel()
                if self.stop_event.is_set():
                    break
                if self._wait(0.15):
                    break
            except Exception as exc:
                self.log(f"招募抢环异常（将停止）: {exc}")
                self.stop_event.set()
                break

    # ---------- 对外接口 ----------
    def start_huan(
        self,
        lobby_wait_timeout: int | None = None,
        min_difficulty: int | None = None,
    ):
        """启动打环；可指定超时离队秒数，以及最低接受难度（1~20）。"""
        if lobby_wait_timeout is not None:
            self.lobby_wait_timeout = int(lobby_wait_timeout)
        if min_difficulty is not None:
            self.min_difficulty = max(
                MIN_DIFFICULTY_LO,
                min(MIN_DIFFICULTY_HI, int(min_difficulty)),
            )
        self._start("打环", self._run_huan)

    def start_saodang(self):
        """启动扫荡任务。"""
        self._start("扫荡", self._run_saodang)

    def start_me_invite(self, friend_name: str = "", friend_template: str = ""):
        """启动主动邀请；可指定好友名字和/或名字截图路径。"""
        self.friend_name = (friend_name or "").strip()
        self.friend_template = (friend_template or "").strip()
        self._start("主动邀请", self._run_me_invite)

    def start_qianghuan(self):
        """启动招募抢环：抢卡入队→等开战/离队续抢→开战则等返回。"""
        self._start("招募抢环", self._run_qianghuan)

    def _start(self, task_name, func):
        """若无任务在跑则启动新线程执行 func。"""
        if self.is_running:
            self.log(f"已有任务[{self.current_task}]运行中，请先停止")
            return
        self.stop_event.clear()
        # 点击后延迟可响应停止，避免点停止时还卡在 sleep
        self.auto.stop_check = self.stop_event.is_set
        self.current_task = task_name
        self.task_thread = threading.Thread(target=func, daemon=True)
        self.task_thread.start()
        self.log(f"已启动任务：{task_name}")

    @property
    def is_stopping(self) -> bool:
        """已请求停止但任务线程尚未退出。"""
        return bool(
            self.stop_event.is_set()
            and self.task_thread
            and self.task_thread.is_alive()
        )

    def stop(self):
        """
        请求停止：立即返回，不在 UI 线程 join。
        后台短暂等待线程退出后清状态并打日志。
        """
        if not self.is_running:
            self.log("当前没有运行中的任务")
            return
        if self.is_stopping:
            self.log("正在停止中，请稍候…")
            return
        name = self.current_task or "未知"
        self.log(f"正在停止任务：{name}")
        self.stop_event.set()
        t = self.task_thread

        def _cleanup():
            if t is not None:
                t.join(timeout=8)
            # 仅当仍是同一次任务线程时清理，避免误清新任务
            if self.task_thread is t:
                self.current_task = None
                self.task_thread = None
            self.log("任务已停止")

        threading.Thread(target=_cleanup, daemon=True).start()
