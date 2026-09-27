"""显存守卫：超出本机显存预算的 batch/imgsz 必须在起进程之前被挡下。

2026-09-27 那次 `batch=48 × imgsz=640` 在 8 GB 的 RTX 4060 Laptop 上废掉了一轮
训练。等 ultralytics 自己报 CUDA OOM，模型已经加载、显存已经吃满、十分钟没了。

同日的第二个缺口：预算里**没有模型规模项**。它是在 yolo11n 上标定的，于是
yolo11m 也能拿到 n 的额度——实测 m 在 `batch=32 × imgsz=480` 就用掉 8186 MiB，
差不多是整张卡。现在按实测因子分档。
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
# 模型规模因子
# --------------------------------------------------------------------------


def test_factors_are_consistent_with_the_recorded_measurements():
    """因子必须能从留档的实测数据重算出来。

    防的是「手改常数不重测」——那样这组数字就变成了凭感觉的魔法值，
    而它本来是从真实训练里量出来的。
    """
    from fire_sight_platform.training import vram

    def slope(points: dict) -> float:
        (b1, p1), (b2, p2) = sorted(points.items())
        return (p2 - p1) / (b2 - b1)

    base = slope(vram.MODEL_MEASUREMENTS["yolo11n"])
    for name, points in vram.MODEL_MEASUREMENTS.items():
        measured = slope(points) / base
        assert abs(vram.MODEL_FACTORS[name] - measured) < 0.02, (
            f"{name}: 表里写 {vram.MODEL_FACTORS[name]}，"
            f"实测数据算出 {measured:.3f}——改了因子就请重测"
        )


def test_measurement_table_covers_every_factor():
    from fire_sight_platform.training import vram

    assert set(vram.MODEL_FACTORS) == set(vram.MODEL_MEASUREMENTS)
    # 标定点必须是基准（因子 1.0），否则整套换算失去参照
    assert vram.MODEL_FACTORS[vram.REFERENCE_MODEL] == 1.0


def test_bigger_model_gets_a_smaller_batch():
    from fire_sight_platform.training import vram

    limits = [
        vram.max_batch(480, VRAM_MB, m)
        for m in ("yolo11n.pt", "yolo11s.pt", "yolo11m.pt", "yolo11l.pt", "yolo11x.pt")
    ]
    assert limits == sorted(limits, reverse=True), limits
    assert limits[0] > limits[-1]


def test_cost_scales_with_model_size():
    from fire_sight_platform.training import vram

    n = vram.cost(640, 8, "yolo11n.pt")
    s = vram.cost(640, 8, "yolo11s.pt")
    m = vram.cost(640, 8, "yolo11m.pt")
    assert n < s < m
    # 因子直接体现在开销里，不是另外挂了个字段
    assert s == int(n * vram.MODEL_FACTORS["yolo11s"])


def test_factor_is_read_from_name_or_path():
    from fire_sight_platform.training import vram

    for form in ("yolo11s.pt", "models/yolo11s.pt", "a\\b\\yolo11s.pt"):
        assert vram.model_factor(form)[0] == vram.MODEL_FACTORS["yolo11s"], form


def test_unknown_model_is_conservative_and_flagged():
    """未知模型（含从归档 best.pt 续训）按 m 估，并标明只是估计。

    不标出来，用户会以为这个上限也实测过——那是最坏的一种诚实缺失。
    """
    from fire_sight_platform.training import vram

    factor, measured = vram.model_factor("models/trained/xxx/best.pt")
    assert factor == vram.UNKNOWN_MODEL_FACTOR
    assert measured is False

    verdict = vram.check(imgsz=480, batch=40, total_mb=VRAM_MB, model="best.pt")
    assert verdict.ok is False
    assert verdict.measured is False
    assert "估算" in verdict.message


def test_the_config_that_actually_ate_the_card_is_rejected():
    """实测 yolo11m 在 batch=32 × imgsz=480 用掉 8186 MiB，几乎是整张卡。

    旧守卫（无模型因子）会放行——它按 yolo11n 的额度算，32 远低于 72。
    """
    from fire_sight_platform.training import vram

    verdict = vram.check(imgsz=480, batch=32, total_mb=VRAM_MB, model="yolo11m.pt")
    assert verdict.ok is False
    assert verdict.model == "yolo11m"
    assert verdict.factor == vram.MODEL_FACTORS["yolo11m"]
    assert verdict.measured is True
    assert "yolo11m" in verdict.message and "实测" in verdict.message
    # 同一组配置在 yolo11n 上是通过的——差别全部来自模型规模
    assert vram.check(imgsz=480, batch=32, total_mb=VRAM_MB, model="yolo11n.pt").ok


def test_advertised_max_batch_is_tight_for_every_model():
    from fire_sight_platform.training import vram

    for model in vram.MODEL_FACTORS:
        for imgsz in (320, 480, 640, 960):
            limit = vram.max_batch(imgsz, VRAM_MB, model)
            assert vram.cost(imgsz, limit, model) <= vram.px_budget(VRAM_MB), (model, imgsz)
            assert vram.cost(imgsz, limit + 1, model) > vram.px_budget(VRAM_MB), (model, imgsz)


def test_reference_point_passes_with_its_own_model():
    from fire_sight_platform.training import vram

    v = vram.check(
        imgsz=vram.REFERENCE_IMGSZ,
        batch=vram.REFERENCE_BATCH,
        total_mb=vram.REFERENCE_VRAM_MB,
        model=vram.REFERENCE_MODEL,
    )
    assert v.ok and v.measured


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


def test_limits_endpoint_reports_the_model_factor(client, monkeypatch):
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "gpu_info", lambda: dict(GPU_OK))
    common = {"imgsz": 480, "device": "0"}
    n = client.get("/api/train/limits", params={**common, "model": "yolo11n.pt"}).json()
    m = client.get("/api/train/limits", params={**common, "model": "yolo11m.pt"}).json()

    assert n["model"] == "yolo11n" and n["factor"] == 1.0 and n["model_measured"] is True
    assert n["max_batch"] == 72
    assert m["model"] == "yolo11m" and m["factor"] == 3.83
    assert m["max_batch"] < n["max_batch"]


def test_limits_flags_an_unmeasured_model(client, monkeypatch):
    """界面要能说出「这个上限是估的」，所以接口必须把这个事实带出来。"""
    from fire_sight.api import deps

    monkeypatch.setattr(deps, "gpu_info", lambda: dict(GPU_OK))
    body = client.get(
        "/api/train/limits", params={"imgsz": 480, "device": "0", "model": "best.pt"}
    ).json()
    assert body["model"] == "best"
    assert body["model_measured"] is False


def test_start_job_rejects_over_budget_for_a_bigger_model(client, tmp_path, monkeypatch):
    """同一组 batch/imgsz：yolo11n 放行、yolo11m 必须被拒。

    只走拒绝分支（不会真的起训练进程）——放行那一侧由 vram.check 的单测覆盖。
    """
    import fire_sight_platform.training.service as svc
    from fire_sight.api import deps

    monkeypatch.setattr(svc, "_resolve_dataset_yaml", lambda _d: tmp_path / "x.yaml")
    monkeypatch.setattr(deps, "gpu_info", lambda: dict(GPU_OK))

    r = client.post(
        "/api/train/jobs",
        json={
            "dataset": "任意",
            "model": "yolo11m.pt",
            "epochs": 1,
            "batch": 32,
            "imgsz": 480,
        },
    )

    assert r.status_code == 400
    assert "yolo11m" in r.json()["detail"]
    assert deps.GPU_LOCK.holder is None, "被拒的请求不该留下 GPU 锁"
