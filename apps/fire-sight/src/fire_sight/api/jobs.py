"""训练任务管理：起子进程、读管道、持有 GPU 锁、供 SSE 取事件。

单机单用户场景，任务是进程内的内存状态。API 重启会丢失任务记录——但训练子进程
也会随之成为孤儿，本来就该重新起。真正的训练产物落在 runs/ 与 models/ 下，
不依赖本模块的状态。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fire_sight.api import deps
from fire_sight_platform.common.paths import ROOT_DIR

# 保留的任务记录条数，超出的旧记录丢弃
MAX_JOBS = 20


@dataclass
class TrainJob:
    id: str
    dataset: str
    epochs: int
    status: str = "running"  # running | done | failed | stopped
    started_at: str = ""
    finished_at: Optional[str] = None
    run_id: Optional[str] = None
    error: Optional[str] = None
    returncode: Optional[int] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    _proc: Any = None
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
        for job in self._jobs.values():
            if job.status == "running":
                return job
        return None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, config: Dict[str, Any]) -> TrainJob:
        """起一次训练。GPU 被占用时抛 GpuBusy，由路由转成 409。"""
        if self.running() is not None:
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
            cfg_path = Path(tempfile.gettempdir()) / f"firesight-train-{job.id}.json"
            cfg_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

            proc = subprocess.Popen(
                [sys.executable, "-u", "-m", "fire_sight.train_worker", str(cfg_path)],
                cwd=str(ROOT_DIR),
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

        threading.Thread(
            target=self._pump_protocol, args=(job, proc), daemon=True
        ).start()
        threading.Thread(target=self._pump_logs, args=(job, proc), daemon=True).start()
        threading.Thread(target=self._reap, args=(job, proc), daemon=True).start()
        return job

    def stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running" or job._proc is None:
            return False
        job.add_event(
            {
                "type": "log",
                "text": "收到停止请求。注意：进程被强制结束，本次训练不会归档，"
                "但现场记录会留在 runs/ 下。",
            }
        )
        job._proc.terminate()
        return True

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

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
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass

        job.returncode = code
        job.finished_at = datetime.now().isoformat(timespec="seconds")
        if job.status == "running":
            if code == 0 and not job.error:
                job.status = "done"
            elif code == 0:
                job.status = "failed"
            else:
                job.status = "failed"
                job.error = job.error or f"训练进程退出码 {code}"
        job.add_event({"type": "exit", "code": code, "status": job.status})

        deps.GPU_LOCK.release()
        # 权重被替换或新增，模型缓存作废
        deps.clear_infer_cache()


manager = JobManager()
