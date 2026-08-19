"""Trove - claims the games that are temporarily free, on your own accounts.

The API, and the built SPA it serves. One process: FastAPI, a per-account
scheduler and a Playwright browser that is only opened when there is something
to claim.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import scheduler
from . import __version__ as VERSION
from .auth import bootstrap_admin
from .browser import manager
from .config import get_settings
from .db import init_db
from .routers import accounts, ledger, live, settings as settings_router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)

# httpx logs every request URL at INFO, query string included. A Discord
# webhook URL *is* the secret, and it rides in the path. At the default level
# that puts it in `docker logs`, which is what people paste into issues.
if settings.log_level.upper() != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("trove")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_admin()
    scheduler.start()
    log.info("%s %s is ready on port %s.", settings.app_name, VERSION, settings.port)
    try:
        yield
    finally:
        # Order matters. The scheduler is stopped first and *awaited*, so a run
        # that is mid-claim writes its ledger row before the browser it is
        # driving is taken away. A claim that happened with no record of it is
        # the one failure this app must not have.
        await scheduler.stop()
        await manager.stop()


app = FastAPI(
    title="Trove",
    description="Claims the games that are temporarily free, on your own accounts.",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="trove_session",
    max_age=settings.session_ttl_days * 24 * 3600,
    same_site="lax",
    # Not `secure=True`: most self-hosted installs are reached over plain HTTP
    # on a LAN address, and a secure cookie there is a cookie the browser never
    # sends, which presents as a login page that will not stay logged in.
    # Behind a TLS proxy, set it.
    https_only=False,
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

for router in (
    settings_router.router,
    accounts.router,
    ledger.router,
    live.router,
):
    app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "app": settings.app_name}


# ── The built SPA ───────────────────────────────────────────────────────────

FRONTEND_DIR = Path(__file__).resolve().parent / "static"
FRONTEND_ROOT = FRONTEND_DIR.resolve()


def static_file_for(full_path: str) -> Path | None:
    """Resolve a request path to a file inside `static/`, or None.

    `full_path` arrives already percent-decoded, so `%2e%2e%2f` reaches here as
    a real parent-directory hop and Starlette does not collapse it. Without the
    containment check this serves any file the process can read, which in this
    app includes the key that decrypts every stored game key.
    """
    if not full_path:
        return None
    candidate = (FRONTEND_DIR / full_path).resolve()
    if not candidate.is_relative_to(FRONTEND_ROOT):
        return None
    return candidate if candidate.is_file() else None


# Tested on index.html rather than the directory: a half-written `static/`
# makes StaticFiles raise at import time, which turns a partial build into a
# container that cannot boot at all rather than one serving the API without a UI.
if (FRONTEND_DIR / "index.html").is_file():
    if (FRONTEND_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve the SPA, letting client-side routing own unknown paths."""
        candidate = static_file_for(full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")

else:  # pragma: no cover - a development checkout with no built frontend

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "app": settings.app_name,
            "message": "The frontend is not built. Run `npm run build` in ./frontend.",
            "docs": "/api/docs",
        }
