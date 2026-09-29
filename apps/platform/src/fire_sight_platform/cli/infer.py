"""fs-infer: 多源推理 + 火情告警 CLI。"""

from __future__ import annotations

import argparse
import logging
import sys

from fire_sight_platform.common.logging_utils import get_logger
from fire_sight_platform.common.paths import LOGGING_DIR
from fire_sight_platform.inference.service import InferConfig, run_inference

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs-infer", description="视频/摄像头/RTSP 推理与告警")
    p.add_argument("--source", required=True, help="视频路径/图片夹/摄像头号/rtsp://")
    p.add_argument("--weights", required=True, help="best.pt 路径")
    p.add_argument("--conf", type=float, default=0.25)
    p.add_argument("--iou", type=float, default=0.6)
    p.add_argument("--device", default="0")
    p.add_argument("--camera-name", default="", help="位置名，写入 TTS/事件")
    p.add_argument("--confirm-frames", type=int, default=3)
    p.add_argument("--cooldown", type=float, default=30.0, help="告警冷却秒")
    p.add_argument("--clip-before", type=float, default=30.0, help="片段告警前秒数")
    p.add_argument("--clip-after", type=float, default=30.0, help="片段告警后秒数")
    p.add_argument("--no-tts", action="store_true", help="关闭语音")
    p.add_argument("--save-dir", default=None, help="标注图输出目录")
    p.add_argument("--events-db", default=None)
    p.add_argument("--max-frames", type=int, default=None, help="最多处理帧数(调试)")
    return p


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    get_logger(base_path=LOGGING_DIR, log_type="infer")
    cfg = InferConfig(
        source=args.source,
        weights=args.weights,
        conf=args.conf,
        iou=args.iou,
        device=args.device,
        camera_name=args.camera_name,
        confirm_frames=args.confirm_frames,
        cooldown_sec=args.cooldown,
        clip_before_sec=args.clip_before,
        clip_after_sec=args.clip_after,
        enable_tts=not args.no_tts,
        save_annotated_dir=args.save_dir,
        events_db=args.events_db,
        max_frames=args.max_frames,
    )
    try:
        stats = run_inference(cfg)
    except Exception as exc:
        logger.error(f"推理失败: {exc}", exc_info=True)
        return 1
    logger.info(f"完成: frames={stats.frames} alerts={stats.alerts}")
    return 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
