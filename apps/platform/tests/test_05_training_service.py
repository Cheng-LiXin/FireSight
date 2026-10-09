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


# --------------------------------------------------------------------------
# 权重查找：界面与训练必须认同一份真相
#
# 界面（catalog.list_options）只认受管目录、而 ultralytics 解析裸名时先看 CWD
# （训练子进程的 CWD 是仓库根），两边错开就会把本机明明有的 yolo11n.pt 标成
# 「需下载」，还把默认模型顶到 yolo11s 上去。这几条把两个落点都钉住。
# --------------------------------------------------------------------------


@pytest.fixture()
def weight_dirs(tmp_path, monkeypatch):
    """把 catalog 的两个落点都搬到 tmp_path：受管的 weights/ 与仓库根。"""
    from fire_sight_platform.training import catalog

    root = tmp_path / "root"
    managed = tmp_path / "runtime" / "weights"
    root.mkdir()
    managed.mkdir(parents=True)
    monkeypatch.setattr(catalog, "RUNTIME_ULTRALYTICS_DIR", tmp_path / "runtime")
    monkeypatch.setattr(catalog, "ROOT_DIR", root)
    return root, managed


def test_resolve_weight_prefers_the_managed_dir(weight_dirs):
    """两个落点都有时认受管目录：它才是 runtime_env 钉过的那个。"""
    from fire_sight_platform.training.catalog import resolve_weight

    root, managed = weight_dirs
    (root / "yolo11n.pt").write_bytes(b"root-copy")
    (managed / "yolo11n.pt").write_bytes(b"managed-copy")

    assert resolve_weight("yolo11n.pt") == (managed / "yolo11n.pt").resolve()


def test_resolve_weight_falls_back_to_the_repo_root(weight_dirs):
    """只有仓库根有时也要认——ultralytics 自己就是这么找的。"""
    from fire_sight_platform.training.catalog import resolve_weight

    root, _managed = weight_dirs
    (root / "yolo11n.pt").write_bytes(b"root-copy")

    assert resolve_weight("yolo11n.pt") == (root / "yolo11n.pt").resolve()


def test_resolve_weight_returns_none_when_nowhere_to_be_found(weight_dirs):
    from fire_sight_platform.training.catalog import resolve_weight

    assert resolve_weight("yolo11l.pt") is None


def test_resolve_weight_keeps_an_absolute_path_as_given(weight_dirs, tmp_path):
    """续训/推理传进来的绝对路径不能被改名成受管目录里的同名文件。"""
    from fire_sight_platform.training.catalog import resolve_weight

    _root, managed = weight_dirs
    (managed / "yolo11n.pt").write_bytes(b"managed-copy")
    mine = tmp_path / "elsewhere" / "yolo11n.pt"
    mine.parent.mkdir()
    mine.write_bytes(b"mine")

    assert resolve_weight(str(mine)) == mine.resolve()


def test_training_weight_pins_a_download_into_the_managed_dir(weight_dirs):
    """本机没有时也要返回受管目录里的路径。

    返回裸名会让 ultralytics 按 safe_download 的默认行为下到 CWD（仓库根），
    权重从此散在两个地方——这正是这个 bug 的来源。
    """
    from fire_sight_platform.training.catalog import training_weight

    _root, managed = weight_dirs
    assert training_weight("yolo11l.pt") == managed / "yolo11l.pt"


def test_list_options_sees_a_weight_that_lives_in_the_repo_root(weight_dirs):
    """回归：根目录里的 yolo11n.pt 曾被标成「需下载」。"""
    from fire_sight_platform.training.catalog import list_options

    root, _managed = weight_dirs
    (root / "yolo11n.pt").write_bytes(b"x" * 2048)

    opt = next(o for o in list_options() if o.stem == "yolo11n")
    assert opt.present is True
    assert opt.size_mb == 0.0  # 2048 B 四舍五入到 0.0 MB，但**不是 None**
    assert next(o for o in list_options() if o.stem == "yolo11l").present is False
