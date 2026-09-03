"""Dashboard stats router."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import Target, TargetStatus, Anomaly, Screenshot
from backend.app.schemas import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Target.id)))).scalar() or 0
    enabled = (await db.execute(
        select(func.count(Target.id)).where(Target.enabled == True)
    )).scalar() or 0

    healthy = (await db.execute(
        select(func.count(TargetStatus.target_id)).where(TargetStatus.is_ok == True)
    )).scalar() or 0
    unhealthy = (await db.execute(
        select(func.count(TargetStatus.target_id)).where(TargetStatus.is_ok == False)
    )).scalar() or 0

    unknown = enabled - healthy - unhealthy

    open_anomalies = (await db.execute(
        select(func.count(Anomaly.id)).where(Anomaly.state == "open")
    )).scalar() or 0

    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    shots_today = (await db.execute(
        select(func.count(Screenshot.id)).where(Screenshot.taken_at >= today)
    )).scalar() or 0

    return DashboardStats(
        total_targets=total,
        enabled_targets=enabled,
        healthy=healthy,
        unhealthy=unhealthy,
        unknown=max(0, unknown),
        open_anomalies=open_anomalies,
        screenshots_today=shots_today,
    )


@router.get("/group-stats")
async def group_stats(db: AsyncSession = Depends(get_db)):
    """Get stats grouped by target group."""
    from sqlalchemy import case, literal_column
    from backend.app.models import CheckResult

    # 基础统计: 每组的总数、启用数、健康/异常/未知
    stmt = (
        select(
            Target.group,
            func.count(Target.id).label("total"),
            func.count(Target.id).filter(Target.enabled == True).label("enabled"),
            func.count(TargetStatus.target_id).filter(TargetStatus.is_ok == True).label("healthy"),
            func.count(TargetStatus.target_id).filter(TargetStatus.is_ok == False).label("unhealthy"),
            func.count(TargetStatus.target_id).filter(TargetStatus.has_anomaly == True).label("render_anomaly"),
            func.avg(TargetStatus.last_latency_ms).label("avg_latency"),
            func.max(TargetStatus.consecutive_fails).label("max_consecutive_fails"),
        )
        .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
        .group_by(Target.group)
        .order_by(Target.group)
    )
    rows = await db.execute(stmt)

    results = []
    for r in rows.all():
        total = r.total or 0
        enabled = r.enabled or 0
        healthy = r.healthy or 0
        unhealthy = r.unhealthy or 0
        unknown = max(0, enabled - healthy - unhealthy)
        uptime_pct = round(healthy / enabled * 100, 1) if enabled > 0 else None

        results.append({
            "group": r.group,
            "total": total,
            "enabled": enabled,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "unknown": unknown,
            "render_anomaly": r.render_anomaly or 0,
            "avg_latency": round(r.avg_latency, 1) if r.avg_latency else None,
            "max_consecutive_fails": r.max_consecutive_fails or 0,
            "uptime_pct": uptime_pct,
        })

    return results


@router.get("/recent-failures")
async def recent_failures(
    limit: int = 20,
    group: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Get most recently failed targets, optionally filtered by group."""
    from fastapi import Query
    stmt = (
        select(
            TargetStatus.target_id,
            Target.url,
            Target.name,
            Target.group,
            TargetStatus.last_check_at,
            TargetStatus.last_status_code,
            TargetStatus.last_error,
            TargetStatus.consecutive_fails,
            TargetStatus.has_anomaly,
        )
        .join(Target, Target.id == TargetStatus.target_id)
        .where(TargetStatus.is_ok == False)
    )
    if group:
        stmt = stmt.where(Target.group == group)
    stmt = stmt.order_by(TargetStatus.last_check_at.desc()).limit(limit)
    rows = await db.execute(stmt)
    return [
        {
            "target_id": r[0], "url": r[1], "name": r[2], "group": r[3],
            "last_check_at": r[4], "status_code": r[5], "error": r[6],
            "consecutive_fails": r[7], "has_anomaly": r[8],
        }
        for r in rows.all()
    ]
