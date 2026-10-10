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
    """在标签位置铺一层半透明底色。

    **只对标签矩形做混合，不整帧 copy**：底色矩形之外 `overlay` 与 `img` 逐像素
    相同，`0.55*overlay + 0.45*img` 在那里恒等于 `img`，所以裁到 ROI 上算结果
    一样，但代价从「每帧 1080p 一张」降到「每个标签一小块」。实测每个检测约省
    17ms（见 docs/reviews/2026-10-09-代码审查.md 的 R12）。
    """
    h, w = img.shape[:2]
    # cv2.rectangle(x1,y1,x2,y2,-1) 的端点是**闭区间**（含 x2、y2 那一列/行），
    # numpy 切片是半开区间，所以这里要 +1；同时得自己裁到画布内——cv2 会替我们
    # 裁，改成 ROI 切片后负索引会绕到图像另一侧去混合。
    x1, x2 = max(0, min(int(x1), w)), max(0, min(int(x2) + 1, w))
    y1, y2 = max(0, min(int(y1), h)), max(0, min(int(y2) + 1, h))
    if x2 <= x1 or y2 <= y1:
        return
    roi = img[y1:y2, x1:x2]
    layer = np.empty_like(roi)
    layer[:] = color
    # 结果算在新的连续数组上再整体写回：dst 直接传 strided 视图交给 OpenCV 是靠
    # 不住的（非连续数组可能只改到临时副本），而写回用 numpy 切片赋值是确定的。
    img[y1:y2, x1:x2] = cv2.addWeighted(roi, 0.45, layer, 0.55, 0)
