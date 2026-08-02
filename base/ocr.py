"""
中文 OCR：基于本地 Tesseract（pytesseract），支持绿色包内置引擎。
无需云接口；优先使用程序目录下 tesseract/，其次系统 PATH。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract

from base.cv_io import imread as imread_unicode
from base.paths import app_dir, resource_path

logger = logging.getLogger(__name__)

# 默认中文简体；可叠加英文提高混排识别
DEFAULT_LANG = "chi_sim"
FALLBACK_LANG = "chi_sim+eng"


@dataclass
class OcrHit:
    """OCR 命中结果（窗口相对坐标）。"""

    text: str
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2


_configured = False
_available: bool | None = None
_status_message = "未初始化"


def tesseract_candidates() -> list[str]:
    """可能的 tesseract.exe 路径列表。"""
    root = app_dir()
    rels = [
        os.path.join("tesseract", "tesseract.exe"),
        os.path.join("_internal", "tesseract", "tesseract.exe"),
    ]
    paths = [os.path.join(root, r) for r in rels]
    meipass = getattr(__import__("sys"), "_MEIPASS", None)
    if meipass:
        paths.append(os.path.join(meipass, "tesseract", "tesseract.exe"))
    # 常见系统安装路径
    paths.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    )
    # PATH 中的命令名（交由 pytesseract 解析）
    paths.append("tesseract")
    return paths


def tessdata_dir_for(exe_path: str) -> str | None:
    """根据 exe 推断 tessdata 目录。"""
    if not exe_path or exe_path == "tesseract":
        # 绿色包默认位置
        bundled = resource_path("tesseract", "tessdata")
        if os.path.isdir(bundled):
            return bundled
        return None
    base = os.path.dirname(os.path.abspath(exe_path))
    for name in ("tessdata", os.path.join("..", "tessdata")):
        d = os.path.normpath(os.path.join(base, name))
        if os.path.isdir(d):
            return d
    return None


def configure_tesseract(force: bool = False) -> tuple[bool, str]:
    """
    配置 pytesseract 可执行文件与 TESSDATA_PREFIX。
    返回 (是否可用, 说明文案)。
    """
    global _configured, _available, _status_message
    if _configured and not force and _available is not None:
        return _available, _status_message

    last_err = ""
    for candidate in tesseract_candidates():
        try:
            if candidate != "tesseract" and not os.path.isfile(candidate):
                continue
            if candidate != "tesseract":
                pytesseract.pytesseract.tesseract_cmd = candidate
            tessdata = tessdata_dir_for(candidate)
            if tessdata:
                os.environ["TESSDATA_PREFIX"] = tessdata
            # 探测版本
            ver = pytesseract.get_tesseract_version()
            langs = []
            try:
                langs = pytesseract.get_languages(config="")
            except Exception:
                pass
            has_zh = "chi_sim" in langs if langs else _has_chi_sim_file(tessdata)
            if not has_zh:
                last_err = f"已找到 Tesseract {ver}，但缺少中文语言包 chi_sim"
                logger.warning(last_err)
                continue
            _configured = True
            _available = True
            _status_message = (
                f"OCR 就绪 · Tesseract {ver} · 中文(chi_sim) · "
                f"{'内置' if 'tesseract' in os.path.normpath(str(candidate)).lower() and candidate != 'tesseract' else '系统'}"
            )
            logger.info(_status_message)
            return True, _status_message
        except Exception as exc:
            last_err = str(exc)
            continue

    _configured = True
    _available = False
    _status_message = (
        "OCR 不可用：未找到带中文包的 Tesseract。"
        "请将便携版放到程序目录 tesseract/（含 tesseract.exe 与 tessdata/chi_sim.traineddata）"
        + (f" · 详情: {last_err}" if last_err else "")
    )
    logger.warning(_status_message)
    return False, _status_message


def _has_chi_sim_file(tessdata: str | None) -> bool:
    if not tessdata:
        return False
    return os.path.isfile(os.path.join(tessdata, "chi_sim.traineddata"))


def is_available() -> bool:
    """OCR 是否可用。"""
    ok, _ = configure_tesseract()
    return ok


def status_text() -> str:
    """当前 OCR 状态说明。"""
    _, msg = configure_tesseract()
    return msg


def preprocess_for_ocr(image_bgr: np.ndarray, scale: float = 2.0) -> np.ndarray:
    """放大 + 灰度 + 自适应二值化，提高游戏 UI 小字识别率。"""
    if image_bgr is None or image_bgr.size == 0:
        return image_bgr
    h, w = image_bgr.shape[:2]
    scaled = cv2.resize(
        image_bgr,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_CUBIC,
    )
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    # 轻度锐化
    blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
    sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    binary = cv2.adaptiveThreshold(
        sharp, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8
    )
    return binary


def _black_on_white(bin_img: np.ndarray) -> np.ndarray:
    """保证二值图为黑字白底，并加白边（Tesseract 对贴边数字更友好）。"""
    if bin_img is None or bin_img.size == 0:
        return bin_img
    out = bin_img
    if np.mean(out) < 127:
        out = 255 - out
    return cv2.copyMakeBorder(out, 10, 10, 12, 12, cv2.BORDER_CONSTANT, value=255)


def _thicken_ink(mask: np.ndarray) -> np.ndarray:
    """加粗笔画，缓解「11」等细竖线被 OCR 漏读。"""
    k2 = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k2, iterations=1)
    return cv2.dilate(closed, k3, iterations=1)


def preprocess_orange_digits(image_bgr: np.ndarray, scale: float = 5.0) -> list[np.ndarray]:
    """
    针对游戏橙棕字 + 米色底的难度数字做预处理（黑字白底，已放大）。
    只保留对 10~20 最有效的少量变体，控制 OCR 耗时。
    """
    if image_bgr is None or image_bgr.size == 0:
        return []
    pad = 4
    src = cv2.copyMakeBorder(
        image_bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE
    )
    h0, w0 = src.shape[:2]
    scaled = cv2.resize(
        src,
        (max(1, int(w0 * scale)), max(1, int(h0 * scale))),
        interpolation=cv2.INTER_CUBIC,
    )
    variants: list[np.ndarray] = []

    # 1) HSV 橙/棕笔画：加粗版优先（利于「11」）
    hsv = cv2.cvtColor(scaled, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(
        cv2.inRange(hsv, np.array([2, 30, 60]), np.array([30, 255, 255])),
        cv2.inRange(hsv, np.array([5, 20, 40]), np.array([40, 230, 240])),
    )
    if int(mask.sum()) > 60:
        variants.append(_black_on_white(255 - _thicken_ink(mask)))
        variants.append(_black_on_white(255 - mask))

    # 2) 锐化灰度 Otsu（补充 HSV 漏墨时）
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    sharp = cv2.addWeighted(gray, 1.7, blur, -0.7, 0)
    _, otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 先得到白墨掩膜再加粗，最后黑字白底
    ink = otsu if np.mean(otsu) < 127 else (255 - otsu)
    variants.append(_black_on_white(255 - _thicken_ink(ink)))

    return variants


def _normalize_digit_lookalikes(raw: str) -> str:
    """把 OCR 常见形近字收成数字字符。"""
    if not raw:
        return ""
    t = str(raw).translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    # 两位数场景：l/I/| → 1，O/o → 0，S→5，B→8，Z→2
    repl = {
        "l": "1",
        "I": "1",
        "i": "1",
        "|": "1",
        "!": "1",
        "！": "1",
        "O": "0",
        "o": "0",
        "Q": "0",
        "S": "5",
        "s": "5",
        "B": "8",
        "Z": "2",
        "z": "2",
        "g": "9",
        "q": "9",
    }
    return "".join(repl.get(ch, ch) for ch in t)


def extract_levels_10_20(raw: str) -> list[tuple[int, int]]:
    """
    从单次 OCR 原文解析 10~20 难度候选及权重。
    优先整串恰为 10~20；其次去前导噪声后的末两位。
    """
    t = _normalize_digit_lookalikes(raw)
    digits = re.sub(r"\D+", "", t)
    if not digits:
        return []
    out: list[tuple[int, int]] = []
    if digits.isdigit():
        v = int(digits)
        if 10 <= v <= 20:
            out.append((v, 6))
            return out
    # 前导噪声：210→10，013→13
    if len(digits) >= 3:
        tail = int(digits[-2:])
        if 10 <= tail <= 20:
            out.append((tail, 4))
        head = int(digits[:2])
        if 10 <= head <= 20:
            out.append((head, 2))
    # 滑窗补漏（如 1912）
    for i in range(max(0, len(digits) - 1)):
        v = int(digits[i : i + 2])
        if 10 <= v <= 20:
            out.append((v, 1))
    return out


def read_difficulty_digits(
    window_image: np.ndarray,
    region: dict | None = None,
) -> list[str]:
    """
    专用于「难度」后数字条：橙字预处理 + 仅数字 OCR，返回候选字符串。
    针对 10~20：加粗竖笔、形近字纠正、加权早停（够票即停以控耗时）。
    """
    if not is_available() or window_image is None:
        return []
    image = window_image
    if region:
        ox, oy = int(region["x"]), int(region["y"])
        image = window_image[
            oy : oy + int(region["height"]),
            ox : ox + int(region["width"]),
        ]
        if image.size == 0:
            return []

    # 辅路径：少量预处理 + 单 PSM，命中 10~20 即停
    cfg = (
        "--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789 "
        "-c classify_bln_numeric_mode=1"
    )
    raw_candidates: list[str] = []
    weights: dict[int, int] = {}
    views = preprocess_orange_digits(image, scale=5.0)[:2]

    for img in views:
        if img is None or img.size == 0:
            continue
        try:
            raw = pytesseract.image_to_string(img, lang="eng", config=cfg)
        except Exception:
            continue
        norm = _normalize_digit_lookalikes(raw or "")
        digits = re.sub(r"\D+", "", norm)
        if not digits:
            continue
        raw_candidates.append(digits)
        for val, w in extract_levels_10_20(digits):
            weights[val] = weights.get(val, 0) + w
        if weights and max(weights.values()) >= 12:
            break

    seen: set[str] = set()
    out: list[str] = []
    for d in raw_candidates:
        if d not in seen:
            seen.add(d)
            out.append(d)

    def _rank(s: str) -> tuple:
        if s.isdigit() and 10 <= int(s) <= 20:
            return (0, -len(s), s)
        return (1, -len(s), s)

    out.sort(key=_rank)
    return out


def vote_difficulty_10_20(digit_strs: list[str]) -> int | None:
    """
    对多个 OCR 数字串加权投票，专攻难度 10~20。
    整串命中权重最高；平票时取票数多者，再平则取出现顺序更靠前者。
    """
    weights: dict[int, int] = {}
    first_seen: dict[int, int] = {}
    order = 0
    for raw in digit_strs:
        for val, w in extract_levels_10_20(raw):
            weights[val] = weights.get(val, 0) + w
            if val not in first_seen:
                first_seen[val] = order
                order += 1
    if not weights:
        return None
    # 票数高优先；同票取先出现（通常来自橙字主预处理）
    best = max(
        weights.keys(),
        key=lambda v: (weights[v], -first_seen.get(v, 999)),
    )
    return int(best)


def parse_explicit_nandu(text: str) -> int | None:
    """
    从 OCR 文本提取「难度N」（N=1~20）。
    命中即视为权威结果，不被 La!|→11 这类噪声投票覆盖。
    """
    if not text or "难度" not in text:
        return None
    t = str(text).translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    t = re.sub(r"\s+", "", t)

    # 「难度」后 1~3 字符做形近字纠正再抽数字：1S→15、1l→11
    def _fix_tail(m: re.Match) -> str:
        tail = _normalize_digit_lookalikes(m.group(1))
        digits = re.sub(r"\D+", "", tail)
        return "难度" + digits

    t = re.sub(r"难度(.{1,3})", _fix_tail, t, count=1)
    # 常见残留：难度ll
    t = re.sub(r"难度[lI|！]{2}", "难度11", t)
    for n in range(20, 0, -1):
        if re.search(rf"难度{n}(?!\d)", t):
            return int(n)
    return None


def extract_difficulty_10_20_from_text(text: str) -> list[tuple[int, int]]:
    """
    从整行 OCR（含中文「难度」）解析 10~20。
    「难度XX」绝对优先（高权重）；无「难度」时再看末尾真数字。
    """
    if not text:
        return []
    # 先查「难度XX」，不做 |/!→1，避免把噪声抬成 11
    nandu = parse_explicit_nandu(text)
    if nandu is not None and 10 <= nandu <= 20:
        return [(nandu, 100)]
    if nandu is not None:
        return []

    # 仅当原文本身含阿拉伯数字时，才做末尾/滑窗解析
    if not re.search(r"\d", text):
        return []
    t = _normalize_digit_lookalikes(text)
    t = re.sub(r"\s+", "", t)
    out: list[tuple[int, int]] = []
    m = re.search(r"(20|1[0-9])(?!\d).{0,2}$", t)
    if not m:
        m = re.search(r"(20|1[0-9])(?!\d)", t)
    if m:
        out.append((int(m.group(1)), 7))
        return out
    out.extend(extract_levels_10_20(t))
    return out


def read_difficulty_line_texts(
    window_image: np.ndarray,
    region: dict | None = None,
) -> list[str]:
    """
    识别「…难度XX」整行：优先快速路径（预处理 + chi_sim），命中「难度」即停。
    """
    if not is_available() or window_image is None:
        return []
    image = window_image
    if region:
        ox, oy = int(region["x"]), int(region["y"])
        image = window_image[
            oy : oy + int(region["height"]),
            ox : ox + int(region["width"]),
        ]
        if image.size == 0:
            return []

    # 快速路径在前；失败再补橙字/原图，控制 Tesseract 调用次数
    views: list[np.ndarray] = []
    try:
        views.append(preprocess_for_ocr(image, scale=2.5))
    except Exception:
        pass
    views.append(image)
    views.extend(preprocess_orange_digits(image, scale=4.0)[:1])

    texts: list[str] = []
    seen: set[str] = set()
    # 先 chi_sim+psm7；不够再 psm6 / 中英混合
    attempts = (
        (DEFAULT_LANG, "--psm 7"),
        (DEFAULT_LANG, "--psm 6"),
        (FALLBACK_LANG, "--psm 7"),
    )

    for img in views:
        if img is None or img.size == 0:
            continue
        for lang, cfg in attempts:
            try:
                raw = pytesseract.image_to_string(img, lang=lang, config=cfg)
            except Exception:
                continue
            norm = _normalize_text(raw or "")
            if not norm or norm in seen:
                continue
            seen.add(norm)
            texts.append(norm)
            # 仅 10~20 早停；「难度1」可能是「难度15」截断，继续扫
            nandu = parse_explicit_nandu(norm)
            if nandu is not None and 10 <= nandu <= 20:
                return texts
    return texts


# 环难度数字条模板缓存：{难度: BGR 小图}，从 image/8.png~20.png 右缘裁切
_huan_digit_templates: dict[int, np.ndarray] | None = None


def load_huan_digit_templates(
    huan_path: str,
    sample_paths: dict[int, str],
) -> dict[int, np.ndarray]:
    """
    从带数字的整行截图里，相对 huan.png 右缘裁出数字条模板。
    sample_paths: {8: path/to/8.png, ...}；失败的难度会跳过。
    """
    global _huan_digit_templates
    if _huan_digit_templates is not None:
        return _huan_digit_templates

    # 支持中文路径（绿色包目录名可能含中文）
    huan = imread_unicode(huan_path)
    out: dict[int, np.ndarray] = {}
    if huan is None:
        logger.warning("无法加载 huan.png，跳过难度数字模板: %s", huan_path)
        _huan_digit_templates = out
        return out

    hh, hw = huan.shape[:2]
    for n, path in sorted(sample_paths.items(), reverse=True):
        im = imread_unicode(path)
        if im is None:
            continue
        # 样本可能比 huan 矮/窄，先垫边再匹配
        pad = cv2.copyMakeBorder(im, 4, 4, 4, 24, cv2.BORDER_REPLICATE)
        if pad.shape[0] < hh or pad.shape[1] < hw:
            continue
        res = cv2.matchTemplate(pad, huan, cv2.TM_CCOEFF_NORMED)
        _, score, _, max_loc = cv2.minMaxLoc(res)
        mx, my = max_loc
        if score < 0.85:
            logger.warning("难度样本 %s 与 huan 对齐分过低: %.3f", path, score)
            continue
        # 紧贴「难度」右侧，覆盖 1~2 位数字
        dig = pad[my : my + hh, mx + hw : mx + hw + 30]
        if dig.size == 0 or dig.shape[1] < 8:
            continue
        out[int(n)] = dig

    _huan_digit_templates = out
    logger.info("已加载环难度数字模板 %d 个: %s", len(out), sorted(out.keys()))
    return out


def match_difficulty_by_template(
    window_image: np.ndarray,
    region: dict,
    templates: dict[int, np.ndarray],
    threshold: float = 0.82,
    min_margin: float = 0.008,
) -> tuple[int | None, float, list[tuple[int, float]]]:
    """
    在数字条区域内用模板匹配识别难度。
    返回 (难度或 None, 最高分, 全部得分列表)。
    """
    if window_image is None or not templates or not region:
        return None, 0.0, []
    ox, oy = int(region["x"]), int(region["y"])
    rw, rh = int(region["width"]), int(region["height"])
    crop = window_image[oy : oy + rh, ox : ox + rw]
    if crop.size == 0:
        return None, 0.0, []
    # 略垫边，容忍 huan 定位偏差 1~2px
    search = cv2.copyMakeBorder(crop, 3, 3, 3, 3, cv2.BORDER_REPLICATE)
    search_gray = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

    scores: list[tuple[int, float]] = []
    for n, tpl in templates.items():
        if tpl is None or tpl.size == 0:
            continue
        tg = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
        if tg.shape[0] > search_gray.shape[0] or tg.shape[1] > search_gray.shape[1]:
            continue
        sc = float(cv2.minMaxLoc(cv2.matchTemplate(search_gray, tg, cv2.TM_CCOEFF_NORMED))[1])
        scores.append((int(n), sc))

    if not scores:
        return None, 0.0, []
    scores.sort(key=lambda x: -x[1])
    best_n, best_sc = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    if best_sc < threshold or (best_sc - second) < min_margin:
        return None, best_sc, scores
    return best_n, best_sc, scores


def _normalize_text(text: str) -> str:
    """去掉空白与常见 OCR 噪声，便于中文比对。"""
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r"\s+", "", text)
    return text


def _text_match(ocr_text: str, target: str) -> float:
    """
    计算 OCR 文本与目标的匹配度 0~1。
    中文以包含/相等为主；英文忽略大小写。
    """
    a = _normalize_text(ocr_text)
    b = _normalize_text(target)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b in a or a in b:
        return min(len(b), len(a)) / max(len(b), len(a))
    # 逐字命中比例（适合短按钮）
    hit = sum(1 for ch in b if ch in a)
    return hit / len(b) if b else 0.0


def find_text(
    window_image: np.ndarray,
    target_text: str,
    language: str = DEFAULT_LANG,
    threshold: float = 0.6,
    region: dict | None = None,
) -> OcrHit | None:
    """
    在窗口截图（或子区域）中用 OCR 查找中文/文本按钮。
    region: 可选 {x,y,width,height}，仅在该区域识别。
    """
    if not is_available():
        return None
    if window_image is None or not target_text:
        return None

    ox = oy = 0
    image = window_image
    if region:
        ox, oy = int(region["x"]), int(region["y"])
        image = window_image[
            oy : oy + int(region["height"]),
            ox : ox + int(region["width"]),
        ]
        if image.size == 0:
            return None

    # 先原图；未命中再预处理（少一次完整 OCR）
    candidates = [image]
    try:
        candidates.append(preprocess_for_ocr(image, scale=2.0))
    except Exception:
        pass
    best: OcrHit | None = None

    for img in candidates:
        try:
            data = pytesseract.image_to_data(
                img,
                lang=language,
                output_type=pytesseract.Output.DICT,
                config="--psm 6",
            )
        except pytesseract.TesseractError:
            # 语言包名不对时尝试中英混合
            if language != FALLBACK_LANG:
                try:
                    data = pytesseract.image_to_data(
                        img,
                        lang=FALLBACK_LANG,
                        output_type=pytesseract.Output.DICT,
                        config="--psm 6",
                    )
                except Exception as exc:
                    logger.debug("OCR 失败: %s", exc)
                    continue
            else:
                continue
        except Exception as exc:
            logger.debug("OCR 失败: %s", exc)
            continue

        n = len(data.get("text", []))
        # 预处理图被放大时，坐标缩回原图尺寸
        if img.shape[1] > 0 and img.shape[0] > 0:
            scale_back_x = image.shape[1] / img.shape[1]
            scale_back_y = image.shape[0] / img.shape[0]
        else:
            scale_back_x = scale_back_y = 1.0

        # 同时检查单词与同行拼接
        line_buckets: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            raw = (data["text"][i] or "").strip()
            if not raw:
                continue
            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = -1
            if conf < 0:
                continue
            score = _text_match(raw, target_text)
            if score >= threshold:
                x = int(data["left"][i] * scale_back_x) + ox
                y = int(data["top"][i] * scale_back_y) + oy
                w = max(1, int(data["width"][i] * scale_back_x))
                h = max(1, int(data["height"][i] * scale_back_y))
                conf_factor = conf / 100.0 if conf <= 100 else 1.0
                hit = OcrHit(raw, x, y, w, h, score * conf_factor)
                if best is None or hit.confidence > best.confidence:
                    best = hit

            key = (data["block_num"][i], data["line_num"][i])
            line_buckets.setdefault(key, []).append(i)

        for indices in line_buckets.values():
            parts = [(data["text"][i] or "").strip() for i in indices]
            joined = "".join(parts)
            score = _text_match(joined, target_text)
            if score < threshold:
                continue
            xs = [data["left"][i] for i in indices]
            ys = [data["top"][i] for i in indices]
            rights = [data["left"][i] + data["width"][i] for i in indices]
            bottoms = [data["top"][i] + data["height"][i] for i in indices]
            x = int(min(xs) * scale_back_x) + ox
            y = int(min(ys) * scale_back_y) + oy
            w = max(1, int((max(rights) - min(xs)) * scale_back_x))
            h = max(1, int((max(bottoms) - min(ys)) * scale_back_y))
            hit = OcrHit(joined, x, y, w, h, score)
            if best is None or hit.confidence > best.confidence:
                best = hit

        # 已足够好则跳过后续预处理图，省一次 OCR
        if best is not None and best.confidence >= max(threshold, 0.75):
            return best

    return best


def read_region_text(
    window_image: np.ndarray,
    region: dict | None = None,
    language: str = FALLBACK_LANG,
    digits_only: bool = False,
) -> str:
    """
    识别窗口截图（或子区域）全文，用于解析「难度12」等数字。
    digits_only=True 时仅识别 0-9（适合难度后的数字条）。
    返回拼接后的纯文本（去空白）。
    """
    if not is_available() or window_image is None:
        return ""
    image = window_image
    if region:
        ox, oy = int(region["x"]), int(region["y"])
        image = window_image[
            oy : oy + int(region["height"]),
            ox : ox + int(region["width"]),
        ]
        if image.size == 0:
            return ""

    if digits_only:
        configs = (
            "--psm 7 -c tessedit_char_whitelist=0123456789",
            "--psm 8 -c tessedit_char_whitelist=0123456789",
            "--psm 13 -c tessedit_char_whitelist=0123456789",
        )
        lang = "eng"
    else:
        configs = ("--psm 6", "--psm 7")
        lang = language

    texts: list[str] = []
    for img in (image, preprocess_for_ocr(image, scale=2.5 if digits_only else 2.0)):
        for cfg in configs:
            try:
                raw = pytesseract.image_to_string(img, lang=lang, config=cfg)
            except Exception:
                if not digits_only and lang != DEFAULT_LANG:
                    try:
                        raw = pytesseract.image_to_string(
                            img, lang=DEFAULT_LANG, config=cfg
                        )
                    except Exception as exc:
                        logger.debug("OCR 读全文失败: %s", exc)
                        continue
                else:
                    continue
            norm = _normalize_text(raw)
            if digits_only:
                norm = re.sub(r"\D+", "", norm)
            if norm:
                texts.append(norm)
    # 选最长结果（通常信息更全）
    return max(texts, key=len) if texts else ""


def list_texts(
    window_image: np.ndarray,
    language: str = DEFAULT_LANG,
    min_confidence: float = 40,
) -> list[OcrHit]:
    """列出截图中识别到的文本块（调试用）。"""
    if not is_available() or window_image is None:
        return []
    img = preprocess_for_ocr(window_image)
    try:
        data = pytesseract.image_to_data(
            img, lang=language, output_type=pytesseract.Output.DICT, config="--psm 6"
        )
    except Exception:
        return []
    sx = window_image.shape[1] / img.shape[1]
    sy = window_image.shape[0] / img.shape[0]
    hits = []
    for i, raw in enumerate(data.get("text", [])):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            continue
        if conf < min_confidence:
            continue
        hits.append(
            OcrHit(
                text,
                int(data["left"][i] * sx),
                int(data["top"][i] * sy),
                max(1, int(data["width"][i] * sx)),
                max(1, int(data["height"][i] * sy)),
                conf / 100.0,
            )
        )
    return hits
