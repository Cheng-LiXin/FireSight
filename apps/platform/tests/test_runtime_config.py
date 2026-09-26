"""S2: 配置合并与模板生成。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_three_source_merge_priority():
    from fire_sight_platform.runtime_config.base import TrainConfig
    from fire_sight_platform.runtime_config.merger import ConfigMerger

    cfg, sources = ConfigMerger(TrainConfig).merge(
        {"epochs": 10, "model": "yolo11s.pt"},
        {"epochs": 3},
    )
    assert cfg.epochs == 3  # CLI 覆盖 YAML
    assert cfg.model == "yolo11s.pt"
    assert sources["epochs"] == "cli"
    assert sources["model"] == "yaml"
    assert sources["imgsz"] == "default"


def test_generate_template(tmp_path):
    from fire_sight_platform.runtime_config.generator import generate

    out = generate("train", tmp_path / "train.yaml")
    text = out.read_text(encoding="utf-8")
    assert "epochs:" in text
    assert "yolo11n.pt" in text
