from typing import Optional
"""Manual trigger router - for triggering checks/screenshots on demand."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db, AsyncSessionLocal
from backend.app.models import Target, CheckResult, TargetStatus, Anomaly
from backend.app.checker import run_checks, check_single, check_progress
from backend.app.screenshoter import run_screenshots

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/progress")
async def get_check_progress():
    """Get current check round progress."""
    return check_progress


@router.post("/check-all")
async def trigger_check_all(group: Optional[str] = Query(None)):
    """Manually trigger a check round (all targets or specific group)."""
    if check_progress.get("running"):
        return {"message": "Check already running", "progress": check_progress}
    asyncio.create_task(run_checks(group=group))
    msg = f"Check round triggered for group '{group}'" if group else "Full check round triggered"
    return {"message": msg}


@router.post("/check-group/{group}")
async def trigger_check_group(group: str):
    """Trigger check for a specific group."""
    if check_progress.get("running"):
        return {"message": "Check already running", "progress": check_progress}
    asyncio.create_task(run_checks(group=group))
    return {"message": f"Check round triggered for group '{group}'"}


@router.get("/scheduler-status")
async def get_scheduler_status():
    """Get current scheduler status and next run times."""
    from backend.app.scheduler import scheduler
    jobs = []
    for j in scheduler.get_jobs():
        jobs.append({
            "id": j.id,
            "name": j.name,
            "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
        })
    return {
        "running": scheduler.running,
        "jobs": jobs,
    }


@router.post("/screenshot-all")
async def trigger_screenshot_all():
    """Manually trigger a full screenshot round (async background)."""
    asyncio.create_task(run_screenshots())
    return {"message": "Screenshot round triggered"}


@router.post("/check/{target_id}")
async def trigger_single_check(target_id: int, db: AsyncSession = Depends(get_db)):
    """Check a single target immediately and save result."""
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")

    import httpx
    from backend.app.checker import _make_ssl_context
    async with httpx.AsyncClient(
        headers={"User-Agent": "SiteMonitor/1.0"}, verify=_make_ssl_context()
    ) as client:
        result = await check_single(client, target.id, target.url, target.expect_status, target.expect_keyword)

    # Extract ssl_info before saving (CheckResult doesn't have it)
    ssl_info = result.pop("ssl_info", None)

    # Save check result to DB
    db.add(CheckResult(**result))

    # Build upsert values
    upsert_vals = {
        "target_id": result["target_id"],
        "is_ok": result["is_ok"],
        "last_check_at": result["checked_at"],
        "last_status_code": result["status_code"],
        "last_latency_ms": result["latency_ms"],
        "last_error": result["error"],
        "consecutive_fails": 0 if result["is_ok"] else 1,
    }
    update_set = {
        "is_ok": result["is_ok"],
        "last_check_at": result["checked_at"],
        "last_status_code": result["status_code"],
        "last_latency_ms": result["latency_ms"],
        "last_error": result["error"],
        "consecutive_fails": (
            0 if result["is_ok"]
            else text("target_status.consecutive_fails + 1")
        ),
    }
    if ssl_info:
        ssl_not_after = None
        if ssl_info.get("ssl_not_after"):
            try:
                from datetime import datetime as _dt
                ssl_not_after = _dt.fromisoformat(ssl_info["ssl_not_after"])
            except (ValueError, TypeError):
                pass
        ssl_fields = {
            "ssl_valid": ssl_info.get("ssl_valid"),
            "ssl_error": ssl_info.get("ssl_error"),
            "ssl_issuer": ssl_info.get("ssl_issuer"),
            "ssl_subject": ssl_info.get("ssl_subject"),
            "ssl_not_after": ssl_not_after,
            "ssl_days_left": ssl_info.get("ssl_days_left"),
            "ssl_warning": ssl_info.get("ssl_warning"),
            "ssl_checked_at": result["checked_at"],
        }
        upsert_vals.update(ssl_fields)
        update_set.update(ssl_fields)

    upsert = insert(TargetStatus).values(**upsert_vals).on_conflict_do_update(
        index_elements=["target_id"],
        set_=update_set,
    )
    await db.execute(upsert)
    await db.commit()

    # Re-attach ssl_info for API response
    if ssl_info:
        result["ssl_info"] = ssl_info

    return result


@router.post("/screenshot/{target_id}")
async def trigger_single_screenshot(target_id: int, db: AsyncSession = Depends(get_db)):
    """Take a screenshot of a single target immediately."""
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")

    from backend.app.screenshoter import take_screenshot
    from backend.app.analyzer import analyze_screenshot
    from backend.app.models import Screenshot
    from backend.app.config import settings
    from pathlib import Path

    result = await take_screenshot(target.id, target.url)
    if result is None:
        return {"error": "Screenshot failed"}

    # Run analysis
    img_path = Path(settings.screenshots_dir) / result["file_path"]
    analysis = analyze_screenshot(
        str(img_path),
        page_title=result.get("page_title", ""),
        console_errors=result.get("console_errors", 0),
        failed_requests=result.get("failed_requests", 0),
        dom_text_length=result.get("dom_text_length", 0),
    )
    result["is_anomaly"] = analysis["is_anomaly"]
    result["anomaly_score"] = analysis["score"]
    result["anomaly_reasons"] = analysis["reasons"]

    # Save to DB
    shot = Screenshot(**result)
    db.add(shot)
    await db.flush()

    if analysis["is_anomaly"]:
        existing = await db.execute(
            select(Anomaly).where(
                Anomaly.target_id == target.id,
                Anomaly.anomaly_type == "render_anomaly",
                Anomaly.state == "open",
            )
        )
        if not existing.scalar_one_or_none():
            anomaly = Anomaly(
                target_id=target.id,
                detected_at=result["taken_at"],
                anomaly_type="render_anomaly",
                score=analysis["score"],
                reasons=analysis["reasons"],
                screenshot_id=shot.id,
                state="open",
                notified=False,
            )
            db.add(anomaly)
    else:
        open_anomalies = await db.execute(
            select(Anomaly).where(
                Anomaly.target_id == target.id,
                Anomaly.anomaly_type == "render_anomaly",
                Anomaly.state == "open",
            )
        )
        for oa in open_anomalies.scalars().all():
            oa.state = "resolved"
            oa.resolved_at = result["taken_at"]

    upsert = insert(TargetStatus).values(
        target_id=target.id,
        last_screenshot_id=shot.id,
        last_screenshot_at=result["taken_at"],
        has_anomaly=analysis["is_anomaly"],
    ).on_conflict_do_update(
        index_elements=["target_id"],
        set_={
            "last_screenshot_id": shot.id,
            "last_screenshot_at": result["taken_at"],
            "has_anomaly": analysis["is_anomaly"],
        },
    )
    await db.execute(upsert)
    await db.commit()

    return result


# ---------------------------------------------------------------------------
# Retry-failed: re-check failed targets with strict group rate limiting
# ---------------------------------------------------------------------------

# Patterns that indicate domain-level issues (should NOT be retried)
_DOMAIN_EXPIRED_PATTERNS = [
    "域名过期/注册商处",
    "domain_expired", "domain_parked", "domain_not_found", "registrar_held",
    "DNS无法解析", "NXDOMAIN",
]


def _is_domain_expired_error(error: str | None) -> bool:
    """Return True if the error indicates domain expiry / parking / DNS not found."""
    if not error:
        return False
    err_lower = error.lower()
    return any(p.lower() in err_lower for p in _DOMAIN_EXPIRED_PATTERNS)


retry_progress = {"running": False, "total": 0, "done": 0, "ok": 0, "fail": 0, "skipped_expired": 0}


async def _run_retry_failed(group: str = None):
    """Re-check failed targets.

    - Skips domain-expired targets (no point retrying)
    - Respects per-group concurrency limits (same as run_checks)
    - Uses global + group semaphores
    """
    import httpx
    from backend.app.checker import (
        _make_ssl_context, _flush_buffer, _load_group_settings, check_single,
    )
    from backend.app.config import settings as app_settings
    from loguru import logger

    retry_progress.update(
        {"running": True, "total": 0, "done": 0, "ok": 0, "fail": 0, "skipped_expired": 0}
    )

    # ---- Load failed targets (with last_error so we can filter) ----
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Target, TargetStatus.last_error)
            .join(TargetStatus, Target.id == TargetStatus.target_id)
            .where(TargetStatus.is_ok == False)
            .where(Target.enabled == True)
        )
        if group:
            stmt = stmt.where(Target.group == group)
        rows = await session.execute(stmt)
        all_rows = rows.all()

    # Separate retryable from domain-expired
    targets = []
    skipped = 0
    for target, last_error in all_rows:
        if _is_domain_expired_error(last_error):
            skipped += 1
        else:
            targets.append(target)

    retry_progress["skipped_expired"] = skipped

    if not targets:
        retry_progress.update({"running": False, "total": 0})
        return

    total = len(targets)
    retry_progress["total"] = total

    # ---- Load group concurrency settings ----
    group_cfg = await _load_group_settings()
    default_group_concurrency = 10
    max_concurrent = app_settings.max_concurrent_checks

    groups_in_use = set(t.group for t in targets)
    global_sem = asyncio.Semaphore(max_concurrent)
    group_sems: dict[str, asyncio.Semaphore] = {}
    for g in groups_in_use:
        gc = group_cfg.get(g, {})
        limit = gc.get("max_concurrency", default_group_concurrency)
        group_sems[g] = asyncio.Semaphore(limit)

    logger.info(
        f"Retry-failed: {total} targets to retry, {skipped} domain-expired skipped "
        f"(global max={max_concurrent})"
    )
    for g in sorted(groups_in_use):
        gc = group_cfg.get(g, {})
        logger.info(
            f"  Retry group [{g}]: concurrency={gc.get('max_concurrency', default_group_concurrency)}"
        )

    default_ua = app_settings.default_user_agent
    buffer: list[dict] = []

    async def check_one(target):
        url = target.url
        if not url.startswith("http"):
            protocol = getattr(target, 'protocol', 'https') or 'https'
            url = f"{protocol}://{url}"

        # Per-group overrides
        gc = group_cfg.get(target.group, {})
        ua = getattr(target, 'user_agent', None) or gc.get('user_agent') or default_ua
        timeout = getattr(target, 'request_timeout', None) or gc.get('request_timeout') or app_settings.check_timeout
        follow = getattr(target, 'follow_redirects', True)
        extra_headers = getattr(target, 'request_headers', None) or {}
        headers = {"User-Agent": ua}
        headers.update(extra_headers)

        group_sem = group_sems.get(target.group, asyncio.Semaphore(default_group_concurrency))

        async with global_sem:
            async with group_sem:
                r = await check_single(
                    client, target.id, url,
                    expect_status=target.expect_status,
                    expect_keyword=target.expect_keyword,
                    request_timeout=timeout,
                    follow_redirects=follow if follow is not None else True,
                    custom_headers=headers,
                )

        buffer.append(r)
        retry_progress["done"] += 1
        if r["is_ok"]:
            retry_progress["ok"] += 1
        else:
            retry_progress["fail"] += 1

    async with httpx.AsyncClient(
        verify=_make_ssl_context(),
        limits=httpx.Limits(max_connections=max_concurrent, max_keepalive_connections=50),
    ) as client:
        chunk_size = max(500, max_concurrent * 2)
        for i in range(0, total, chunk_size):
            chunk = targets[i:i + chunk_size]
            tasks = [check_one(t) for t in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)
            if len(buffer) >= 200:
                await _flush_buffer(buffer)
                buffer.clear()

    # Flush remaining results to DB
    if buffer:
        await _flush_buffer(buffer)
        buffer.clear()

    retry_progress["running"] = False
    logger.info(
        f"Retry-failed done: {retry_progress['ok']} recovered, "
        f"{retry_progress['fail']} still failing, {skipped} expired skipped"
    )


@router.post("/retry-failed")
async def trigger_retry_failed(group: str = None):
    """Re-check all currently failed targets (skips domain-expired)."""
    if retry_progress.get("running"):
        return {"message": "Retry already in progress", "progress": retry_progress}
    asyncio.create_task(_run_retry_failed(group))
    return {"message": "Retry failed targets triggered"}


@router.get("/retry-progress")
async def get_retry_progress():
    """Get retry-failed progress."""
    return retry_progress
