"""环形帧缓冲：告警时导出前后 N 秒片段。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, List, Optional, Tuple

import numpy as np


@dataclass
class BufferedFrame:
    index: int
    timestamp: float
    image: np.ndarray


class RingClipBuffer:
    """内存环形缓冲，不绑定视频编解码(低耦合)。"""

    def __init__(self, max_frames: int = 90, fps: float = 30.0) -> None:
        self.max_frames = max_frames
        self.fps = fps
        self._buf: Deque[BufferedFrame] = deque(maxlen=max_frames)

    def push(self, index: int, timestamp: float, image: np.ndarray) -> None:
        self._buf.append(BufferedFrame(index=index, timestamp=timestamp, image=image))

    def snapshot(self, tail_frames: int = 0) -> List[np.ndarray]:
        """返回当前缓冲中的帧(可追加告警后的 tail_frames 由调用方续推)。"""
        return [f.image.copy() for f in self._buf]

    def clear(self) -> None:
        self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


def write_clip(
    frames: List[np.ndarray],
    out_path: Path | str,
    fps: float = 25.0,
) -> Path:
    """用 OpenCV 写出可播放 mp4。"""
    import cv2

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not frames:
        raise ValueError("无帧可写")
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps), (w, h)
    )
    try:
        for img in frames:
            if img.shape[0] != h or img.shape[1] != w:
                img = cv2.resize(img, (w, h))
            writer.write(img)
    finally:
        writer.release()
    return out_path
