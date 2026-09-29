"""S3 集成测试：fs-infer 全链路（帧源→推理→告警→片段/事件）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for extra in (
    ROOT / "apps" / "fire-sight" / "src",
    ROOT,
):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def _find_weights() -> Path | None:
    # 先找训练出来的火情权重。yolo11n.pt 是 COCO 预训练，在火情图上检出的是
    # 无关类别（实测 cls=62），拿它当夹具会让「应当出告警」这类断言永远失败。
    candidates = [
        *sorted((ROOT / "models" / "trained").glob("*/best.pt"), reverse=True),
        ROOT / "yolo11n.pt",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def test_infer_config_defaults():
    from fire_sight_platform.inference.service import InferConfig

    cfg = InferConfig(source="x", weights="y.pt")
    assert cfg.confirm_frames == 3
    assert cfg.cooldown_sec == 30.0
    assert cfg.conf == 0.25
    # 需求口径是「前后 30 秒」，配置直接用秒，不再让调用方换算帧数
    assert cfg.clip_before_sec == 30.0
    assert cfg.clip_after_sec == 30.0


def test_cli_parser():
    from fire_sight_platform.cli.infer import _build_parser

    args = _build_parser().parse_args(
        [
            "--source",
            "demo.mp4",
            "--weights",
            "a.pt",
            "--no-tts",
            "--confirm-frames",
            "2",
            "--clip-before",
            "30",
            "--clip-after",
            "25",
        ]
    )
    assert args.confirm_frames == 2
    assert args.no_tts is True
    assert args.clip_before == 30.0
    assert args.clip_after == 25.0


@pytest.mark.slow
def test_end_to_end_image_dir(tmp_path):
    """需要权重；无权重时跳过。"""
    import cv2

    from fire_sight_platform.inference.service import InferConfig, run_inference

    weights = _find_weights()
    if weights is None:
        pytest.skip("no weights available")

    src = tmp_path / "frames"
    src.mkdir()
    for i in range(3):
        img = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
        cv2.imwrite(str(src / f"{i}.png"), img)

    db = tmp_path / "e.db"
    cfg = InferConfig(
        source=str(src),
        weights=str(weights),
        enable_tts=False,
        confirm_frames=1,
        clip_before_sec=0.2,
        clip_after_sec=0.2,
        events_db=str(db),
        max_frames=3,
        device="cpu",
    )
    stats = run_inference(cfg)
    assert stats.frames == 3
    assert db.exists()


def _make_clip_source(tmp_path, n_lead: int, n_fire: int) -> "Path":
    """前 n_lead 张空帧 + n_fire 张真实火情图，构成「先无事、后告警」的流。"""
    import cv2

    fire = ROOT / "data" / "processed" / "D-Fire" / "train" / "images" / "test" / "AoF04059.jpg"
    if not fire.exists():
        return None

    src = tmp_path / "frames"
    src.mkdir()
    for i in range(n_lead):
        cv2.imwrite(str(src / f"{i:03d}.png"), np.zeros((240, 320, 3), dtype=np.uint8))
    img = cv2.imread(str(fire))
    for i in range(n_fire):
        cv2.imwrite(str(src / f"{n_lead + i:03d}.png"), img)
    return src


def _clip_frame_count(path_str: str) -> int:
    import cv2

    cap = cv2.VideoCapture(path_str)
    got = 0
    while True:
        ok, _ = cap.read()
        if not ok:
            break
        got += 1
    cap.release()
    return got


@pytest.mark.slow
def test_clip_captures_before_and_after(tmp_path):
    """片段必须是「前 N 秒 + 后 N 秒」，且事件先入库、片段写完回填。"""
    from fire_sight_platform.inference.service import InferConfig, run_inference

    weights = _find_weights()
    if weights is None:
        pytest.skip("no weights available")

    src = _make_clip_source(tmp_path, n_lead=8, n_fire=15)
    if src is None:
        pytest.skip("no sample fire image")

    db = tmp_path / "e.db"
    # fps 兜底 25：前 0.2s=5 帧，后 0.4s=10 帧
    cfg = InferConfig(
        source=str(src),
        weights=str(weights),
        enable_tts=False,
        confirm_frames=1,
        cooldown_sec=30.0,
        clip_before_sec=0.2,
        clip_after_sec=0.4,
        events_db=str(db),
        device="cpu",
    )
    run_inference(cfg)

    from fire_sight.alerting import EventStore

    with EventStore(db) as store:
        rows = store.list_events()
    assert rows, "真实火情图应当出告警"
    clip_path = rows[0]["clip_path"]
    assert clip_path, "告警后 30 秒攒满时应把片段路径回填进事件"

    # 前段：告警帧之前有 8 张空帧，缓冲要 5 张 → 5；后段要 10 张，其后有 14 张 → 10
    assert _clip_frame_count(clip_path) == 15


@pytest.mark.slow
def test_clip_truncates_when_stream_ends_mid_tail(tmp_path):
    """源提前结束：尾巴截到现有长度照写，不许丢整段。"""
    from fire_sight_platform.inference.service import InferConfig, run_inference

    weights = _find_weights()
    if weights is None:
        pytest.skip("no weights available")

    src = _make_clip_source(tmp_path, n_lead=8, n_fire=15)
    if src is None:
        pytest.skip("no sample fire image")

    db = tmp_path / "e.db"
    cfg = InferConfig(
        source=str(src),
        weights=str(weights),
        enable_tts=False,
        confirm_frames=1,
        cooldown_sec=30.0,
        clip_before_sec=0.2,
        clip_after_sec=0.4,
        events_db=str(db),
        max_frames=11,  # 告警帧(8) + 后续 2 帧就断流，尾巴只攒到 2
        device="cpu",
    )
    run_inference(cfg)

    from fire_sight.alerting import EventStore

    with EventStore(db) as store:
        rows = store.list_events()
    assert rows and rows[0]["clip_path"], "截短也必须写出片段"
    assert _clip_frame_count(rows[0]["clip_path"]) == 7  # 5 前 + 2 后
