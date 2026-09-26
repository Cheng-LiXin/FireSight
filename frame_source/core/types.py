"""统一帧数据结构(高内聚:与具体源无关)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import numpy as np


@dataclass
class Frame:
    """一帧图像 + 元信息。image 为 BGR ndarray (H, W, 3)。"""

    image: np.ndarray
    index: int = 0
    timestamp: float = 0.0
    source_id: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])
