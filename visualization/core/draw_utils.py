"""BGR 绘制工具: 圆角框、填充标签底。"""

from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np


def draw_rounded_box(
    img: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Tuple[int, int, int],
    thickness: int = 2,
) -> None:
    """简单圆角感: 矩形 + 四角圆点。"""
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thickness, lineType=cv2.LINE_AA)
    r = max(2, thickness)
    for cx, cy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
        cv2.circle(img, (cx, cy), r, color, -1, lineType=cv2.LINE_AA)


def draw_label_bg(
    img: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Tuple[int, int, int],
) -> None:
    overlay = img.copy()
    cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
