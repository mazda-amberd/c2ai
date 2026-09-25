"""Serve the built frontend (``frontend/dist``) from the API process."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from c2ai.config import get_settings

logger = logging.getLogger(__name__)

# Unknown paths under these prefixes are API mistakes, not client-side routes,
# so they get a JSON 404 instead of the SPA's index.html.
_API_PREFIXES = ("api/", "auth/", "users/", "jobs/")
# index.html names the current build's hashed bundles; a browser that reuses a
# cached copy keeps running the previous release. (The bundles themselves are
# content-hashed, so they may be cached.)
_REVALIDATE = {"Cache-Control": "no-cache"}


def _dist_dir() -> Path:
    configured = get_settings().frontend_dist.strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # backend/c2ai/core/frontend.py -> <repo>/frontend/dist
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def setup_frontend_serving(app: FastAPI) -> None:
    """Mount static assets and a client-side-routing fallback to ``index.html``."""

    static_dir = _dist_dir()
    index_html = static_dir / "index.html"
    if not index_html.exists():
        logger.warning(
            "Frontend build not found at '%s'; the UI will not be served. "
            "Run 'npm run build' in frontend/ or set C2AI_FRONTEND_DIST.",
            static_dir,
        )
        return

    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        if full_path.startswith(_API_PREFIXES):
            return JSONResponse(
                status_code=404,
                content={"detail": "Not Found", "code": "HTTP_404"},
            )
        candidate = (static_dir / full_path).resolve()
        # Serve real top-level files (favicon, logos) but never escape dist/.
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(static_dir)
        ):
            return FileResponse(candidate)
        return FileResponse(index_html, headers=_REVALIDATE)
