"""多权重对比评估：发起任务、查进度、取结果。

评估本身在 `fire_sight_platform.evaluation.service.evaluate_weights`，
这里只做校验、排期（GPU 锁）与进度查询。同一份 val split 上逐个跑，
所以模型之间可比；绝对数值请对照归档的 metrics.txt（同一口径，见 ValConfig）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from fire_sight.api import deps, eval_jobs
from fire_sight.api.routers.models import read_metrics, read_train_config
from fire_sight.api.schemas import EvalJobInfo, EvalRequest

router = APIRouter(prefix="/api/eval", tags=["eval"])

# 归档里没记数据集时的兜底：本仓库的默认数据集
_FALLBACK_DATASET = "D-Fire/train"


def _dataset_of(run_id: str) -> Optional[str]:
    """从归档的 metrics.txt / train_config.yaml 里取它当初训练用的数据集。"""
    run = deps.find_archived_run(run_id)
    if run is None:
        return None
    metrics = read_metrics(run.root)
    config = read_train_config(run.root)
    return metrics.get("dataset") or config.get("dataset") or None


def _resolve_dataset(run_ids: List[str], requested: str) -> str:
    """决定在哪个数据集上对比。

    默认取**第一个选中模型**记录的数据集——对比必须落在同一份 val 上，
    而各模型记录的数据集可能不同（比如早先在别的数据集上训的）。
    这种不一致由调用方负责，界面上会把实际用的数据集显示出来。
    """
    if requested.strip():
        return requested.strip()
    for run_id in run_ids:
        found = _dataset_of(run_id)
        if found:
            return found
    return _FALLBACK_DATASET


@router.post("", status_code=201, response_model=EvalJobInfo)
def start_eval(req: EvalRequest) -> Dict[str, Any]:
    """发起一次对比。至少两个模型——单个权重用不到「对比」。"""
    missing = [r for r in req.run_ids if deps.find_archived_run(r) is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"未找到归档: {', '.join(missing)}")

    dataset = _resolve_dataset(req.run_ids, req.dataset)
    try:
        job = eval_jobs.manager.start(
            run_ids=req.run_ids,
            dataset=dataset,
            imgsz=req.imgsz,
            device=req.device,
        )
    except deps.GpuBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@router.get("/jobs", response_model=List[EvalJobInfo])
def list_jobs() -> List[Dict[str, Any]]:
    """本次服务进程起过的对比任务，新→旧。服务重启即清空。"""
    return [j.public() for j in eval_jobs.manager.list()]


@router.get("/jobs/{job_id}", response_model=EvalJobInfo)
def get_job(job_id: str) -> Dict[str, Any]:
    """轮询进度。`done`/`total` 让界面能画「正在评估 2/3」。"""
    job = eval_jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"未找到评估任务: {job_id}")
    return job.public()
