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


# --------------------------------------------------------------------------
# R12：绘制热路径不能再按检测数整帧来回拷
# --------------------------------------------------------------------------


def _old_draw(vis, image, detections):
    """旧的逐条绘制路径：只用来对照，隔离出「文字攒到最后统一画」这一处变化。

    底色本身仍调现在的 `draw_label_bg`，否则会把 R12 的两处改动混在一起比。
    """
    from visualization.core.draw_utils import draw_label_bg, draw_rounded_box

    out = image.copy()
    for det in detections:
        color = det.color if det.color is not None else vis.color_for(det.name)
        x1, y1, x2, y2 = int(det.x1), int(det.y1), int(det.x2), int(det.y2)
        draw_rounded_box(out, x1, y1, x2, y2, color, thickness=2)
        text = f"{vis.label_mapping.get(det.name, det.name)} {det.conf:.2f}"
        tw = 8 + int(vis.text.font_size * 0.7 * len(text))
        th = vis.text.font_size + 10
        ty = max(0, y1 - th)
        draw_label_bg(out, x1, ty, min(x1 + tw, out.shape[1] - 1), ty + th, color)
        out = vis.text.draw(out, text, (x1 + 4, ty + 2), color=(255, 255, 255))
    return out


def test_draw_converts_color_space_once_per_frame(monkeypatch):
    """R12：整帧 BGR<->RGB 每帧只做一趟，与标签条数无关。

    旧写法在循环里调 `TextRenderer.draw()`，每条标签两次整帧拷贝；1080p 约
    17ms/条，20 个框 0.44 秒/帧。`visualizer.draw` 与 `TextRenderer.draw` 都走
    模块级的 `cv2_bgr2rgb`，所以数它的调用次数就能钉住这条路径：3 个框也必须是
    1 趟。
    """
    from visualization import BeautifyVisualizer, Detection
    from visualization.core import text_cache

    calls = []
    real = text_cache.cv2_bgr2rgb

    def counting(img):
        calls.append(img.shape)
        return real(img)

    monkeypatch.setattr(text_cache, "cv2_bgr2rgb", counting)

    img = np.zeros((200, 400, 3), dtype=np.uint8)
    vis = BeautifyVisualizer()
    dets = [
        Detection(cls_id=0, name="fire", conf=0.90, x1=10, y1=40, x2=90, y2=90),
        Detection(cls_id=1, name="smoke", conf=0.70, x1=120, y1=120, x2=200, y2=170),
        Detection(cls_id=0, name="fire", conf=0.50, x1=250, y1=40, x2=330, y2=90),
    ]

    out = vis.draw(img, dets)

    assert len(calls) == 1, f"3 个检测做了 {len(calls)} 趟整帧转换，应为一趟"
    assert calls[0] == img.shape
    assert out.shape == img.shape
    assert np.any(out != img), "标签没画上去"


def test_draw_many_with_no_labels_is_a_noop():
    """零检测时不该发生任何转换——实测这是 0.97ms 那一档的主要成本。"""
    from visualization.core import text_cache

    renderer = text_cache.TextRenderer()
    img = np.random.default_rng(0).integers(0, 256, (40, 60, 3), dtype=np.uint8)
    assert renderer.draw_many(img, []) is img


def test_draw_matches_per_label_path_when_labels_do_not_overlap():
    """文字攒到最后画，与逐条画在不重叠时逐像素一致。

    唯一的差别出在标签矩形互相重叠时：旧顺序是「底色→文字→下一框底色」，
    后画的底色会盖住先画好的文字；新顺序是「所有底色→所有文字」。不重叠的
    标签之间不存在这个层次差，必须完全一致。
    """
    from visualization import BeautifyVisualizer, Detection

    img = np.random.default_rng(11).integers(0, 256, (300, 400, 3), dtype=np.uint8)
    vis = BeautifyVisualizer()
    # 两个框相距很远，标签矩形不可能相交
    dets = [
        Detection(cls_id=0, name="fire", conf=0.9, x1=10, y1=40, x2=90, y2=90),
        Detection(cls_id=1, name="smoke", conf=0.8, x1=260, y1=200, x2=340, y2=260),
    ]

    assert np.array_equal(_old_draw(vis, img, dets), vis.draw(img, dets))


def test_label_bg_only_touches_the_label_rect():
    """R12：底色只混合标签矩形，不再整帧 copy。

    矩形是闭区间（`cv2.rectangle(...,-1)` 含 x2/y2），numpy 切片是半开区间，
    所以写回范围必须正好是 [x1, x2+1) x [y1, y2+1)。
    """
    from visualization.core.draw_utils import draw_label_bg

    img = np.zeros((60, 80, 3), dtype=np.uint8)
    draw_label_bg(img, 20, 10, 50, 30, (0, 0, 255))

    changed = np.any(img != 0, axis=2)
    ys, xs = np.nonzero(changed)
    assert (xs.min(), xs.max()) == (20, 50), f"水平范围 {xs.min()}..{xs.max()}"
    assert (ys.min(), ys.max()) == (10, 30), f"垂直范围 {ys.min()}..{ys.max()}"


def test_label_bg_blends_only_the_rect_not_the_whole_frame(monkeypatch):
    """R12 的核心：交给 `addWeighted` 的必须只是标签那一小块。

    旧写法是 `overlay = img.copy()` 再整帧混合，1080p 每帧要多一次整帧拷贝 +
    一次整帧加权的读写。矩形之外两者逐像素相同、结果也相同，所以只能从「喂进去
    的数组多大」来钉——直接看它拿到的 shape，比掐时间稳。
    """
    from visualization.core import draw_utils

    seen = []
    real = draw_utils.cv2.addWeighted

    def spy(src1, alpha, src2, beta, gamma, dst=None):
        seen.append(src1.shape)
        return real(src1, alpha, src2, beta, gamma, dst)

    monkeypatch.setattr(draw_utils.cv2, "addWeighted", spy)

    img = np.zeros((600, 800, 3), dtype=np.uint8)
    draw_utils.draw_label_bg(img, 20, 10, 50, 30, (0, 0, 255))

    assert len(seen) == 1, f"混合了 {len(seen)} 次"
    # 闭区间端点 → 高度 30-10+1=21、宽度 50-20+1=31
    assert seen[0][:2] == (21, 31), f"混了 {seen[0][:2]}，应只有标签矩形那一块"


def test_label_bg_clips_out_of_bounds_without_wrapping():
    """越界矩形要自己裁：cv2 会替我们裁，换成 numpy 切片后负索引会绕到另一侧。"""
    from visualization.core.draw_utils import draw_label_bg

    # 左边越界：负 x1 若直接拿去切片，会从右边开始涂
    img = np.zeros((60, 80, 3), dtype=np.uint8)
    draw_label_bg(img, -10, 5, 20, 25, (0, 0, 255))
    changed = np.any(img != 0, axis=2)
    ys, xs = np.nonzero(changed)
    assert (xs.min(), xs.max()) == (0, 20), f"水平范围 {xs.min()}..{xs.max()}"
    assert not changed[:, 60:].any(), "负坐标绕到了画布另一侧"

    # 完全在画布外：直接返回，一个像素都不该动
    img2 = np.zeros((60, 80, 3), dtype=np.uint8)
    draw_label_bg(img2, -50, 5, -20, 25, (0, 0, 255))
    assert not img2.any()

    # 右下越界：裁到边为止
    img3 = np.zeros((60, 80, 3), dtype=np.uint8)
    draw_label_bg(img3, 70, 50, 200, 200, (0, 0, 255))
    changed3 = np.any(img3 != 0, axis=2)
    ys3, xs3 = np.nonzero(changed3)
    assert (xs3.min(), xs3.max()) == (70, 79)
    assert (ys3.min(), ys3.max()) == (50, 59)
