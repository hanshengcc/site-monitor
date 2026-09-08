"""Scheduler - manages periodic check and screenshot tasks."""
import asyncio
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from backend.app.config import settings
from backend.app.checker import run_checks, check_progress
from backend.app.screenshoter import run_screenshots, close_browser
from backend.app.alerter import dispatch_alerts

scheduler = AsyncIOScheduler()

# Lock to prevent overlapping runs
_check_running = asyncio.Lock()
_shot_running = asyncio.Lock()


async def _safe_run_checks():
    if _check_running.locked() or check_progress.get("running"):
        logger.warning("Previous check round still running, skip auto check.")
        return
    async with _check_running:
        try:
            logger.info("Auto-trigger: Starting scheduled HTTP checks...")
            await run_checks()
        except Exception as e:
            logger.exception(f"Check round failed: {e}")
    # Dispatch alerts after checks
    try:
        await dispatch_alerts()
    except Exception as e:
        logger.exception(f"Alert dispatch failed: {e}")


async def _safe_run_screenshots():
    if _shot_running.locked():
        logger.warning("Previous screenshot round still running, skip.")
        return
    async with _shot_running:
        try:
            await run_screenshots()
        except Exception as e:
            logger.exception(f"Screenshot round failed: {e}")
    # Dispatch alerts after screenshots
    try:
        await dispatch_alerts()
    except Exception as e:
        logger.exception(f"Alert dispatch failed: {e}")


async def run_daily_maintenance():
    """Daily maintenance:
    1. Ensure future monthly partitions exist.
    2. Clean up old check results (>30 days).
    3. Clean up old normal screenshots (>14 days, preserving anomaly & baseline).
    """
    logger.info("Running daily maintenance task...")
    from backend.app.database import AsyncSessionLocal
    from backend.app.models import Screenshot, Baseline, CheckResult
    from sqlalchemy import text, select, delete
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        # 1. Ensure monthly partitions exist
        try:
            await session.execute(text("SELECT create_monthly_partitions();"))
            await session.commit()
            logger.info("Monthly partitions verified/created.")
        except Exception as e:
            logger.warning(f"Failed to create monthly partitions: {e}")

        # 2. Clean old check results (>30 days)
        cutoff_30d = now - timedelta(days=30)
        try:
            res = await session.execute(
                delete(CheckResult).where(CheckResult.checked_at < cutoff_30d)
            )
            await session.commit()
            if res.rowcount and res.rowcount > 0:
                logger.info(f"Cleaned up {res.rowcount} old check_results records (>30 days).")
        except Exception as e:
            logger.warning(f"Failed to clean old check_results: {e}")

        # 3. Clean old normal screenshots (>14 days)
        cutoff_14d = now - timedelta(days=14)
        try:
            b_rows = await session.execute(select(Baseline.screenshot_id))
            baseline_ids = set(b_rows.scalars().all())

            stmt = select(Screenshot).where(
                Screenshot.taken_at < cutoff_14d,
                Screenshot.is_anomaly == False,
            )
            if baseline_ids:
                stmt = stmt.where(Screenshot.id.not_in(baseline_ids))

            old_shots = (await session.execute(stmt.limit(1000))).scalars().all()
            shots_dir = Path(settings.screenshots_dir)
            deleted_files = 0

            for shot in old_shots:
                if shot.file_path:
                    fp = shots_dir / shot.file_path
                    if fp.exists():
                        try:
                            fp.unlink()
                            deleted_files += 1
                        except OSError:
                            pass
                if shot.thumb_path:
                    tp = shots_dir / shot.thumb_path
                    if tp.exists():
                        try:
                            tp.unlink()
                        except OSError:
                            pass
                await session.delete(shot)

            await session.commit()
            if old_shots:
                logger.info(f"Cleaned up {len(old_shots)} old normal screenshots ({deleted_files} files deleted).")
        except Exception as e:
            logger.warning(f"Failed to clean old screenshots: {e}")


def start_scheduler():
    """Start the background scheduler."""
    now = datetime.now(timezone.utc)
    scheduler.add_job(
        _safe_run_checks,
        trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        id="http_checks",
        name="HTTP Health Checks",
        replace_existing=True,
        next_run_time=now,  # Trigger immediately on startup, then repeat every check_interval_minutes
    )

    scheduler.add_job(
        _safe_run_screenshots,
        trigger=IntervalTrigger(minutes=settings.screenshot_interval_minutes),
        id="screenshots",
        name="Page Screenshots",
        replace_existing=True,
        # Omit next_run_time so first screenshot round runs after screenshot_interval_minutes
    )

    scheduler.add_job(
        run_daily_maintenance,
        trigger=IntervalTrigger(hours=24),
        id="daily_maintenance",
        name="Daily Maintenance & Cleanup",
        replace_existing=True,
        next_run_time=now,  # Run once on startup, then every 24h
    )

    scheduler.start()
    logger.info(
        f"Scheduler started: checks every {settings.check_interval_minutes}min (first run immediate), "
        f"screenshots every {settings.screenshot_interval_minutes}min, "
        f"maintenance every 24h"
    )


async def stop_scheduler():
    scheduler.shutdown(wait=False)
    await close_browser()
    logger.info("Scheduler stopped")


def restart_scheduler():
    """Restart scheduler with updated intervals."""
    try:
        scheduler.reschedule_job(
            "http_checks",
            trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        )
        scheduler.reschedule_job(
            "screenshots",
            trigger=IntervalTrigger(minutes=settings.screenshot_interval_minutes),
        )
        logger.info(
            f"Scheduler rescheduled: checks every {settings.check_interval_minutes}min, "
            f"screenshots every {settings.screenshot_interval_minutes}min"
        )
    except Exception as e:
        logger.error(f"Failed to reschedule: {e}")
