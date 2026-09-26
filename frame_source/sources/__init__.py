"""内置帧源: 导入即注册。"""

from frame_source.sources import image_dir, video, camera  # noqa: F401

__all__ = ["image_dir", "video", "camera"]
