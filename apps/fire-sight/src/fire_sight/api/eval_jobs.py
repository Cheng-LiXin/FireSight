"""多权重对比评估：后台任务 + 轮询。

为什么不是同步接口
------------------
一次评估要跑完整个 val split（D-Fire/train 的 val 是 1722 张）。同步 POST 会先撞上
前端超时，而且中途没有任何反馈——用户看到的是一个卡住的按钮。

为什么用轮询而不是 SSE
----------------------
训练用 SSE 是因为进度是**逐轮**的、需要实时曲线。对比评估的进度粒度是**逐个模型**
（一到几分钟一个），隔两秒问一次足够，也不必占住一条长连接。SSE 那套坑
（事件必须带 `type`）在这里没有回报。

GPU 互斥
--------
起任务前先抢 `GPU_LOCK`：被训练占用时立刻 409，而不是起个任务再失败。
抢不到就抛 `GpuBusy`，由路由转成可读提示。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from fire_sight.api import deps


def result_row(run_id: str, **kw: Any) -> Dict[str, Any]:
    """给结果行**固定**的字段集合。

    成功与失败两种情况下键必须一致：形状随结果变化，前端就得给每个字段写两套分支，
    而漏掉一个键的后果是渲染时崩掉，不是显示一个「—」。
    """
    base: Dict[str, Any] = {
        "run_id": run_id,
        "mAP50": None,
        "mAP50_95": None,
        "precision": None,
        "recall": None,
        "per_class": {},
        "speed_ms": {},
        "error": None,
    }
    base.update(kw)
    return base


@dataclass
class EvalJob:
    id: str
    run_ids: List[str]
    dataset: str
    imgsz: int
    device: str
    status: str = "running"
    started_at: str = ""
    finished_at: Optional[str] = None
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    # 后台线程逐个追加结果，轮询线程同时读——必须加锁
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def append(self, item: Dict[str, Any]) -> None:
        with self._lock:
            self.results.append(item)

    def public(self) -> Dict[str, Any]:
        with self._lock:
            results = [dict(r) for r in self.results]
        return {
            "id": self.id,
            "run_ids": list(self.run_ids),
            "dataset": self.dataset,
            "imgsz": self.imgsz,
            # 评估固定跑 val split（ultralytics 的默认），显式回给前端而不是让它猜
            "split": "val",
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            # 逐个模型出结果，界面据此画「正在评估 2/3」
            "done": len(results),
            "total": len(self.run_ids),
            "results": results,
            "error": self.error,
        }


class EvalManager:
    """进程内的对比任务表。服务重启即清空，与训练任务一致。"""

    def __init__(self) -> None:
        self._jobs: Dict[str, EvalJob] = {}
        self._lock = threading.Lock()

    def list(self) -> List[EvalJob]:
        with self._lock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def get(self, job_id: str) -> Optional[EvalJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def start(
        self,
        run_ids: List[str],
        dataset: str,
        imgsz: int,
        device: str,
    ) -> EvalJob:
        # 同一个 run 选两次没有意义，去重但保序
        ordered = list(dict.fromkeys(run_ids))

        if not deps.GPU_LOCK.acquire("eval"):
            raise deps.GpuBusy(deps.GPU_LOCK.holder)

        job = EvalJob(
            id=uuid.uuid4().hex[:12],
            run_ids=ordered,
            dataset=dataset,
            imgsz=imgsz,
            device=device,
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._jobs[job.id] = job

        try:
            thread = threading.Thread(
                target=self._run, args=(job,), daemon=True, name=f"eval-{job.id}"
            )
            thread.start()
        except Exception:
            # 抢到的锁必须还回去，否则这台机器再也起不了训练或评估
            deps.GPU_LOCK.release()
            with self._lock:
                self._jobs.pop(job.id, None)
            raise

        return job

    def _run(self, job: EvalJob) -> None:
        from fire_sight_platform.evaluation.service import evaluate_weights
        from fire_sight_platform.runtime_config.base import ValConfig

        try:
            for run_id in job.run_ids:
                run = deps.find_archived_run(run_id)
                if run is None:
                    job.append(result_row(run_id, error="归档已不存在"))
                    continue
                try:
                    result = evaluate_weights(
                        ValConfig(
                            weights=str(run.best_pt),
                            dataset=job.dataset,
                            imgsz=job.imgsz,
                            device=job.device,
                        )
                    )
                    job.append(
                        result_row(
                            run_id,
                            mAP50=result.metrics.get("mAP50"),
                            mAP50_95=result.metrics.get("mAP50_95"),
                            precision=result.metrics.get("precision"),
                            recall=result.metrics.get("recall"),
                            per_class=dict(result.per_class),
                            speed_ms=dict(result.speed_ms),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - 单个模型失败不该毁掉整个对比
                    job.append(result_row(run_id, error=f"{type(exc).__name__}: {exc}"))
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - 兜底，保证状态不会永远停在 running
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            deps.GPU_LOCK.release()


manager = EvalManager()
