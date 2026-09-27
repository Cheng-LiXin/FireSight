"""环形帧缓冲：告警时导出前后 N 秒片段。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, List, Optional, Tuple

import numpy as np

# 浏览器可播放的编码，顺序即优先级。
# VP9/WebM 无授权依赖；avc1 需要系统有 H.264 编码器（本机缺 OpenH264，但实测 Edge
# 能播 avc1 产物，说明写出来的是别的路径）。每个都会回读验证，不通过就换下一个。
# mp4v 不在列表里，理由见 write_clip 文档。
_CODECS: Tuple[Tuple[str, str], ...] = (
    ("VP90", ".webm"),
    ("VP80", ".webm"),
    ("avc1", ".mp4"),
    ("AV01", ".mp4"),
)


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
    """写出**浏览器可播放**的片段，返回实际落盘路径（后缀可能被换掉）。

    为什么不是一句 VideoWriter 就完事：

    1. 浏览器只认 H.264 / VP8 / VP9 / AV1。OpenCV 惯用的 `mp4v`（MPEG-4 Part 2）
       不在其中——实测 `<video>` 直接报 `DEMUXER_ERROR_NO_SUPPORTED_STREAMS`，
       画面全无、只剩一条控制栏。所以按 `_CODECS` 顺序挑一个能用的。
    2. 只信 `writer.isOpened()` 会踩坑：本机缺 OpenH264 时它照样返回 True，却写出一个
       只有文件头、没有帧的空容器，且不抛任何异常。因此每写一份都**回读帧数**验证。
    3. `mp4v` 故意不作为兜底。浏览器播不了的片段比「没有片段」更糟：界面会给出回放
       按钮，用户点了永远不出画面，还查不出原因。
    """
    import cv2

    if not frames:
        raise ValueError("无帧可写")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    failures: List[str] = []

    for fourcc, suffix in _CODECS:
        candidate = out_path.with_suffix(suffix)
        try:
            if _encode(cv2, frames, candidate, fps, fourcc):
                _drop_stale(out_path, candidate)
                return candidate
            failures.append(f"{fourcc}: 回读校验未通过")
        except Exception as exc:  # noqa: BLE001 - 换下一个编码再试
            failures.append(f"{fourcc}: {type(exc).__name__}: {exc}")
        if candidate.exists():
            candidate.unlink()

    raise RuntimeError("没有可用的视频编码：" + "；".join(failures))


def _encode(cv2, frames: List[np.ndarray], path: Path, fps: float, fourcc: str) -> bool:
    """写一份并回读验证，返回是否真的得到了可解码的片段。"""
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*fourcc), float(fps), (w, h)
    )
    try:
        if not writer.isOpened():
            return False
        for img in frames:
            if img.shape[0] != h or img.shape[1] != w:
                img = cv2.resize(img, (w, h))
            writer.write(img)
    finally:
        writer.release()

    cap = cv2.VideoCapture(str(path))
    got = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        got += 1
    cap.release()
    return got >= len(frames)


def _drop_stale(base: Path, keep: Path) -> None:
    """换编码成功时清掉其它后缀的残留，避免同一事件留下两份片段。"""
    for _, suffix in _CODECS:
        stale = base.with_suffix(suffix)
        if stale != keep and stale.exists():
            stale.unlink()
