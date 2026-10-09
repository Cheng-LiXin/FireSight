"""帧源工厂: 按路径/类型自动识别。"""

from __future__ import annotations

from pathlib import Path
from typing import Union

from frame_source.core.base import FrameSource
from frame_source.core.registry import get_factory
from frame_source.sources.camera import is_stream_url

#: 视频文件后缀——归 video 帧源。
VIDEO_SUFFIXES = (".mp4", ".avi", ".mov", ".mkv", ".webm")


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

    本函数只负责「选哪种帧源」，真正的构造交给注册表里的工厂。此前这里是
    一串写死的 elif 直接 new 具体类，`@register_source` 注册了也白注册
    —— 新加一种源必须在两个地方各改一次（2026-10-09 代码审查）。

    stop_event：可选的停止信号（只要有 is_set()）。传了之后，源在每轮取帧前
    都会看一眼它，常驻监控因此能真正打断「正在读帧」的等待，而不只是打断
    已经拿到手的帧。不传则行为与从前一致。
    """
    if is_stream_url(source):
        fs: FrameSource = get_factory("camera")(source, source_id=source_id)
    elif isinstance(source, int):
        fs = get_factory("camera")(source, source_id=source_id)
    elif isinstance(source, str) and source.isdigit():
        fs = get_factory("camera")(int(source), source_id=source_id)
    else:
        path = Path(str(source))
        if path.is_dir():
            fs = get_factory("image_dir")(str(path), source_id=source_id)
        elif path.suffix.lower() in VIDEO_SUFFIXES:
            fs = get_factory("video")(str(path), source_id=source_id)
        elif path.is_file():
            # 单张图片：退化成它所在目录的图片源，但这个文件名要成为 source_id，
            # 否则 meta 里认不出帧来自哪。
            fs = get_factory("image_dir")(str(path.parent), source_id=source_id or path.name)
        else:
            raise ValueError(f"无法识别帧源: {source!r}")
    if stop_event is not None:
        fs.with_stop_event(stop_event)
    return fs
