"""Scheduler - manages periodic check and screenshot tasks."""
import asyncio
import re
from datetime import date, datetime, timezone
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
_snap_running = asyncio.Lock()


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
        finally:
            try:
                await close_browser()
            except Exception:
                pass
    # Dispatch alerts after screenshots
    try:
        await dispatch_alerts()
    except Exception as e:
        logger.exception(f"Alert dispatch failed: {e}")


async def _safe_run_snapshots():
    if _snap_running.locked():
        logger.warning("Previous snapshot round still running, skip.")
        return
    async with _snap_running:
        try:
            from backend.app.snapshoter import run_snapshots
            await run_snapshots()
        except Exception as e:
            logger.exception(f"Snapshot round failed: {e}")
        finally:
            try:
                await close_browser()
            except Exception:
                pass


PARTITION_NAME_PATTERN = re.compile(r"^check_results_(\d{4})_(\d{2})$")


def is_partition_expired(partition_name: str, cutoff) -> bool:
    """True when every row a partition can hold predates cutoff.

    Partitions are named check_results_YYYY_MM by create_monthly_partitions() and
    span exactly one month. A partition is expired only once its month has fully
    ended before the cutoff, so the month being trimmed is never dropped. Names
    that do not match the pattern are never considered expired.
    """
    match = PARTITION_NAME_PATTERN.match(partition_name)
    if not match:
        return False
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return False
    month_end = date(year + month // 12, month % 12 + 1, 1)
    return month_end < cutoff.date()


async def _drop_expired_partitions(session, cutoff) -> list[str]:
    """Drop every check_results partition whose whole month predates cutoff."""
    from sqlalchemy import text

    rows = await session.execute(text("""
        SELECT c.relname
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          JOIN pg_class p ON p.oid = i.inhparent
         WHERE p.relname = 'check_results'
    """))

    expired = [name for (name,) in rows.all() if is_partition_expired(name, cutoff)]
    for name in expired:
        await session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))

    await session.commit()
    return expired


async def run_daily_maintenance():
    """Daily maintenance:
    1. Ensure future monthly partitions exist.
    2. Clean up old check results (>30 days).
    3. Clean up old normal screenshots (>14 days, preserving anomaly & baseline).
    """
    logger.info("Running daily maintenance task...")
    from backend.app.database import AsyncSessionLocal
    from backend.app.models import Screenshot, Baseline, CheckResult, Snapshot
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

        # 2. Clean old check results (>30 days) by dropping whole monthly partitions.
        #    A row-wise DELETE over a partitioned table with tens of millions of rows
        #    scans every partition and bloats the heap; dropping the partitions whose
        #    entire range predates the cutoff is near-instant and reclaims disk at once.
        cutoff_30d = now - timedelta(days=30)
        try:
            dropped = await _drop_expired_partitions(session, cutoff_30d)
            if dropped:
                logger.info(f"Dropped {len(dropped)} expired check_results partitions: {', '.join(dropped)}")
        except Exception as e:
            logger.warning(f"Failed to drop expired check_results partitions: {e}")

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
                            deleted_files += 1
                        except OSError:
                            pass
                await session.delete(shot)

            await session.commit()
            if old_shots:
                logger.info(f"Cleaned up {len(old_shots)} old normal screenshots ({deleted_files} files deleted).")
        except Exception as e:
            logger.warning(f"Failed to clean old screenshots: {e}")

        # 4. Clean old unchanged snapshots (>30 days, preserving changed snapshots for history)
        try:
            snap_stmt = select(Snapshot).where(
                Snapshot.taken_at < cutoff_30d,
                Snapshot.has_changed == False,
            ).limit(1000)
            old_snaps = (await session.execute(snap_stmt)).scalars().all()
            snap_paths = [s.file_path for s in old_snaps]

            for snap in old_snaps:
                await session.delete(snap)
            await session.flush()

            # Unchanged snapshots share the archive of the last changed one, so
            # files are removed only once nothing references them any more.
            from backend.app.snapshoter import unlink_unreferenced_snapshot_files
            deleted_snaps = await unlink_unreferenced_snapshot_files(session, snap_paths)

            await session.commit()
            if old_snaps:
                logger.info(f"Cleaned up {len(old_snaps)} old unchanged snapshots ({deleted_snaps} files deleted).")
        except Exception as e:
            logger.warning(f"Failed to clean old snapshots: {e}")


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
        _safe_run_snapshots,
        trigger=IntervalTrigger(minutes=settings.snapshot_interval_minutes),
        id="snapshots",
        name="Page Snapshots (Wayback Machine)",
        replace_existing=True,
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
        f"snapshots every {settings.snapshot_interval_minutes}min, "
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
