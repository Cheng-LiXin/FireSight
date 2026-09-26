"""类别映射约定测试（0=smoke, 1=fire）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def test_class_names_order_is_contract():
    from fire_sight.scene.class_map import CLASS_NAMES, class_to_id, names_dict

    assert CLASS_NAMES == ("smoke", "fire")
    assert class_to_id() == {"smoke": 0, "fire": 1}
    assert names_dict() == {0: "smoke", 1: "fire"}


def test_display_name_from_id():
    from fire_sight.scene.class_map import display_label, display_name

    assert display_name(0) == "smoke"
    assert display_name(1) == "fire"
    assert display_label(0, 0.8).startswith("烟雾")
    assert display_label(1, 0.8).startswith("明火")


def test_transform_default_classes_order():
    from fire_sight_platform.common.constants import DEFAULT_FIRE_SMOKE_CLASSES

    assert DEFAULT_FIRE_SMOKE_CLASSES == ("smoke", "fire")
