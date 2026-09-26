"""推理编排：帧源 → YOLO → scene → 绘制/告警(组合各模块，保持薄)。"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from fire_sight_platform.common.paths import DATA_DIR

logger = logging.getLogger(__name__)


@dataclass
class InferConfig:
    source: str
    weights: str
    conf: float = 0.25
    iou: float = 0.6
    device: str = "0"
    camera_name: str = ""
    confirm_frames: int = 3
    cooldown_sec: float = 30.0
    buffer_frames: int = 90
    enable_tts: bool = True
    save_annotated_dir: Optional[str] = None
    events_db: Optional[str] = None
    max_frames: Optional[int] = None


@dataclass
class InferStats:
    frames: int = 0
    raw_dets: int = 0
    alerts: int = 0
    events: List[dict] = field(default_factory=list)


def _xyxy_from_ultralytics(boxes) -> List[tuple]:
    out = []
    for b in boxes:
        xyxy = b.xyxy[0].tolist()
        out.append(tuple(float(x) for x in xyxy))
    return out


def run_inference(cfg: InferConfig) -> InferStats:
    """执行一轮推理；模块间通过类型/回调协作，不互相 import 内部实现。"""
    import cv2

    from fire_sight.alerting import EventStore, RingClipBuffer, TtsNotifier
    from fire_sight.alerting.clip_buffer import write_clip
    from fire_sight.scene import AlertPolicy, DetectionLite, FrameObservation, MultiFrameConfirm
    from frame_source import create_frame_source
    from visualization import BeautifyVisualizer, Detection

    from ultralytics import YOLO

    model = YOLO(cfg.weights)
    names = model.names if isinstance(model.names, dict) else {i: n for i, n in enumerate(model.names)}

    visualizer = BeautifyVisualizer()
    confirm = MultiFrameConfirm(confirm_frames=cfg.confirm_frames)
    policy = AlertPolicy(cooldown_sec=cfg.cooldown_sec, location=cfg.camera_name)
    buffer = RingClipBuffer(max_frames=cfg.buffer_frames)
    tts = TtsNotifier(enabled=cfg.enable_tts)

    db_path = cfg.events_db or (DATA_DIR / "events" / "events.db")
    store = EventStore(db_path)
    clip_dir = Path(db_path).parent / "clips"

    if cfg.save_annotated_dir:
        Path(cfg.save_annotated_dir).mkdir(parents=True, exist_ok=True)

    stats = InferStats()
    tail_queue: List = []  # 告警后收集少量帧

    with create_frame_source(cfg.source, source_id=cfg.camera_name or str(cfg.source)) as source:
        for frame in source:
            if cfg.max_frames is not None and stats.frames >= cfg.max_frames:
                break
            stats.frames += 1
            buffer.push(frame.index, frame.timestamp, frame.image)

            results = model.predict(
                source=frame.image,
                conf=cfg.conf,
                iou=cfg.iou,
                device=cfg.device,
                verbose=False,
            )
            r = results[0]
            lite: List[DetectionLite] = []
            vis_dets: List[Detection] = []
            if r.boxes is not None and len(r.boxes) > 0:
                for b in r.boxes:
                    cls_id = int(b.cls[0])
                    name = str(names.get(cls_id, cls_id))
                    conf = float(b.conf[0])
                    x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                    lite.append(DetectionLite(name=name, conf=conf, box=(x1, y1, x2, y2)))
                    vis_dets.append(
                        Detection(cls_id=cls_id, name=name, conf=conf, x1=x1, y1=y1, x2=x2, y2=y2)
                    )
                    stats.raw_dets += 1

            confirmed = confirm.update(FrameObservation(frame.index, lite))
            if confirmed and policy.should_fire(confirmed):
                conf = max((d.conf for d in lite if d.name in confirmed), default=0.0)
                event = policy.fire(confirmed, conf=conf, frame_index=frame.index)
                # 导出缓冲 + 后续几帧
                tail = list(buffer.snapshot())[-cfg.buffer_frames :]
                clip_name = f"{event.event_id}.mp4"
                clip_path = clip_dir / clip_name
                try:
                    write_clip(tail, clip_path, fps=25.0)
                    clip_str = str(clip_path)
                except Exception as exc:
                    logger.warning(f"片段写出失败: {exc}")
                    clip_str = ""
                store.insert(event, clip_path=clip_str)
                tts.speak(event.message)
                stats.alerts += 1
                stats.events.append(
                    {
                        "event_id": event.event_id,
                        "message": event.message,
                        "clip": clip_str,
                        "frame_index": event.frame_index,
                    }
                )
                logger.info(f"[告警] {event.message} clip={clip_str}")

            annotated = visualizer.draw(frame.image, vis_dets)
            if cfg.save_annotated_dir:
                out_p = Path(cfg.save_annotated_dir) / f"{frame.index:06d}.jpg"
                cv2.imwrite(str(out_p), annotated)

    store.close()
    logger.info(
        f"推理结束 frames={stats.frames} raw_dets={stats.raw_dets} alerts={stats.alerts}"
    )
    return stats
