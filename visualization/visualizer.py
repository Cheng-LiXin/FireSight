"""BeautifyVisualizer: 检测框 + 中文类别标签。"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from visualization.core.draw_utils import draw_label_bg, draw_rounded_box
from visualization.core.text_cache import TextRenderer
from visualization.core.types import Detection

# 火灾场景默认配色 (BGR)
DEFAULT_COLORS: Dict[str, Tuple[int, int, int]] = {
    "fire": (0, 64, 255),      # 橙红
    "smoke": (160, 160, 160),  # 灰
}

DEFAULT_LABEL_MAP: Dict[str, str] = {
    "fire": "明火",
    "smoke": "烟雾",
}


class BeautifyVisualizer:
    def __init__(
        self,
        label_mapping: Optional[Dict[str, str]] = None,
        color_mapping: Optional[Dict[str, Tuple[int, int, int]]] = None,
        font_size: int = 18,
    ) -> None:
        self.label_mapping = {**DEFAULT_LABEL_MAP, **(label_mapping or {})}
        self.color_mapping = {**DEFAULT_COLORS, **(color_mapping or {})}
        self.text = TextRenderer(font_size=font_size)

    def color_for(self, name: str) -> Tuple[int, int, int]:
        return self.color_mapping.get(name, (0, 255, 0))

    def draw(
        self,
        image: np.ndarray,
        detections: Sequence[Detection],
    ) -> np.ndarray:
        """画框与标签。

        热路径：**框和底色用 cv2 直接画在 BGR 图上，所有标签文字攒起来一次画完**。
        以前每条标签单独调 `TextRenderer.draw()`，那是两次整帧颜色往返——1080p
        下约 17ms/条，20 个框 0.44 秒/帧。文字统一到最后画，重叠标签时的层次与
        旧行为略有差别（后画的底色不再盖住先画好的文字），视觉上等价。
        """
        out = image.copy()

        labels: List[Tuple[str, Tuple[int, int]]] = []
        for det in detections:
            color = det.color if det.color is not None else self.color_for(det.name)
            x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)
            draw_rounded_box(out, x1, y1, x2, y2, color, thickness=2)
            label = self.label_mapping.get(det.name, det.name)
            text = f"{label} {det.conf:.2f}"
            tw = 8 + int(self.text.font_size * 0.7 * len(text))
            th = self.text.font_size + 10
            ty = max(0, y1 - th)
            draw_label_bg(out, x1, ty, min(x1 + tw, out.shape[1] - 1), ty + th, color)
            labels.append((text, (x1 + 4, ty + 2)))

        return self.text.draw_many(out, labels, color=(255, 255, 255))
