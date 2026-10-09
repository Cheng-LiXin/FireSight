"""停止训练：状态机、进程树，以及「停止后立刻再开始」。

用户在界面上点了停止，紧接着点开始训练，收到「GPU 正被「train」占用」。根因是
`stop()` 只调了 `terminate()`，状态留给 `_reap` 去改——从点停止到进程真正退出
这段时间里 `running()` 仍然认定任务在跑。Windows 上强杀返回退出码 1，旧代码
还会把它记成 `failed`，界面显示「失败 · 退出码 1」，而它其实是一次正常停止。
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class FakeProc:
    """可控的假训练进程。

    关键：`wait()` 必须像真进程一样**阻塞到进程结束**。让它立刻返回会让后台
    `_reap` 抢在测试断言之前把任务判成 failed，测试就测不到状态机了。
    """

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self._code = None
        self._done = threading.Event()
        self.stdout = iter(())
        self.stderr = iter(())
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        if self._code is None:
            self._done.wait(timeout)
        return self._code

    def _exit(self, code: int) -> None:
        if self._code is None:
            self._code = code
        self._done.set()

    def terminate(self) -> None:
        self.terminated = True
        self._exit(1)  # Windows 强杀就是返回 1

    def kill(self) -> None:
        self.killed = True
        self._exit(1)

    def fail(self, code: int) -> None:
        """模拟进程自己崩了（不是被停止）。"""
        self.terminated = False
        self._exit(code)


def settle(job, timeout: float = 5.0) -> str:
    """等任务收尾。状态由后台线程改，断言要跟它同步。"""
    from fire_sight.api.jobs import TERMINAL_STATUSES

    deadline = time.time() + timeout
    while time.time() < deadline:
        if job.status in TERMINAL_STATUSES:
            return job.status
        time.sleep(0.02)
    raise AssertionError(f"任务未在 {timeout}s 内收敛，当前状态 {job.status}")


@pytest.fixture()
def jobs_env(monkeypatch, tmp_path):
    """隔离的 JobManager 环境：假进程、stub 掉进程树强杀、清干净全局状态。"""
    from fire_sight.api import deps, jobs

    def fake_popen(cmd, **kwargs):
        return FakeProc()

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "apply_runtime_env", lambda: {})
    # 必须给 tmp_path：任务配置会写到这个目录，用 Path(".") 会把
    # firesight-train-*.json 撒进仓库根目录（第一版就是这么干的，23 个文件）
    monkeypatch.setattr(jobs, "RUNTIME_TMP_DIR", tmp_path)
    # 进程树强杀单独测，别在这里真去 taskkill
    monkeypatch.setattr(jobs, "_kill_tree", lambda proc: proc.terminate())

    jobs.manager._jobs.clear()
    jobs.manager._order.clear()
    yield jobs
    jobs.manager._jobs.clear()
    jobs.manager._order.clear()
    deps.GPU_LOCK.release()


@pytest.fixture()
def frozen_terminate(monkeypatch):
    """冻结停止动作：状态停在 stopping，好断言同步转换。"""
    from fire_sight.api import jobs

    monkeypatch.setattr(jobs.JobManager, "_terminate", lambda self, job: None)


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


# --------------------------------------------------------------------------
# 状态机
# --------------------------------------------------------------------------


def test_stop_moves_status_off_running_synchronously(jobs_env, frozen_terminate):
    """回归：这条挂了，用户点完停止再点开始就会看到「train 被占用」。"""
    jobs = jobs_env

    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})
    assert job.status == "running"

    assert jobs.manager.stop(job.id) is True

    # 不等进程退出，状态就该挪走
    assert job.status == "stopping"
    assert job.stop_requested_at is not None
    # 但仍在 ACTIVE 里：进程树没收尾完，GPU 还没还回来
    assert jobs.manager.running() is job


def test_second_stop_is_refused(jobs_env, frozen_terminate):
    jobs = jobs_env
    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})

    assert jobs.manager.stop(job.id) is True
    assert jobs.manager.stop(job.id) is False, "已在停止中，不该重复受理"


def test_stop_unknown_or_finished_job_is_false(jobs_env):
    jobs = jobs_env

    assert jobs.manager.stop("不存在") is False

    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})
    job.status = "done"
    assert jobs.manager.stop(job.id) is False


def test_stopped_job_settles_to_stopped_not_failed(jobs_env):
    """Windows 上强杀返回 1，不能按「退出码非零」记成失败。"""
    from fire_sight.api import deps

    jobs = jobs_env
    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})
    jobs.manager.stop(job.id)

    assert settle(job) == "stopped"
    assert job.error is None, "主动停止不该留下错误信息"
    assert job.returncode == 1
    # 锁必须还回来，否则后续训练永远起不来
    assert deps.GPU_LOCK.holder is None
    assert jobs.manager.running() is None


def test_real_crash_is_still_recorded_as_failed(jobs_env):
    jobs = jobs_env
    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})

    job._proc.fail(2)

    assert settle(job) == "failed"
    assert "退出码 2" in (job.error or "")


# --------------------------------------------------------------------------
# 现场名：不能只等 done
# --------------------------------------------------------------------------


def test_run_event_records_the_run_id_before_done(jobs_env):
    """被暂停的任务**永远走不到 done**，界面只能靠 run 事件知道该续哪个现场。

    这条挂了的话，「暂停 → 继续训练」在按钮区就没有落点：界面说不出上次训练
    落在 runs/ 下哪个目录。
    """
    job = jobs_env.manager.start({"dataset": "demo_fire", "epochs": 1})
    assert job.run_id is None

    jobs_env.manager._absorb(job, {"type": "run", "run_id": "20261009-170000-D-Fire-train-yolo11n"})

    assert job.run_id == "20261009-170000-D-Fire-train-yolo11n"

    # 暂停收尾：走不到 done，现场名必须还在
    jobs_env.manager.stop(job.id)
    assert settle(job) == "stopped"
    assert job.run_id == "20261009-170000-D-Fire-train-yolo11n"


def test_done_event_does_not_erase_the_reported_run_id(jobs_env):
    """done 缺 run_id 时不能把 run 事件报过的现场抹成 None。

    两个事件说的应当是同一个现场；真出现分歧也宁可留早先那个——它已经显示给
    用户了，中途换一个等于让「继续训练」指向别处。
    """
    job = jobs_env.manager.start({"dataset": "demo_fire", "epochs": 1})
    jobs_env.manager._absorb(job, {"type": "run", "run_id": "现场A"})
    jobs_env.manager._absorb(job, {"type": "done"})

    assert job.run_id == "现场A"


def test_run_event_is_forwarded_to_the_stream(jobs_env):
    """SSE 层不用改：凡是进事件缓冲的都会透传出去，界面按 type 分发。"""
    from fire_sight.api.routers import train as train_router

    jobs_mod = jobs_env
    job = jobs_mod.TrainJob(id="jrun", dataset="demo", epochs=1, status="running")
    jobs_mod.manager._absorb(job, {"type": "run", "run_id": "现场B"})
    jobs_mod.manager._jobs[job.id] = job
    jobs_mod.manager._order.append(job.id)

    async def scenario() -> str:
        resp = await train_router.stream_job(job.id, since=0, last_event_id="")
        gen = resp.body_iterator
        first = await asyncio.wait_for(anext(gen), timeout=5)
        return first

    try:
        first = asyncio.run(scenario())
    finally:
        jobs_mod.manager._jobs.pop(job.id, None)
        jobs_mod.manager._order.remove(job.id)
        job.status = "done"

    assert "event: run" in first
    assert "现场B" in first


# --------------------------------------------------------------------------
# 重启训练：要给出可照做的提示，而不是含糊的「train 占用」
# --------------------------------------------------------------------------


def test_restart_while_stopping_explains_why(
    client, jobs_env, frozen_terminate, tmp_path, monkeypatch
):
    import fire_sight_platform.training.service as svc
    from fire_sight.api import jobs

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")

    started = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})
    jobs.manager.stop(started.id)

    r = client.post("/api/train/jobs", json={"dataset": "任意", "epochs": 1})

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "正在停止" in detail, detail
    assert started.id in detail


# --------------------------------------------------------------------------
# SSE：stopping 期间不许提前收流
# --------------------------------------------------------------------------


def test_terminal_statuses_exclude_stopping():
    """SSE 的收流判据用 TERMINAL_STATUSES。若把 stopping 也算终态，
    界面会在进程树还没死的时候就显示「已停止」。"""
    from fire_sight.api import jobs

    assert "stopping" not in jobs.TERMINAL_STATUSES
    assert "stopping" in jobs.ACTIVE_STATUSES
    assert jobs.TERMINAL_STATUSES == {"done", "failed", "stopped"}


def test_stream_stays_open_while_stopping(jobs_env):
    from fire_sight.api.routers import train as train_router

    jobs_mod = jobs_env
    job = jobs_mod.TrainJob(id="jstop", dataset="demo", epochs=1, status="stopping")
    job.add_event({"type": "log", "text": "正在结束进程树"})
    jobs_mod.manager._jobs[job.id] = job
    jobs_mod.manager._order.append(job.id)

    async def scenario() -> tuple[str, str]:
        resp = await train_router.stream_job(job.id, since=0, last_event_id="")
        gen = resp.body_iterator
        first = await asyncio.wait_for(anext(gen), timeout=5)
        job.status = "stopped"
        second = await asyncio.wait_for(anext(gen), timeout=5)
        return first, second

    try:
        first, second = asyncio.run(scenario())
    finally:
        jobs_mod.manager._jobs.pop(job.id, None)
        jobs_mod.manager._order.remove(job.id)

    assert "event: close" not in first, "stopping 期间就收流了"
    assert "event: log" in first
    assert "event: close" in second
    assert '"status": "stopped"' in second


# --------------------------------------------------------------------------
# 进程树
# --------------------------------------------------------------------------


def test_kill_tree_uses_taskkill_on_windows(monkeypatch):
    """只 terminate 直接子进程会留下 DataLoader 孤儿继续占显存。"""
    from fire_sight.api import jobs

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))

        class Done:
            returncode = 0

        return Done()

    monkeypatch.setattr(jobs.os, "name", "nt")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)

    proc = FakeProc(pid=9876)
    jobs._kill_tree(proc)

    assert calls == [["taskkill", "/F", "/T", "/PID", "9876"]]
    assert proc.terminated is False, "整树强杀成功就不必再 terminate"


def test_kill_tree_falls_back_to_terminate(monkeypatch):
    from fire_sight.api import jobs

    def boom(argv, **kwargs):
        raise FileNotFoundError("taskkill 不在")

    monkeypatch.setattr(jobs.os, "name", "nt")
    monkeypatch.setattr(jobs.subprocess, "run", boom)

    proc = FakeProc()
    jobs._kill_tree(proc)

    assert proc.terminated is True


def test_kill_tree_on_posix_terminates(monkeypatch):
    from fire_sight.api import jobs

    monkeypatch.setattr(jobs.os, "name", "posix")
    proc = FakeProc()
    jobs._kill_tree(proc)

    assert proc.terminated is True
