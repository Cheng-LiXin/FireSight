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
    """每个用例一个干净的监控管理器（走生产同一个单例，推流端点读的就是它）。

    收尾必须把 GPU 锁也等回来：上一个用例要是留下了占锁状态，后面每个
    start() 都会撞 GpuBusy，看起来像新改坏了，其实只是脏了。
    """
    from fire_sight.api import deps, monitor_job

    mgr = monitor_job.MONITOR
    mgr.stop()
    mgr.wait(timeout=5)
    deadline = time.time() + 5
    while deps.GPU_LOCK.holder == "monitor" and time.time() < deadline:
        time.sleep(0.02)
    if deps.GPU_LOCK.holder == "monitor":
        deps.GPU_LOCK.release("monitor")
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


def test_monitor_stop_returns_settled_state(monitor, fake_pipeline):
    """/monitor/stop 应直接给出终态，而不是把「停止中」丢给前端。

    用户报的第二个缺陷就是「停止中滞留很久」：接口只置位信号就返回，前端紧接着
    刷新拿到的必然是 stopping。现在接口在返回前等到收尾完成。
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    client = TestClient(app)
    client.post("/api/monitor/start", json={"source": "dir://frames"})
    assert client.get("/api/monitor/status").json()["status"] in {"connecting", "running"}

    body = client.post("/api/monitor/stop").json()
    assert body["stopped"] is True
    # 返回时状态必须已经是终态，而不是「停止中」
    assert body["status"] not in {"stopping"}, f"停止接口仍返回中间态: {body['status']}"
    assert body["status"] in {"stopped", "ended", "failed"}


def test_monitor_stop_router_wait_is_bounded(monitor, monkeypatch):
    """即使收流迟迟不结束，停止接口也必须在有限时间内返回。

    卡死的另一种表现是请求本身挂住——浏览器会一直转圈。所以路由里的等待是
    有上限的：超时后就照常回当前状态（stopping），后台继续收尾，稍后自己落终态。

    假推理会在 4 秒后才认停止信号，比路由的等待上限（STOP_WAIT_SEC=3s）长，
    所以「接口先于收尾返回」这一点是可以被断言出来的。
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import fire_sight.api.monitor_job as mj
    from fire_sight.api.app import app
    from fire_sight.api.routers.monitor import STOP_WAIT_SEC

    def _slow_to_obey(cfg, frame_sink=None, stop_event=None, **kwargs):
        # 假装卡在阻塞读里：停止信号要过一会儿才生效
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.05)
        time.sleep(1.0)
        from fire_sight_platform.inference.service import InferStats

        return InferStats()

    class _FakeRun:
        run_id = "stub-run"
        best_pt = Path("stub.pt")

    monkeypatch.setattr(mj, "run_inference", _slow_to_obey)
    monkeypatch.setattr(mj.deps, "resolve_run", lambda name=None: _FakeRun())

    client = TestClient(app)
    client.post("/api/monitor/start", json={"source": "rtsp://cam/1"})

    t0 = time.time()
    body = client.post("/api/monitor/stop").json()
    elapsed = time.time() - t0

    assert body["stopped"] is True
    # 接口没有跟着收尾一起吊着：它在 STOP_WAIT_SEC 附近就放手了
    assert elapsed < 1.0 + STOP_WAIT_SEC + 0.5, f"停止接口等了 {elapsed:.1f}s"
    # 收尾仍在后台进行，稍后自己落终态
    assert monitor.wait(timeout=10), "后台收尾最终应落到终态"
    assert monitor.status()["status"] == "stopped"


def test_monitor_stop_for_training_is_not_blocked_by_clip_flush(monitor, monkeypatch):
    """停止后 GPU 锁必须及时归还——起训练不该撞上「monitor（正在停止）」。

    这里模拟最坏情况：推理轮次收尾很慢（真实场景是等 VP9 片段编码完）。
    修复后主流程不再等编码，锁也就不会被压在后台等待上。
    """
    import fire_sight.api.monitor_job as mj
    from fire_sight.api import deps

    def _slow_infer(cfg, frame_sink=None, stop_event=None, **kwargs):
        state = {"calls": 0}
        while stop_event is None or not stop_event.is_set():
            time.sleep(0.01)
        # 模拟片段编码收尾：给锁留出真实的释放窗口
        time.sleep(0.05)
        from fire_sight_platform.inference.service import InferStats

        s = InferStats()
        s.frames = state["calls"]
        return s

    class _FakeRun:
        run_id = "stub-run"
        best_pt = Path("stub.pt")

    monkeypatch.setattr(mj, "run_inference", _slow_infer)
    monkeypatch.setattr(mj.deps, "resolve_run", lambda name=None: _FakeRun())

    monitor.start(source="rtsp://cam/1", device="cpu")
    assert deps.GPU_LOCK.holder == "monitor"

    t0 = time.time()
    assert monitor.stop_for_training(timeout=5.0) is True
    elapsed = time.time() - t0

    assert elapsed < 3.0, f"让路给训练花了 {elapsed:.1f}s"
    assert deps.GPU_LOCK.holder is None


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
        "/api/monitor/tts/pause",
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


def test_tts_pause_is_human_controlled_and_visible_in_status(monitor, fake_pipeline):
    """人工暂停播报：接口能切，status 能看见，恢复后声音回来。

    现场要求是「持续实时播报 + 由人工决定是否暂停」——所以暂停必须是一个
    独立的、可来回切的开关，而不是启动参数里那个 enable_tts（改了要重启监控）。
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.alerting import get_tts
    from fire_sight.api.app import app

    client = TestClient(app)
    tts = get_tts(enabled=True)

    assert client.get("/api/monitor/status").json()["tts_paused"] is False

    paused = client.post("/api/monitor/tts/pause", json={"paused": True}).json()
    assert paused["paused"] is True
    assert paused["tts_paused"] is True
    assert tts.paused is True, "暂停必须落到真正发声的那个单例上"
    assert monitor.status()["tts_paused"] is True

    # 暂停期间不许再往播报队列里塞东西
    assert tts.speak("暂停期间的告警") is False

    resumed = client.post("/api/monitor/tts/pause", json={"paused": False}).json()
    assert resumed["paused"] is False
    assert resumed["tts_paused"] is False
    assert tts.paused is False

    try:
        client.post("/api/monitor/start", json={"source": "dir://frames", "enable_tts": True})
        client.post("/api/monitor/tts/pause", json={"paused": True})
        client.post("/api/monitor/stop")
        monitor.wait(timeout=5)
        # 暂停状态挂在单例上，监控重启后仍然是暂停的——不随重启偷偷恢复
        assert monitor.status()["tts_paused"] is True
    finally:
        get_tts(enabled=True).resume()


def test_tts_pause_defaults_to_pausing(monitor):
    """POST /tts/pause 不带 body 时按「暂停」处理——这是按钮上的默认动作。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.alerting import get_tts
    from fire_sight.api.app import app

    client = TestClient(app)
    try:
        assert client.post("/api/monitor/tts/pause", json={}).json()["paused"] is True
        assert get_tts(enabled=True).paused is True
    finally:
        get_tts(enabled=True).resume()


def test_configured_sources_come_from_env(monkeypatch):
    """`FIRESIGHT_MONITOR_SOURCES` 的每一项都要变成一个下拉选项。"""
    from fire_sight.services import monitor_sources

    monkeypatch.setenv(
        monitor_sources.SOURCES_ENV,
        "东门=rtsp://10.0.0.7/live; 北门=rtsp://10.0.0.8/live, rtsp://10.0.0.9/live",
    )
    opts = monitor_sources.configured_sources()
    assert [o["key"] for o in opts] == [
        "rtsp://10.0.0.7/live",
        "rtsp://10.0.0.8/live",
        "rtsp://10.0.0.9/live",
    ]
    assert [o["label"] for o in opts] == ["东门", "北门", "10.0.0.9"]
    assert {o["kind"] for o in opts} == {"stream"}


def test_camera_probe_never_opens_cameras_in_tests(monkeypatch):
    """单元测试不该碰真摄像头——打桩代替，顺带验「试不开就不算数」。"""
    import threading

    from fire_sight.services import monitor_sources

    class _FakeCap:
        def __init__(self, index):
            self.index = index
            self.released = False

        def isOpened(self):
            return self.index in (0, 2)

        def get(self, prop):
            return 640.0 if prop == 3 else 480.0

        def release(self):
            self.released = True

    import cv2

    monkeypatch.setattr(cv2, "VideoCapture", _FakeCap)
    monkeypatch.setattr(monitor_sources, "_lock", threading.Lock())
    monkeypatch.setitem(monitor_sources._cache, "at", 0.0)

    cams = monitor_sources.local_cameras(force=True)
    # 序号 1 试不开就不算数；3 也试不开就收手，不再往后试
    assert [c["key"] for c in cams] == ["0", "2"]
    assert all(c["kind"] == "camera" for c in cams)


def test_sources_endpoint_skips_camera_probe_while_monitoring(monitor, monkeypatch):
    """监控进行中不能去试开摄像头——会跟流水线抢同一台设备。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app
    from fire_sight.services import monitor_sources

    monkeypatch.setenv(monitor_sources.SOURCES_ENV, "东门=rtsp://10.0.0.7/live")
    probed = []
    monkeypatch.setattr(
        monitor_sources, "local_cameras", lambda force=False: probed.append(force) or []
    )

    client = TestClient(app)
    assert [o["key"] for o in client.get("/api/monitor/sources").json()] == ["rtsp://10.0.0.7/live"]
    assert probed == [False], "空闲时仍应探测本机摄像头"

    monitor._state["status"] = "running"
    client.get("/api/monitor/sources")
    assert probed == [False], "运行中不得再试开摄像头"
