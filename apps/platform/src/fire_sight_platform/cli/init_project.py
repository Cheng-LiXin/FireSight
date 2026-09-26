#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""项目自动初始化——目录创建 + 数据集状态检查。"""

import logging
from pathlib import Path
from typing import List

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import (
    LOGGING_DIR,
    RAW_DATA_DIR,
    ROOT_DIR,
    get_dirs_to_initialize,
)
from fire_sight_platform.common.performance_utils import time_it
from fire_sight_platform.common.system_utils import log_device_info

LINE_WIDTH: int = 60

logger = logging.getLogger(__name__)


def _check_raw_data_status() -> List[str]:
    """检查 raw 数据目录的状态。"""
    raw_status: List[str] = []
    rel_raw = RAW_DATA_DIR.relative_to(ROOT_DIR)

    if not RAW_DATA_DIR.exists():
        logger.warning(
            f"原始数据集根目录不存在: {RAW_DATA_DIR}\n"
            f"请在该目录下创建以「数据集名称」命名的文件夹。"
        )
        raw_status.append(f"{rel_raw} 不存在 → 请创建并放入数据集")
    elif not any(RAW_DATA_DIR.iterdir()):
        logger.warning(
            f"原始数据集根目录为空: {RAW_DATA_DIR}\n"
            f"预期结构:\n"
            f"  {rel_raw}/<数据集名>/\n"
            f"  ├── images/\n"
            f"  └── annotations/"
        )
        raw_status.append(f"{rel_raw} 为空 → 请放入至少一个数据集")
    else:
        sub_dirs = [p for p in RAW_DATA_DIR.iterdir() if p.is_dir()]
        logger.info(f"原始数据集根目录就绪, 检测到 {len(sub_dirs)} 个数据集文件夹")
        raw_status.append(f"{rel_raw} 就绪(包含 {len(sub_dirs)} 个数据集)")
        for sub in sorted(sub_dirs):
            raw_status.append(f"  · {sub.name}")
    return raw_status


@time_it(iterations=1, name="项目初始化", logger_instance=logger)
def initialize_project() -> None:
    """初始化项目核心目录 + 检查原始数据状态。

    本函数是 CLI 入口——负责装配根 logger 的 handler。
    """
    get_logger(
        base_path=LOGGING_DIR,
        log_type="init_project",
        temp_log=False,
    )

    logger.info("开始初始化项目核心目录".center(LINE_WIDTH, "="))
    logger.info(f"项目核心目录为: {ROOT_DIR}")

    log_device_info()

    logger.info("检查并创建项目核心目录".center(LINE_WIDTH, "="))

    created: List[Path] = []
    existed: List[Path] = []

    for d in get_dirs_to_initialize():
        if d.exists():
            existed.append(d)
            logger.info(f"已存在: {d.relative_to(ROOT_DIR)}")
        else:
            d.mkdir(parents=True, exist_ok=True)
            created.append(d)
            logger.info(f"已创建: {d.relative_to(ROOT_DIR)}")

    logger.info(f"目录检查完成: 新建 {len(created)} 个, 已存在 {len(existed)} 个")

    logger.info("原始数据状态".center(LINE_WIDTH, "="))
    for line in _check_raw_data_status():
        logger.info(line)

    logger.info("初始化完成".center(LINE_WIDTH, "="))


def main() -> None:
    """控制台脚本入口。"""
    initialize_project()


if __name__ == "__main__":
    main()
