"""图片文件夹帧源。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, Sequence

import cv2

from frame_source.core.base import FrameSource
from frame_source.core.registry import register_source
from frame_source.core.types import Frame

_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class ImageDirSource(FrameSource):
    def __init__(self, directory, source_id: str = "") -> None:
        super().__init__(source_id or str(directory))
        self.directory = Path(directory)
        self.paths: Sequence[Path] = sorted(
            p for p in self.directory.iterdir() if p.suffix.lower() in _EXTS
        )
        if not self.paths:
            raise FileNotFoundError(f"目录中无图像: {self.directory}")

    def __iter__(self) -> Iterator[Frame]:
        for i, path in enumerate(self.paths):
            # 目录源读的是本地磁盘（毫秒级），但万张图的目录仍要能被立刻叫停。
            if self.stopped():
                return
            img = cv2.imread(str(path))
            if img is None:
                continue
            yield Frame(
                image=img,
                index=i,
                timestamp=time.time(),
                source_id=self.source_id,
                meta={"path": str(path), "stem": path.stem},
            )


@register_source("image_dir")
def _factory(directory: str, **kwargs) -> FrameSource:
    return ImageDirSource(directory, **kwargs)
