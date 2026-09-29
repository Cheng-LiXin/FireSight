"""视频文件帧源。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator

import cv2

from frame_source.core.base import FrameSource
from frame_source.core.registry import register_source
from frame_source.core.types import Frame


class VideoFileSource(FrameSource):
    def __init__(self, path, source_id: str = "") -> None:
        super().__init__(source_id or Path(path).name)
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"视频不存在: {self.path}")
        self._cap = cv2.VideoCapture(str(self.path))
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps > 0:
            self.fps = fps

    def __iter__(self) -> Iterator[Frame]:
        i = 0
        while True:
            ok, img = self._cap.read()
            if not ok:
                break
            yield Frame(
                image=img,
                index=i,
                timestamp=time.time(),
                source_id=self.source_id,
                meta={"path": str(self.path)},
            )
            i += 1

    def close(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.release()
            self._cap = None


@register_source("video")
def _factory(path: str, **kwargs) -> FrameSource:
    return VideoFileSource(path, **kwargs)
