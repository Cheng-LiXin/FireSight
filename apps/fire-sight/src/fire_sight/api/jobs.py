"""训练任务管理：起子进程、读管道、持有 GPU 锁、供 SSE 取事件。

单机单用户场景，任务是进程内的内存状态。API 重启会丢失任务记录——但训练子进程
也会随之成为孤儿，本来就该重新起。真正的训练产物落在 runs/ 与 models/ 下，
不依赖本模块的状态。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from fire_sight.api import deps
from fire_sight_platform.common.paths import ROOT_DIR, RUNTIME_TMP_DIR
from fire_sight_platform.common.runtime_env import apply_runtime_env

# 保留的任务记录条数，超出的旧记录丢弃
MAX_JOBS = 20

# 任务状态机：
#   running   进程在跑
#   stopping  已收到停止请求，正在结束进程树
#   done      正常结束并归档
#   failed    异常结束（崩溃、退出码非零、协议里报了 error）
#   stopped   用户主动停止，本次不归档
# 只有 terminal 状态才算「settled」——stopping 还在收尾，SSE 不能提前收流。
TERMINAL_STATUSES = frozenset({"done", "failed", "stopped"})
ACTIVE_STATUSES = frozenset({"running", "stopping"})

# 停止时的宽限：先整树强杀，没退再补刀。Windows 上 TerminateProcess 只杀直接
# 子进程，DataLoader 的 workers 个子进程会变成孤儿继续占显存。
STOP_GRACE_SEC = 6.0
STOP_POLL_SEC = 1.0


@dataclass
class TrainJob:
    id: str
    dataset: str
    epochs: int
    status: str = "running"  # running | stopping | done | failed | stopped
    started_at: str = ""
    finished_at: Optional[str] = None
    stop_requested_at: Optional[str] = None
    run_id: Optional[str] = None
    error: Optional[str] = None
    returncode: Optional[int] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    _proc: Any = None
    _pumps: List[Any] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add_event(self, event: Dict[str, Any]) -> None:
        with self._lock:
            event["seq"] = len(self.events) + 1
            self.events.append(event)

    def events_since(self, seq: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self.events if e["seq"] > seq]

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "dataset": self.dataset,
            "epochs": self.epochs,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stop_requested_at": self.stop_requested_at,
            "run_id": self.run_id,
            "error": self.error,
            "returncode": self.returncode,
            "config": self.config,
            "event_count": len(self.events),
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, TrainJob] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[TrainJob]:
        return self._jobs.get(job_id)

    def list(self) -> List[TrainJob]:
        return [self._jobs[i] for i in reversed(self._order) if i in self._jobs]

    def running(self) -> Optional[TrainJob]:
        """当前占着 GPU 的任务。**包含正在停止的**——它的进程树还没死、显存
        还没还回来，此时放行新训练就是等着 OOM。"""
        for job in self._jobs.values():
            if job.status in ACTIVE_STATUSES:
                return job
        return None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, config: Dict[str, Any]) -> TrainJob:
        """起一次训练。GPU 被占用时抛 GpuBusy，由路由转成 409。"""
        active = self.running()
        if active is not None:
            if active.status == "stopping":
                # 别只说「train 占用」——用户刚点过停止，这个提示会显得停止没生效。
                raise deps.GpuBusy(f"训练 {active.id}（正在停止，几秒内释放）")
            raise deps.GpuBusy("train")

        # 先拿锁再起进程：拿不到就根本不该起
        if not deps.GPU_LOCK.acquire("train"):
            raise deps.GpuBusy(deps.GPU_LOCK.holder)

        job = TrainJob(
            id=uuid.uuid4().hex[:12],
            dataset=str(config.get("dataset") or ""),
            epochs=int(config.get("epochs") or 0),
            started_at=datetime.now().isoformat(timespec="seconds"),
            config=config,
        )

        try:
            # 任务配置落运行时目录,不用 tempfile.gettempdir()——后者在 Windows
            # 上是 C:\Users\<u>\AppData\Local\Temp,系统盘本来就紧张。
            env = {**os.environ, **apply_runtime_env()}
            cfg_path = RUNTIME_TMP_DIR / f"firesight-train-{job.id}.json"
            cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "fire_sight.train_worker", str(cfg_path)],
                cwd=str(ROOT_DIR),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001 - 起不来就要把锁还回去
            deps.GPU_LOCK.release()
            job.status = "failed"
            job.error = f"无法启动训练进程: {exc}"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            self._remember(job)
            return job

        job._proc = proc
        job.add_event({"type": "log", "text": f"训练进程已启动 (pid {proc.pid})"})
        self._remember(job)

        # 存下句柄，_reap 要 join 它们才不会丢掉最后几行（done / error）
        job._pumps = [
            threading.Thread(target=self._pump_protocol, args=(job, proc), daemon=True),
            threading.Thread(target=self._pump_logs, args=(job, proc), daemon=True),
        ]
        for pump in job._pumps:
            pump.start()
        threading.Thread(target=self._reap, args=(job, proc), daemon=True).start()
        return job

    def stop(self, job_id: str) -> bool:
        """请求停止。

        关键在于**立刻**把状态从 running 挪走。之前 stop() 只调 terminate、
        状态留给 _reap 去改，于是从点击停止到进程真正退出这段时间里，
        `running()` 仍然认定这个任务在跑 —— 用户紧接着点「开始训练」就会收到
        「GPU 正被「train」占用」，看起来像停止没生效。进程被强杀在 Windows 上
        返回退出码 1，旧代码还会把它记成 failed（界面显示「失败 · 退出码 1」）。
        """
        job = self._jobs.get(job_id)
        if job is None or job.status != "running" or job._proc is None:
            return False
        job.status = "stopping"
        job.stop_requested_at = datetime.now().isoformat(timespec="seconds")
        job.add_event(
            {
                "type": "log",
                "text": "收到停止请求，正在结束训练进程及其 DataLoader 子进程。"
                "本次训练不会归档，现场记录会留在 runs/ 下。",
            }
        )
        threading.Thread(target=self._terminate, args=(job,), daemon=True).start()
        return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _terminate(self, job: TrainJob) -> None:
        """结束训练进程的**整棵树**，并在宽限期内确认它真的走了。

        只对直接子进程 terminate 是不够的：DataLoader 有 workers 个子进程，父进程
        一死它们就成了孤儿，继续占着显存和数据管道，下一次训练可能因此 OOM。
        """
        proc = job._proc
        _kill_tree(proc)
        for _ in range(int(STOP_GRACE_SEC / STOP_POLL_SEC)):
            if proc.poll() is not None:
                return
            time.sleep(STOP_POLL_SEC)
        try:
            proc.kill()
        except Exception as exc:  # noqa: BLE001
            job.add_event({"type": "log", "text": f"补刀失败，等待其自行退出: {exc}"})
            return
        job.add_event({"type": "log", "text": "进程未在宽限期内退出，已再次强杀"})

    def _remember(self, job: TrainJob) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > MAX_JOBS:
                self._jobs.pop(self._order.pop(0), None)

    def _pump_protocol(self, job: TrainJob, proc: Any) -> None:
        """读子进程 stdout：每行一个协议 JSON。"""
        for line in proc.stdout or []:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # 协议流里出现非 JSON 说明有东西漏了改道，当日志留着便于排查
                job.add_event({"type": "log", "text": line})
                continue
            self._absorb(job, event)

    def _absorb(self, job: TrainJob, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "start":
            job.epochs = int(event.get("epochs") or job.epochs)
            job.dataset = str(event.get("dataset") or job.dataset)
        elif kind == "done":
            job.run_id = event.get("run_id")
        elif kind == "error":
            job.error = event.get("message")
        job.add_event(event)

    def _pump_logs(self, job: TrainJob, proc: Any) -> None:
        """转发子进程 stderr：ultralytics 与日志库的输出都在这里。"""
        for line in proc.stderr or []:
            line = line.rstrip()
            if line:
                job.add_event({"type": "log", "text": line})

    def _reap(self, job: TrainJob, proc: Any) -> None:
        """等进程退出，定状态、放 GPU 锁。

        先 join 两个读线程再定状态，否则可能丢掉最后几行——包括 done 或 error。
        """
        code = proc.wait()
        for pump in job._pumps:
            pump.join(timeout=2.0)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass

        job.returncode = code
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        if job.status == "stopping":
            # 用户主动停止。Windows 上强杀返回 1，不能按「退出码非零」记成 failed，
            # 那会让界面显示「失败」，而它其实是一次正常的停止。
            job.status = "stopped"
        elif job.status == "running":
            if code == 0 and not job.error:
                job.status = "done"
            else:
                job.status = "failed"
                if code != 0:
                    job.error = job.error or f"训练进程退出码 {code}"
        job.add_event({"type": "exit", "code": code, "status": job.status})

        deps.GPU_LOCK.release()
        # 权重被替换或新增，模型缓存作废
        deps.clear_infer_cache()


def _kill_tree(proc: Any) -> None:
    """强杀进程及其所有子进程。

    Windows 用 `taskkill /F /T`：`Popen.terminate()` 走的是 TerminateProcess，
    只作用于直接子进程，DataLoader 的 workers 会被留下变孤儿。POSIX 下没有等价的
    一行写法（要 killpg 就得让子进程自成进程组），退化为 terminate。
    """
    pid = getattr(proc, "pid", None)
    if os.name == "nt" and pid:
        try:
            # 不用管道接输出：这里只关心有没有杀成，且避开受限环境下的管道限制
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            return
        except Exception:  # noqa: BLE001 - taskkill 不在就退回 terminate
            pass
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass


manager = JobManager()
