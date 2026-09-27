"""训练中心：历史记录、训练曲线、现场图件。

run_id 里含斜杠（如 `20260926-190635-D-Fire/train-yolo11n`），走路径参数会被
路由切碎，因此一律用查询参数传递，并只接受已发现的 run，不做任意路径拼接。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from fire_sight.api import deps
from fire_sight.api.jobs import TERMINAL_STATUSES, manager
from fire_sight.api.schemas import ArtifactInfo, CurvesResponse, TrainRun
from fire_sight_platform.common.paths import PROCESSED_DATA_DIR

router = APIRouter(prefix="/api/train", tags=["train"])

# SSE 取事件的间隔。训练进度是秒级粒度，250ms 足够且实现简单。
_STREAM_TICK = 0.25


class TrainJobRequest(BaseModel):
    """启动一次训练。字段与 TrainConfig 对齐，未列出的走默认值。"""

    dataset: str
    model: str = "yolo11n.pt"
    epochs: int = Field(50, ge=1, le=1000)
    batch: int = Field(16, ge=1, le=512)
    imgsz: int = Field(640, ge=32, le=2048)
    device: str = "0"
    fraction: float = Field(1.0, gt=0.0, le=1.0)
    patience: int = Field(10, ge=0, le=1000)
    workers: int = Field(4, ge=0, le=64)
    cache: bool = False
    seed: int = 42
    experiment: str = ""


def _read_args(run_root: Path) -> Dict[str, Any]:
    """读训练现场 args.yaml。缺了不影响列表，只是数据集名显示为空。"""
    path = run_root / "args.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 元信息读取失败不该让列表接口挂掉
        return {}


def _dataset_label(data_path: Any) -> Optional[str]:
    """把 data.yaml 的绝对路径收成 `D-Fire/train` 这样的可读标签。"""
    if not data_path:
        return None
    try:
        p = Path(str(data_path))
        rel = p.parent.relative_to(PROCESSED_DATA_DIR)
        return rel.as_posix()
    except (ValueError, OSError):
        return Path(str(data_path)).parent.name or None


def _num(metrics: Dict[str, Any], key: str) -> Optional[float]:
    raw = metrics.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _archived_metrics(run_id: str) -> Dict[str, Any]:
    from fire_sight.api.routers.models import read_metrics

    for run in deps.archived_runs():
        if run.run_id == run_id:
            return read_metrics(run.root)
    return {}


@router.get("/runs", response_model=List[TrainRun])
def list_runs() -> List[TrainRun]:
    """全部训练记录，新→旧。无记录时返回空数组。"""
    from fire_sight_platform.training.curves import read_results_csv

    out: List[TrainRun] = []
    for run in deps.training_runs():
        args = _read_args(run.root)
        metrics = _archived_metrics(run.run_id)
        curves = read_results_csv(run.root / "results.csv")
        try:
            created = datetime.fromtimestamp(run.root.stat().st_mtime).isoformat(
                timespec="seconds"
            )
        except OSError:
            created = None

        out.append(
            TrainRun(
                run_id=run.run_id,
                dataset=_dataset_label(args.get("data")),
                model=Path(str(args.get("model"))).name if args.get("model") else None,
                epochs_done=curves.total_epochs,
                archived=bool(metrics),
                mAP50=_num(metrics, "mAP50"),
                mAP50_95=_num(metrics, "mAP50_95"),
                precision=_num(metrics, "precision"),
                recall=_num(metrics, "recall"),
                artifact_count=len(deps.run_artifacts(run.root)),
                created=created,
            )
        )
    return out


@router.get("/curves", response_model=CurvesResponse)
def get_curves(run_id: str = Query(...)) -> CurvesResponse:
    """某次训练逐轮曲线，直接来自现场 results.csv。"""
    from fire_sight_platform.training.curves import read_results_csv

    try:
        run = deps.find_training_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    curves = read_results_csv(run.root / "results.csv")
    return CurvesResponse(
        run_id=run_id,
        epochs=curves.epochs,
        losses=curves.losses,
        metrics=curves.metrics,
        lr=curves.lr,
        total_epochs=curves.total_epochs,
    )


@router.get("/artifacts", response_model=List[ArtifactInfo])
def list_artifacts(run_id: str = Query(...)) -> List[ArtifactInfo]:
    """现场图件清单（混淆矩阵、PR 曲线、批次样例等）。"""
    try:
        run = deps.find_training_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ArtifactInfo(**a) for a in deps.run_artifacts(run.root)]


@router.get("/artifact")
def get_artifact(run_id: str = Query(...), name: str = Query(...)) -> FileResponse:
    """取单张现场图件。

    name 只允许现场目录下的顶层图片文件名——不接受子路径，避免借这个接口
    读出工作区里的任意文件。
    """
    try:
        run = deps.find_training_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if Path(name).name != name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail="图件名不合法")

    target = run.root / name
    if not target.is_file() or target.suffix.lower() not in deps.IMAGE_SUFFIXES:
        raise HTTPException(status_code=404, detail=f"图件不存在: {name}")
    return FileResponse(target)


# --------------------------------------------------------------------------
# 训练任务
# --------------------------------------------------------------------------


@router.get("/limits")
def get_limits(
    imgsz: int = Query(480, ge=32, le=2048),
    device: str = Query("0"),
    model: str = Query("yolo11n.pt", description="权重名或路径；决定模型规模因子"),
) -> Dict[str, Any]:
    """本机在给定 imgsz 与模型下的 batch 上限。

    前端据此提前提示，用户不必先提交一次、拿到 400 才知道超了。
    `checked=false` 表示没做限制（CPU 或探测不到显卡）；
    `model_measured=false` 表示该模型规模没有实测数据，上限是估的。
    """
    return deps.vram_limits(device, imgsz, model)


@router.post("/jobs", status_code=201)
def start_job(req: TrainJobRequest) -> Dict[str, Any]:
    """启动一次训练。GPU 被占用时返回 409 并说明占用者。"""
    from fire_sight_platform.training.service import _resolve_dataset_yaml
    from fire_sight_platform.training.vram import check as check_vram

    # 先确认数据集找得到，别把进程起起来才发现——那样只会浪费一次模型加载
    try:
        _resolve_dataset_yaml(req.dataset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # 再挡显存。等 ultralytics 自己报 OOM，已经烧掉一次模型加载和几分钟了。
    # 必须带上模型：不同规模的激活开销差到 7 倍，只看 batch × imgsz² 会放行 yolo11m
    # 在 n 的额度上跑，实测那会超预算 2.2 倍。
    limits = deps.vram_limits(req.device, req.imgsz, req.model)
    if limits["checked"]:
        verdict = check_vram(
            imgsz=req.imgsz,
            batch=req.batch,
            total_mb=limits["total_mb"],
            model=req.model,
        )
        if not verdict.ok:
            raise HTTPException(status_code=400, detail=verdict.message)

    try:
        job = manager.start(req.model_dump())
    except deps.GpuBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


@router.get("/jobs")
def list_jobs() -> List[Dict[str, Any]]:
    """本次服务进程起过的训练任务，新→旧。服务重启即清空。"""
    return [j.public() for j in manager.list()]


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"未找到任务: {job_id}")
    return job.public()


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: str) -> Dict[str, Any]:
    """停止训练。

    进程被强制结束，因此**本次训练不会归档**，但现场记录会留在 runs/ 下，
    曲线仍可在训练记录里查看。
    """
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到任务: {job_id}")
    return {"stopped": manager.stop(job_id)}


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    since: int = Query(0, ge=0),
    last_event_id: str = Header("", alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE：逐轮指标与日志。

    用轮询事件缓冲实现，不做线程间推送——实现简单，且天然支持断线续传：
    客户端带上已收到的最后一个 seq，服务端从那里接着发。

    浏览器自动重连时发的是 Last-Event-ID 头而不是查询参数，两个都要认，
    否则一断线就会从头重放，实时曲线上会出现重复点。
    """
    if last_event_id:
        try:
            since = max(since, int(last_event_id))
        except ValueError:
            pass

    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"未找到任务: {job_id}")

    async def gen() -> AsyncIterator[str]:
        last = since
        while True:
            for event in job.events_since(last):
                last = int(event["seq"])
                payload = json.dumps(event, ensure_ascii=False)
                kind = event.get("type", "message")
                yield f"id: {last}\nevent: {kind}\ndata: {payload}\n\n"

            if job.status in TERMINAL_STATUSES and not job.events_since(last):
                # 收尾：把最终状态作为一条事件送出，客户端据此收流。
                # 必须带上 type——客户端按 type 分发，少了它这条会被静默丢弃，
                # 状态条就永远停在「训练中」。
                #
                # 判据用 TERMINAL_STATUSES 而不是「状态 != running」：stopping
                # 期间进程树还在收尾，提前收流会把界面显示成已经结束。
                final = {"type": "close", **job.public()}
                yield f"event: close\ndata: {json.dumps(final, ensure_ascii=False)}\n\n"
                return

            await asyncio.sleep(_STREAM_TICK)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 反代下若不禁用缓冲，事件会被攒住一起发，实时性全无
            "X-Accel-Buffering": "no",
        },
    )
