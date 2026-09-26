"""REST 层模块测试：GPU 锁、系统状态、仪表盘聚合。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


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
                conf=0.42,
                location="东门",
                message="检测到烟雾",
            )
        )
        store.insert(
            AlertEvent(
                event_id="e2",
                frame_index=2,
                labels=["fire"],
                conf=0.91,
                location="仓库",
                message="检测到明火",
            )
        )
        store.mark_false_positive("e1")
    return db


# --------------------------------------------------------------------------
# GPU 互斥锁：纯逻辑，不依赖 fastapi
# --------------------------------------------------------------------------


def test_gpu_lock_rejects_when_held():
    from fire_sight.api.deps import GpuLock

    lock = GpuLock()
    assert lock.acquire("train") is True
    assert lock.status()["holder"] == "train"

    # 非阻塞获取：拿不到直接 False，不排队
    assert lock.acquire("infer") is False
    assert lock.status()["waiting"] == []

    lock.release()
    assert lock.acquire("infer") is True
    lock.release()
    assert lock.status() == {"holder": None, "waiting": [], "busy": False}


# --------------------------------------------------------------------------
# HTTP 契约
# --------------------------------------------------------------------------


def test_archived_runs_handles_both_layouts(tmp_path, monkeypatch):
    """归档有两种历史布局，只认一种会让最新一轮整轮消失。

    实际踩到过：最新 50ep 那轮落在 trained/<run_id>/<experiment>/best.pt，
    而 trained/*/best.pt 匹配不到，仪表盘于是显示了更旧的一轮。
    """
    from fire_sight.api import deps

    trained = tmp_path / "trained"
    flat = trained / "20260101-000000-flat-yolo11n"
    nested = trained / "20260102-000000-nested" / "train-yolo11n"
    for d in (flat, nested):
        d.mkdir(parents=True)
        (d / "best.pt").write_bytes(b"stub")
        (d / "metrics.txt").write_text("mAP50=0.5\n", encoding="utf-8")

    monkeypatch.setattr(deps, "TRAINED_MODELS_DIR", trained)

    assert [r.run_id for r in deps.archived_runs()] == [
        "20260102-000000-nested/train-yolo11n",
        "20260101-000000-flat-yolo11n",
    ]
    assert deps.active_run().run_id == "20260102-000000-nested/train-yolo11n"


def test_archived_runs_empty_when_dir_missing(tmp_path, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "TRAINED_MODELS_DIR", tmp_path / "nope")
    assert deps.archived_runs() == []
    assert deps.active_run() is None


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "spa_built" in body


def test_system_status_shape(client):
    body = client.get("/api/system/status").json()

    assert "workspace" in body
    assert body["python_version"].startswith("3.")
    # GPU 可能不存在，但字段必须在，前端据此显示空态
    assert "available" in body["gpu"]
    assert set(body["gpu_job"]) == {"holder", "waiting", "busy"}
    assert body["trained_model_count"] >= 0

    # 类别契约：0=smoke / 1=fire
    classes = {c["id"]: c for c in body["classes"]}
    assert classes[0]["name"] == "smoke"
    assert classes[0]["name_cn"] == "烟雾"
    assert classes[1]["name"] == "fire"
    assert classes[1]["name_cn"] == "明火"


def test_overview_empty_state(client, tmp_path, monkeypatch):
    """事件库不存在时返回零值而非报错——首次部署就是这个状态。"""
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "EVENTS_DB", tmp_path / "nope.db")

    body = client.get("/api/overview").json()
    assert body["alerts"]["total"] == 0
    assert body["risk"]["score"] == 0.0
    assert body["risk"]["level"] == "normal"
    assert body["class_ratio"] == {"smoke": 0, "fire": 0}
    assert body["model"]["exists"] in (True, False)

    # 序列仍保持连续（30 天全 0），但「有没有数据」由 total 判定——
    # 连续性属于序列本身，空态属于总量，两者不能混用一个信号。
    assert len(body["daily"]) == 30
    assert all(d["count"] == 0 for d in body["daily"])


def test_overview_with_events(client, seeded_db, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "EVENTS_DB", seeded_db)

    body = client.get("/api/overview").json()
    assert body["alerts"]["total"] == 2
    assert body["alerts"]["pending"] == 1  # e1 已标误报
    assert body["alerts"]["false_positive_rate"] == pytest.approx(0.5, abs=1e-6)
    assert body["class_ratio"]["smoke"] == 1
    assert body["class_ratio"]["fire"] == 1
    assert body["risk"]["alerts_24h"] == 2
    assert body["risk"]["peak_conf_24h"] == pytest.approx(0.91, abs=1e-6)
    # 峰值 0.91 > 量 2/10，游标应由峰值推高
    assert body["risk"]["score"] == pytest.approx(0.91, abs=1e-6)
    assert body["risk"]["level"] == "alert"

    # 每日序列必须连续且末端为今天：只回有数据的日子会让坐标轴说谎
    daily = body["daily"]
    assert len(daily) == 30
    today = daily[-1]
    assert today["count"] == 2
    assert today["smoke"] == 1
    assert today["fire"] == 1
    assert today["avg_conf"] == pytest.approx((0.42 + 0.91) / 2, abs=1e-4)
    # 更早的日子没有事件，必须是 0 而不是缺项
    assert daily[0]["count"] == 0
    assert daily[0]["avg_conf"] == 0.0


def test_daily_series_is_continuous(client, seeded_db, monkeypatch):
    """回归：缺日补 0。曾因只回有数据的日子，X 轴上出现假间隔。"""
    from datetime import datetime, timedelta

    from fire_sight.api import deps

    monkeypatch.setattr(deps, "EVENTS_DB", seeded_db)
    daily = client.get("/api/overview?days=7").json()["daily"]

    assert len(daily) == 7
    dates = [datetime.fromisoformat(d["date"]).date() for d in daily]
    assert dates == sorted(dates)
    for prev, cur in zip(dates, dates[1:]):
        assert (cur - prev) == timedelta(days=1), f"{prev} -> {cur} 有断档"
    assert dates[-1] == datetime.now().date()


def test_risk_levels_are_monotonic():
    """风险分档必须单调：置信度越高，档位不得倒退。"""
    from fire_sight.api.routers.overview import _risk_from

    def rows(conf: float):
        from datetime import datetime

        return [{"ts": datetime.now().isoformat(), "conf": conf}]

    levels = [_risk_from(rows(c)).level for c in (0.0, 0.2, 0.5, 0.7, 0.95)]
    order = {"normal": 0, "watch": 1, "warn": 2, "alert": 3}
    ranks = [order[lv] for lv in levels]
    assert ranks == sorted(ranks), levels
