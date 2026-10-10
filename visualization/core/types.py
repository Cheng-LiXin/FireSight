"""检测结果绘制用数据结构(与模型解耦)。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


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
    # 不传就按名字查配色；以前默认 (0,255,0)，而那个值恒为真，
    # 导致 `det.color if det.color else ...` 的自定义配色永远不生效。
    color: Optional[Tuple[int, int, int]] = None
