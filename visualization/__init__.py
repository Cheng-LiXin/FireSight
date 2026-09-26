"""visualization — 检测结果美化绘制。"""

from visualization._version import __version__
from visualization.core.types import Detection
from visualization.visualizer import DEFAULT_COLORS, DEFAULT_LABEL_MAP, BeautifyVisualizer

__all__ = [
    "__version__",
    "Detection",
    "BeautifyVisualizer",
    "DEFAULT_COLORS",
    "DEFAULT_LABEL_MAP",
]
