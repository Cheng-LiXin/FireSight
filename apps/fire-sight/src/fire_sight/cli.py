"""fs-serve: 启动 FireSight REST 服务，并托管打包好的前端。"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="fs-serve",
        description="启动 FireSight 服务（REST + 前端）",
    )
    ap.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重载")
    args = ap.parse_args()

    import uvicorn

    # 用 import string 而非 app 对象，--reload 才能生效
    uvicorn.run(
        "fire_sight.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
