"""几何换算:绝对坐标 → YOLO 归一化。内部工具,不注册。"""

from typing import List, Sequence, Tuple


def normalize_box(
    x1: float, y1: float, x2: float, y2: float, width: float, height: float
) -> Tuple[float, float, float, float]:
    """绝对像素框 → (cx, cy, w, h) 归一化,并 clip 到 [0, 1]。"""
    if width <= 0 or height <= 0:
        raise ValueError(f"非法图像尺寸: {width}x{height}")
    x_min, x_max = min(x1, x2), max(x1, x2)
    y_min, y_max = min(y1, y2), max(y1, y2)
    cx = ((x_min + x_max) / 2.0) / width
    cy = ((y_min + y_max) / 2.0) / height
    w = (x_max - x_min) / width
    h = (y_max - y_min) / height
    return (
        min(max(cx, 0.0), 1.0),
        min(max(cy, 0.0), 1.0),
        min(max(w, 0.0), 1.0),
        min(max(h, 0.0), 1.0),
    )


def format_yolo_line(class_id: int, box: Sequence[float], conf: float | None = None) -> str:
    cx, cy, w, h = box
    base = f"{int(class_id)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
    return base if conf is None else f"{base} {conf:.6f}"


def is_degenerate(w: float, h: float, min_size: float = 1e-6) -> bool:
    """宽/高非正或过小视为退化框,应在转换时剔除。"""
    return w <= min_size or h <= min_size
