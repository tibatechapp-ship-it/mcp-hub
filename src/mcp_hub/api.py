from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from . import __version__, alerts, saved_searches, scheduler, taxonomy
from .auth import add_auth_middleware, get_api_key_from_env
from .dashboard import HTML as DASHBOARD_HTML
from .database import Catalog


def create_app(db_path: str | None = None, catalog: Catalog | None = None, api_key: str | None = None):
    catalog = catalog or Catalog(db_path or "mcp_hub.sqlite3")
    app = FastAPI(title="mcp-hub", version=__version__)
    add_auth_middleware(app, api_key or get_api_key_from_env())

    @app.get("/", response_class=HTMLResponse)
    def index():
        return DASHBOARD_HTML

    @app.get("/api/health")
    def health():
        try:
            with catalog.connect() as con:
                con.execute("SELECT 1").fetchone()
            return {"status": "ok", "version": __version__, "db": "ok"}
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})

    @app.get("/api/stats")
    def stats():
        return catalog.stats()

    @app.get("/api/search")
    def search(q: str = "", limit: int = Query(50, ge=1, le=200), fts: bool = False):
        results = catalog.search_fts(q, limit=limit) if fts else catalog.search(q, limit=limit)
        return {"query": q, "count": len(results), "results": results}

    @app.get("/api/servers/{server_id}")
    def get_server(server_id: int):
        row = catalog.get_server(server_id)
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return row

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
                ",".join(payload.get("terms", [])) if isinstance(payload.get("terms"), list) else payload.get("terms", ""),
                (payload or {}).get("notes", ""),
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(e)) from e
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
            raise HTTPException(status_code=400, detail=str(e)) from e

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
