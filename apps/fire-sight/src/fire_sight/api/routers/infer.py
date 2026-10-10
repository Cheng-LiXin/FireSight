"""识别检测：单图上传与目录批量。

推理走 GPU 全局锁；已被训练占用时返回 409 并说明占用者，而不是让 CUDA 报错。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from fire_sight.api import deps
from fire_sight.api.schemas import (
    BatchResult,
    BatchRow,
    Detection,
    InferResult,
    SourceOption,
)
from fire_sight.scene.class_map import NAME_CN, display_name
from fire_sight_platform.common.constants import IMAGE_EXTENSIONS
from fire_sight_platform.common.paths import PROCESSED_DATA_DIR, RAW_DATA_DIR

router = APIRouter(prefix="/api/infer", tags=["infer"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BATCH_FILES = 300
# 标注图以 JPEG 回传：PNG 对照片动辄数 MB，base64 后更大，单图响应会拖到秒级
JPEG_QUALITY = 90


class BatchRequest(BaseModel):
    """批量推理。source_key 由 /api/infer/sources 给出，不接受任意路径。"""

    source_key: str
    weights: str = ""
    conf: float = Field(0.25, ge=0.0, le=1.0)
    iou: float = Field(0.6, ge=0.0, le=1.0)
    device: str = "0"
    limit: int = Field(50, ge=1, le=MAX_BATCH_FILES)


def _encode_jpeg(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise ValueError("标注图编码失败")
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")


def _to_detections(raw: List[Dict[str, Any]]) -> List[Detection]:
    return [
        Detection(
            cls_id=int(d["cls_id"]),
            name=d["name"],
            name_cn=NAME_CN.get(d["name"], d["name"]),
            conf=round(float(d["conf"]), 4),
            xyxy=[round(float(v), 1) for v in d["xyxy"]],
        )
        for d in raw
    ]


def _dataset_dirs() -> List[Tuple[str, Path]]:
    """可用于训练/推理的数据集，返回 (标签, data.yaml 所在目录)。

    两个来源，标签空间由这里统一：

    - `data/processed/*/data.yaml` → 标签 `D-Fire/train` 这样保留子路径。
      产物名可能自带一层子路径（`--dataset "D-Fire/train"` 的产物是
      `processed/D-Fire/train/`），所以最多认两层。以 data.yaml 是否存在
      作为完整性标志——只看目录会因为半成品也被列出来。
    - `data/raw/D-Fire/data.yaml` → 标签 `D-Fire/raw`。
      这一份是数据集提供方切好的 train/val/test，不是 pipeline 产物
      （fire_sight_meta.split_strategy=provided），故意放在 raw 下就地训练；
      所以这里要一并认。**它排在前面**：用户已指定以后统一用它。
    """
    found: List[Tuple[str, Path]] = []

    raw_yaml = RAW_DATA_DIR / "D-Fire" / "data.yaml"
    if raw_yaml.exists():
        found.append(("D-Fire/raw", raw_yaml.parent))

    if PROCESSED_DATA_DIR.exists():
        for pattern in ("*/data.yaml", "*/*/data.yaml"):
            for p in PROCESSED_DATA_DIR.glob(pattern):
                found.append((p.parent.relative_to(PROCESSED_DATA_DIR).as_posix(), p.parent))

    # 同标签只留一个（浅层优先，与 archived_runs 的口径一致）
    seen: Dict[str, Path] = {}
    for name, directory in found:
        seen.setdefault(name, directory)
    return [(name, seen[name]) for name in seen]


def _source_options() -> List[SourceOption]:
    """可用于批量推理的图片目录。

    raw 下那份的图在 `train/images/`（split 目录在上一层），processed 下的在
    `images/train/`。两种布局都要认，否则新数据集选不了来源。
    """
    out: List[SourceOption] = []
    for name, ds in _dataset_dirs():
        for split in ("train", "val", "test"):
            for directory in (ds / "images" / split, ds / split / "images"):
                if not directory.is_dir():
                    continue
                n = sum(
                    1
                    for p in directory.iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
                )
                if n:
                    out.append(
                        SourceOption(
                            key=f"{name}/{split}",
                            dataset=name,
                            split=split,
                            path=str(directory),
                            image_count=n,
                        )
                    )
                break
    return out


def _resolve_source(key: str) -> Path:
    for opt in _source_options():
        if opt.key == key:
            return Path(opt.path)
    raise HTTPException(status_code=404, detail=f"未知的批量来源: {key}")


def _prepare(weights: str, device: str, conf: float, iou: float):
    """解析权重与设备，返回可直接调用的服务实例（模型已缓存）。"""
    try:
        run = deps.resolve_run(weights or None)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    dev = deps.resolve_device(device)
    return deps.get_infer_service(run.best_pt, dev), run, dev, conf, iou


def _with_gpu(fn):
    try:
        with deps.gpu_job(deps.OWNER_INFER):
            return fn()
    except deps.GpuBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sources", response_model=List[SourceOption])
def list_sources() -> List[SourceOption]:
    """可批量推理的目录。前端据此填下拉，避免让用户手敲路径。"""
    return _source_options()


@router.post("/image", response_model=InferResult)
async def infer_image(
    file: UploadFile = File(...),
    weights: str = Form(""),
    conf: float = Form(0.25, ge=0.0, le=1.0),
    iou: float = Form(0.6, ge=0.0, le=1.0),
    device: str = Form("0"),
) -> InferResult:
    """上传单张图像识别，返回检测框与标注图。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="上传内容为空")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图像过大（{len(raw) // 1024 // 1024}MB），上限 {MAX_UPLOAD_BYTES // 1024 // 1024}MB",
        )

    img = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="无法解码该图像，请更换文件")

    svc, run, dev, c, i = _prepare(weights, device, conf, iou)
    out = _with_gpu(lambda: svc.infer_array(img, conf=c, iou=i, plot=True))

    h, w = img.shape[:2]
    return InferResult(
        detections=_to_detections(out["detections"]),
        count=out["count"],
        classes=out["classes"],
        max_conf=out["max_conf"],
        elapsed_ms=out["elapsed_ms"],
        width=w,
        height=h,
        weights=run.run_id,
        device=dev,
        annotated=_encode_jpeg(out["annotated"]),
    )


@router.post("/batch", response_model=BatchResult)
def infer_batch(req: BatchRequest) -> BatchResult:
    """对已处理数据集的某个划分批量推理，返回逐图统计。"""
    directory = _resolve_source(req.source_key)
    svc, run, dev, c, i = _prepare(req.weights, req.device, req.conf, req.iou)

    rows_raw = _with_gpu(
        lambda: svc.infer_batch(
            directory, limit=req.limit, conf=c, iou=i, plot=False
        )
    )

    rows: List[BatchRow] = []
    class_totals: Dict[str, int] = {}
    for r in rows_raw:
        for name in r["classes"]:
            class_totals[name] = class_totals.get(name, 0) + 1
        rows.append(
            BatchRow(
                file=Path(r["path"]).name,
                count=r["count"],
                classes=r["classes"],
                max_conf=r["max_conf"],
                elapsed_ms=r["elapsed_ms"],
            )
        )

    hit = sum(1 for r in rows if r.count > 0)
    avg_ms = round(sum(r.elapsed_ms for r in rows) / len(rows), 2) if rows else 0.0
    return BatchResult(
        rows=rows,
        files=len(rows),
        hit_files=hit,
        total_dets=sum(r.count for r in rows),
        avg_ms=avg_ms,
        class_totals=class_totals,
        weights=run.run_id,
        device=dev,
    )
