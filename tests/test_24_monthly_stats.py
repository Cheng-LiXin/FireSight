"""月度火情统计：聚合桶、月度明细、环比、REST 契约。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _seed(db: Path, rows) -> None:
    """rows: (event_id, labels, conf, ts, is_false_positive)。

    ts 由 EventStore.insert 写成「现在」，月度统计要指定月份，
    所以这里回改 ts——测试自己造时间线，不给生产代码开后门。
    """
    from fire_sight.alerting import EventStore
    from fire_sight.scene.types import AlertEvent

    with EventStore(db) as store:
        for eid, labels, conf, ts, fp in rows:
            store.insert(
                AlertEvent(
                    event_id=eid,
                    frame_index=1,
                    labels=list(labels),
                    conf=conf,
                    location="东门",
                    message=f"检测到{labels[0]}",
                )
            )
            if fp:
                store.mark_false_positive(eid)
    con = sqlite3.connect(str(db))
    for eid, _, _, ts, _ in rows:
        con.execute("UPDATE alerts SET ts=? WHERE event_id=?", (ts, eid))
    con.commit()
    con.close()


def test_monthly_buckets_fill_empty_months(tmp_path):
    """时间序列必须连续：空月份补 0 柱，而不是缺一根让坐标轴把空窗画成正常间隔。"""
    from datetime import date

    from fire_sight.services import StatsService

    db = tmp_path / "events.db"
    _seed(db, [])
    buckets = StatsService(db).monthly_alerts(months=12)

    assert len(buckets) == 12
    assert buckets[0]["month"] < buckets[-1]["month"]
    assert buckets[-1]["month"] == date.today().strftime("%Y-%m")
    assert all(b["count"] == 0 for b in buckets)
    assert [b["month"] for b in buckets] == sorted(b["month"] for b in buckets)


def test_month_detail_counts_classes_fp_and_hourly(tmp_path):
    from fire_sight.services import StatsService

    db = tmp_path / "events.db"
    _seed(
        db,
        [
            ("m1", ["fire"], 0.9, "2026-03-05T14:23:11", False),
            ("m2", ["fire", "smoke"], 0.5, "2026-03-06T14:50:00", True),
            ("m3", ["smoke"], 0.7, "2026-03-07T09:10:00", False),
        ],
    )
    d = StatsService(db).month_detail("2026-03")

    assert d["month"] == "2026-03"
    assert d["total"] == 3
    assert d["fire"] == 2  # m1、m2（m2 是 fire,smoke 混合，两类各计一次）
    assert d["smoke"] == 2  # m2、m3
    assert d["false_positives"] == 1
    assert d["pending"] == 2
    assert d["false_positive_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert d["avg_conf"] == pytest.approx(0.7, abs=1e-3)
    assert len(d["hourly"]) == 24
    assert d["hourly"][14] == 2
    assert d["hourly"][9] == 1
    assert sum(d["hourly"]) == 3


def test_month_over_month_ratio(tmp_path):
    from fire_sight.services import StatsService

    db = tmp_path / "events.db"
    _seed(
        db,
        [
            ("a1", ["fire"], 0.8, "2026-01-10T10:00:00", False),
            ("a2", ["fire"], 0.8, "2026-01-11T10:00:00", False),
            ("b1", ["fire"], 0.8, "2026-02-10T10:00:00", False),
            ("b2", ["fire"], 0.8, "2026-02-11T10:00:00", False),
            ("b3", ["fire"], 0.8, "2026-02-12T10:00:00", False),
            ("b4", ["smoke"], 0.8, "2026-02-13T10:00:00", False),
        ],
    )
    svc = StatsService(db)

    assert svc.month_detail("2026-02")["mom_pct"] == pytest.approx(100.0, abs=0.1)
    # 上月是 0 条：环比「涨了多少」没有意义，给 None 而不是 100 或 inf
    assert svc.month_detail("2026-01")["mom_pct"] is None


def test_api_monthly_contract(monkeypatch, tmp_path):
    from fire_sight.api import deps

    db = tmp_path / "events.db"
    _seed(
        db,
        [("x1", ["fire"], 0.8, "2026-03-10T10:00:00", False)],
    )
    monkeypatch.setattr(deps, "EVENTS_DB", db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    body = TestClient(app).get("/api/stats/monthly", params={"months": 12}).json()

    assert len(body["months"]) == 12
    assert body["detail"]["month"] == body["months"][-1]["month"]
    assert {"month", "count", "fire", "smoke", "false_positives"} <= set(body["months"][0])
    assert {"total", "hourly", "mom_pct", "false_positive_rate"} <= set(body["detail"])

    picked = TestClient(app).get(
        "/api/stats/monthly", params={"month": "2026-03"}
    ).json()
    assert picked["detail"]["month"] == "2026-03"
    assert picked["detail"]["total"] == 1


def test_api_monthly_rejects_bad_inputs(tmp_path, monkeypatch):
    """month/months 是公开查询参数：非法值必须 4xx，不能 500 也不能无界分配。"""
    from fire_sight.api import deps

    db = tmp_path / "events.db"
    _seed(db, [])
    monkeypatch.setattr(deps, "EVENTS_DB", db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    # raise_server_exceptions=False：坏值若走成 500，这里拿到的是 500 而不是异常，
    # 断言才有机会把它判成红
    client = TestClient(app, raise_server_exceptions=False)

    # 日期形状对、但月份不存在 → 现在会变成 500（strptime 抛 ValueError）
    for bad in ("garbage", "2026-13", "2026-09-99", "09/2026"):
        resp = client.get("/api/stats/monthly", params={"month": bad})
        assert resp.status_code in (400, 422), f"month={bad} → {resp.status_code}"
        assert "month" in str(resp.json().get("detail", "")).lower()

    # 巨大的 months 会走进 range(max(1, n)) 无上界循环
    resp = client.get("/api/stats/monthly", params={"months": 10**6})
    assert resp.status_code == 422
