"""S4 冒烟：页面/入口可导入，服务可组合。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "apps" / "fire-sight" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def test_pages_importable():
    import fire_sight.web.pages.p1_detect as p1
    import fire_sight.web.pages.p2_history as p2
    import fire_sight.web.pages.p3_dashboard as p3

    assert callable(p1.render)
    assert callable(p2.render)
    assert callable(p3.render)


def test_app_module_importable():
    # 只验证可被找到，不执行 streamlit 脚本体
    import importlib.util

    app = ROOT / "apps" / "fire-sight" / "src" / "fire_sight" / "web" / "app.py"
    assert app.exists()
    spec = importlib.util.spec_from_file_location("fs_web_app", app)
    assert spec is not None and spec.loader is not None


def test_fs_web_entry_exists():
    from pathlib import Path

    pyproject = ROOT / "apps" / "fire-sight" / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "fs-web" in text
