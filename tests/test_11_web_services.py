"""数据服务层模块测试（EventService / StatsService / InferService）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def seeded_db(tmp_path):
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    db = tmp_path / "events.db"
    with EventStore(db) as store:
        store.insert(
            AlertEvent(
                event_id="e1",
                frame_index=1,
                labels=["smoke"],
                conf=0.8,
                location="东门",
                message="检测到烟雾",
            ),
            clip_path="a.mp4",
        )
        store.insert(
            AlertEvent(
                event_id="e2",
                frame_index=2,
                labels=["fire"],
                conf=0.9,
                location="仓库",
                message="检测到明火",
            ),
            clip_path="b.mp4",
        )
        store.mark_false_positive("e2")
    return db


def test_event_service_list_filter_export(seeded_db, tmp_path):
    from fire_sight.services import EventService

    svc = EventService(seeded_db)
    assert svc.count() == 2
    assert len(svc.list_events()) == 2
    assert len(svc.list_events(label="smoke")) == 1
    assert len(svc.list_events(keyword="东门")) == 1
    out = svc.export_csv(tmp_path / "e.csv")
    text = out.read_text(encoding="utf-8-sig")
    assert "e1" in text and "e2" in text


def test_stats_service_aggregations(seeded_db):
    from fire_sight.alerting import EventStore
    from fire_sight.services import StatsService

    stats = StatsService(seeded_db)
    ratio = stats.class_ratio()
    assert ratio["smoke"] >= 1 and ratio["fire"] >= 1
    fp = stats.false_positive_rate()
    assert 0 < fp <= 1
    daily = stats.daily_alerts(days=1)
    assert isinstance(daily, list)
    with EventStore(seeded_db) as store:
        assert store.count() == 2


def test_infer_service_batch(tmp_path):
    import cv2

    from fire_sight.services import InferService

    weights = ROOT / "yolo11n.pt"
    if not weights.exists():
        cands = sorted((ROOT / "models" / "trained").glob("*/best.pt"))
        if not cands:
            pytest.skip("no weights")
        weights = cands[-1]

    d = tmp_path / "imgs"
    d.mkdir()
    for i in range(2):
        cv2.imwrite(str(d / f"{i}.png"), np.zeros((64, 64, 3), dtype=np.uint8))

    svc = InferService(weights=str(weights), device="cpu")
    results = svc.infer_batch(d)
    assert len(results) == 2
    assert "elapsed_ms" in results[0]
    assert "detections" in results[0]
