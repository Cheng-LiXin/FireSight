"""显存守卫：超出本机显存预算的 batch/imgsz 必须在起进程之前被挡下。

2026-09-27 那次 `batch=48 × imgsz=640` 在 8 GB 的 RTX 4060 Laptop 上废掉了一轮
训练。等 ultralytics 自己报 CUDA OOM，模型已经加载、显存已经吃满、十分钟没了。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# 本机那张卡
VRAM_MB = 8188
GPU_OK = {
    "available": True,
    "name": "NVIDIA GeForce RTX 4060 Laptop GPU",
    "memory_total_mb": VRAM_MB,
    "memory_used_mb": 0,
    "utilization_pct": 0,
}


@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from fire_sight.api.app import app

    return TestClient(app)


# --------------------------------------------------------------------------
# 预算模型
# --------------------------------------------------------------------------


def test_reference_point_survives_its_own_guard():
    """标定点必须放行。

    预算常数就是从它反推的，如果它自己被拒，说明守卫比它要保护的东西还严格
    ——那会把唯一一个跑出可信指标的配置挡在门外。
    """
    from fire_sight_platform.training import vram

    assert vram.check(imgsz=vram.REFERENCE_IMGSZ, batch=vram.REFERENCE_BATCH,
                      total_mb=vram.REFERENCE_VRAM_MB).ok


def test_the_combination_that_actually_died_is_rejected():
    from fire_sight_platform.training import vram

    verdict = vram.check(imgsz=640, batch=48, total_mb=VRAM_MB)
    assert verdict.ok is False
    assert verdict.max_batch == 40
    # 提示必须可照做：给出 imgsz 下的上限
    assert "imgsz=640 时 batch 最多 40" in verdict.message


def test_max_batch_shrinks_as_imgsz_grows():
    from fire_sight_platform.training import vram

    small = vram.max_batch(320, VRAM_MB)
    medium = vram.max_batch(640, VRAM_MB)
    large = vram.max_batch(1280, VRAM_MB)
    assert small > medium > large
    assert large >= 1


def test_budget_is_respected_by_the_advertised_max_batch():
    from fire_sight_platform.training import vram

    for imgsz in (320, 416, 480, 640, 960, 1280):
        limit = vram.max_batch(imgsz, VRAM_MB)
        assert vram.cost(imgsz, limit) <= vram.px_budget(VRAM_MB)
        # 再多一个就该超——上限是紧的，不是随手给的保守值
        assert vram.cost(imgsz, limit + 1) > vram.px_budget(VRAM_MB)


def test_smaller_gpu_gets_a_smaller_budget():
    from fire_sight_platform.training import vram

    assert vram.px_budget(4096) < vram.px_budget(VRAM_MB) < vram.px_budget(24576)


def test_rejects_nonsense_input():
    from fire_sight_platform.training import vram

    with pytest.raises(ValueError):
        vram.px_budget(0)
    with pytest.raises(ValueError):
        vram.max_batch(0, VRAM_MB)


# --------------------------------------------------------------------------
# 接口
# --------------------------------------------------------------------------


def test_limits_endpoint_returns_max_batch(client, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "gpu_info", lambda: dict(GPU_OK))
    body = client.get("/api/train/limits", params={"imgsz": 640, "device": "0"}).json()

    assert body["checked"] is True
    assert body["total_mb"] == VRAM_MB
    assert body["max_batch"] == 40
    assert "batch=72" in body["reference"]


def test_limits_on_cpu_is_unchecked(client):
    body = client.get("/api/train/limits", params={"imgsz": 640, "device": "cpu"}).json()
    assert body["checked"] is False
    assert body["max_batch"] is None


def test_start_job_rejects_over_budget_before_spawning(client, tmp_path, monkeypatch):
    """必须返回 400，而不是把训练进程起起来再让 CUDA 溢出。"""
    import fire_sight_platform.training.service as svc
    from fire_sight.api import deps

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")
    monkeypatch.setattr(deps, "gpu_info", lambda: dict(GPU_OK))

    r = client.post(
        "/api/train/jobs",
        json={"dataset": "任意", "epochs": 1, "batch": 48, "imgsz": 640},
    )

    assert r.status_code == 400
    assert "imgsz=640 时 batch 最多 40" in r.json()["detail"]
    assert deps.GPU_LOCK.holder is None, "被拒的请求不该留下 GPU 锁"


def test_start_job_dataset_error_still_wins(client):
    """数据集不存在仍是 404——它的检查在显存之前，不能被打乱顺序。"""
    r = client.post(
        "/api/train/jobs",
        json={"dataset": "根本不存在的数据集", "epochs": 1, "batch": 48, "imgsz": 640},
    )
    assert r.status_code == 404


def test_vram_limits_skips_guard_when_gpu_probe_fails(monkeypatch):
    """探测不到显卡时不该拦——那是环境问题，不是配置问题。"""
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "gpu_info", lambda: {"available": False, "reason": "未找到 nvidia-smi"})
    assert deps.vram_limits("0", 640)["checked"] is False
    assert deps.vram_limits("cpu", 640)["checked"] is False
