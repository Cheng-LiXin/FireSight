"""visualization —— YOLO 检测结果美化绘制。

提供圆角检测框、中英文标签、类别映射与配色。S3/S4 实装。

使用示例(目标 API):
    from visualization import BeautifyVisualizer
    vis = BeautifyVisualizer(
        labels=["flame", "smoke"],
        label_mapping={"flame": "明火", "smoke": "烟雾"},
    )
"""

from visualization._version import __version__

__all__ = ["__version__"]
