"""报告服务：事件统计摘要、Prompt 组装、Markdown 归档。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _seed(db: Path, rows) -> None:
    """rows: (event_id, labels, conf, location, ts, is_fp)。ts 由测试指定。"""
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    with EventStore(db) as store:
        for eid, labels, conf, loc, _ts, fp in rows:
            store.insert(
                AlertEvent(
                    event_id=eid,
                    frame_index=1,
                    labels=list(labels),
                    conf=conf,
                    location=loc,
                    message=f"检测到{labels[0]}，位置：{loc}",
                )
            )
            if fp:
                store.mark_false_positive(eid)
    con = sqlite3.connect(str(db))
    for eid, _l, _c, _loc, ts, _fp in rows:
        con.execute("UPDATE alerts SET ts=? WHERE event_id=?", (ts, eid))
    con.commit()
    con.close()


def test_aggregate_empty_range(tmp_path):
    from fire_sight.services.report_service import aggregate_events

    db = tmp_path / "e.db"
    _seed(db, [("a", ["fire"], 0.9, "东门", "2026-01-05T10:00:00", False)])

    s = aggregate_events(db, "2026-03-01", "2026-03-31")
    assert s["total"] == 0
    assert s["by_class"] == {"fire": 0, "smoke": 0}
    assert s["samples"] == []
    assert len(s["by_hour"]) == 24


def test_aggregate_counts_classes_locations_and_samples(tmp_path):
    from fire_sight.services.report_service import aggregate_events

    db = tmp_path / "e.db"
    _seed(
        db,
        [
            ("a", ["fire"], 0.91, "东门", "2026-03-05T14:10:00", False),
            ("b", ["fire", "smoke"], 0.80, "东门", "2026-03-06T14:20:00", False),
            ("c", ["smoke"], 0.70, "仓库", "2026-03-07T09:00:00", True),
            ("d", ["fire"], 0.60, "仓库", "2026-03-08T09:30:00", False),
        ],
    )
    s = aggregate_events(db, "2026-03-01", "2026-03-31")

    assert s["total"] == 4
    assert s["by_class"]["fire"] == 3
    assert s["by_class"]["smoke"] == 2
    assert s["by_hour"][14] == 2
    assert s["by_hour"][9] == 2
    assert s["by_location"][0] == {"location": "东门", "count": 2}
    assert s["false_positives"] == 1
    assert s["false_positive_rate"] == pytest.approx(0.25, abs=1e-3)
    assert s["avg_conf"] == pytest.approx(0.7525, abs=1e-3)
    # 每类取置信度最高的 3 条（本例 fire 恰好 3 条：a/b/d）
    fire_samples = [x for x in s["samples"] if x["label"] == "fire"]
    assert [x["event_id"] for x in fire_samples] == ["a", "b", "d"]


def test_build_prompt_has_fixed_sections_and_data():
    import json

    from fire_sight.services.report_service import build_prompt

    summary = {
        "range": {"start": "2026-03-01", "end": "2026-03-31"},
        "total": 4,
        "by_class": {"fire": 3, "smoke": 2},
        "by_hour": [0] * 24,
        "by_location": [{"location": "东门", "count": 2}],
        "false_positives": 1,
        "false_positive_rate": 0.25,
        "avg_conf": 0.75,
        "samples": [],
    }
    system, user = build_prompt(summary)

    for heading in (
        "## 火情态势",
        "## 时段与场所规律",
        "## 风险点",
        "## 防火建议",
        "## 处置优先级",
    ):
        assert heading in system
    assert "不得编造" in system or "不要编造" in system
    assert "2026-03-01" in user
    assert json.dumps(summary, ensure_ascii=False) in user


def test_archive_save_load_list(tmp_path):
    from fire_sight.services.report_service import list_reports, load_report, save_report

    meta = {
        "title": "火情分析与防火建议 · 2026-03-01 ~ 2026-03-31",
        "start": "2026-03-01",
        "end": "2026-03-31",
        "model": "deepseek-chat",
        "created_at": "2026-09-29T10:00:00",
    }
    path = save_report("20260929-100000-abc123", meta, "# 报告正文", directory=tmp_path)

    assert path.suffix == ".md"
    assert path.with_suffix(".json").exists()
    assert path.read_text(encoding="utf-8") == "# 报告正文"

    loaded = load_report("20260929-100000-abc123", directory=tmp_path)
    assert loaded["markdown"] == "# 报告正文"
    assert loaded["meta"]["model"] == "deepseek-chat"

    items = list_reports(directory=tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == "20260929-100000-abc123"
    assert items[0]["title"].startswith("火情分析")
