"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.app.config import settings
from backend.app.scheduler import start_scheduler, stop_scheduler
from backend.app.routers import targets, results, screenshots, alerts, dashboard, tasks, settings as settings_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Site Monitor...")

    # Ensure screenshots dir exists
    Path(settings.screenshots_dir).mkdir(parents=True, exist_ok=True)

    # Start scheduler
    start_scheduler()

    yield

    # Shutdown
    await stop_scheduler()
    logger.info("Site Monitor stopped.")


app = FastAPI(
    title="Site Monitor",
    description="大规模域名健康检测 + 截图渲染异常告警平台",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS (allow frontend dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routers
app.include_router(targets.router)
app.include_router(results.router)
app.include_router(screenshots.router)
app.include_router(alerts.router)
app.include_router(dashboard.router)
app.include_router(tasks.router)
app.include_router(settings_router.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve screenshots as static files
app.mount("/screenshots", StaticFiles(directory=settings.screenshots_dir), name="screenshots")

# Serve frontend (production build) with SPA fallback
frontend_dir = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dir.exists():
    # Mount /assets for JS/CSS bundles
    assets_dir = frontend_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # SPA fallback: all other paths return index.html
    index_html = frontend_dir / "index.html"

    @app.get("/{full_path:path}")
    async def spa_fallback(request: Request, full_path: str):
        # If there's an actual file in dist/, serve it (e.g. favicon.ico)
        file_path = frontend_dir / full_path
        if full_path and file_path.is_file() and file_path.is_relative_to(frontend_dir):
            return FileResponse(str(file_path))
        # Otherwise return index.html for Vue Router to handle
        return FileResponse(str(index_html))
