from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import markdown
from .models import Preferences, Schedule, Sources
from .scheduler import Scheduler
from .service import NewsService
from .settings import Settings


class GenerateRequest(BaseModel):
    send: bool = False


def create_app(settings: Settings | None = None, *, enable_scheduler: bool = True) -> FastAPI:
    settings = settings or Settings.from_env()
    service = NewsService(settings)
    scheduler = Scheduler(service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Abandoned tasks from a dead process are surfaced instead of shown as running forever.
        for run in service.store.records("runs"):
            if run.get("status") not in {"queued", "running"}:
                continue
            try:
                if run.get("pid", 0) <= 0:
                    raise ProcessLookupError()
                os.kill(run["pid"], 0)
            except ProcessLookupError:
                run.update(status="failed", error="上次进程已退出，请重新执行任务")
                service.store.write(service.run_path(run["id"]), run)
            except PermissionError:
                pass
        if enable_scheduler:
            scheduler.start()
        yield
        scheduler.stop()
        service.close()

    app = FastAPI(title="新闻简报 · 个性化新闻 Agent", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        # No-login app: prevent arbitrary websites from driving a local service.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请从本应用页面执行操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "scheduler_enabled": scheduler.scheduler.running}

    @app.get("/api/status")
    def status():
        return service.status()

    @app.get("/api/preferences")
    def preferences():
        return service.store.preferences()

    @app.put("/api/preferences")
    def save_preferences(value: Preferences):
        return service.store.save_preferences(value)

    @app.get("/api/config")
    def config():
        return {"sources": service.store.sources(), "scheduler": service.store.schedule()}

    @app.put("/api/sources")
    def save_sources(value: Sources):
        with service.store.lock("sources"):
            service.store.write("config/sources.json", value.model_dump(mode="json"))
        return value

    @app.put("/api/schedule")
    def save_schedule(value: Schedule):
        with service.store.lock("schedule"):
            service.store.write("config/scheduler.json", value.model_dump(mode="json"))
        return value

    @app.get("/api/news")
    def news(q: str = "", limit: int = Query(default=30, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        articles = sorted(
            service.store.articles(), key=lambda a: a.published_at or a.first_seen_at, reverse=True
        )
        if q:
            articles = [
                a
                for a in articles
                if q.casefold() in f"{a.title} {a.content.snippet} {a.content.text}".casefold()
            ]
        return {
            "total": len(articles),
            "items": [
                {
                    "id": a.id,
                    "title": a.title,
                    "url": a.canonical_url,
                    "publisher": a.publisher.name or a.publisher.domain,
                    "published_at": a.published_at,
                    "first_seen_at": a.first_seen_at,
                    "snippet": a.content.snippet[:500],
                    "content_status": a.content.status,
                }
                for a in articles[offset : offset + limit]
            ],
        }

    @app.get("/api/briefings")
    def briefings():
        rows = sorted(service.store.records("briefings"), key=lambda b: b.get("created_at", ""), reverse=True)
        return [
            {
                key: value
                for key, value in b.items()
                if key in {"id", "title", "overview", "mode", "created_at", "delivery", "tool_call_count"}
            }
            | {"item_count": len(b.get("items", []))}
            for b in rows
        ]

    @app.get("/api/briefings/{identity}")
    def briefing(identity: str):
        value = service.briefing(identity)
        if not value:
            raise HTTPException(404, "简报不存在")
        return value

    @app.get("/api/briefings/{identity}/markdown")
    def export(identity: str):
        value = briefing(identity)
        return Response(
            markdown(value),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{identity}.md"'},
        )

    @app.post("/api/briefings/{identity}/send")
    def send(identity: str):
        value = service.send_briefing(briefing(identity))
        return value["delivery"]

    @app.post("/api/collect", status_code=202)
    def start_collection():
        return service.launch("collect", backfill=True)

    @app.post("/api/generate", status_code=202)
    def start_generation(value: GenerateRequest):
        return service.launch("generate", send=value.send)

    @app.post("/api/demo", status_code=202)
    def demo():
        return service.launch("demo")

    @app.post("/api/cleanup", status_code=202)
    def cleanup():
        return service.launch("cleanup")

    @app.get("/api/runs")
    def runs():
        return sorted(service.store.records("runs"), key=lambda r: r.get("created_at", ""), reverse=True)[:40]

    @app.get("/api/runs/{identity}")
    def run(identity: str):
        value = service.store.read(service.run_path(identity))
        if not value:
            raise HTTPException(404, "任务不存在")
        return value

    frontend = settings.frontend_dir or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(frontend / "index.html")
    else:

        @app.get("/")
        def index():
            return {
                "message": "后端已启动。请在 frontend 目录执行 npm ci && npm run build 后重启，或运行 npm run dev。",
                "api_docs": "/docs",
            }

    return app


def app_factory():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
