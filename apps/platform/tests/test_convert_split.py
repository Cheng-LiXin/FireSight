"""S1: 转换 + 冻结划分冒烟测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture()
def demo_yolo(tmp_path, monkeypatch):
    from fire_sight_platform.common import paths as paths_mod

    monkeypatch.setattr(paths_mod, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths_mod, "PROCESSED_DATA_DIR", tmp_path / "processed")

    import make_demo_dataset as demo

    raw = demo.build("yolo", "demo_fire", n=12, out_root=tmp_path / "raw")
    return raw, tmp_path


def test_transform_yolo_and_frozen_split(demo_yolo):
    raw, tmp_path = demo_yolo
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    r1 = run_transform("demo_fire", source_format="yolo", seed=42)
    assert r1.yaml_path.exists()
    assert sum(r1.split_counts.values()) == 12

    stems1 = {
        split: sorted(p.stem for p in (r1.processed_root / "images" / split).glob("*.png"))
        for split in ("train", "val", "test")
    }
    r2 = run_transform("demo_fire", source_format="yolo", seed=42)
    stems2 = {
        split: sorted(p.stem for p in (r2.processed_root / "images" / split).glob("*.png"))
        for split in ("train", "val", "test")
    }
    assert stems1 == stems2, "同种子划分必须可复现"


def test_transform_voc(demo_yolo):
    raw, tmp_path = demo_yolo
    import make_demo_dataset as demo
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    demo.build("pascal_voc", "demo_voc", n=8, out_root=tmp_path / "raw")
    r = run_transform("demo_voc", source_format="pascal_voc", seed=42)
    assert r.total_boxes > 0
    assert r.yaml_path.exists()
