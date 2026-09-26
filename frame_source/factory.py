"""帧源工厂: 按路径/类型自动识别。"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from frame_source.core.base import FrameSource
from frame_source.sources.camera import CameraSource
from frame_source.sources.image_dir import ImageDirSource
from frame_source.sources.video import VideoFileSource


def create_frame_source(source: Union[str, int], source_id: str = "") -> FrameSource:
    """根据 source 创建帧源。

    - "0" / 0 → 摄像头
    - rtsp/http URL → 网络流
    - 目录 → 图片文件夹
    - *.mp4 等 → 视频文件
    """
    if isinstance(source, str) and source.lower().startswith(
        ("rtsp://", "rtmp://", "http://", "https://")
    ):
        return CameraSource(source, source_id=source_id)
    if isinstance(source, int):
        return CameraSource(source, source_id=source_id)
    if isinstance(source, str) and source.isdigit():
        return CameraSource(int(source), source_id=source_id)

    path = Path(str(source))
    if path.is_dir():
        return ImageDirSource(path, source_id=source_id)
    if path.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        return VideoFileSource(path, source_id=source_id)
    if path.is_file():
        return ImageDirSource(path.parent, source_id=source_id or path.name)

    raise ValueError(f"无法识别帧源: {source!r}")
