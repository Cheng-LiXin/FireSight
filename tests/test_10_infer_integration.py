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
    candidates = [
        ROOT / "yolo11n.pt",
        *sorted((ROOT / "models" / "trained").glob("*/best.pt")),
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


def test_cli_parser():
    from fire_sight_platform.cli.infer import _build_parser

    args = _build_parser().parse_args(
        ["--source", "demo.mp4", "--weights", "a.pt", "--no-tts", "--confirm-frames", "2"]
    )
    assert args.confirm_frames == 2
    assert args.no_tts is True


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
        buffer_frames=5,
        events_db=str(db),
        max_frames=3,
        device="cpu",
    )
    stats = run_inference(cfg)
    assert stats.frames == 3
    assert db.exists()
