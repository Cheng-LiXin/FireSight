"""API 共享依赖：工作区路径、事件库位置、GPU 任务互斥。

单一消费点，页面/路由不各自解析路径，避免出现第二份真相。
"""

from __future__ import annotations

import os
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fire_sight_platform.common.paths import (
    DATA_DIR,
    MODELS_DIR,
    ROOT_DIR,
    RUNS_DIR,
    TRAINED_MODELS_DIR,
)

# 事件库：默认 data/events/events.db，可用环境变量覆盖（便于测试与多实例）
EVENTS_DB: Path = Path(
    os.environ.get("FIRE_SIGHT_EVENTS_DB", str(DATA_DIR / "events" / "events.db"))
)


class GpuLock:
    """显存互斥：8GB 下训练与推理不能并行。

    不做限制的后果是 CUDA OOM 在演示现场炸掉，所以这里明确串行化，
    并保留持锁者与排队者信息，供前端提示「训练进行中，可排队」。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: Optional[str] = None
        self._waiting: List[str] = []

    def acquire(self, who: str, blocking: bool = False) -> bool:
        """尝试获取。blocking=False 时拿不到立即返回 False（不排队）。"""
        if not blocking:
            if self._lock.acquire(blocking=False):
                self._holder = who
                return True
            return False
        self._waiting.append(who)
        try:
            self._lock.acquire()
            self._holder = who
            return True
        finally:
            if who in self._waiting:
                self._waiting.remove(who)

    def release(self) -> None:
        self._holder = None
        if self._lock.locked():
            self._lock.release()

    @property
    def holder(self) -> Optional[str]:
        return self._holder

    @property
    def waiting(self) -> List[str]:
        return list(self._waiting)

    def status(self) -> Dict[str, Any]:
        return {"holder": self._holder, "waiting": self.waiting, "busy": self._holder is not None}


GPU_LOCK = GpuLock()


def gpu_info() -> Dict[str, Any]:
    """GPU 状态。优先 nvidia-smi（~50ms），避免为此 import torch（数秒）。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return {"available": False, "reason": out.stderr.strip() or "nvidia-smi 返回非零"}
        first = out.stdout.strip().splitlines()[0]
        name, total, used, util = [p.strip() for p in first.split(",")]
        return {
            "available": True,
            "name": name,
            "memory_total_mb": int(total),
            "memory_used_mb": int(used),
            "utilization_pct": int(util),
        }
    except FileNotFoundError:
        return {"available": False, "reason": "未找到 nvidia-smi"}
    except Exception as exc:  # noqa: BLE001 - 状态接口不应因探测失败而 500
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


@dataclass(frozen=True)
class ArchivedRun:
    """一次归档训练。run_id 是相对 trained/ 的路径，故两种布局都能唯一标识。"""

    run_id: str
    root: Path  # 含 best.pt 的目录
    best_pt: Path


def archived_runs() -> List[ArchivedRun]:
    """归档训练，按 run_id 新→旧。

    历史上有两种布局，都必须认，否则整轮实验会凭空消失：

        trained/<run_id>/best.pt
        trained/<run_id>/<experiment>/best.pt   # 早期 archive 多套一层

    只 glob `*/best.pt` 会漏掉后者——最新一轮恰恰是这种。
    """
    if not TRAINED_MODELS_DIR.exists():
        return []

    runs: Dict[str, ArchivedRun] = {}
    for best in TRAINED_MODELS_DIR.rglob("best.pt"):
        root = best.parent
        if root == TRAINED_MODELS_DIR:
            continue
        run_id = root.relative_to(TRAINED_MODELS_DIR).as_posix()
        # 同名时优先浅层布局，避免把内层当成独立 run
        if run_id not in runs:
            runs[run_id] = ArchivedRun(run_id=run_id, root=root, best_pt=best)

    return sorted(runs.values(), key=lambda r: r.run_id, reverse=True)


def active_run() -> Optional[ArchivedRun]:
    """当前权重：取最新一个归档。前端「设为当前」后续接入显式选择。"""
    runs = archived_runs()
    return runs[0] if runs else None


def workspace_info() -> Dict[str, Any]:
    return {
        "root": str(ROOT_DIR),
        "data_dir": str(DATA_DIR),
        "models_dir": str(MODELS_DIR),
        "events_db": str(EVENTS_DB),
        "events_db_exists": EVENTS_DB.exists(),
    }


# --------------------------------------------------------------------------
# GPU 任务
# --------------------------------------------------------------------------


class GpuBusy(RuntimeError):
    """GPU 已被别的任务占用。携带占用者，便于前端给出可读提示。"""

    def __init__(self, holder: Optional[str]) -> None:
        self.holder = holder
        super().__init__(f"GPU 正被「{holder or '其他任务'}」占用")


@contextmanager
def gpu_job(who: str):
    """占用 GPU 执行任务；已被占用则立刻抛出，不排队。

    8GB 显存下训练与推理不能并行，不拦就是现场 CUDA 溢出。
    """
    if not GPU_LOCK.acquire(who):
        raise GpuBusy(GPU_LOCK.holder)
    try:
        yield
    finally:
        GPU_LOCK.release()


def resolve_device(requested: str) -> str:
    """设备回退：请求 GPU 但显卡不可用时降级到 CPU，而不是让 CUDA 报错。"""
    if str(requested).lower() == "cpu":
        return "cpu"
    return str(requested) if gpu_info().get("available") else "cpu"


# --------------------------------------------------------------------------
# 显存预算
# --------------------------------------------------------------------------


def vram_limits(device: str, imgsz: int, model: str = "yolo11n.pt") -> Dict[str, Any]:
    """给定设备、imgsz 与模型，本机允许的最大 batch。

    预算模型、标定点与**模型规模因子**的实测依据见
    `fire_sight_platform.training.vram`。

    探测不到显卡（或显式选了 CPU）时一律不拦：探测失败是环境问题，
    不该变成训练的拦路石。拦错比不拦更烦人。
    """
    from fire_sight_platform.training import vram

    stem = vram.model_stem(model)
    if str(device).lower() == "cpu":
        return {"checked": False, "reason": "cpu", "max_batch": None, "model": stem}

    info = gpu_info()
    if not info.get("available"):
        return {
            "checked": False,
            "reason": info.get("reason", "GPU 不可用"),
            "max_batch": None,
            "model": stem,
        }

    total = int(info["memory_total_mb"])
    factor, measured = vram.model_factor(model)
    return {
        "checked": True,
        "total_mb": total,
        "px_budget": vram.px_budget(total),
        "max_batch": vram.max_batch(imgsz, total, model),
        "model": stem,
        "factor": factor,
        # False 表示这个模型规模没有实测依据，上限是估的——界面要说明
        "model_measured": measured,
        "reference": vram.REFERENCE_NOTE,
    }


# --------------------------------------------------------------------------
# 模型缓存
# --------------------------------------------------------------------------

_INFER_CACHE: Dict[Tuple[str, str], Any] = {}
_INFER_CACHE_LOCK = threading.Lock()


def get_infer_service(weights: Path | str, device: str) -> Any:
    """按 (weights, device) 复用 InferService。

    必须缓存：InferService 的模型是实例级懒加载的，每次请求新建实例就会
    重新读一遍权重，单图推理要等上一两秒。conf / iou 由调用方逐次传入。
    """
    key = (str(weights), str(device))
    with _INFER_CACHE_LOCK:
        svc = _INFER_CACHE.get(key)
        if svc is None:
            from fire_sight.services import InferService

            svc = InferService(weights=key[0], device=key[1], conf=0.25, iou=0.6)
            _INFER_CACHE[key] = svc
    return svc


def clear_infer_cache() -> None:
    """释放已加载的模型（权重被替换或删除后调用）。"""
    with _INFER_CACHE_LOCK:
        _INFER_CACHE.clear()


def training_runs() -> List[ArchivedRun]:
    """runs/training 下每次训练的现场记录，新→旧。

    判据是 results.csv 存在——只有它才能画出曲线。run_id 是相对
    runs/training 的路径，与归档侧的 run_id 同构，两边可直接对应。
    """
    root = RUNS_DIR / "training"
    if not root.is_dir():
        return []

    out: List[ArchivedRun] = []
    for csv_path in root.rglob("results.csv"):
        run_root = csv_path.parent
        if run_root == root:
            continue
        run_id = run_root.relative_to(root).as_posix()
        best = run_root / "weights" / "best.pt"
        out.append(
            ArchivedRun(
                run_id=run_id,
                root=run_root,
                best_pt=best if best.exists() else run_root / "best.pt",
            )
        )
    return sorted(out, key=lambda r: r.run_id, reverse=True)


def find_training_run(run_id: str) -> ArchivedRun:
    """按 run_id 取训练现场。只认已发现的，不接受任意路径。"""
    for run in training_runs():
        if run.run_id == run_id:
            return run
    raise FileNotFoundError(f"未找到训练记录: {run_id}")


# 现场图件的用途分类：前端据此分组展示，而不是把一堆文件名平铺出来
_ARTIFACT_KINDS = (
    ("confusion_matrix", "confusion"),
    ("results.png", "curves"),
    ("BoxPR_curve", "pr"),
    ("BoxF1_curve", "pr"),
    ("BoxP_curve", "pr"),
    ("BoxR_curve", "pr"),
    ("labels.jpg", "labels"),
    ("train_batch", "batch"),
    ("val_batch", "batch"),
)
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def run_artifacts(run_root: Path) -> List[Dict[str, Any]]:
    """训练现场的图件清单。只列顶层文件，不递归进 weights/。"""
    out: List[Dict[str, Any]] = []
    if not run_root.is_dir():
        return out
    for p in sorted(run_root.iterdir()):
        if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        kind = "other"
        for needle, label in _ARTIFACT_KINDS:
            if needle in p.name:
                kind = label
                break
        out.append({"name": p.name, "kind": kind, "size": p.stat().st_size})
    return out


def resolve_run(name: Optional[str]) -> ArchivedRun:
    """把前端传来的 run_id 解析成归档训练（含权重路径）。

    返回整个 ArchivedRun 而不是只有路径：run_id 无法从路径反推——
    嵌套布局下 best.pt 的父目录是实验名，不是 run_id。

    name 为空时取最新归档。只接受已归档的 run，不接受任意路径，
    避免接口被用来读取工作区外的文件。
    """
    if not name:
        active = active_run()
        if active is None:
            raise FileNotFoundError("尚无已归档的模型，请先运行 fs-train")
        return active

    for run in archived_runs():
        if run.run_id == name:
            return run
    raise FileNotFoundError(f"未找到归档模型: {name}")
