"""FastAPI 应用：REST 层 + 托管 Vue 单页应用。

开发：Vite dev server (5173) 代理到本服务 (8000)，见 apps/web/vite.config.ts
生产：`npm run build` 产出 apps/web/dist，由本服务在根路径托管。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from fire_sight_platform.common.paths import ROOT_DIR

from fire_sight._version import __version__ as fire_sight_version
from fire_sight.api.routers import overview, system

WEB_DIST: Path = ROOT_DIR / "apps" / "web" / "dist"

# Vite 开发服务器；仅本机演示，不做鉴权
DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def create_app() -> FastAPI:
    app = FastAPI(
        title="FireSight API",
        description="火灾烟雾智能预警系统 —— 推理、训练、事件与模型接口",
        version=fire_sight_version,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(system.router)
    app.include_router(overview.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "spa_built": WEB_DIST.exists()}

    # 静态资源最后挂载，避免吞掉 /api 路由
    if WEB_DIST.exists():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="spa")

    return app


app = create_app()
