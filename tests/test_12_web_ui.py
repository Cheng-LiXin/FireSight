"""S4-2 web UI 纯函数测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def test_kpi_items_validate():
    from fire_sight.web.ui.kpi import kpi_items

    items = kpi_items([("告警", "3"), ("误报率", "10%")])
    assert items[0][0] == "告警"
    with pytest.raises(ValueError):
        kpi_items([("bad",)])


def test_charts_build_figures():
    from fire_sight.web.ui.charts import class_ratio_figure, daily_alert_figure

    fig = daily_alert_figure(
        [{"date": "2026-09-26", "count": 2, "avg_conf": 0.8}]
    )
    assert fig is not None
    pie = class_ratio_figure({"fire": 1, "smoke": 3})
    assert pie is not None


def test_theme_constants():
    from fire_sight.web.ui.theme import ACCENT, PAGE_TITLE

    assert "FireSight" in PAGE_TITLE
    assert ACCENT.startswith("#")
