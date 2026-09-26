"""训练中心：历史记录、训练曲线、现场图件。

run_id 里含斜杠（如 `20260926-190635-D-Fire/train-yolo11n`），走路径参数会被
路由切碎，因此一律用查询参数传递，并只接受已发现的 run，不做任意路径拼接。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from fire_sight.api import deps
from fire_sight.api.schemas import ArtifactInfo, CurvesResponse, TrainRun
from fire_sight_platform.common.paths import PROCESSED_DATA_DIR

router = APIRouter(prefix="/api/train", tags=["train"])


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
