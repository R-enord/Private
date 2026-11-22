"""FastAPI application: API + built SPA, local-only by default.

Security middleware:
* Host header allowlist (TrustedHostMiddleware) — defends the localhost server against DNS rebinding.
* CSRF: state-changing /api requests require ``X-Hub-Request: 1`` (forces a CORS preflight that we never
  grant) and, when present, a same-origin ``Origin``; session cookie is SameSite=Strict + HttpOnly.
* Strict CSP and other headers; no CORS is configured (same-origin only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from hub import __version__
from hub.api import routes_admin, routes_community, routes_items, routes_user
from hub.config import get_settings

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, allowed_hosts: list[str]):
        super().__init__(app)
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith("/api/") and request.method not in SAFE_METHODS:
            if request.headers.get("x-hub-request") != "1":
                return JSONResponse({"detail": "missing X-Hub-Request header (CSRF protection)"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and (urlsplit(origin).hostname or "").lower() not in self.allowed_hosts:
                return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def create_app() -> FastAPI:
    settings = get_settings()
    settings.check_exposure()
    app = FastAPI(title="AI Intelligence Hub", version=__version__, docs_url="/api/docs" if settings.env != "production" else None,
                  redoc_url=None, openapi_url="/api/openapi.json")
    hosts = list(settings.allowed_hosts) + (["testserver"] if settings.is_test else [])
    app.add_middleware(SecurityMiddleware, allowed_hosts=hosts)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.include_router(routes_admin.router)
    app.include_router(routes_items.router)
    app.include_router(routes_user.router)
    app.include_router(routes_community.router)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from sqlalchemy import text

        from hub.db import session_scope

        db_ok = True
        try:
            with session_scope() as s:
                s.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "version": __version__, "database": db_ok,
                "zero_cost_mode": True}

    dist: Path = settings.frontend_dist
    if (dist / "index.html").exists():
        if (dist / "assets").exists():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            return HTMLResponse("<h1>AI Intelligence Hub</h1><p>The web UI is not built yet. Run "
                                "<code>make build</code> (or <code>cd frontend &amp;&amp; pnpm build</code>). "
                                "The API is available under <code>/api</code>.</p>")
    return app
