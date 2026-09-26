"""训练子进程：跑一次 train_yolo，把逐轮进度以 JSON 行写回父进程。

为什么是子进程而不是线程：训练崩了不该把 API 一起带走。父进程读管道转 SSE，
并在进程退出时释放 GPU 锁。

协议 —— 每行一个 JSON 对象，写在**真正的** stdout 上：

    {"type":"start", "epochs": 50, "dataset": "demo_fire"}
    {"type":"epoch", "epoch": 1, "epochs": 50, "metrics": {...}, "losses": {...}}
    {"type":"done",  "run_id": "...", "best_pt": "..."}
    {"type":"error", "message": "..."}

ultralytics 自己会往 stdout 打大量日志，所以本模块导入的第一件事就是把
sys.stdout 改道到 stderr——父进程照常把 stderr 当日志流转发，协议流保持干净。
改道必须发生在 import ultralytics **之前**，否则它的日志 handler 已经绑定了
原来的 stdout。

用法：python -m fire_sight.train_worker <config.json>
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

_PROTOCOL = sys.stdout
sys.stdout = sys.stderr  # 越早越好：下面所有 import 的日志都会落到 stderr

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)


def emit(payload: Dict[str, Any]) -> None:
    """写一行协议。必须 flush——父进程按行读，缓冲住就等于卡死。"""
    _PROTOCOL.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _PROTOCOL.flush()


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        emit({"type": "error", "message": "缺少配置文件路径"})
        return 2

    try:
        cfg = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        emit({"type": "error", "message": f"配置读取失败: {exc}"})
        return 2

    try:
        from fire_sight_platform.runtime_config.base import TrainConfig
        from fire_sight_platform.training.service import train_yolo

        config = TrainConfig(**cfg)
        emit({"type": "start", "epochs": config.epochs, "dataset": config.dataset})

        result = train_yolo(config, on_epoch=lambda info: emit({"type": "epoch", **info}))

        emit(
            {
                "type": "done",
                "run_id": result.run_id,
                "best_pt": str(result.best_pt),
                "save_dir": str(result.save_dir),
            }
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - 任何失败都要让父进程知道，不要只留个退出码
        logging.exception("训练失败")
        emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
