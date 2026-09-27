"""运行时目录：缓存与临时文件必须落在工作区里，不许外溢到用户目录。

背景：ultralytics 把 runs_dir / weights_dir / datasets_dir 存在机器级的
`%APPDATA%\\Ultralytics\\settings.json` 里，本机那份指向 C 盘。实测 fs-val 的
`model.val()` 因此往 `C:\\Users\\19832\\runs\\detect\\val` 写了 24 个图件，AMP
自检还把 yolo26n.pt 下到了 `C:\\Users\\19832\\weights`。
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src", ROOT / "apps" / "platform" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture()
def redirected_runtime(tmp_path, monkeypatch):
    """把运行时目录指到 tmp_path，用完恢复模块状态。"""
    from fire_sight_platform.common import paths, runtime_env

    monkeypatch.setenv("FIRESIGHT_RUNTIME_DIR", str(tmp_path / "rt"))
    importlib.reload(paths)
    reloaded = importlib.reload(runtime_env)
    try:
        yield reloaded
    finally:
        monkeypatch.delenv("FIRESIGHT_RUNTIME_DIR", raising=False)
        importlib.reload(paths)
        importlib.reload(runtime_env)


# --------------------------------------------------------------------------
# 环境变量映射
# --------------------------------------------------------------------------


def test_every_redirect_target_is_under_runtime_dir():
    from fire_sight_platform.common import runtime_env
    from fire_sight_platform.common.paths import RUNTIME_DIR

    mapping = runtime_env.runtime_env()
    assert set(mapping) >= {"YOLO_CONFIG_DIR", "TEMP", "TMP", "MPLCONFIGDIR"}

    root = RUNTIME_DIR.resolve()
    for key, value in mapping.items():
        resolved = Path(value).resolve()
        assert resolved == root or root in resolved.parents, f"{key} 跑到 {resolved} 了"


def test_runtime_dir_follows_the_env_var(redirected_runtime, tmp_path):
    """路径要能靠环境变量换掉——项目挪盘时不必改代码。"""
    assert redirected_runtime.RUNTIME_DIR == tmp_path / "rt"
    assert redirected_runtime.RUNTIME_TMP_DIR == tmp_path / "rt" / "tmp"


def test_apply_creates_dirs_and_writes_environ(redirected_runtime, tmp_path):
    """apply_runtime_env 必须既建目录、又真的写 os.environ——子进程靠继承拿。"""
    mapping = redirected_runtime.apply_runtime_env()

    for key, value in mapping.items():
        assert os.environ[key] == value, key
    assert (tmp_path / "rt" / "tmp").is_dir()
    assert (tmp_path / "rt" / "cache" / "matplotlib").is_dir()
    # ultralytics 的三处目录也要先建好，否则首次 val 由它自己创建
    assert (tmp_path / "rt" / "ultralytics" / "runs").is_dir()
    assert (tmp_path / "rt" / "ultralytics" / "weights").is_dir()


# --------------------------------------------------------------------------
# 拒绝改写用户的全局 ultralytics 配置
# --------------------------------------------------------------------------


def test_pin_refuses_to_touch_a_config_dir_outside_runtime(tmp_path, monkeypatch):
    """用户的机器级配置不该被项目偷改。

    只有在 YOLO_CONFIG_DIR 生效（配置目录落在 .runtime 下）时才允许落盘；
    否则说明改道晚了一步，此时应当拒绝而不是污染全局设置。
    """
    ultralytics = pytest.importorskip("ultralytics.utils")
    from fire_sight_platform.common import runtime_env

    wrote: list[dict] = []
    monkeypatch.setattr(runtime_env, "RUNTIME_ULTRALYTICS_DIR", tmp_path / "runtime")
    monkeypatch.setattr(runtime_env, "apply_runtime_env", lambda: {})
    monkeypatch.setattr(
        ultralytics,
        "USER_CONFIG_DIR",
        Path("C:/Users/someone/AppData/Roaming/Ultralytics"),
    )
    monkeypatch.setattr(
        ultralytics,
        "SETTINGS",
        type("S", (dict,), {"update": lambda self, *a, **k: wrote.append(k)})(),
    )

    assert runtime_env.pin_ultralytics_paths() is False
    assert wrote == []


def test_pin_writes_when_config_dir_is_inside_runtime(tmp_path, monkeypatch):
    ultralytics = pytest.importorskip("ultralytics.utils")
    from fire_sight_platform.common import runtime_env

    runtime_root = tmp_path / "runtime"
    wrote: list[dict] = []
    monkeypatch.setattr(runtime_env, "RUNTIME_ULTRALYTICS_DIR", runtime_root)
    monkeypatch.setattr(runtime_env, "apply_runtime_env", lambda: {})
    monkeypatch.setattr(ultralytics, "USER_CONFIG_DIR", runtime_root / "Ultralytics")
    monkeypatch.setattr(
        ultralytics,
        "SETTINGS",
        type("S", (dict,), {"update": lambda self, *a, **k: wrote.append(a[0] if a else k)})(),
    )

    assert runtime_env.pin_ultralytics_paths() is True
    assert wrote and set(wrote[0]) == {"datasets_dir", "weights_dir", "runs_dir"}
    for value in wrote[0].values():
        assert str(runtime_root) in value


# --------------------------------------------------------------------------
# 训练任务的配置文件落点
# --------------------------------------------------------------------------


@pytest.fixture()
def clean_manager():
    from fire_sight.api import deps, jobs

    jobs.manager._jobs.clear()
    jobs.manager._order.clear()
    yield
    jobs.manager._jobs.clear()
    jobs.manager._order.clear()
    deps.GPU_LOCK.release()


def test_job_config_lands_in_runtime_tmp(tmp_path, monkeypatch, clean_manager):
    """回归：任务配置曾用 tempfile.gettempdir() 写进 C 盘 Temp。"""
    from fire_sight.api import jobs

    captured: dict = {}

    class FakeProc:
        pid = 4242
        stdout = iter(())
        stderr = iter(())

        def wait(self) -> int:
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env") or {}
        return FakeProc()

    monkeypatch.setattr(jobs.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "RUNTIME_TMP_DIR", tmp_path)
    monkeypatch.setattr(jobs, "apply_runtime_env", lambda: {"TEMP": str(tmp_path)})

    job = jobs.manager.start({"dataset": "demo_fire", "epochs": 1})

    cfg_files = list(tmp_path.glob("firesight-train-*.json"))
    assert len(cfg_files) == 1, "任务配置没有落在运行时目录里"
    assert cfg_files[0].name == f"firesight-train-{job.id}.json"
    assert captured["cmd"][-1] == str(cfg_files[0])
    # 子进程必须拿到改道后的 TEMP，否则库自己又往 C 盘写
    assert captured["env"]["TEMP"] == str(tmp_path)
