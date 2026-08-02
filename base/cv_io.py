"""
OpenCV 读写封装。
Windows 下 cv2.imread 无法可靠打开含中文的路径，统一用 fromfile + imdecode。
"""

from __future__ import annotations

import cv2
import numpy as np


def imread(path: str, flags: int = cv2.IMREAD_COLOR):
    """
    读取图片，行为接近 cv2.imread，但支持中文/非 ASCII 路径。
    失败返回 None。
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except (OSError, ValueError):
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)
