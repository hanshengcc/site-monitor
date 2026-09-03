"""Scheduler - manages periodic check and screenshot tasks."""
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from backend.app.config import settings
from backend.app.checker import run_checks
from backend.app.screenshoter import run_screenshots, close_browser
from backend.app.alerter import dispatch_alerts

scheduler = AsyncIOScheduler()

# Lock to prevent overlapping runs
_check_running = asyncio.Lock()
_shot_running = asyncio.Lock()


async def _safe_run_checks():
    if _check_running.locked():
        logger.warning("Previous check round still running, skip.")
        return
    async with _check_running:
        try:
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


def start_scheduler():
    """Start the background scheduler."""
    scheduler.add_job(
        _safe_run_checks,
        trigger=IntervalTrigger(minutes=settings.check_interval_minutes),
        id="http_checks",
        name="HTTP Health Checks",
        replace_existing=True,
        next_run_time=None,  # Don't run immediately; trigger manually first time
    )

    scheduler.add_job(
        _safe_run_screenshots,
        trigger=IntervalTrigger(minutes=settings.screenshot_interval_minutes),
        id="screenshots",
        name="Page Screenshots",
        replace_existing=True,
        next_run_time=None,
    )

    scheduler.start()
    logger.info(
        f"Scheduler started: checks every {settings.check_interval_minutes}min, "
        f"screenshots every {settings.screenshot_interval_minutes}min"
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
