"""S1: 质检 + HTML 报告。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fire_sight_platform.data_validation.registry import CheckSeverity

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture()
def prepared(tmp_path, monkeypatch):
    from fire_sight_platform.common import paths as paths_mod

    monkeypatch.setattr(paths_mod, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(paths_mod, "PROCESSED_DATA_DIR", tmp_path / "processed")

    import make_demo_dataset as demo

    demo.build("yolo", "demo_fire", n=12, out_root=tmp_path / "raw")
    from fire_sight_platform.data_pipeline.orchestrator import run_transform

    return run_transform("demo_fire", source_format="yolo", seed=42)


def test_validate_and_html(prepared):
    from fire_sight_platform.data_validation.report import render_html_report
    from fire_sight_platform.data_validation.service import validate_dataset
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    results = validate_dataset(prepared.yaml_path)
    names = {r.name for r in results}
    assert "pair_existence" in names
    assert "label_format" in names
    assert "split_uniqueness" in names

    ctx = build_snapshot(prepared.yaml_path)
    out = render_html_report(
        results,
        dataset="demo_fire",
        n_images=sum(len(v) for v in ctx.splits.values()),
        n_labels=1,
        out_path=prepared.processed_root / "reports" / "quality_report.html",
    )
    text = out.read_text(encoding="utf-8")
    assert "FireSight 数据质量体检报告" in text


# --------------------------------------------------------------------------
# 提供方划分的布局：labels 是 split 的兄弟目录，不是 data_root 下的平级目录
# --------------------------------------------------------------------------


@pytest.fixture()
def provided_split(tmp_path):
    """`split/images/` + `split/labels/` 这种布局，也就是 data/raw/D-Fire 的实际形状。

    与 prepared 的产物布局（`images/<split>` + `labels/<split>`）唯一的差别就是
    标签在哪。此前体检只认后者，于是对新数据集报「缺失标签 21527/21527 (100%)」
    ——数据其实完好，是解析器找错了目录。
    """
    root = tmp_path / "raw" / "D-Fire"
    for split in ("train", "val", "test"):
        img_dir = root / split / "images"
        lbl_dir = root / split / "labels"
        img_dir.mkdir(parents=True)
        lbl_dir.mkdir(parents=True)
        for i in range(2):
            stem = f"{split}{i}"
            (img_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff")
            (lbl_dir / f"{stem}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    posix = root.as_posix()
    (root / "data.yaml").write_text(
        f"path: {posix}\n"
        f"train: {posix}/train/images\n"
        f"val: {posix}/val/images\n"
        f"test: {posix}/test/images\n"
        "names:\n  0: smoke\n  1: fire\n"
        "fire_sight_meta:\n"
        "  dataset: D-Fire/raw\n"
        "  split_strategy: provided\n",
        encoding="utf-8",
    )
    return root / "data.yaml"


def test_snapshot_finds_labels_in_provided_layout(provided_split):
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    ctx = build_snapshot(provided_split)

    assert sum(len(v) for v in ctx.splits.values()) == 6
    # 六个标签文件一个都不能少——这正是之前报「100% 缺失」的同一批文件
    assert sum(len(v) for v in ctx.label_files.values()) == 6
    assert ctx.label_dirs["train"] == provided_split.parent / "train" / "labels"


def test_validate_passes_on_intact_provided_layout(provided_split):
    from fire_sight_platform.data_validation.service import validate_dataset

    results = {r.name: r for r in validate_dataset(provided_split)}

    assert results["pair_existence"].severity is CheckSeverity.PASS
    assert "0/6" in results["pair_existence"].message
    assert results["label_format"].severity is CheckSeverity.PASS
    assert results["split_presence"].severity is CheckSeverity.PASS


def test_missing_label_dir_says_so_not_missing_every_box(provided_split):
    """标签目录整个不在，要说「目录不存在」，而不是「每张图都缺标签」。

    后者会把排查方向指向逐个补标签，而真正的问题是一次目录解析错误。
    """
    from fire_sight_platform.data_validation.service import validate_dataset

    (provided_split.parent / "train" / "labels").rename(provided_split.parent / "train" / "labels_off")

    results = {r.name: r for r in validate_dataset(provided_split)}
    pair = results["pair_existence"]

    assert pair.severity is CheckSeverity.ERROR
    assert "标签目录不存在" in pair.message
    assert "缺失标签" not in pair.message


def test_validate_cli_accepts_raw_label(tmp_path, monkeypatch):
    """fs-validate 与训练/评估同一口径：`--dataset D-Fire/raw` 直接可用。"""
    from fire_sight_platform.cli import validate_data as cli

    raw_root = tmp_path / "raw"
    (raw_root / "D-Fire").mkdir(parents=True)
    (raw_root / "D-Fire" / "data.yaml").write_text("names:\n  0: smoke\n", encoding="utf-8")
    monkeypatch.setattr(cli, "RAW_DATA_DIR", raw_root)

    assert cli._resolve_yaml("D-Fire/raw") == raw_root / "D-Fire" / "data.yaml"
    # 其它标签仍走 processed，不被这个特例劫持
    monkeypatch.setattr(cli, "PROCESSED_DATA_DIR", tmp_path / "processed")
    assert cli._resolve_yaml("demo_fire") == tmp_path / "processed" / "demo_fire" / "data.yaml"


def test_report_dir_keeps_raw_read_only(provided_split, monkeypatch, tmp_path):
    """raw 是提供方的只读数据，体检报告不许写进去——改落运行时目录。"""
    from fire_sight_platform.cli import validate_data as cli

    monkeypatch.setattr(cli, "RAW_DATA_DIR", tmp_path / "raw")
    monkeypatch.setattr(cli, "RUNTIME_DIR", tmp_path / "runtime")

    out = cli._report_dir(provided_split, "D-Fire/raw", None)

    assert out == tmp_path / "runtime" / "quality" / "D-Fire-raw"
    # 显式 --out-dir 仍然优先
    assert cli._report_dir(provided_split, "D-Fire/raw", str(tmp_path / "x")) == tmp_path / "x"
