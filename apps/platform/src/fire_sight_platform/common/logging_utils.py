"""高度可配置的日志工具,创建独立日志文件 + 彩色控制台输出。

设计哲学:
  - 业务模块: 顶部一行 logger = logging.getLogger(__name__), 不配 handler
  - 本模块: 提供 get_logger(), 把 handler 挂到根 logger "fire_sight_platform" 上
  - CLI 入口: 调用一次 get_logger() 完成 handler 装配
"""

import logging
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# 项目根 logger 名 = 顶层 Python 包名
ROOT_LOGGER_NAME: str = "fire_sight_platform"

try:
    from colorlog import ColoredFormatter

    _HAS_COLORLOG = True
except ImportError:
    _HAS_COLORLOG = False


def _default_stem(log_type: str) -> str:
    """生成默认日志文件主干名: type-YYYYMMDD-HHMMSS-ffffff。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return f"{log_type}-{ts}"


def get_logger(
    base_path: Path,
    log_type: str = "general",
    model_name: Optional[str] = None,
    log_level: int = logging.INFO,
    temp_log: bool = False,
    encoding: str = "utf-8",
    logger_name: str = ROOT_LOGGER_NAME,
    run_id: Optional[str] = None,
    dataset_name: Optional[str] = None,
) -> logging.Logger:
    """配置项目【根 logger】, 挂上 console + file handler。

    重复调用幂等(检查 handlers 是否已配置, 避免重复挂)。
    """
    logger = logging.getLogger(logger_name)
    if logger.handlers:
        return logger

    logger.setLevel(log_level)
    logger.propagate = False

    log_dir = Path(base_path) / log_type
    log_dir.mkdir(parents=True, exist_ok=True)

    parts = []
    if temp_log:
        parts.append("temp")
    if dataset_name:
        parts.append(dataset_name)
    if model_name:
        parts.append(model_name)
    if run_id:
        parts.append(run_id)
    stem = "-".join(parts) if parts else _default_stem(log_type)
    if not temp_log and not (dataset_name or model_name or run_id):
        stem = _default_stem(log_type)

    log_file = log_dir / f"{stem}.log"

    fmt_plain = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    fmt_color = (
        "%(log_color)s%(asctime)s | %(levelname)-7s%(reset)s | "
        "%(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(log_file, encoding=encoding)
    file_handler.setFormatter(logging.Formatter(fmt_plain))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    if _HAS_COLORLOG:
        console_handler.setFormatter(
            ColoredFormatter(
                fmt_color,
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    else:
        console_handler.setFormatter(logging.Formatter(fmt_plain))
    logger.addHandler(console_handler)

    logger.info(f"日志已装配 → {log_file}")
    logger.info(f"主机: {platform.node()} | Python {platform.python_version()}")
    return logger
