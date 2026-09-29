"""摄像头 / RTSP 帧源。"""

from __future__ import annotations

import time
from typing import Iterator, Union

import cv2

from frame_source.core.base import FrameSource
from frame_source.core.registry import register_source
from frame_source.core.types import Frame


class CameraSource(FrameSource):
    """device 可为摄像头序号(int/str) 或 rtsp/http URL。"""

    def __init__(self, device: Union[int, str] = 0, source_id: str = "") -> None:
        self.device = device
        super().__init__(source_id or f"camera-{device}")
        self._cap = cv2.VideoCapture(device)
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
                meta={"device": str(self.device)},
            )
            i += 1

    def close(self) -> None:
        if getattr(self, "_cap", None) is not None:
            self._cap.release()
            self._cap = None


@register_source("camera")
def _factory(device: Union[int, str] = 0, **kwargs) -> FrameSource:
    return CameraSource(device, **kwargs)
