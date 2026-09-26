"""YOLO 标签行解析:全框架唯一权威。"""

from __future__ import annotations

from typing import Optional, Tuple


def parse_label_line(line: str) -> Optional[Tuple[int, float, float, float, float]]:
    """解析 `class cx cy w h`,失败返回 None。"""
    parts = line.split()
    if len(parts) < 5:
        return None
    try:
        cid = int(float(parts[0]))
        cx, cy, w, h = (float(x) for x in parts[1:5])
    except ValueError:
        return None
    return cid, cx, cy, w, h
