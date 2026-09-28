"""模型库：归档权重列表、设为当前、下载权重。

「当前权重」由用户显式指定（`models/active.json`），不再等于「最新归档」——
只取最新会让每一次实验都悄悄顶上线上模型，2026-09-27 那次 30 轮实验
（mAP50 0.7432）就这样顶掉了 0.7675，页面上没有任何提示。

下载与「当前」都用 `run_id` 查询参数而不是路径段：run_id 里含斜杠
（早期布局是 `20260926-190635-D-Fire/train-yolo11n`），走路径会被路由切碎。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from fire_sight.api import deps
from fire_sight.api.schemas import (
    ActiveModel,
    ActiveModelRequest,
    DeleteRequest,
    DeleteResult,
    ModelInfo,
)
from fire_sight.services import recycle

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


def read_train_config(run_root: Path) -> Dict[str, Any]:
    """读归档的 train_config.yaml。

    文件后缀是 `.yaml`，内容其实是 JSON（训练时直接 dump 的 dict）。这里按 JSON 解析，
    失败再退回 YAML——不因为一个副产物格式不统一就让整页报错。
    """
    path = run_root / "train_config.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - 配置读不出来只影响展示
        return {}


def _num(metrics: Dict[str, Any], key: str) -> Optional[float]:
    """指标缺省或不可解析时返回 None——前端显示「—」，而不是显示 0。"""
    raw = metrics.get(key)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _int(config: Dict[str, Any], key: str) -> Optional[int]:
    raw = config.get(key)
    try:
        return int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _created(root: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(root.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


@router.get("", response_model=List[ModelInfo])
def list_models() -> List[ModelInfo]:
    """全部归档训练，新→旧。无归档时返回空数组，不报错。"""
    state = deps.active_model_state()
    out: List[ModelInfo] = []
    for run in deps.archived_runs():
        metrics = read_metrics(run.root)
        config = read_train_config(run.root)
        try:
            size_mb = round(run.best_pt.stat().st_size / 1024 / 1024, 1)
        except OSError:
            size_mb = None
        out.append(
            ModelInfo(
                run_id=run.run_id,
                mAP50=_num(metrics, "mAP50"),
                mAP50_95=_num(metrics, "mAP50_95"),
                precision=_num(metrics, "precision"),
                recall=_num(metrics, "recall"),
                epochs=_int(config, "epochs"),
                size_mb=size_mb,
                dataset=metrics.get("dataset") or config.get("dataset") or None,
                created=_created(run.root),
                is_active=run.run_id == state.run_id,
            )
        )
    return out


@router.get("/active", response_model=ActiveModel)
def get_active() -> ActiveModel:
    """当前权重，以及它为什么是这一个。

    单独一个接口是因为识别页只需要 run_id，而模型库页要把 `note` 显示出来——
    「没指定过，按最新取」和「指定的已被删，暂用最新」对用户是两件事。
    """
    state = deps.active_model_state()
    return ActiveModel(
        run_id=state.run_id, pinned=state.pinned, source=state.source, note=state.note
    )


@router.post("/active", response_model=ActiveModel)
def set_active(req: ActiveModelRequest) -> ActiveModel:
    """把某个归档设为当前权重。"""
    if deps.find_archived_run(req.run_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到归档: {req.run_id}")
    deps.ACTIVE_MODEL_STORE.write(req.run_id)
    state = deps.active_model_state()
    return ActiveModel(
        run_id=state.run_id, pinned=state.pinned, source=state.source, note=state.note
    )


@router.delete("/active", response_model=ActiveModel)
def clear_active() -> ActiveModel:
    """清除指定，回到「取最新归档」。

    删状态文件而不是写 null：文件存在就等于「用户指定过」，这样语义只有一种。
    """
    deps.ACTIVE_MODEL_STORE.write(None)
    state = deps.active_model_state()
    return ActiveModel(
        run_id=state.run_id, pinned=state.pinned, source=state.source, note=state.note
    )


@router.get("/download")
def download(run_id: str = Query(..., description="归档 run_id（含斜杠，故走查询参数）")):
    """下载归档权重（best.pt）。"""
    run = deps.find_archived_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"未找到归档: {run_id}")
    if not run.best_pt.is_file():
        raise HTTPException(status_code=404, detail=f"归档缺少权重文件: {run_id}")

    # run_id 可能含斜杠，做文件名时要换掉；它已在上面的精确匹配里校验过，
    # 不是从用户输入拼出来的路径。
    filename = run_id.replace("/", "_").replace("\\", "_") + ".pt"
    return FileResponse(
        run.best_pt,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.delete("", response_model=DeleteResult)
def delete_model(req: DeleteRequest) -> DeleteResult:
    """删除一个归档（移进回收目录，可恢复）。

    DELETE 带 body 不常见，但 run_id 里含斜杠、走路径会被路由切碎，而查询参数
    又放不下 `with_run` 这种结构化意图。FastAPI 支持带 body 的 DELETE，前端
    fetch 也发得出去，就用了——比把 run_id 编码进路径再解码回来说得清楚。

    删的恰好是**当前权重**时，`active` 会把回落结果一并返回：界面必须显示
    「当前权重已变为 X」，而不是让用户下次打开识别页才发现模型换了。
    """
    run = deps.find_archived_run(req.run_id)
    # 训练现场可能单独存在（被强行停止、还没归档的训练），所以两个目标分别查
    run_site = next(
        (r for r in deps.training_runs() if r.run_id == req.run_id), None
    )

    if run is None and run_site is None:
        raise HTTPException(status_code=404, detail=f"未找到归档: {req.run_id}")

    if run is None and not req.with_run:
        # 只有现场、没有归档。此时「只删归档」等于什么都没删，如实拒绝，
        # 而不是返回一个 removed_* 全 False 的成功结果让人以为删掉了。
        raise HTTPException(
            status_code=400,
            detail=f"{req.run_id} 只有训练现场、没有归档权重；如需删除请勾选「同时删除训练现场」",
        )

    removed = recycle(
        run_id=req.run_id,
        archive_root=run.root if run is not None else None,
        run_root=run_site.root if (req.with_run and run_site is not None) else None,
        # 归档根由这里给出（本模块的 deps 侧最清楚它指向哪）；清空壳时不许越过它
        archive_base=deps.TRAINED_MODELS_DIR,
    )

    # 权重被搬走之后，内存里那份缓存必须扔掉：否则识别页还拿着已经不在磁盘上的
    # 模型继续推理，直到服务重启。删的就是当前权重时尤其明显。
    deps.clear_infer_cache()

    state = deps.active_model_state()
    active = ActiveModel(
        run_id=state.run_id, pinned=state.pinned, source=state.source, note=state.note
    )

    parts = []
    if run is not None:
        parts.append("归档权重")
    if req.with_run and run_site is not None:
        parts.append("训练现场")
    # 归档在、现场不在：早期手工归档或现场已被清过。不算错误，但要说清，
    # 免得用户以为漏删了。
    if req.with_run and run_site is None and run is not None:
        parts.append("训练现场（本来就不存在）")
    note = "已移入回收目录：" + "、".join(parts) if parts else "没有可删除的内容"
    if run is not None and state.source == "fallback-missing":
        note += f"；当前权重已回落到 {state.run_id}"

    return DeleteResult(
        run_id=req.run_id,
        removed_archive=run is not None,
        removed_run=bool(req.with_run and run_site is not None),
        trash_dir=str(removed.trash_dir) if removed.items else None,
        active=active,
        note=note,
    )
