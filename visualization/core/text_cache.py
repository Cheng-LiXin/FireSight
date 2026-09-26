"""中文文本绘制(PIL)，避免 cv2 中文乱码。"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/Deng.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


class TextRenderer:
    """在 BGR ndarray 上绘制中英文标签。"""

    def __init__(self, font_size: int = 18) -> None:
        self.font_size = font_size
        self._font = _load_font(font_size)

    def draw(
        self,
        img: np.ndarray,
        text: str,
        org: Tuple[int, int],
        color: Tuple[int, int, int] = (255, 255, 255),
        bg: Optional[Tuple[int, int, int]] = None,
    ) -> np.ndarray:
        pil = Image.fromarray(cv2_bgr2rgb(img))
        draw = ImageDraw.Draw(pil)
        x, y = org
        if bg is not None:
            try:
                bbox = draw.textbbox((x, y), text, font=self._font)
            except Exception:
                bbox = (x, y, x + self.font_size * len(text), y + self.font_size)
            draw.rectangle(bbox, fill=bg)
        draw.text((x, y), text, font=self._font, fill=color)
        return cv2_rgb2bgr(np.array(pil))


def cv2_bgr2rgb(img: np.ndarray) -> np.ndarray:
    return img[:, :, ::-1].copy()


def cv2_rgb2bgr(img: np.ndarray) -> np.ndarray:
    return img[:, :, ::-1].copy()
