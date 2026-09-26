"""S2: 训练服务结构测试(不跑完整 GPU 训练)。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_train_config_defaults():
    from fire_sight_platform.runtime_config.base import TrainConfig

    cfg = TrainConfig()
    assert cfg.model == "yolo11n.pt"
    assert cfg.epochs == 50
    assert cfg.imgsz == 640
    assert cfg.batch == 8


def test_archive_creates_snapshot(tmp_path, monkeypatch):
    from fire_sight_platform.common import paths as paths_mod
    from fire_sight_platform.training.archive import archive_best_weight

    monkeypatch.setattr(paths_mod, "TRAINED_MODELS_DIR", tmp_path / "trained")
    # archive imported TRAINED_MODELS_DIR at module level
    import fire_sight_platform.training.archive as arch

    monkeypatch.setattr(arch, "TRAINED_MODELS_DIR", tmp_path / "trained")

    fake_best = tmp_path / "best.pt"
    fake_best.write_bytes(b"fake-weights")
    dest = archive_best_weight(fake_best, run_id="20260101-demo", config_text="a: 1", metrics_text="mAP=0.5")
    assert dest.exists()
    assert (dest.parent / "train_config.yaml").read_text(encoding="utf-8") == "a: 1"


def test_train_config_speed_fields():
    from fire_sight_platform.runtime_config.base import TrainConfig

    cfg = TrainConfig()
    assert cfg.workers == 8
    assert cfg.fraction == 1.0
    assert cfg.cache is False


def test_yolo_drops_degenerate_boxes(tmp_path):
    from fire_sight_platform.data_pipeline.convert.converters.yolo import convert_yolo

    src = tmp_path / "labels"
    src.mkdir()
    (src / "a.txt").write_text(
        "0 0.5 0.5 0.2 0.2\n1 0.4 0.4 0.0 0.1\n0 0.1 0.1 0.3 0.0\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    n_files, n_boxes = convert_yolo(src, tmp_path, out, {"fire": 0, "smoke": 1})
    content = (out / "a.txt").read_text(encoding="utf-8").strip().splitlines()
    assert n_boxes == 1
    assert len(content) == 1
    assert content[0].startswith("0 ")
