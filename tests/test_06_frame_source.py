"""frame_source 模块测试：图片夹 / 视频 / 工厂识别。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def image_dir(tmp_path):
    import cv2

    d = tmp_path / "imgs"
    d.mkdir()
    for i in range(3):
        img = np.zeros((32, 48, 3), dtype=np.uint8)
        img[:, :] = (i * 40, 60, 80)
        cv2.imwrite(str(d / f"f{i}.png"), img)
    return d


def test_image_dir_iterates_all(image_dir):
    from frame_source import create_frame_source

    with create_frame_source(str(image_dir)) as src:
        frames = list(src)
    assert len(frames) == 3
    assert frames[0].image.shape == (32, 48, 3)
    assert frames[1].index == 1


def test_factory_recognizes_dir_and_video(image_dir, tmp_path):
    import cv2

    from frame_source import create_frame_source

    assert type(create_frame_source(str(image_dir))).__name__ == "ImageDirSource"

    vp = tmp_path / "clip.mp4"
    w = cv2.VideoWriter(str(vp), cv2.VideoWriter_fourcc(*"mp4v"), 5, (48, 32))
    for i in range(4):
        w.write(np.full((32, 48, 3), i * 10, dtype=np.uint8))
    w.release()
    assert type(create_frame_source(str(vp))).__name__ == "VideoFileSource"
    with create_frame_source(str(vp)) as src:
        assert len(list(src)) == 4


def test_read_one(image_dir):
    from frame_source import create_frame_source

    src = create_frame_source(str(image_dir))
    f = src.read_one()
    assert f is not None
    src.close()


def test_camera_type_registered():
    from frame_source.core.registry import list_sources

    names = list_sources()
    assert "camera" in names
    assert "video" in names
    assert "image_dir" in names
