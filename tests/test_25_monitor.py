"""RTSP 常驻监控：启停生命周期、GPU 互斥、断线重连、MJPEG 出帧。"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def monitor():
    """每个用例一个干净的监控管理器（走生产同一个单例，推流端点读的就是它）。"""
    from fire_sight.api import monitor_job

    mgr = monitor_job.MONITOR
    mgr.stop()
    mgr.wait(timeout=5)
    yield mgr
    mgr.stop()
    mgr.wait(timeout=5)


@pytest.fixture()
def fake_pipeline(monkeypatch):
    """把推理流水线换成可控的假实现：

    真跑 YOLO 会让这组用例既慢又依赖权重，而这里要验的是**管理器**的状态机、
    锁与重连，不是模型本身。假流水线仍走同一个接口（frame_sink / stop_event）。
    """
    import numpy as np

    state = {"calls": 0, "frames": 0, "released": threading.Event()}

    def _fake_run_inference(cfg, frame_sink=None, stop_event=None, **kwargs):
        state["calls"] += 1
        if frame_sink is not None:
            frame_sink(np.zeros((8, 8, 3), dtype=np.uint8))
            state["frames"] += 1
        if state["calls"] == 1 and cfg.source.startswith("rtsp://"):
            # 第一次连上就「断线」：模拟 RTSP 读流结束
            return _stats(alerts=1)
        # 之后一直跑到收到停止信号
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.01)
        return _stats(alerts=1)

    def _stats(alerts=0):
        from fire_sight_platform.inference.service import InferStats

        s = InferStats()
        s.alerts = alerts
        return s

    import fire_sight.api.monitor_job as mj

    monkeypatch.setattr(mj, "run_inference", _fake_run_inference)

    # 权重解析也一并打桩：这组用例验管理器，不该被「本机有没有归档」卡住
    class _FakeRun:
        run_id = "stub-run"
        best_pt = Path("stub.pt")

    monkeypatch.setattr(mj.deps, "resolve_run", lambda name=None: _FakeRun())
    return state


def test_monitor_lifecycle_and_frame_sink(monitor, fake_pipeline):
    monitor.start(source="dir://frames", camera_name="东门", device="cpu")
    assert monitor.status()["status"] in {"connecting", "running"}
    assert monitor.wait_until(lambda s: s["frames"] >= 1, timeout=5)

    monitor.stop()
    st = monitor.status()
    assert monitor.wait_until(lambda s: s["status"] == "stopped", timeout=5)
    assert st["camera_name"] == "东门"
    # 告警数要从轮次统计里回填：一直显示 0 会让人以为监控没在做事
    assert monitor.wait_until(lambda s: s["alerts"] == 1, timeout=5)


def test_monitor_stop_is_not_undone_by_late_frames(monitor, fake_pipeline):
    """停止后还有帧到达时，状态不能被改回「监控中」——那看起来像停止没生效。"""
    monitor.start(source="dir://frames", device="cpu")
    monitor.wait_until(lambda s: s["frames"] >= 1, timeout=5)

    assert monitor.stop() is True
    assert monitor.status()["status"] == "stopping"
    assert monitor.wait_until(lambda s: s["status"] == "stopped", timeout=5)


def test_monitor_releases_gpu_lock(monitor, fake_pipeline):
    from fire_sight.api import deps

    monitor.start(source="dir://frames", device="cpu")
    assert deps.GPU_LOCK.holder in {"monitor", "infer"}
    monitor.stop()
    monitor.wait(timeout=5)
    assert deps.GPU_LOCK.holder is None


def test_monitor_start_refused_while_training(monitor, fake_pipeline):
    from fire_sight.api import deps

    assert deps.GPU_LOCK.acquire("train") is True
    try:
        with pytest.raises(deps.GpuBusy):
            monitor.start(source="dir://frames", device="cpu")
    finally:
        deps.GPU_LOCK.release()


def test_training_start_stops_monitor(monitor, fake_pipeline, monkeypatch):
    """训练优先：起训练时监控自动让路，而不是让两边抢到 OOM。"""
    import subprocess

    from fire_sight.api import deps
    from fire_sight.api.jobs import JobManager

    monitor.start(source="dir://frames", device="cpu")
    assert monitor.status()["status"] in {"connecting", "running"}

    class _FakeProc:
        # pid=None：让 _kill_tree 跳过 taskkill，别真去杀系统里的同号进程
        pid = None

        def __init__(self):
            # 真训练会一直跑；假进程也要驻留到被停，否则 reap 线程立刻放锁，
            # 「锁在 train 手上」就变成竞态断言。
            self._done = threading.Event()

        def poll(self):
            return 0 if self._done.is_set() else None

        def wait(self):
            self._done.wait(timeout=10)
            return 0

        def terminate(self):
            self._done.set()

        def kill(self):
            self._done.set()

        @property
        def stdout(self):
            return iter(())

        @property
        def stderr(self):
            return iter(())

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProc())

    mgr = JobManager()
    job = mgr.start({"dataset": "D-Fire/raw", "epochs": 1})

    assert monitor.wait_until(lambda s: s["status"] == "stopped", timeout=5)
    assert any("监控" in e.get("text", "") for e in job.events)
    assert deps.GPU_LOCK.holder == "train"

    # 收尾：停掉假训练，等 reap 线程把锁还回去，别把锁泄漏给下一个用例
    mgr.stop(job.id)
    deadline = time.time() + 5
    while deps.GPU_LOCK.holder is not None and time.time() < deadline:
        time.sleep(0.02)
    assert deps.GPU_LOCK.holder is None


def test_monitor_reconnects_after_disconnect(monitor, fake_pipeline):
    """RTSP 掉线不是错误，是常态：自动重连并计数，不静默死亡。"""
    monitor.start(source="rtsp://cam/1", device="cpu")
    assert monitor.wait_until(lambda s: s["reconnects"] >= 1, timeout=5)
    assert monitor.status()["status"] in {"connecting", "running"}
    monitor.stop()
    monitor.wait(timeout=5)


def test_monitor_local_source_failure_does_not_retry(monitor, monkeypatch):
    """本地源读不了是配置错，重试一万次也一样——直接 failed，别空转烧 CPU。"""
    import fire_sight.api.monitor_job as mj

    def _boom(cfg, frame_sink=None, stop_event=None, **kwargs):
        raise FileNotFoundError(f"视频不存在: {cfg.source}")

    class _FakeRun:
        run_id = "stub-run"
        best_pt = Path("stub.pt")

    monkeypatch.setattr(mj, "run_inference", _boom)
    monkeypatch.setattr(mj.deps, "resolve_run", lambda name=None: _FakeRun())

    monitor.start(source="missing.mp4", device="cpu")
    assert monitor.wait_until(lambda s: s["status"] == "failed", timeout=5)
    assert "不存在" in (monitor.status()["error"] or "")
    assert monitor.status()["reconnects"] == 0


def test_monitor_stream_frame_contract(monitor, fake_pipeline):
    """MJPEG 帧段契约：boundary + 类型 + 长度 + 载荷。

    不走 HTTP 客户端读无限流——传输层会等响应体收完，而 MJPEG 永不结束，
    测试会挂死。这里直接取生成器的第一段，契约一样能验。
    """
    from itertools import islice

    from fire_sight.api.routers.monitor import _mjpeg

    monitor.start(source="dir://frames", device="cpu")
    monitor.wait_until(lambda s: s["frames"] >= 1, timeout=5)

    parts = list(islice(_mjpeg(), 1))
    assert len(parts) == 1
    head = parts[0]
    assert head.startswith(b"--frame\r\n")
    assert b"Content-Type: image/jpeg" in head
    assert b"Content-Length:" in head

    monitor.stop()
    monitor.wait(timeout=5)


def test_monitor_routes_registered():
    pytest.importorskip("fastapi")
    from fire_sight.api.app import app

    # 走 OpenAPI 契约而不是 app.routes：新版 FastAPI 把 include_router 挂进来的东西
    # 包成没有 .path 的 _IncludedRouter，直接翻属性会漏掉整套路由。
    paths = set(app.openapi()["paths"])
    assert {
        "/api/monitor/start",
        "/api/monitor/stop",
        "/api/monitor/status",
        "/api/monitor/stream",
    } <= paths


def test_monitor_http_start_stop_and_409(monitor, fake_pipeline):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api import deps
    from fire_sight.api.app import app

    client = TestClient(app)

    body = client.post("/api/monitor/start", json={"source": "dir://frames"}).json()
    assert body["status"] in {"connecting", "running"}

    assert client.get("/api/monitor/status").json()["status"] in {"connecting", "running"}

    stopped = client.post("/api/monitor/stop").json()
    assert stopped["stopped"] is True
    monitor.wait(timeout=5)

    # 训练占卡时启动监控要 409，而不是排队等着 OOM
    assert deps.GPU_LOCK.acquire("train") is True
    try:
        resp = client.post("/api/monitor/start", json={"source": "dir://frames"})
        assert resp.status_code == 409
        assert "train" in resp.json()["detail"]
    finally:
        deps.GPU_LOCK.release()
