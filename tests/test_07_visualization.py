"""visualization 模块测试：中文标签与检测框绘制。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_draw_fire_smoke_boxes():
    from visualization import BeautifyVisualizer, Detection

    img = np.zeros((120, 160, 3), dtype=np.uint8)
    dets = [
        Detection(cls_id=0, name="fire", conf=0.9, x1=10, y1=10, x2=60, y2=50),
        Detection(cls_id=1, name="smoke", conf=0.7, x1=80, y1=40, x2=150, y2=100),
    ]
    vis = BeautifyVisualizer()
    out = vis.draw(img, dets)
    assert out.shape == img.shape
    assert out.sum() > 0  # 有绘制内容


def test_label_mapping_chinese():
    from visualization import BeautifyVisualizer

    vis = BeautifyVisualizer()
    assert vis.label_mapping["fire"] == "明火"
    assert vis.label_mapping["smoke"] == "烟雾"
    assert vis.color_for("fire") != vis.color_for("smoke")
