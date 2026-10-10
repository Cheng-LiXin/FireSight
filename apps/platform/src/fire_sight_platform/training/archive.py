"""训练产物归档:runs 现场 + models/trained 精选。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fire_sight_platform.common.naming import sanitize
from fire_sight_platform.common.paths import RUNS_DIR, TRAINED_MODELS_DIR

logger = logging.getLogger(__name__)


def make_run_id(dataset: str, model: str) -> str:
    """实验标识，用作归档目录名。

    两段都过 sanitize：数据集名可能带斜杠（如 "D-Fire/train"），
    直接拼进路径会让归档多出一层嵌套目录，那些实验会被按
    `trained/*/best.pt` 查找的旧代码整轮漏掉——实际踩过这个坑。
    """
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{sanitize(dataset)}-{sanitize(Path(model).stem)}"


def archive_best_weight(
    best_pt: Path,
    run_id: str,
    config_text: str = "",
    metrics_text: str = "",
) -> Path:
    """把 best.pt 与配置/指标快照复制到 models/trained/<run_id>/。"""
    best_pt = Path(best_pt)
    if not best_pt.exists():
        raise FileNotFoundError(f"找不到 best.pt: {best_pt}")
    # run_id 可能来自用户输入的 experiment（见 service.run_training 的说明），
    # 任由 `..` 进来就会把归档写到 models/trained 之外——而归档扫描只认
    # 这一层目录，那些权重此后就成了没人管得到的孤儿。这里按目录名单段收口。
    if run_id in {"", ".", ".."} or "/" in run_id or "\\" in run_id or ":" in run_id:
        raise ValueError(f"归档名只能是单个目录名，不含路径分隔符: {run_id!r}")
    out = TRAINED_MODELS_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    dest_pt = out / "best.pt"
    shutil.copy2(best_pt, dest_pt)
    if config_text:
        (out / "train_config.yaml").write_text(config_text, encoding="utf-8")
    if metrics_text:
        (out / "metrics.txt").write_text(metrics_text, encoding="utf-8")
    logger.info(f"已归档精选权重: {dest_pt}")
    return dest_pt
