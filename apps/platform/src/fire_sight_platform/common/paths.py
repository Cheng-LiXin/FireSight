#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""集中定义项目路径常量。

- 使用 marker file 模式定位 ROOT_DIR(workspace 根)
- 引入 APP_DIR 概念,把【共享资产】和【端私有资产】分开
"""

import os
from pathlib import Path
from typing import List, Tuple

# ============================================================
# Workspace 根目录定位 (marker file 模式)
# ============================================================
WORKSPACE_MARKER: str = ".firesight-workspace"


def _find_workspace_root(
    start: Path,
    markers: Tuple[str, ...] = (WORKSPACE_MARKER,),
) -> Path:
    """从 start 开始沿父目录向上查找,返回包含任一 marker 文件的目录。

    Args:
        start: 起始路径(通常是 Path(__file__))
        markers: 一组 marker 文件名,任一存在即视为找到

    Returns:
        Path: workspace 根目录

    Raises:
        FileNotFoundError: 一直爬到文件系统根仍没找到
    """
    current = start.resolve()
    if current.is_file():
        current = current.parent

    for parent in [current, *current.parents]:
        for marker in markers:
            if (parent / marker).exists():
                return parent

    raise FileNotFoundError(
        f"找不到 workspace marker 文件 ({markers})。"
        f"请确认仓库根存在 {WORKSPACE_MARKER} 文件。"
    )


# 计算 ROOT_DIR(模块加载时执行一次)
ROOT_DIR: Path = _find_workspace_root(Path(__file__))

# 顶层能力包(frame_source / visualization)与参考项目一致：不单独发 wheel，
# 挂在工作区根上；导入 fire_sight_platform 时把 ROOT_DIR 放进 sys.path。
import sys

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# ============================================================
# 端根目录 APP_DIR (platform 这一个端的根)
# ============================================================
APP_DIR: Path = ROOT_DIR / "apps" / "platform"
# 应用端根(火灾烟雾场景包)
APP_FIRE_SIGHT_DIR: Path = ROOT_DIR / "apps" / "fire-sight"


# ============================================================
# 【共享资产】(在 ROOT_DIR 下,所有端可访问)
# ============================================================
DATA_DIR: Path = ROOT_DIR / "data"
MODELS_DIR: Path = ROOT_DIR / "models"
RUNS_DIR: Path = ROOT_DIR / "runs"

PRETRAINED_MODELS_DIR: Path = MODELS_DIR / "pretrained"
TRAINED_MODELS_DIR: Path = MODELS_DIR / "trained"
CHECKPOINTS_DIR: Path = TRAINED_MODELS_DIR

# 「删除」归档与训练现场时的落脚点：**移进来，不是抹掉**。
# 权重是几十分钟到两小时的产物，误点一下就永久消失的代价太大。移进这里之后
# 页面上不再出现、磁盘不立即释放，代价换来的是「删错了能捞回来」。
# 想真正释放空间，由用户手工清空这个目录——这个决定故意不自动化。
TRASH_DIR: Path = MODELS_DIR / ".trash"

# 设计依据见 D0 决定 5:原始(只读)与派生(可再生)分家。
RAW_DATA_DIR: Path = DATA_DIR / "raw"
PROCESSED_DATA_DIR: Path = DATA_DIR / "processed"


# ============================================================
# 【运行时目录】会外溢到用户目录/系统盘的缓存与临时文件都收敛到这里
# ============================================================
# 为什么需要它:ultralytics 把 runs_dir / weights_dir / datasets_dir 存在用户
# 配置目录里(Windows 上是 %APPDATA%\Ultralytics\settings.json),而那份配置是
# 机器级的、跟本仓库无关。任何没显式传 project 的调用——例如 fs-val 的
# model.val()、AMP 自检下载的 yolo26n.pt——都会落到那份配置指向的路径上。
# 把 YOLO_CONFIG_DIR 连同 TEMP / 各类缓存一起改道进工作区,整条链路就不再
# 写系统盘。改道动作见 common/runtime_env.py。
RUNTIME_DIR: Path = Path(
    os.environ.get("FIRESIGHT_RUNTIME_DIR") or (ROOT_DIR / ".runtime")
).expanduser()
RUNTIME_TMP_DIR: Path = RUNTIME_DIR / "tmp"
RUNTIME_CACHE_DIR: Path = RUNTIME_DIR / "cache"
RUNTIME_ULTRALYTICS_DIR: Path = RUNTIME_DIR / "ultralytics"
# 评估现场(混淆矩阵 / PR 曲线)是过程副产物,没人读它,不进 runs/
RUNTIME_EVAL_DIR: Path = RUNTIME_DIR / "evaluation"


# ============================================================
# 【端私有资产】(在 APP_DIR 中,只属于 platform 这个端)
# ============================================================
CONFIGS_DIR: Path = APP_DIR / "configs"
LOGGING_DIR: Path = APP_DIR / "logging"
UNIT_TEST_DIR: Path = APP_DIR / "tests"


# ============================================================
# 【顶层文档目录】
# ============================================================
DOCS_DIR: Path = ROOT_DIR / "docs"


def get_dirs_to_initialize() -> List[Path]:
    """返回需要在项目初始化时确保存在的核心目录列表。"""
    return [
        RAW_DATA_DIR,
        PROCESSED_DATA_DIR,
        PRETRAINED_MODELS_DIR,
        TRAINED_MODELS_DIR,
        TRASH_DIR,
        RUNS_DIR,
        RUNTIME_DIR,
        CONFIGS_DIR,
        LOGGING_DIR,
        APP_DIR / "configs" / "datasets",
        APP_DIR / "configs" / "runtime",
    ]
