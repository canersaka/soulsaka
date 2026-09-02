"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from soulsaka import __version__
from soulsaka.config import Settings, get_settings
from soulsaka.hub.jobs import JobRunner, default_handlers
from soulsaka.hub.routes import (
    admin,
    captures,
    chat,
    corpus,
    eval,
    memories,
    sync,
    system,
    training,
    voice,
)
from soulsaka.hub.state import HubState

log = logging.getLogger(__name__)

DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def find_web_dir(settings: Settings) -> Path | None:
    candidates = []
    if settings.hub.web_dir:
        candidates.append(Path(settings.hub.web_dir))
    if os.environ.get("SOULSAKA_WEB_DIR"):
        candidates.append(Path(os.environ["SOULSAKA_WEB_DIR"]))
    here = Path(__file__).resolve()
    candidates.append(here.parents[1] / "web_dist")  # packaged build
    candidates.append(here.parents[3] / "web" / "dist")  # repo checkout
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def create_app(
    settings: Settings | None = None,
    *,
    state: HubState | None = None,
    start_workers: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    hub = state or HubState(settings)
    runner = JobRunner(hub)
    for kind, handler in default_handlers().items():
        runner.register(kind, handler)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_workers:
            runner.start(settings.hub.workers)
        try:
            yield
        finally:
            runner.stop()
            if state is None:
                hub.close()

    app = FastAPI(
        title="soulsaka hub",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.hub = hub
    app.state.runner = runner
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (system, corpus, captures, memories, sync, chat, admin, training, eval, voice):
        app.include_router(r.router, prefix="/api")

    @app.exception_handler(404)
    async def not_found(request: Request, exc):  # noqa: ARG001
        return JSONResponse({"detail": "not found"}, status_code=404)

    web_dir = find_web_dir(settings)
    if web_dir is not None:
        assets = web_dir / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = web_dir / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (web_dir / path).resolve() if path else index
            if path and candidate.is_file() and str(candidate).startswith(str(web_dir.resolve())):
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @app.get("/", include_in_schema=False)
        async def no_web():
            return JSONResponse(
                {
                    "hub": "soulsaka",
                    "version": __version__,
                    "web_ui": "not built; run `npm run build` in web/ or set hub.web_dir",
                    "api_docs": "/api/docs",
                }
            )

    return app
