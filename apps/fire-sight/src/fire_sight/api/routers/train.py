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
from pydantic import BaseModel, Field, field_validator

from fire_sight.api import deps
from fire_sight.api.jobs import TERMINAL_STATUSES, manager
from fire_sight.api.schemas import ArtifactInfo, CurvesResponse, ModelOption, TrainRun
from fire_sight_platform.common.paths import PROCESSED_DATA_DIR, RAW_DATA_DIR

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
    #: 非空时改为从该训练现场断点续训。除 device 外，请求里其余字段基本不生效——
    #: ultralytics 以检查点里的 train_args 为准（几何、epochs、lr 全取它）。
    resume_run_id: str = ""

    @field_validator("experiment")
    @classmethod
    def _check_experiment(cls, value: str) -> str:
        """实验名会经 `increment_path` 变成 `runs/training/<name>` 的目录名。

        它不做 resolve、也不夹取，所以 `../../../../EvilExp` 会让整轮训练
        （权重、results.csv、图件）落到工作区之外，还进不了归档。
        这里只放行单个目录名，不给任何路径语义。
        """
        name = value.strip()
        if not name:
            return ""
        if name in {".", ".."} or "/" in name or "\\" in name or ":" in name:
            raise ValueError(f"实验名只能是单个目录名，不含路径分隔符: {value!r}")
        if name != name.strip("."):
            raise ValueError(f"实验名不能以点开头或结尾: {value!r}")
        return name

    @field_validator("dataset")
    @classmethod
    def _check_dataset(cls, value: str) -> str:
        """数据集名允许 `D-Fire/raw` 这类相对子路径，但不许逃出 data/。

        真正兜底的是 `_resolve_dataset_yaml` 的存在性检查，这里只挡掉
        `..` 与绝对路径这类一眼可见的越界写法。
        """
        name = value.strip()
        if not name:
            raise ValueError("数据集不能为空")
        if ":" in name or name.startswith(("/", "\\")):
            raise ValueError(f"数据集应为 data/ 下的相对路径: {value!r}")
        parts = name.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError(f"数据集路径不能含空段或 `..`: {value!r}")
        return name


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
    """把 data.yaml 的绝对路径收成 `D-Fire/train` 这样的可读标签。

    先按 processed 反算（pipeline 产物），再试 raw——`D-Fire/raw` 那份不在
    processed 下，只按 processed 算会退化成目录名 `D-Fire`，训练记录里就看不出
    它是哪一份了。两处都不匹配才退回目录名。
    """
    if not data_path:
        return None
    p = Path(str(data_path))
    for base, label in ((PROCESSED_DATA_DIR, None), (RAW_DATA_DIR, "D-Fire/raw")):
        try:
            rel = p.parent.relative_to(base)
        except (ValueError, OSError):
            continue
        if label is not None:
            # raw 下目前只有 D-Fire 一份；换名字要同步这里与 _resolve_dataset_yaml
            return label if rel.as_posix() == "D-Fire" else rel.as_posix()
        return rel.as_posix()
    return p.parent.name or None


# 能否续训要读 last.pt（torch.load 一次约 100ms，完整态 54MB）。按 (mtime, size)
# 记住结果，列表接口就不会每次重读。
_resume_cache: Dict[str, Any] = {}


def _resume_info(run_root: Path) -> Optional[Dict[str, Any]]:
    """读现场的 last.pt。没有或读不出来返回 None。"""
    last = run_root / "weights" / "last.pt"
    try:
        st = last.stat()
    except OSError:
        return None

    key = str(last)
    hit = _resume_cache.get(key)
    if hit is not None and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]

    from fire_sight_platform.training.service import read_checkpoint

    try:
        info: Optional[Dict[str, Any]] = read_checkpoint(last)
    except Exception:  # noqa: BLE001 - 检查点坏了就当不能续，不该让列表挂掉
        info = None
    _resume_cache[key] = (st.st_mtime, st.st_size, info)
    return info


def _resume_state(run_root: Path) -> Dict[str, Any]:
    """能不能续训、为什么不能、从第几轮接上。

    「训练已结束」与「还能续」必须分开：`Trainer.final_eval()` 会调
    `strip_optimizer(last)`，跑完的现场只剩权重——从它接着练会重置优化器与 EMA，
    学习率轨迹接不上，那不是续训而是换个权重微调。
    """
    info = _resume_info(run_root)
    if info is None:
        return {
            "resumable": False,
            "resume_note": "现场没有 last.pt，无法续训",
            "resume_done": None,
            "resume_total": None,
        }

    done = info["done_epochs"]
    total = info["total_epochs"]
    base = {"resume_done": done, "resume_total": total}

    if not info["has_optimizer"]:
        return {
            **base,
            "resumable": False,
            "resume_note": "训练已结束，检查点里的优化器状态已被剥离——只能拿该轮权重微调",
        }
    if total and done >= total:
        return {**base, "resumable": False, "resume_note": f"已跑满 {total} 轮"}
    return {**base, "resumable": True, "resume_note": ""}


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


def _int(args: Dict[str, Any], key: str) -> Optional[int]:
    """从 args.yaml 取一个整数。缺失或不是数字时返回 None。

    args.yaml 里的值理论上是 int，但它是外部文件——早先版本、手工改过的现场都
    可能有字符串。显示成 None（界面给「—」）比让整个列表接口 500 好。
    """
    raw = args.get(key)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
                batch=_int(args, "batch"),
                imgsz=_int(args, "imgsz"),
                epochs_done=curves.total_epochs,
                archived=bool(metrics),
                mAP50=_num(metrics, "mAP50"),
                mAP50_95=_num(metrics, "mAP50_95"),
                precision=_num(metrics, "precision"),
                recall=_num(metrics, "recall"),
                artifact_count=len(deps.run_artifacts(run.root)),
                created=created,
                **_resume_state(run.root),
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


@router.get("/models", response_model=List[ModelOption])
def list_model_options() -> List[ModelOption]:
    """可选的预训练模型，附本机是否已有权重。

    之前界面里「模型」是一个自由文本输入框，用户得自己记得 `yolo11s.pt` 这个
    拼写；写错了要等训练起起来才报「找不到权重」。这里把可选清单一次给全，
    并如实标出 `present`——`present=false` 表示本机没有，选了会联网下载，
    离线时就是一次明确的失败，不如提前说。

    判据在 `catalog.list_options`：受管目录与仓库根都认（详见该模块文档）。
    """
    from fire_sight_platform.training.catalog import list_options

    return [ModelOption(**vars(o)) for o in list_options()]


@router.post("/jobs", status_code=201)
def start_job(req: TrainJobRequest) -> Dict[str, Any]:
    """启动一次训练；带 `resume_run_id` 时改为从该现场断点续训。

    GPU 被占用时返回 409 并说明占用者。
    """
    config = _resume_config(req) if req.resume_run_id else _fresh_config(req)
    try:
        job = manager.start(config)
    except deps.GpuBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.public()


def _fresh_config(req: TrainJobRequest) -> Dict[str, Any]:
    """常规训练：数据集 → 显存 → 放行。"""
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

    return req.model_dump()


def _checkpoint_model(info: Dict[str, Any], run_id: str) -> str:
    """判断续训该按哪个模型规模算显存。

    **不能只看检查点的 `train_args.model`**：续训一次之后，ultralytics 会把它改写成
    `.../weights/last.pt`（`check_resume` 里 `self.args.model = self.args.resume = str(last)`）。
    于是第二次续训会把模型认成 `last` → 守卫按未知模型（yolo11m 的 3.83 倍）估 →
    一个本来合法的 batch 被拒。实测踩到：
    「batch=16 × imgsz=640 在 last 上……超过预算……imgsz=640 时 batch 最多 10」。

    `run_id` 的最后一段是模型主名（`make_run_id(dataset, model)` 拼出来的），
    它不随检查点被改写，所以优先用它。

    > 「看起来能用、第二次才坏」的典型：第一次续训正常（检查点里还是 `yolo11s.pt`），
    > 那次完成时被改写，第二次就成了未知模型。
    """
    from fire_sight_platform.training import vram

    candidate = run_id.rsplit("-", 1)[-1]
    if candidate in vram.MODEL_FACTORS:
        return f"{candidate}.pt"

    raw = Path(str(info.get("model") or "")).name
    if raw and raw not in {"last.pt", "best.pt"}:
        return raw
    return raw


def _resume_config(req: TrainJobRequest) -> Dict[str, Any]:
    """断点续训：校验检查点 → 显存（按检查点的几何）→ 放行。

    与常规训练的两处关键差别：

    - **几何与总轮数以检查点为准**，不是请求里的。ultralytics 会从
      `train_args` 重建配置，请求里那些值基本不生效（见 `_train_from_checkpoint`）。
    - **显存守卫按检查点的 imgsz/batch/model 判**。用请求里的默认值会拿 yolo11n
      的额度去放行一个 yolo11s 的续训，实测差 1.82 倍。
    """
    from fire_sight_platform.training.service import read_checkpoint
    from fire_sight_platform.training.vram import check as check_vram

    try:
        run = deps.find_training_run(req.resume_run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    last_pt = run.root / "weights" / "last.pt"
    if not last_pt.is_file():
        raise HTTPException(
            status_code=404, detail=f"该现场没有 last.pt，无法续训: {run.run_id}"
        )

    state = _resume_state(run.root)
    if not state["resumable"]:
        raise HTTPException(
            status_code=400,
            detail=f"无法从这个现场续训：{state['resume_note']}",
        )

    info = _resume_info(run.root) or {}
    imgsz = int(info.get("imgsz") or req.imgsz)
    batch = int(info.get("batch") or req.batch)
    # 模型名要能扛住「检查点被上一次续训改写」，见 _checkpoint_model
    model = _checkpoint_model(info, run.run_id)

    limits = deps.vram_limits(req.device, imgsz, model)
    if limits["checked"]:
        verdict = check_vram(
            imgsz=imgsz, batch=batch, total_mb=limits["total_mb"], model=model
        )
        if not verdict.ok:
            raise HTTPException(status_code=400, detail=verdict.message)

    config = req.model_dump()
    config.update(
        {
            # 交给子进程的是配置里的 resume，不是 resume_run_id（后者只是路由参数）
            "resume": str(last_pt),
            "imgsz": imgsz,
            "batch": batch,
            "model": model,
            # epochs 只是给界面显示总轮数用的——续训改不了它，ultralytics 取检查点里的
            "epochs": int(info.get("total_epochs") or req.epochs),
            "dataset": _dataset_label(info.get("data")) or req.dataset,
            "project_name": "fire-sight",
            "experiment": "",
        }
    )
    return config


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
