"""帧源工厂: 按路径/类型自动识别。"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from frame_source.core.base import FrameSource
from frame_source.sources.camera import CameraSource
from frame_source.sources.image_dir import ImageDirSource
from frame_source.sources.video import VideoFileSource


def create_frame_source(
    source: Union[str, int],
    source_id: str = "",
    stop_event=None,
) -> FrameSource:
    """根据 source 创建帧源。

    - "0" / 0 → 摄像头
    - rtsp/http URL → 网络流
    - 目录 → 图片文件夹
    - *.mp4 等 → 视频文件

    stop_event：可选的停止信号（只要有 is_set()）。传了之后，源在每轮取帧前
    都会看一眼它，常驻监控因此能真正打断「正在读帧」的等待，而不只是打断
    已经拿到手的帧。不传则行为与从前一致。
    """
    if isinstance(source, str) and source.lower().startswith(
        ("rtsp://", "rtmp://", "http://", "https://")
    ):
        fs: FrameSource = CameraSource(source, source_id=source_id)
    elif isinstance(source, int):
        fs = CameraSource(source, source_id=source_id)
    elif isinstance(source, str) and source.isdigit():
        fs = CameraSource(int(source), source_id=source_id)
    else:
        path = Path(str(source))
        if path.is_dir():
            fs = ImageDirSource(path, source_id=source_id)
        elif path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
            fs = VideoFileSource(path, source_id=source_id)
        elif path.is_file():
            fs = ImageDirSource(path.parent, source_id=source_id or path.name)
        else:
            raise ValueError(f"无法识别帧源: {source!r}")
    if stop_event is not None:
        fs.with_stop_event(stop_event)
    return fs
