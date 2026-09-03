"""Manual trigger router - for triggering checks/screenshots on demand."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import Target, CheckResult, TargetStatus
from backend.app.checker import run_checks, check_single, check_progress
from backend.app.screenshoter import run_screenshots

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/progress")
async def get_check_progress():
    """Get current check round progress."""
    return check_progress


@router.post("/check-all")
async def trigger_check_all():
    """Manually trigger a full check round (async background)."""
    asyncio.create_task(run_checks())
    return {"message": "Check round triggered"}


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

    # Save check result to DB
    db.add(CheckResult(**result))

    # Upsert target_status
    upsert = insert(TargetStatus).values(
        target_id=result["target_id"],
        is_ok=result["is_ok"],
        last_check_at=result["checked_at"],
        last_status_code=result["status_code"],
        last_latency_ms=result["latency_ms"],
        last_error=result["error"],
        consecutive_fails=0 if result["is_ok"] else 1,
    ).on_conflict_do_update(
        index_elements=["target_id"],
        set_={
            "is_ok": result["is_ok"],
            "last_check_at": result["checked_at"],
            "last_status_code": result["status_code"],
            "last_latency_ms": result["latency_ms"],
            "last_error": result["error"],
            "consecutive_fails": (
                0 if result["is_ok"]
                else text("target_status.consecutive_fails + 1")
            ),
        },
    )
    await db.execute(upsert)
    await db.commit()

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
