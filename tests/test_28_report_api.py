"""报告任务与 REST：生命周期、拒绝条件、归档回读。"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# 区间不写死月份：种子事件的 ts 是 EventStore.insert 打的 **now**，而过滤是日期前缀
# 比较——写死成某个月份（如 2026-09），过了月末种子事件就掉到区间外，整套测试到期作废。
_TODAY = date.today()
# 「有事件」区间：以今天为锚、前后各十年。既永远盖住 now，也让**生产库里任何一条
# 真实事件**都落进来——守卫测试靠「读生产库会数出非零」来证伪，窗口越宽越不会落空。
HAS_START = (_TODAY - timedelta(days=3650)).isoformat()
HAS_END = (_TODAY + timedelta(days=3650)).isoformat()
# 「无事件」区间：昨天。种子事件（今天）永远落在外面。
EMPTY_START = EMPTY_END = (_TODAY - timedelta(days=1)).isoformat()


@pytest.fixture()
def reports_dir(tmp_path, monkeypatch):
    import fire_sight.services.report_service as rs

    monkeypatch.setattr(rs, "REPORTS_DIR", tmp_path / "reports")
    return tmp_path / "reports"


@pytest.fixture()
def fake_llm(monkeypatch):
    """假模型：返回带五节的正文，不联网、不花钱；并记下收到的 user prompt。

    返回**单例**而不是类：守卫测试要拿它验证 prompt 里的统计数字，每次 chat 都
    新建实例的话，captured 就落不到测试手里。
    """
    import fire_sight.api.report_jobs as rj

    # 「通道已配置」这个前置条件也要在测试里成立，不能依赖跑测试的机器恰好配了
    # 真 key——顺带保证：万一假客户端没接上，请求也会因假 key 立刻失败，
    # 不会打到真实接口花掉真钱。
    monkeypatch.setenv("FIRESIGHT_LLM_API_KEY", "sk-test-not-real")

    class _Fake:
        def __init__(self) -> None:
            #: 最近一次收到的 user prompt（内含统计 JSON）
            self.user = ""

        def chat(self, system, user, *, max_tokens=4000):
            self.user = user
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

    client = _Fake()
    monkeypatch.setattr(rj, "resolve_client", lambda: client)
    return client


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

    job = report_jobs.REPORTS.start(HAS_START, HAS_END)
    # start() 直接置 running——不存在 queued 态，断言不给幽灵状态留后门
    assert job["status"] == "running"

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
    assert loaded["meta"]["start"] == HAS_START


def test_post_rejects_empty_range_and_missing_key(reports_dir, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)

    # 区间无事件 → 400，说清「没有告警记录」
    resp = client.post("/api/reports", json={"start": EMPTY_START, "end": EMPTY_END})
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
    resp = client.post("/api/reports", json={"start": HAS_START, "end": HAS_END})
    assert resp.status_code == 400
    assert "FIRESIGHT_LLM_API_KEY" in resp.json()["detail"]


def test_post_reads_events_db_at_call_time(tmp_path, monkeypatch):
    """路由必须在**调用时**读 report_jobs.EVENTS_DB，换成空库就该当场拒绝。

    若按值把路径拷进路由，这里读到的会是生产库 data/events/events.db——HAS 区间
    （前后各十年）保证生产库里任何一条真实事件都落进来，拒绝就永远测不到，测试
    也就落到了生产数据上。
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

    resp = TestClient(app).post("/api/reports", json={"start": HAS_START, "end": HAS_END})
    assert resp.status_code == 400
    assert "没有告警" in resp.json()["detail"]


def test_worker_aggregates_from_patched_events_db(reports_dir, fake_llm, tmp_path, monkeypatch):
    """后台线程也必须在**调用时**读 report_jobs.EVENTS_DB——空库就该数出 0 起。

    路由守卫只证明了 POST 那一侧；真正的聚合发生在 _run 的线程里（若线程按值/
    硬编码读生产库 data/events/events.db，前一条守卫照样全绿）。HAS 区间保证生产库
    里任何一条真实事件都会落进来，于是「读生产库」会让 fake 客户端收到的统计 JSON
    变成真实计数，下面这条断言失败。
    """
    from fire_sight.alerting import EventStore
    from fire_sight.api import report_jobs

    empty_db = tmp_path / "worker_empty.db"
    with EventStore(empty_db):
        pass
    monkeypatch.setattr(report_jobs, "EVENTS_DB", empty_db)

    job = report_jobs.REPORTS.start(HAS_START, HAS_END)
    deadline = time.time() + 10
    while time.time() < deadline:
        j = report_jobs.REPORTS.get(job["id"])
        if j["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    j = report_jobs.REPORTS.get(job["id"])
    assert j["status"] == "done", j.get("error")

    # fake 客户端收到的 user prompt 内嵌统计 JSON：空库 → total 必为 0
    assert '"total": 0' in fake_llm.user, fake_llm.user


def test_reports_rest_roundtrip(reports_dir, fake_llm, seeded_db, monkeypatch):
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)
    resp = client.post("/api/reports", json={"start": HAS_START, "end": HAS_END})
    # 显式断言响应形状：POST 失败时这里给出原因，而不是下游一个裸 KeyError
    assert resp.status_code == 201, resp.text
    created = resp.json()

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
    assert detail["meta"]["start"] == HAS_START

    dl = client.get(f"/api/reports/{rid}", params={"download": 1})
    assert dl.status_code == 200
    assert "text/markdown" in dl.headers["content-type"]


def test_get_report_rejects_traversal_and_dotted_ids(reports_dir, tmp_path):
    """坏 id 一律 404，且绝不把 reports 目录**之外**的内容吐出来。

    id 会被拼进文件路径和 Content-Disposition：Windows 上 `..%5C..%5C` 同样能穿出
    目录，点号别名（with_suffix）还能读到**别的**报告——所以白名单必须卡在接口这一层。
    """
    import json as _json

    from fire_sight.services.report_service import save_report

    meta = {"title": "真报告", "start": HAS_START, "end": HAS_END}
    save_report("20260929-100000-abc123", meta, "# 真报告", directory=reports_dir)
    # reports 目录的上一级放「秘密」：穿出去就该读到它
    (tmp_path / "secret.md").write_text("SECRET-OUTSIDE", encoding="utf-8")
    (tmp_path / "secret.json").write_text(_json.dumps(meta), encoding="utf-8")

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)
    for bad in ("..%5Csecret", "..%2Fsecret", "..%5C..%5Csecret", "20260929-100000-abc123.extra", "a b"):
        resp = client.get(f"/api/reports/{bad}")
        assert resp.status_code == 404, (bad, resp.text)
        assert "SECRET-OUTSIDE" not in resp.text
        assert "真报告" not in resp.text

    # 合法 id 照常
    ok = client.get("/api/reports/20260929-100000-abc123")
    assert ok.status_code == 200, ok.text
    assert ok.json()["markdown"] == "# 真报告"


def test_post_rejects_malformed_dates(reports_dir, seeded_db, monkeypatch):
    """日期不是 YYYY-MM-DD 就在 schema 层拒掉——不再落到「没有告警」这种误导文案。"""
    import fire_sight.api.report_jobs as report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    resp = TestClient(app).post("/api/reports", json={"start": "abcdefgh", "end": HAS_END})
    assert resp.status_code == 422, resp.text
    assert "start" in resp.text
    assert "没有告警" not in resp.text


def test_duplicate_generation_is_refused(reports_dir, seeded_db, monkeypatch):
    """同一区间连发两次是真花钱的重复调用——必须挡在接口层。

    界面的 busy 只挡双击，挡不住超时重试、脚本和 curl。
    """
    import threading

    from fire_sight.api import report_jobs

    monkeypatch.setattr(report_jobs, "EVENTS_DB", seeded_db)

    entered = threading.Event()
    release = threading.Event()

    class BlockingLLM:
        settings = type("S", (), {"model": "blocked"})()

        def chat(self, system, user, *, max_tokens=None):
            entered.set()
            release.wait(timeout=15)
            return "## 火情态势\n占位\n"

    monkeypatch.setattr(report_jobs, "resolve_client", lambda: BlockingLLM())

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app, raise_server_exceptions=False)
    try:
        first = client.post("/api/reports", json={"start": HAS_START, "end": HAS_END})
        assert first.status_code == 201, first.text
        assert entered.wait(timeout=5), "第一个任务没有进入生成阶段"

        dup = client.post("/api/reports", json={"start": HAS_START, "end": HAS_END})
        assert dup.status_code == 409, dup.text
        # 409 的 detail 要说清在等什么，而不是甩一个 id
        assert "进行" in str(dup.json().get("detail", ""))

        # 不同区间不受影响
        other = client.post(
            "/api/reports", json={"start": HAS_START, "end": _TODAY.isoformat()}
        )
        assert other.status_code == 201, other.text
        other_id = other.json()["id"]
    finally:
        release.set()

    # 等两个任务进终态，别把线程漏给下一个用例（每条按 id 查，不靠 list 接口）
    for job_id in (first.json()["id"], other_id):
        for _ in range(200):
            job = report_jobs.REPORTS.get(job_id)
            if job is None or job["status"] != "running":
                break
            time.sleep(0.02)


def test_job_registry_caps_itself(monkeypatch):
    """任务表只增不减是常驻服务里的内存漏斗——对齐 jobs.py 的 MAX_JOBS。"""
    import threading

    from fire_sight.api import report_jobs

    gate = threading.Event()

    def fake_run(self, job, start, end, report_id):
        # start=="BLOCK" 的那条停住，用来验证「运行中的任务绝不被裁掉」
        if start == "BLOCK":
            gate.wait(timeout=15)
        self._set(job["id"], status="done", stage="")

    monkeypatch.setattr(report_jobs.ReportJobManager, "_run", fake_run)

    mgr = report_jobs.ReportJobManager()
    terminal_ids = []
    held_id = None
    try:
        held_id = mgr.start("BLOCK", "BLOCK")["id"]
        for _ in range(report_jobs.MAX_JOBS + 3):
            job_id = mgr.start(EMPTY_START, EMPTY_START)["id"]
            for _ in range(200):
                job = mgr.get(job_id)
                if job and job["status"] == "done":
                    break
                time.sleep(0.005)
            assert (mgr.get(job_id) or {}).get("status") == "done", "任务没进终态，测试前提不成立"
            terminal_ids.append(job_id)
    finally:
        gate.set()

    live = [i for i in terminal_ids + [held_id] if mgr.get(i) is not None]
    assert len(live) <= report_jobs.MAX_JOBS, f"任务表涨到了 {len(live)} 条"
    assert mgr.get(held_id) is not None, "运行中的任务被裁掉了——它的回填就找不到落点了"
    assert mgr.get(terminal_ids[0]) is None, "最旧的已结束任务该被裁掉"
    assert mgr.get(terminal_ids[-1]) is not None, "最新一条还该在"
