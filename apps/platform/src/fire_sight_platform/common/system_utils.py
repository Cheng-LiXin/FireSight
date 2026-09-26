"""环境快照——OS / Python / PyTorch / CPU / GPU / 内存。"""

from __future__ import annotations

import logging
import os
import platform
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _format_size(bytes_size: Any) -> str:
    """简洁的字节单位格式化。"""
    if not bytes_size or not isinstance(bytes_size, (int, float)):
        return "N/A"
    if bytes_size >= 1024**3:
        return f"{bytes_size / (1024 ** 3):.2f} GB"
    if bytes_size >= 1024**2:
        return f"{bytes_size / (1024 ** 2):.2f} MB"
    return f"{bytes_size / 1024:.2f} KB"


def get_basic_device_info() -> Dict[str, Any]:
    """返回结构化的环境信息字典。"""
    cpu_name = platform.processor() or platform.machine() or "Unknown CPU"
    cpu_cores = os.cpu_count() or "Unknown"

    try:
        import psutil

        memory = psutil.virtual_memory()
        total_ram = _format_size(memory.total)
        available_ram = _format_size(memory.available)
        ram_usage = f"{memory.percent:.1f}%"
    except ImportError:
        total_ram = "Unknown (psutil 未安装)"
        available_ram = "Unknown"
        ram_usage = "Unknown"

    try:
        import torch

        torch_version = torch.__version__
        cuda_available = torch.cuda.is_available()
        gpu_count = torch.cuda.device_count() if cuda_available else 0
        _torch = torch
    except ImportError:
        torch_version = "Unknown (torch 未安装)"
        cuda_available = False
        gpu_count = 0
        _torch = None

    try:
        from ultralytics import __version__ as ultralytics_version
    except ImportError:
        ultralytics_version = "Unknown (ultralytics 未安装)"

    gpu_info: Dict[str, Any] = {
        "CUDA可用": cuda_available,
        "GPU数量": gpu_count,
    }
    if cuda_available and _torch is not None:
        for i in range(gpu_count):
            gpu_info[f"GPU_{i}"] = _torch.cuda.get_device_name(i)
            gpu_info[f"GPU_{i}_显存"] = _format_size(
                _torch.cuda.get_device_properties(i).total_memory
            )

    return {
        "系统信息": {
            "OS": platform.platform(),
            "Python": platform.python_version(),
            "CPU": cpu_name,
            "CPU核心": cpu_cores,
            "总内存": total_ram,
            "可用内存": available_ram,
            "内存占用": ram_usage,
        },
        "深度学习": {
            "PyTorch": torch_version,
            "Ultralytics": ultralytics_version,
        },
        "GPU信息": gpu_info,
    }


def log_device_info() -> Dict[str, Any]:
    """打印并返回环境快照。"""
    info = get_basic_device_info()
    logger.info("环境快照".center(60, "="))
    for section, payload in info.items():
        logger.info(f"【{section}】")
        if isinstance(payload, dict):
            for k, v in payload.items():
                logger.info(f"  {k}: {v}")
        else:
            logger.info(f"  {payload}")
    return info
