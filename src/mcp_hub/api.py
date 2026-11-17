from __future__ import annotations

import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import __version__, alerts, saved_searches, scheduler, taxonomy
from . import provenance as _provenance
from .auth import add_auth_middleware
from .dashboard import HTML as DASHBOARD_HTML
from .database import Catalog
from .headers import CSP_NONCE_PLACEHOLDER, install_security_headers
from .metrics import install_observability, metrics_endpoint
from .payload_limit import install_payload_limit
from .ratelimit import install_rate_limit
from .redact import redact

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
)


def _cors_origins() -> list[str]:
    raw = os.environ.get("MCP_HUB_CORS_ORIGINS", "").strip()
    if not raw:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(
    db_path: str | None = None, catalog: Catalog | None = None, api_key: str | None = None
):
    catalog = catalog or Catalog(db_path or "mcp_hub.sqlite3")
    app = FastAPI(title="mcp-hub", version=__version__, debug=False)
    add_auth_middleware(app, api_key)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["X-API-Key", "Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )

    # Runtime hardening: per-IP rate limit (P1.1) and body-size cap (P2.2).
    install_rate_limit(app)  # 120 req/min per IP, MCP_HUB_RATE_LIMIT_PER_MIN
    install_payload_limit(app)  # 1 MiB body cap, MCP_HUB_MAX_BODY_BYTES
    # Middleware added later wraps earlier ones. Security headers are added
    # after the limiters, so their 401/429/413 responses carry the header set;
    # observability goes on top of everything so it can count those refusals.
    install_security_headers(app)
    install_observability(app)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """Return a stable body; never leak a traceback or a token."""
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "type": redact(type(exc).__name__)},
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        nonce = getattr(request.state, "csp_nonce", "")
        return DASHBOARD_HTML.replace(CSP_NONCE_PLACEHOLDER, nonce)

    @app.get("/api/health")
    def health():
        try:
            with catalog.connect() as con:
                con.execute("SELECT 1").fetchone()
            return {"status": "ok", "version": __version__, "db": "ok"}
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "error", "detail": redact(e)})

    @app.get("/api/stats")
    def stats():
        return catalog.stats()

    @app.get("/api/search")
    def search(
        q: str = "",
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        fts: bool = False,
    ):
        # Fetch one extra row so `next_offset` is exact instead of a guess: a
        # full page is not proof that another page exists.
        probe = (
            catalog.search_fts(q, limit=limit + 1, offset=offset)
            if fts
            else catalog.search(q, limit=limit + 1, offset=offset)
        )
        has_more = len(probe) > limit
        results = probe[:limit]
        next_offset = offset + limit if has_more else None
        return {
            "query": q,
            "count": len(results),
            "limit": limit,
            "offset": offset,
            "next_offset": next_offset,
            "results": results,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return metrics_endpoint(catalog)

    @app.get("/api/servers/{server_id}")
    def get_server(server_id: int):
        row = catalog.get_server(server_id)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return row

    @app.get("/api/servers/{server_id}/provenance")
    @app.get("/api/server/{server_id}/provenance")
    def server_provenance(
        server_id: int,
        field: str | None = Query(None, description="Filter to one field"),
        mode: Literal["fields", "conflicts", "events"] = Query("fields"),
        limit: int = Query(50, ge=1, le=500),
    ):
        if not catalog.get_server(server_id):
            raise HTTPException(status_code=404, detail=f"server {server_id} not found")
        if mode == "conflicts":
            return {
                "server_id": server_id,
                "mode": "conflicts",
                "conflicts": _provenance.field_conflicts(catalog, server_id),
            }
        if mode == "events":
            return {
                "server_id": server_id,
                "mode": "events",
                "events": _provenance.event_history(catalog, server_id, limit=limit),
            }
        return {
            "server_id": server_id,
            "mode": "fields",
            "history": _provenance.field_history(catalog, server_id, field),
        }

    @app.get("/api/searches")
    def list_searches():
        return {"searches": saved_searches.list_all(catalog)}

    @app.post("/api/searches")
    def save_search(payload: dict):
        name = (payload or {}).get("name", "")
        query = (payload or {}).get("query", "")
        if not name or not query:
            raise HTTPException(status_code=400, detail="name and query required")
        saved_searches.save(catalog, name, query)
        return {"ok": True}

    @app.delete("/api/searches/{name}")
    def delete_search(name: str):
        ok = saved_searches.delete(catalog, name)
        if not ok:
            raise HTTPException(status_code=404, detail="not found")
        return {"ok": True}

    @app.post("/api/searches/{name}/run")
    def run_search(name: str):
        try:
            return saved_searches.run(catalog, name)
        except KeyError:
            raise HTTPException(status_code=404, detail="not found") from None

    @app.post("/api/searches/run-all")
    def run_all():
        return scheduler.run_all(catalog)

    @app.get("/api/scheduler/runs")
    def scheduler_runs(limit: int = Query(20, ge=1, le=100)):
        return {"runs": catalog.scheduler_runs(limit=limit)}

    @app.get("/api/categories")
    def categories():
        return {
            "custom": catalog.custom_categories(),
            "overrides": catalog.category_overrides(),
        }

    @app.post("/api/categories")
    def add_category(payload: dict):
        try:
            catalog.add_custom_category(
                (payload or {}).get("name", ""),
                ",".join(payload.get("terms", []))
                if isinstance(payload.get("terms"), list)
                else payload.get("terms", ""),
                (payload or {}).get("notes", ""),
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=redact(e)) from e
        return {"ok": True}

    @app.post("/api/categories/classify")
    def classify(overwrite: bool = False):
        return taxonomy.classify_catalog(catalog, overwrite=overwrite)

    @app.get("/api/categories/export")
    def export_categories():
        return catalog.export_custom_categories_json()

    @app.post("/api/categories/import")
    def import_categories(payload: dict):
        replace = bool((payload or {}).get("replace", False))
        data = (payload or {}).get("data", payload)
        try:
            return catalog.import_custom_categories_json(data, replace=replace)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=redact(e)) from e

    @app.get("/api/alerts")
    def list_alerts_api():
        return {"alerts": alerts.list_alerts(catalog)}

    @app.post("/api/alerts")
    def add_alert_api(payload: dict):
        channel = (payload or {}).get("channel", "webhook")
        target = (payload or {}).get("target", "")
        query = (payload or {}).get("query", "")
        if not target:
            raise HTTPException(status_code=400, detail="target required")
        alert_id = alerts.add_alert(catalog, channel, target, query)
        return {"id": alert_id}

    return app
