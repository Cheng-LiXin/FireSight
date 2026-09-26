"""检测结果绘制用数据结构(与模型解耦)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Detection:
    cls_id: int
    name: str
    conf: float
    # xyxy 像素坐标
    x1: float
    y1: float
    x2: float
    y2: float
    color: Tuple[int, int, int] = (0, 255, 0)
