"""中文文本绘制(PIL)，避免 cv2 中文乱码。"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

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

    def draw_many(
        self,
        img: np.ndarray,
        items: Sequence[Tuple[str, Tuple[int, int]]],
        color: Tuple[int, int, int] = (255, 255, 255),
    ) -> np.ndarray:
        """一次画多条标签，**整帧只做一趟 BGR<->RGB**。

        这是热路径的关键：每条标签单独走一次 `draw()` 就是两次整帧拷贝
        （BGR->RGB 再 RGB->BGR），1080p 下约 17ms。20 个框 = 0.44 秒/帧。
        所有标签共用一个 PIL 图，转换次数降到每帧一次。
        """
        if not items:
            return img
        pil = Image.fromarray(cv2_bgr2rgb(img))
        draw = ImageDraw.Draw(pil)
        for text, (x, y) in items:
            draw.text((x, y), text, font=self._font, fill=color)
        return cv2_rgb2bgr(np.asarray(pil))

    def draw(
        self,
        img: np.ndarray,
        text: str,
        org: Tuple[int, int],
        color: Tuple[int, int, int] = (255, 255, 255),
        bg: Optional[Tuple[int, int, int]] = None,
    ) -> np.ndarray:
        """画一条标签。批量场景请用 `draw_many`，别在循环里调它。"""
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
        return cv2_rgb2bgr(np.asarray(pil))


def cv2_bgr2rgb(img: np.ndarray) -> np.ndarray:
    return img[:, :, ::-1].copy()


def cv2_rgb2bgr(img: np.ndarray) -> np.ndarray:
    return img[:, :, ::-1].copy()
