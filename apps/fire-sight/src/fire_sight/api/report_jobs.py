"""火情分析报告：后台任务 + 轮询。

为什么不是同步接口：生成要几十秒，同步 POST 会先撞前端超时，而且中途没有任何
反馈——用户看到的是一个卡住的按钮。为什么不用 SSE：进度粒度是「一次生成」，
两秒问一次足够，不值得占一条长连接（同 eval_jobs 的结论）。

不抢 GPU 锁：生成是网络 IO，既不占显存也不该跟训练/监控抢资源。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

# 顶层绑定而不是在 _run 里局部 import：生成走的通道要能在测试里换成假客户端
# （monkeypatch 报的是本模块的这个名字，局部 import 会把它盖掉）。
from fire_sight.services.llm import resolve_client
from fire_sight_platform.common.paths import DATA_DIR

logger = logging.getLogger(__name__)

# 事件库位置。可被 monkeypatch，便于测试喂临时库
EVENTS_DB = DATA_DIR / "events" / "events.db"

# 状态机：running → done | failed。start() 直接置 running，没有中间态
# （早先注释里的 queued 从未被发出过，别再按它写断言）
ACTIVE_STATUSES = frozenset({"running"})

# 任务带完整统计快照（summary），常驻服务里只增不减就是内存漏斗。
# 与 jobs.py / eval_jobs 同一个口径。
MAX_JOBS = 20


class GenerationInProgress(RuntimeError):
    """同一区间已有生成在跑——LLM 调用按次计费，重复发起就是重复花钱。"""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"该时间段已有生成任务在进行（{job_id}）")


class ReportJobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self, start: str, end: str) -> Dict[str, Any]:
        """起一次生成。无事件、未配通道由调用方先拦，这里只管跑。

        同区间的重复发起在这里拦（在锁内判，检查与登记是原子的）。
        """
        from fire_sight.services.report_service import make_report_id

        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "running",
            "stage": "聚合数据",
            "start": start,
            "end": end,
            "error": None,
            "report_id": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            for other in self._jobs.values():
                if (
                    other["status"] in ACTIVE_STATUSES
                    and other["start"] == start
                    and other["end"] == end
                ):
                    raise GenerationInProgress(other["id"])
            self._jobs[job["id"]] = job
            self._order.append(job["id"])
            self._evict_locked()

        threading.Thread(
            target=self._run,
            args=(job, start, end, make_report_id()),
            daemon=True,
        ).start()
        return dict(job)

    def _evict_locked(self) -> None:
        """超过上限时丢最旧的**已结束**任务。

        运行中的绝不裁：它收尾时要按 id 回填片段/状态，表里找不到就静默丢了。
        但如果全都在跑（单机上罕见），宁可暂时超上限也不能硬裁。
        """
        while len(self._order) > MAX_JOBS:
            for idx, key in enumerate(self._order):
                if self._jobs.get(key, {}).get("status") not in ACTIVE_STATUSES:
                    self._jobs.pop(self._order.pop(idx), None)
                    break
            else:
                return

    def _set(self, job_id: str, **kw: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(kw)

    def _run(self, job: Dict[str, Any], start: str, end: str, report_id: str) -> None:
        """后台线程：聚合 → 生成 → 归档。任何一步失败都落到 failed 并带中文原因。"""
        from fire_sight.services.report_service import (
            aggregate_events,
            build_prompt,
            save_report,
        )

        job_id = job["id"]
        try:
            summary = aggregate_events(EVENTS_DB, start, end)
            self._set(job_id, stage="模型生成中")
            system, user = build_prompt(summary)
            client = resolve_client()
            markdown = client.chat(system, user)

            self._set(job_id, stage="归档中")
            meta = {
                "title": f"火情分析与防火建议 · {start} ~ {end}",
                "start": start,
                "end": end,
                "model": getattr(getattr(client, "settings", None), "model", ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "summary": summary,
            }
            save_report(report_id, meta, markdown)
            self._set(job_id, status="done", stage="", report_id=report_id)
        except Exception as exc:  # noqa: BLE001 - 单次生成失败不该让线程死掉
            logger.warning(f"报告生成失败: {exc}")
            self._set(job_id, status="failed", stage="", error=str(exc))


REPORTS = ReportJobManager()
