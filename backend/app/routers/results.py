"""Check results router."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import CheckResult
from backend.app.schemas import CheckResultOut

router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("/{target_id}", response_model=dict)
async def get_check_results(
    target_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    base = select(CheckResult).where(CheckResult.target_id == target_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar()

    stmt = base.order_by(CheckResult.checked_at.desc()).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    items = [CheckResultOut.model_validate(r) for r in rows.scalars().all()]
    return {"total": total, "items": items, "page": page, "size": size}


@router.get("/{target_id}/latest", response_model=Optional[CheckResultOut])
async def get_latest_result(target_id: int, db: AsyncSession = Depends(get_db)):
    stmt = (
        select(CheckResult)
        .where(CheckResult.target_id == target_id)
        .order_by(CheckResult.checked_at.desc())
        .limit(1)
    )
    row = await db.execute(stmt)
    result = row.scalar_one_or_none()
    return CheckResultOut.model_validate(result) if result else None


@router.get("/{target_id}/stats")
async def get_stats(target_id: int, hours: int = Query(24, ge=1, le=720), db: AsyncSession = Depends(get_db)):
    """Get uptime stats for a target in the last N hours."""
    from datetime import datetime, timedelta, timezone
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    total_stmt = select(func.count()).where(
        CheckResult.target_id == target_id, CheckResult.checked_at >= since
    )
    ok_stmt = select(func.count()).where(
        CheckResult.target_id == target_id, CheckResult.checked_at >= since, CheckResult.is_ok == True
    )
    avg_latency_stmt = select(func.avg(CheckResult.latency_ms)).where(
        CheckResult.target_id == target_id, CheckResult.checked_at >= since, CheckResult.latency_ms.isnot(None)
    )

    total = (await db.execute(total_stmt)).scalar() or 0
    ok = (await db.execute(ok_stmt)).scalar() or 0
    avg_latency = (await db.execute(avg_latency_stmt)).scalar()

    return {
        "hours": hours,
        "total_checks": total,
        "ok_checks": ok,
        "uptime_pct": round(ok / total * 100, 2) if total > 0 else None,
        "avg_latency_ms": round(avg_latency, 1) if avg_latency else None,
    }
