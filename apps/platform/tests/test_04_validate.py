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


# --------------------------------------------------------------------------
# yaml 里的路径解析：相对值相对数据集自己，列表值要能解析（R4 / R6）
# --------------------------------------------------------------------------


def _write_mini_dataset(root: Path, *, split_values: str) -> Path:
    """三划分各 2 图 2 标签，`split/` 下 images 与 labels 是兄弟。"""
    for split in ("train", "val", "test"):
        img_dir = root / split / "images"
        lbl_dir = root / split / "labels"
        img_dir.mkdir(parents=True)
        lbl_dir.mkdir(parents=True)
        for i in range(2):
            stem = f"{split}{i}"
            (img_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff")
            (lbl_dir / f"{stem}.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    yaml_path = root / "data.yaml"
    yaml_path.write_text(split_values, encoding="utf-8")
    return yaml_path


def test_snapshot_resolves_relative_split_against_data_root(tmp_path):
    """回归：`path: <绝对>` + `train: train/images` 是 ultralytics 标准写法。

    以前相对值按进程 CWD 解析，于是这种 yaml 指向一个不存在的目录，一份完好的
    数据集被判「没有任何图像样本」；随后又有一次静默回退把错误彻底藏进报告。
    """
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    root = tmp_path / "raw" / "Demo"
    yaml_path = _write_mini_dataset(
        root,
        split_values=(
            f"path: {root.as_posix()}\n"
            "train: train/images\n"
            "val: val/images\n"
            "test: test/images\n"
            "names:\n  0: smoke\n  1: fire\n"
        ),
    )

    ctx = build_snapshot(yaml_path)

    assert ctx.data_root == root
    assert sum(len(v) for v in ctx.splits.values()) == 6
    assert sum(len(v) for v in ctx.label_files.values()) == 6
    assert ctx.label_dirs["train"] == root / "train" / "labels"


def test_snapshot_accepts_list_valued_split(tmp_path):
    """回归：`train:` 可以是列表（多目录）。此前 list 直接进 `Path()` 抛 TypeError。

    更糟的是那个 TypeError 逃出了逐检查守卫，整轮体检带 traceback 死掉，
    连报告都产不出来。
    """
    from fire_sight_platform.data_validation.snapshot import build_snapshot

    root = tmp_path / "raw" / "Multi"
    extra = root / "extra" / "images"
    extra.mkdir(parents=True)
    (extra / "extra0.jpg").write_bytes(b"\xff\xd8\xff")
    yaml_path = _write_mini_dataset(
        root,
        split_values=(
            f"path: {root.as_posix()}\n"
            "train: ['train/images', 'extra/images']\n"
            "val: val/images\n"
            "test: test/images\n"
            "names:\n  0: smoke\n  1: fire\n"
        ),
    )

    ctx = build_snapshot(yaml_path)

    assert len(ctx.splits["train"]) == 3, "列表里的每个目录都要扫到"


def test_unparseable_dataset_yaml_becomes_one_error_not_a_traceback(tmp_path):
    """回归：快照构建失败要降级成一条 ERROR，不能让 fs-validate 崩掉。

    以前 `build_snapshot` 在逐检查守卫**之外**，yaml 写坏一个字段就整轮死。
    """
    from fire_sight_platform.data_validation.registry import CheckSeverity
    from fire_sight_platform.data_validation.service import run_all_checks

    # names 是字符串而不是映射：build_snapshot 里 .items() 会炸
    broken = tmp_path / "broken" / "data.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("names: smoke\n", encoding="utf-8")

    results = run_all_checks(broken)

    assert len(results) == 1
    assert results[0].name == "snapshot"
    assert results[0].severity is CheckSeverity.ERROR
    assert "无法解析" in results[0].message


def test_absent_class_is_reported_as_error(tmp_path):
    """回归 R5：整个类别一个框都没有，以前被算成「失衡比 1.0」判 PASS。

    分母只在**出现过**的 class id 上取 min，于是 100 个框全是 class 0 时
    「自己比自己」= 1.0；反倒是补进一个 class-1 框会立刻变成 WARNING 100.0
    ——缺类比失衡严重得多，不该判得更轻。
    """
    from fire_sight_platform.data_validation.registry import CheckSeverity
    from fire_sight_platform.data_validation.service import validate_dataset

    root = tmp_path / "raw" / "OnlySmoke"
    yaml_path = _write_mini_dataset(
        root,
        split_values=(
            f"path: {root.as_posix()}\n"
            "train: train/images\n"
            "val: val/images\n"
            "test: test/images\n"
            "names:\n  0: smoke\n  1: fire\n"
        ),
    )
    # 所有标签都写 class 0，class 1（fire）一个框都没有
    for txt in root.glob("*/labels/*.txt"):
        txt.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    results = {r.name: r for r in validate_dataset(yaml_path)}
    balance = results["class_balance"]

    assert balance.severity is CheckSeverity.ERROR
    assert "fire" in balance.message
    # 明细形状必须保持「名字: 整数」——report._class_chart_b64 靠它画柱状图
    assert "smoke: 6" in balance.details
    assert "fire: 0" in balance.details


def test_class_balance_is_pass_when_every_class_has_boxes(tmp_path):
    """R5 的反面：两个类别都有框且数量相当时仍然是 PASS，别把缺类检测做过了头。"""
    from fire_sight_platform.data_validation.registry import CheckSeverity
    from fire_sight_platform.data_validation.service import validate_dataset

    root = tmp_path / "raw" / "Both"
    yaml_path = _write_mini_dataset(
        root,
        split_values=(
            f"path: {root.as_posix()}\n"
            "train: train/images\n"
            "val: val/images\n"
            "test: test/images\n"
            "names:\n  0: smoke\n  1: fire\n"
        ),
    )
    for i, txt in enumerate(sorted(root.glob("*/labels/*.txt"))):
        cid = 0 if i % 2 == 0 else 1
        txt.write_text(f"{cid} 0.5 0.5 0.2 0.2\n", encoding="utf-8")

    results = {r.name: r for r in validate_dataset(yaml_path)}

    assert results["class_balance"].severity is CheckSeverity.PASS


def test_tips_cover_absent_class_and_broken_snapshot():
    """报告整改建议要认这两个新 ERROR，否则报告只列问题不给方向。"""
    from fire_sight_platform.data_validation.registry import CheckResult, CheckSeverity
    from fire_sight_platform.data_validation.report import _tips

    tips = _tips(
        [
            CheckResult("class_balance", CheckSeverity.ERROR, "x", []),
            CheckResult("snapshot", CheckSeverity.ERROR, "y", []),
        ]
    )

    assert any("没有任何标注框" in t for t in tips)
    assert any("yaml" in t for t in tips)


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
