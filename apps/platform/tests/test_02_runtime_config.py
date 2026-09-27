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


def test_val_config_uses_the_map_measurement_scale():
    """回归：评估口径必须与 ultralytics 算 mAP 时一致。

    曾经 ValConfig 默认 conf=0.25 / iou=0.6（那是部署口径），导致同一个权重
    fs-val 报 mAP50 0.5831，而归档 metrics.txt 记的是 0.7052 —— 差 0.12 全部
    来自阈值。这两个数字放在一起看，会让人以为评估工具坏了或者权重被换过。
    """
    from fire_sight_platform.runtime_config.base import InferConfig, ValConfig

    val = ValConfig()
    assert val.conf == 0.001, "评估用完整 PR 曲线，不能截在 0.25"
    assert val.iou == 0.7, "要跟训练期验证的 NMS 口径一致"

    # 部署阈值归 InferConfig 管，两边不该再混用
    assert InferConfig().conf == 0.25
