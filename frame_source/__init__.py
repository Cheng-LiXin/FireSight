"""frame_source: 统一帧输入源抽象层(可插拔 · 多模态)。

把任意输入源封装成同一套 `for frame in source` 接口。
支持摄像头 / 视频文件 / 图片文件夹 / RTSP 流。

使用示例(目标 API, S3 实装):
    from frame_source import create_frame_source
    with create_frame_source("0") as src:
        for frame in src:
            model(frame.image)
"""

from frame_source._version import __version__

__all__ = ["__version__"]
