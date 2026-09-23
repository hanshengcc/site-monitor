"""FastAPI application entry point."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from loguru import logger
from sqlalchemy import select

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import GlobalSetting
from backend.app.scheduler import start_scheduler, stop_scheduler
from backend.app.routers import targets, results, screenshots, snapshots, alerts, dashboard, tasks, settings as settings_router, system


async def load_settings_from_db():
    """Load persisted global settings from DB into memory settings on startup."""
    try:
        async with AsyncSessionLocal() as session:
            rows = await session.execute(select(GlobalSetting))
            for r in rows.scalars().all():
                val = r.value
                if r.key == "check_interval":
                    val_int = int(val) if str(val).isdigit() else 300
                    settings.check_interval_minutes = max(1, val_int // 60) if val_int >= 60 else 1
                elif r.key == "screenshot_interval":
                    val_int = int(val) if str(val).isdigit() else 21600
                    settings.screenshot_interval_minutes = max(1, val_int // 60) if val_int >= 60 else 1
                elif r.key == "snapshot_interval":
                    val_int = int(val) if str(val).isdigit() else 21600
                    settings.snapshot_interval_minutes = max(1, val_int // 60) if val_int >= 60 else 1
                elif r.key == "max_concurrent_checks":
                    settings.max_concurrent_checks = int(val)
                elif r.key == "playwright_concurrency":
                    settings.playwright_concurrency = int(val)
                elif r.key == "default_timeout":
                    settings.check_timeout = int(val)
                elif r.key == "consecutive_fails_threshold":
                    settings.consecutive_fails_threshold = int(val)
        logger.info(
            f"Loaded settings from DB: check_interval={settings.check_interval_minutes}min, "
            f"screenshot_interval={settings.screenshot_interval_minutes}min, "
            f"max_concurrent={settings.max_concurrent_checks}"
        )
    except Exception as e:
        logger.warning(f"Failed to load global settings from DB on startup: {e}")


async def init_db_schema():
    """Ensure newly added columns and tables exist (lightweight auto-migration)."""
    from sqlalchemy import text
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS global_settings (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                )
            """))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS group_settings (
                    group_name TEXT PRIMARY KEY,
                    max_concurrency INTEGER DEFAULT 10,
                    rate_limit INTEGER DEFAULT 0,
                    request_timeout INTEGER DEFAULT 15,
                    user_agent TEXT,
                    enabled BOOLEAN DEFAULT TRUE,
                    note TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """))
            await session.execute(text("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS rate_limit INTEGER DEFAULT 0"))
            await session.execute(text("ALTER TABLE targets ADD COLUMN IF NOT EXISTS expect_dns_server TEXT"))
            await session.execute(text("ALTER TABLE targets ADD COLUMN IF NOT EXISTS snapshot_interval INTEGER DEFAULT 21600"))
            await session.execute(text("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS expect_dns_server TEXT"))
            await session.execute(text("ALTER TABLE group_settings ADD COLUMN IF NOT EXISTS snapshot_interval INTEGER DEFAULT 21600"))
            await session.execute(text("ALTER TABLE target_status ADD COLUMN IF NOT EXISTS dns_server TEXT"))
            await session.execute(text("CREATE INDEX IF NOT EXISTS idx_target_status_dns_server ON target_status(dns_server)"))
            await session.execute(text("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
                    taken_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    file_path TEXT NOT NULL,
                    file_size INTEGER,
                    compressed_size INTEGER,
                    content_hash VARCHAR(64),
                    page_title TEXT,
                    http_status SMALLINT,
                    dom_text_length INTEGER DEFAULT 0,
                    has_changed BOOLEAN DEFAULT TRUE,
                    headers JSONB DEFAULT '{}'
                )
            """))
            await session.execute(text("CREATE INDEX IF NOT EXISTS idx_snapshots_target ON snapshots(target_id, taken_at DESC)"))
            await session.execute(text("CREATE INDEX IF NOT EXISTS idx_snapshots_hash ON snapshots(target_id, content_hash)"))
            await session.execute(text("ALTER TABLE target_status ADD COLUMN IF NOT EXISTS last_snapshot_id BIGINT"))
            await session.execute(text("ALTER TABLE target_status ADD COLUMN IF NOT EXISTS last_snapshot_at TIMESTAMPTZ"))
            # Every check round looks up open anomalies by target; a partial index
            # keeps that lookup proportional to open anomalies, not to all history.
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_anomalies_open_by_target "
                "ON anomalies(target_id, anomaly_type) WHERE state = 'open'"
            ))
            # Screenshot/snapshot rounds select targets whose interval has elapsed.
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_target_status_last_screenshot_at "
                "ON target_status(last_screenshot_at ASC NULLS FIRST)"
            ))
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_target_status_last_snapshot_at "
                "ON target_status(last_snapshot_at ASC NULLS FIRST)"
            ))
            await session.commit()
            logger.info("Database schema auto-check completed.")
    except Exception as e:
        logger.warning(f"Database schema auto-check warning: {e}")

    # Trigram index for the targets list search box (ILIKE '%term%'), which is a
    # sequential scan without it. Needs the pg_trgm extension, which may not be
    # grantable — keep it in its own transaction so a failure cannot roll back
    # the schema work above.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await session.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_targets_url_trgm "
                "ON targets USING gin (url gin_trgm_ops)"
            ))
            await session.commit()
    except Exception as e:
        logger.info(f"Skipping trigram search index (pg_trgm unavailable): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Site Monitor...")

    # Ensure screenshots and snapshots dirs exist
    Path(settings.screenshots_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.snapshots_dir).mkdir(parents=True, exist_ok=True)

    # Auto-migrate/verify database schema
    await init_db_schema()

    # Load persistent settings from DB
    await load_settings_from_db()

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

# Compress JS/CSS bundles, JSON list responses and CSV exports. The frontend
# bundle alone is ~1.6MB of highly compressible text.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS (allow frontend dev server)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def static_cache_headers_middleware(request: Request, call_next):
    """Let browsers cache hashed build assets permanently.

    Vite fingerprints every file under /assets, so a given URL's content can never
    change and revalidation requests are pure waste.
    """
    response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.middleware("http")
async def localhost_only_middleware(request: Request, call_next):
    if settings.localhost_only:
        probe_key = request.headers.get("X-System-Probe-Key")
        if probe_key == "site-monitor-probe-2026":
            return await call_next(request)
        client_ip = request.client.host if request.client else ""
        if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return PlainTextResponse(
                "Forbidden: Localhost access only. Please use SSH tunnel or local reverse proxy.",
                status_code=403,
            )
    return await call_next(request)

# API routers
app.include_router(targets.router)
app.include_router(results.router)
app.include_router(screenshots.router)
app.include_router(snapshots.router)
app.include_router(alerts.router)
app.include_router(dashboard.router)
app.include_router(tasks.router)
app.include_router(settings_router.router)
app.include_router(system.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve screenshots as static files. The directories must exist before the mount,
# which runs at import time — lifespan startup is too late and a missing directory
# would make the whole app fail to import on a fresh deployment.
Path(settings.screenshots_dir).mkdir(parents=True, exist_ok=True)
Path(settings.snapshots_dir).mkdir(parents=True, exist_ok=True)
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
