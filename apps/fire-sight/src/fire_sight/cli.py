"""fs-web: 启动 Streamlit 监测界面。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    app = Path(__file__).resolve().parent / "web" / "app.py"
    if not app.exists():
        raise FileNotFoundError(f"找不到 web 入口: {app}")

    cmd = [sys.executable, "-m", "streamlit", "run", str(app)]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
