"""报告任务与 REST：生命周期、拒绝条件、归档回读。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    import fire_sight.services.report_service as rs

    monkeypatch.setattr(rs, "REPORTS_DIR", tmp_path / "reports")
    return tmp_path / "reports"


@pytest.fixture()
def fake_llm(monkeypatch):
    """假模型：返回带五节的正文，不联网、不花钱。"""
    import fire_sight.api.report_jobs as rj

    # 「通道已配置」这个前置条件也要在测试里成立，不能依赖跑测试的机器恰好配了
    # 真 key——顺带保证：万一假客户端没接上，请求也会因假 key 立刻失败，
    # 不会打到真实接口花掉真钱。
    monkeypatch.setenv("FIRESIGHT_LLM_API_KEY", "sk-test-not-real")

    class _Fake:
        def chat(self, system, user, *, max_tokens=4000):
            return "\n".join(
                [
                    "## 火情态势",
                    "本月告警 4 起。",
                    "## 时段与场所规律",
                    "高发于 14 时。",
                    "## 风险点",
                    "仓库堆放可燃物。",
                    "## 防火建议",
                    "- 东门：每日巡查。",
                    "## 处置优先级",
                    "高：仓库。",
                ]
            )

    monkeypatch.setattr(rj, "resolve_client", lambda: _Fake())
    return _Fake


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
                labels=["fire"],
                conf=0.9,
                location="东门",
                message="检测到明火，位置：东门",
            )
        )
    return db


def test_report_job_lifecycle(reports_dir, fake_llm, seeded_db, monkeypatch):
    from fire_sight.api import report_jobs
    from fire_sight.services import report_service

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    job = report_jobs.REPORTS.start("2026-09-01", "2026-09-30")
    assert job["status"] in {"queued", "running"}

    deadline = time.time() + 10
    while time.time() < deadline:
        j = report_jobs.REPORTS.get(job["id"])
        if j["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    j = report_jobs.REPORTS.get(job["id"])
    assert j["status"] == "done", j.get("error")
    assert j["report_id"]

    loaded = report_service.load_report(j["report_id"], directory=reports_dir)
    assert "## 火情态势" in loaded["markdown"]
    assert loaded["meta"]["start"] == "2026-09-01"


def test_post_rejects_empty_range_and_missing_key(reports_dir, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)

    # 区间无事件 → 400，说清「没有告警记录」
    resp = client.post("/api/reports", json={"start": "2020-01-01", "end": "2020-01-31"})
    assert resp.status_code == 400
    assert "没有告警" in resp.json()["detail"]

    # 未配置模型通道 → 400，附配置说明
    for name in (
        "FIRESIGHT_LLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "SILICONFLOW_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)
    resp = client.post("/api/reports", json={"start": "2026-09-01", "end": "2026-09-30"})
    assert resp.status_code == 400
    assert "FIRESIGHT_LLM_API_KEY" in resp.json()["detail"]


def test_post_reads_events_db_at_call_time(tmp_path, monkeypatch):
    """路由必须在**调用时**读 report_jobs.EVENTS_DB，换成空库就该当场拒绝。

    若按值把路径拷进路由，这里读到的会是生产库 data/events/events.db——那个区间
    在开发机上有事件，「没有告警」这条拒绝就永远测不到，测试也就落到了生产数据上。
    """
    import fire_sight.api.report_jobs as report_jobs
    from fire_sight.alerting import EventStore

    empty_db = tmp_path / "empty.db"
    with EventStore(empty_db):
        pass
    monkeypatch.setattr(report_jobs, "EVENTS_DB", empty_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    resp = TestClient(app).post("/api/reports", json={"start": "2026-09-01", "end": "2026-09-30"})
    assert resp.status_code == 400
    assert "没有告警" in resp.json()["detail"]


def test_reports_rest_roundtrip(reports_dir, fake_llm, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)
    created = client.post("/api/reports", json={"start": "2026-09-01", "end": "2026-09-30"}).json()

    deadline = time.time() + 10
    while time.time() < deadline:
        info = client.get(f"/api/reports/jobs/{created['id']}").json()
        if info["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    info = client.get(f"/api/reports/jobs/{created['id']}").json()
    assert info["status"] == "done", info.get("error")

    listing = client.get("/api/reports").json()
    assert len(listing) == 1
    rid = listing[0]["id"]

    detail = client.get(f"/api/reports/{rid}").json()
    assert "## 火情态势" in detail["markdown"]
    assert detail["meta"]["start"] == "2026-09-01"

    dl = client.get(f"/api/reports/{rid}", params={"download": 1})
    assert dl.status_code == 200
    assert "text/markdown" in dl.headers["content-type"]
