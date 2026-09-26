"""路径层冒烟测试。"""

from pathlib import Path

from fire_sight_platform.common.paths import (
    RAW_DATA_DIR,
    ROOT_DIR,
    WORKSPACE_MARKER,
    get_dirs_to_initialize,
)


def test_workspace_root_has_marker():
    assert (ROOT_DIR / WORKSPACE_MARKER).exists()


def test_raw_data_under_root():
    assert RAW_DATA_DIR == ROOT_DIR / "data" / "raw"


def test_dirs_to_initialize_nonempty():
    dirs = get_dirs_to_initialize()
    assert len(dirs) > 0
    assert all(isinstance(d, Path) for d in dirs)
