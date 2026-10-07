"""推理编排：帧源 → YOLO → scene → 绘制/告警(组合各模块，保持薄)。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, List, Optional

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
    # 片段时长直接用秒——需求口径是「前后 30 秒」，帧数换算见 run_inference
    clip_before_sec: float = 30.0
    clip_after_sec: float = 30.0
    # 源没有标称帧率（图片目录）时的兜底，与 write_clip 的历史默认一致
    clip_fps: float = 25.0
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


def _finalize_stopped_clips(executor, writing: List[tuple], store) -> None:
    """停止路径的后台收尾：等片段写完再回填路径，最后关 store。

    停止时主流程不等编码（否则状态要滞留几十秒），但事件早已入库，
    回填只是补一个 clip 字段——放到后台做完，既不挡停止，也不丢回填。
    store 必须活到这段结束，所以 close() 挪到这里而不是函数末尾。
    """
    try:
        executor.shutdown(wait=True)
        for event_id, entry, fut in list(writing):
            try:
                written = fut.result()
                clip_str = str(written) if written else ""
            except Exception as exc:  # noqa: BLE001 - 单段失败不该影响停止
                logger.warning(f"片段写出失败，本次不记片段: {exc}")
                clip_str = ""
            if clip_str:
                try:
                    store.set_clip(event_id, clip_str)
                    entry["clip"] = clip_str
                except Exception as exc:  # noqa: BLE001 - 关库竞态也不该抛到线程外
                    logger.warning(f"片段路径回填失败: {exc}")
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass


def run_inference(
    cfg: InferConfig,
    frame_sink: Optional[Callable[[Any], None]] = None,
    stop_event: Optional[Any] = None,
) -> InferStats:
    """执行一轮推理；模块间通过类型/回调协作，不互相 import 内部实现。

    frame_sink：每帧标注图回调，供常驻监控推流给浏览器；CLI 不传。
    stop_event：置位即收流退出（threading.Event），常驻监控的停止信号。
    """
    import cv2
    from frame_source import create_frame_source
    from visualization import BeautifyVisualizer, Detection

    from fire_sight.alerting import ClipAssembler, EventStore, RingClipBuffer, get_tts
    from fire_sight.scene import AlertPolicy, DetectionLite, FrameObservation, MultiFrameConfirm
    from fire_sight_platform.common.runtime_env import (
        apply_runtime_env,
        pin_ultralytics_paths,
    )

    # 推理本身不写 runs,但 YOLO(weights) 可能去下预训练权重——那会落到
    # ultralytics 全局的 weights_dir。改道要在 import 之前做。
    apply_runtime_env()

    from ultralytics import YOLO

    pin_ultralytics_paths()

    model = YOLO(cfg.weights)
    from fire_sight.scene.class_map import display_name

    visualizer = BeautifyVisualizer()
    confirm = MultiFrameConfirm(confirm_frames=cfg.confirm_frames)
    # 本次运行的唯一前缀。没有它，event_id 每次运行都从 evt-1 重新开始，而它是
    # 事件库主键 + INSERT OR REPLACE——第二次推理会把第一次的事件逐条覆盖，
    # 片段文件同名也会被覆盖，历史就这样无声地少掉一半。
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    policy = AlertPolicy(
        cooldown_sec=cfg.cooldown_sec,
        location=cfg.camera_name,
        id_prefix=f"{run_stamp}-",
    )
    # 语音引擎全进程只起一次：这里以前每轮 new 一个 TtsNotifier（内含 pyttsx3.init()），
    # RTSP 掉线重连一次就泄漏一个 SAPI COM 句柄。改成进程级单例复用。
    tts = get_tts(enabled=cfg.enable_tts)

    db_path = cfg.events_db or (DATA_DIR / "events" / "events.db")
    store = EventStore(db_path)
    clip_dir = Path(db_path).parent / "clips"

    if cfg.save_annotated_dir:
        Path(cfg.save_annotated_dir).mkdir(parents=True, exist_ok=True)

    stats = InferStats()

    with create_frame_source(
        cfg.source,
        source_id=cfg.camera_name or str(cfg.source),
        # 把停止信号交给帧源：网络流卡在 cap.read() 上时，只有帧源自己知道
        # 这次读什么时候能返回，停止才谈得上「快」。
        stop_event=stop_event,
    ) as source:
        # 秒→帧换算：优先用源自己的帧率（摄像头/视频），图片目录这类没有标称帧率的走兜底
        fps = float(getattr(source, "fps", None) or cfg.clip_fps)
        buffer = RingClipBuffer(before_sec=cfg.clip_before_sec, fps=fps)
        tail_frames = max(0, int(round(cfg.clip_after_sec * fps)))

        # 片段写盘比读帧慢一个数量级（千帧 VP9），放后台单线程写，主循环继续推理。
        # 回填事件库仍留在主线程做，省掉 SQLite 的跨线程约束。
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip-write")
        pending: List[dict] = []  # 还在攒尾巴的：{event_id, assembler, entry}
        writing: List[tuple] = []  # 已提交写盘：(event_id, future)

        def _submit(p: dict) -> None:
            fut = executor.submit(p["assembler"].write)
            writing.append((p["event_id"], p["entry"], fut))

        def _drain(wait: bool = False) -> None:
            keep = []
            for event_id, entry, fut in writing:
                if not wait and not fut.done():
                    keep.append((event_id, entry, fut))
                    continue
                try:
                    written = fut.result()
                    clip_str = str(written)
                except Exception as exc:  # noqa: BLE001 - 单段失败不该打断整轮推理
                    # 写不出可播放片段就不记路径：界面宁可显示「未留存」，
                    # 也不要给一个点了没反应的「回放」按钮。
                    logger.warning(f"片段写出失败，本次不记片段: {exc}")
                    clip_str = ""
                if clip_str:
                    store.set_clip(event_id, clip_str)
                    entry["clip"] = clip_str
            writing[:] = keep

        for frame in source:
            if cfg.max_frames is not None and stats.frames >= cfg.max_frames:
                break
            if stop_event is not None and stop_event.is_set():
                break

            # 先把本帧喂给在攒尾巴的片段。放在告警之前是有意的：
            # 告警帧自己已经进过环形缓冲，属于「前 N 秒」，不能又算进「后 N 秒」。
            still = []
            for p in pending:
                if p["assembler"].add(frame.image):
                    _submit(p)
                else:
                    still.append(p)
            pending = still
            _drain()

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
                    name = display_name(cls_id)
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
                # 事件立刻入库、片段路径等写完再回填：告警当帧就能在账册里看见，
                # 中途断电丢的最多是一段还没写完的片子，不会丢事件本身。
                entry = {
                    "event_id": event.event_id,
                    "message": event.message,
                    "clip": "",
                    "frame_index": event.frame_index,
                }
                assembler = ClipAssembler(
                    pre_frames=buffer.snapshot(),
                    tail_frames=tail_frames,
                    fps=fps,
                    # 用返回路径而不是自己拼的那条：write_clip 会按可用编码挑后缀
                    # （webm / mp4），拼死的 .mp4 会指向一个不存在的文件。
                    out_path=clip_dir / f"{event.event_id}.mp4",
                )
                store.insert(event, clip_path="")
                tts.speak(event.message)
                stats.alerts += 1
                stats.events.append(entry)
                if assembler.ready:
                    _submit({"event_id": event.event_id, "assembler": assembler, "entry": entry})
                else:
                    pending.append(
                        {"event_id": event.event_id, "assembler": assembler, "entry": entry}
                    )
                logger.info(
                    f"[告警] {event.message}（片段待补齐：前 {len(assembler.frames)} 帧 + 后 {tail_frames} 帧）"
                )

            annotated = visualizer.draw(frame.image, vis_dets)
            if frame_sink is not None:
                frame_sink(annotated)
            if cfg.save_annotated_dir:
                out_p = Path(cfg.save_annotated_dir) / f"{frame.index:06d}.jpg"
                cv2.imwrite(str(out_p), annotated)

        # 源结束（视频放完/图片目录读完/被 max_frames 打断/被停止打断）：
        # 没攒满的尾巴截到现有长度照写。短片段好过没片段。
        #
        # 但「停止」时不能等：片段编码是 VP9 千帧，秒级到几十秒，而事件本身
        # 早已入库（见上面 store.insert），等它只是为了回填 clip 路径。把这段
        # 等待丢给后台线程，主流程立刻返回、状态立刻收敛到终态——界面不再在
        # 「停止中」干等；回放路径晚几秒补上不影响任何事。
        stopping = stop_event is not None and stop_event.is_set()
        if stopping:
            for p in pending:
                _submit(p)
            pending.clear()
            executor.shutdown(wait=False)
            # store 不能在这里关：后台线程还要用它回填 clip 路径。
            threading.Thread(
                target=_finalize_stopped_clips,
                args=(executor, writing, store),
                name="clip-finalize",
                daemon=True,
            ).start()
        else:
            for p in pending:
                _submit(p)
            pending.clear()
            _drain(wait=True)
            executor.shutdown(wait=True)
            store.close()
    logger.info(
        f"推理结束 frames={stats.frames} raw_dets={stats.raw_dets} alerts={stats.alerts}"
    )
    return stats
