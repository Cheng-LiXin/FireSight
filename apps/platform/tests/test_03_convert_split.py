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


# --------------------------------------------------------------------------
# 输入路径与产物名的分离
# --------------------------------------------------------------------------


@pytest.fixture()
def split_raw(tmp_path, monkeypatch):
    """原始数据集已自行切分：raw/D-Fire/train/。"""
    from fire_sight_platform.common import paths as paths_mod

    monkeypatch.setattr(paths_mod, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths_mod, "PROCESSED_DATA_DIR", tmp_path / "processed")

    import make_demo_dataset as demo

    demo.build("yolo", "D-Fire/train", n=6, out_root=tmp_path / "raw")
    return tmp_path


def test_output_name_defaults_to_dataset(split_raw):
    """产物名默认与 dataset 相同，输入位置与产物名由两个独立参数决定。"""
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    r = run_transform("D-Fire/train", source_format="yolo", seed=42)

    assert r.processed_root == split_raw / "processed" / "D-Fire" / "train"
    assert r.yaml_path.exists()
    # 原始数据确实读自带斜杠的那条路径
    assert sum(r.split_counts.values()) == 6


def test_output_name_can_be_set_explicitly(split_raw):
    """回归：产物名可独立于输入位置。

    早期两者绑成同一个值，想换个产物名就只能改 --dataset，
    而改 --dataset 又会让原始数据找不到。
    """
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    r = run_transform("D-Fire/train", source_format="yolo", seed=42, name="my_set")

    assert r.processed_root == split_raw / "processed" / "my_set"
    assert r.yaml_path.exists()
    assert sum(r.split_counts.values()) == 6


# --------------------------------------------------------------------------
# 类别契约必须被强制
# --------------------------------------------------------------------------


def test_run_transform_rejects_reversed_class_order(demo_yolo):
    """顺序即 class_id。放行错序会让明火与烟雾整体互换。

    对 VOC / COCO 尤其危险——转换器按名字查表，会直接把数字标签写反，
    而 data.yaml 仍按契约声明，事后几乎无法察觉。
    """
    from fire_sight_platform.common.constants import ClassOrderError
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    with pytest.raises(ClassOrderError):
        run_transform("demo_fire", source_format="yolo", class_names=["fire", "smoke"])


def test_canonical_class_order_is_accepted(demo_yolo):
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    r = run_transform("demo_fire", source_format="yolo", class_names=["smoke", "fire"])
    assert r.yaml_path.exists()


def test_cli_exits_2_on_reversed_class_order(demo_yolo, monkeypatch, capsys):
    """CLI 应在建立任何目录之前就拦下，不留下半成品。"""
    from fire_sight_platform.cli import transform_data as cli

    # 避免写真实日志目录
    monkeypatch.setattr(cli, "get_logger", lambda **kwargs: None)

    rc = cli.main(["--dataset", "demo_fire", "--classes", "fire,smoke"])

    assert rc == 2
    assert "类别顺序必须" in capsys.readouterr().err


def test_make_run_id_sanitizes_dataset():
    """归档目录名的另一半防线：数据集名带斜杠不得产生嵌套目录。"""
    from fire_sight_platform.training.archive import make_run_id

    run_id = make_run_id("D-Fire/train", "yolo11n.pt")

    assert "/" not in run_id and "\\" not in run_id
    assert "D-Fire-train" in run_id
    assert run_id.endswith("-yolo11n")
