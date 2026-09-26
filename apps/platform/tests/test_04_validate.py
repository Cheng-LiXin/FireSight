"""S1: 质检 + HTML 报告。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
