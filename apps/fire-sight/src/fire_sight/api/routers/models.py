"""模型库：归档权重列表。

识别页需要它来填权重下拉，模型库页后续在此扩展（设为当前、下载、对比）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

from fire_sight.api import deps
from fire_sight.api.schemas import ModelInfo

router = APIRouter(prefix="/api/models", tags=["models"])


def read_metrics(run_root: Path) -> Dict[str, Any]:
    """解析归档的 metrics.txt（`key=value` 行）。缺失时返回空字典。"""
    path = run_root / "metrics.txt"
    if not path.exists():
        return {}
    out: Dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _num(metrics: Dict[str, Any], key: str) -> Optional[float]:
    """指标缺省或不可解析时返回 None——前端显示「—」，而不是显示 0。"""
    raw = metrics.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@router.get("", response_model=List[ModelInfo])
def list_models() -> List[ModelInfo]:
    """全部归档训练，新→旧。无归档时返回空数组，不报错。"""
    active = deps.active_run()
    out: List[ModelInfo] = []
    for run in deps.archived_runs():
        metrics = read_metrics(run.root)
        try:
            created = datetime.fromtimestamp(run.root.stat().st_mtime).isoformat(
                timespec="seconds"
            )
        except OSError:
            created = None
        out.append(
            ModelInfo(
                run_id=run.run_id,
                mAP50=_num(metrics, "mAP50"),
                precision=_num(metrics, "precision"),
                recall=_num(metrics, "recall"),
                created=created,
                is_active=active is not None and run.run_id == active.run_id,
            )
        )
    return out
