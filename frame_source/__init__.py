"""frame_source: 可插拔多模态帧输入。

    from frame_source import create_frame_source
    with create_frame_source("clip.mp4") as src:
        for frame in src:
            process(frame.image)
"""

from frame_source._version import __version__
from frame_source.core.base import FrameSource
from frame_source.core.types import Frame
from frame_source.factory import create_frame_source

__all__ = ["__version__", "Frame", "FrameSource", "create_frame_source"]
