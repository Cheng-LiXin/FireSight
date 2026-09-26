"""FastAPI 应用：REST 层 + 托管 Vue 单页应用。

开发：Vite dev server (5173) 代理到本服务 (8000)，见 apps/web/vite.config.ts
生产：`npm run build` 产出 apps/web/dist，由本服务在根路径托管。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from fire_sight_platform.common.paths import ROOT_DIR

from fire_sight._version import __version__ as fire_sight_version
from fire_sight.api.routers import infer, models, overview, system, train

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
    app.include_router(models.router)
    app.include_router(infer.router)
    app.include_router(train.router)

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "spa_built": WEB_DIST.exists()}

    # 前端用 HTML5 history 模式，路由由浏览器接管。必须先挂构建产物、
    # 再用兜底路由把未命中的路径交回 index.html——直接 StaticFiles(html=True)
    # 挂在根路径只会服务真实存在的文件，访问 /detect 会 404，深链接与刷新全废。
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                # 未匹配的接口路径应显式 404，不要回一份 HTML
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = WEB_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")

    return app


app = create_app()
