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


def test_gpu_lock_release_checks_ownership():
    """只有真正拿着锁的人放得开。

    收尾线程是靠「状态」判断自己该收工的，状态可见与锁归还之间总有一小段窗口；
    release() 若不认持有者，迟到的收尾就可能把别人刚拿到的锁一并放掉——
    GPU 互斥一旦放错就是静默失效。
    """
    from fire_sight.api.deps import GpuLock

    lock = GpuLock()
    assert lock.acquire("monitor") is True

    # 报错的人放不开别人的锁
    lock.release("train")
    assert lock.status()["holder"] == "monitor"
    assert lock.acquire("train") is False

    # 本人放得开
    lock.release("monitor")
    assert lock.status()["holder"] is None

    # 不传 who 保持原语义（既有调用点都在确认自己持有之后才调）
    assert lock.acquire("train") is True
    lock.release()
    assert lock.status()["holder"] is None


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


def test_spa_deep_link_falls_back_to_index(client):
    """回归：前端用 HTML5 history 模式，未命中的路径必须交回 index.html。

    曾经把 StaticFiles(html=True) 直接挂在根路径——它只服务真实存在的文件，
    访问 /detect 会得到 404，于是所有子页面的深链接与刷新全是白屏。
    """
    from fire_sight.api.app import WEB_DIST

    if not (WEB_DIST / "index.html").exists():
        pytest.skip("前端未构建")

    r = client.get("/detect")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_unknown_api_path_stays_json_404(client):
    """兜底路由不能把未匹配的接口路径也吞成 HTML。"""
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["detail"] == "Not Found"


def test_spa_does_not_escape_dist(tmp_path, monkeypatch):
    """回归：兜底路由必须把候选路径夹在 dist/ 内。

    dist 位于仓库根下三层，早先直接 `WEB_DIST / full_path` 而未归一化，
    于是 `/../../../AGENTS.md` 能读到工作区里的任意文件（用真实 uvicorn
    验证过：200 text/markdown）。这里自建一份 dist，不依赖前端是否构建过。
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api import app as app_mod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>index", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")

    monkeypatch.setattr(app_mod, "WEB_DIST", dist)
    probe = TestClient(app_mod.create_app())

    # 深链接必须仍然交回 index.html（历史的真实 bug）
    ok = probe.get("/detect")
    assert ok.status_code == 200
    assert "text/html" in ok.headers["content-type"]

    # 各种逃逸写法都不许把 dist 外的文件吐出来
    for path in (
        "/../secret.txt",
        "/../../secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/%2e%2e%2fsecret.txt",
        "/assets/../../secret.txt",
    ):
        r = probe.get(path)
        assert "TOP-SECRET" not in r.text, f"{path} 泄漏了 dist 外的文件"


def test_spa_traversal_blocked_via_raw_asgi(tmp_path, monkeypatch):
    """同上，但绕过客户端对 `..` 的归一化，直接驱动 ASGI 应用。"""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient as RawTestClient

    from fire_sight.api import app as app_mod

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>index", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("TOP-SECRET", encoding="utf-8")

    monkeypatch.setattr(app_mod, "WEB_DIST", dist)
    asgi = app_mod.create_app()

    # httpx 会在客户端就把 `..` 归一化掉，所以这里手写 scope
    def raw_get(target: str) -> bytes:
        import anyio

        captured: dict = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body":
                captured.setdefault("body", b"")
                captured["body"] += message.get("body", b"")

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": target,
            "raw_path": target.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        anyio.run(asgi, scope, receive, send)
        return captured.get("body", b"")

    for target in (
        "/../secret.txt",
        "/../../secret.txt",
        "/../../../secret.txt",
        "/..%2f..%2fsecret.txt",
    ):
        body = raw_get(target)
        assert b"TOP-SECRET" not in body, f"{target} 泄漏了 dist 外的文件"
        # 未命中就交回 index.html，而不是 500 / 空
        assert b"<!doctype html>" in body



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


def test_overview_rejects_out_of_range_days(client, seeded_db, monkeypatch):
    """回归 R2：`days` 以前没有任何上界。

    路由里的补点循环是 `range(days - 1, -1, -1)`，一个 `days=100000000`
    直接抛未捕获的 `OverflowError: date value out of range` 变成 500；
    负值/0 则静默返回空序列。现在由 Query 校验统一给出 422。
    """
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "EVENTS_DB", seeded_db)

    for bad in (0, -5, 100000000):
        assert client.get(f"/api/overview?days={bad}").status_code == 422, bad

    # 合法上界仍然可用
    body = client.get("/api/overview?days=365").json()
    assert len(body["daily"]) == 365


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
