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


def test_detection_color_override_actually_applies():
    """默认走配色表，显式 color 能盖过它。

    `Detection.color` 以前默认成 `(0, 255, 0)`，而那个元组恒为真——
    `det.color if det.color else self.color_for(...)` 于是永远走前半支，
    真正的问题是**配色表从来没生效过**：不传 color 的框一律画成绿色，
    明明 fire 在表里是橙红。默认改成 None，判空也改成 `is not None`
    （否则纯黑 (0,0,0) 这种合法覆盖色还是会被当成「没传」）。
    """
    from visualization import BeautifyVisualizer, Detection
    from visualization.visualizer import DEFAULT_COLORS

    img = np.zeros((40, 60, 3), dtype=np.uint8)
    vis = BeautifyVisualizer()
    box = dict(cls_id=0, name="fire", conf=0.9, x1=5, y1=5, x2=40, y2=30)

    default_det = Detection(**box)
    assert default_det.color is None, "不传 color 时必须是 None，否则配色表永远不生效"
    out_default = vis.draw(img, [default_det])
    fire_bgr = DEFAULT_COLORS["fire"]
    assert tuple(out_default[5, 20]) == fire_bgr, "不传 color 时应取 fire 的配色"
    assert (0, 255, 0) not in {tuple(p) for p in out_default.reshape(-1, 3)}, "画成了默认绿"

    # 显式传一个完全不同的颜色：必须盖过查表结果
    teal = (200, 200, 0)
    out_override = vis.draw(img, [Detection(**box, color=teal)])
    assert not np.array_equal(out_default, out_override), "显式 color 被默认值吃掉了"

    # 纯黑是合法覆盖色，不能用真值判断当「没传」放过去
    out_black = vis.draw(img, [Detection(**box, color=(0, 0, 0))])
    assert not np.array_equal(out_default, out_black), "纯黑覆盖色被当成「没传」"


def test_palette_has_no_duplicate_keys():
    """fire 曾被写了两次（第二份覆盖第一份），ruff F601 能查出来，这里也钉一下。"""
    from visualization.visualizer import DEFAULT_COLORS, DEFAULT_LABEL_MAP

    assert len(DEFAULT_COLORS) == 2
    assert len(DEFAULT_LABEL_MAP) == 2
    assert set(DEFAULT_COLORS) == {"fire", "smoke"}
    assert set(DEFAULT_LABEL_MAP) == {"fire", "smoke"}
