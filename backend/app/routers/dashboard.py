"""Dashboard stats router."""
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, or_, and_, not_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db, AsyncSessionLocal
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
            # SSL certificate stats
            func.count(TargetStatus.target_id).filter(TargetStatus.ssl_checked_at.isnot(None)).label("ssl_checked"),
            func.count(TargetStatus.target_id).filter(TargetStatus.ssl_valid == True).label("ssl_ok"),
            func.count(TargetStatus.target_id).filter(TargetStatus.ssl_valid == False).label("ssl_error"),
            func.count(TargetStatus.target_id).filter(
                TargetStatus.ssl_valid == True,
                TargetStatus.ssl_days_left.isnot(None),
                TargetStatus.ssl_days_left <= 30,
            ).label("ssl_expiring_soon"),
            # HTTP (no HTTPS) count
            func.count(Target.id).filter(not_(Target.url.ilike("https://%"))).label("ssl_no_https"),
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
            # SSL
            "ssl_checked": r.ssl_checked or 0,
            "ssl_ok": r.ssl_ok or 0,
            "ssl_error": r.ssl_error or 0,
            "ssl_expiring_soon": r.ssl_expiring_soon or 0,
            "ssl_no_https": r.ssl_no_https or 0,
        })

    return results


# ---------------------------------------------------------------------------
# Error category helpers shared by recent-failures & export
# ---------------------------------------------------------------------------
_DOMAIN_EXPIRED_PATTERNS = [
    "域名过期/注册商处",
    "domain_expired", "domain_parked", "domain_not_found", "registrar_held",
]


def _classify_error(error: str | None) -> str:
    """Classify a last_error string into a category tag."""
    if not error:
        return "unknown"
    e = error.lower()
    # DNS not found (NXDOMAIN) — NOT the same as domain expired
    if "dns无法解析" in e or "domain_not_found" in e or "nxdomain" in e:
        return "dns_not_found"
    for p in _DOMAIN_EXPIRED_PATTERNS:
        if p.lower() in e:
            return "domain_expired"
    if "timeout" in e:
        return "timeout"
    if "connect_error" in e:
        return "connect_error"
    if "ssl_error" in e:
        return "ssl_error"
    if e.startswith("expected "):
        return "status_mismatch"
    if "keyword" in e and "not found" in e:
        return "keyword_missing"
    if "ssl_cert" in e or "证书" in e:
        return "ssl_cert_error"
    return "other"


def _apply_error_type_filter(stmt, error_types: list[str] | None):
    """Add WHERE clauses based on selected error_types."""
    if not error_types:
        return stmt

    conditions = []
    for et in error_types:
        if et == "dns_not_found":
            conditions.append(
                or_(
                    TargetStatus.last_error.ilike("%DNS无法解析%"),
                    TargetStatus.last_error.ilike("%domain_not_found%"),
                    TargetStatus.last_error.ilike("%NXDOMAIN%"),
                )
            )
        elif et == "domain_expired":
            # Match any of the domain-expired patterns
            for p in _DOMAIN_EXPIRED_PATTERNS:
                conditions.append(TargetStatus.last_error.ilike(f"%{p}%"))
        elif et == "timeout":
            conditions.append(TargetStatus.last_error.ilike("%timeout%"))
        elif et == "connect_error":
            conditions.append(TargetStatus.last_error.ilike("%connect_error%"))
        elif et == "ssl_error":
            conditions.append(TargetStatus.last_error.ilike("%ssl_error%"))
        elif et == "status_mismatch":
            conditions.append(TargetStatus.last_error.ilike("expected %"))
        elif et == "keyword_missing":
            conditions.append(
                and_(
                    TargetStatus.last_error.ilike("%keyword%"),
                    TargetStatus.last_error.ilike("%not found%"),
                )
            )
        elif et == "ssl_cert_error":
            conditions.append(
                or_(
                    TargetStatus.last_error.ilike("%ssl_cert%"),
                    TargetStatus.last_error.ilike("%证书%"),
                )
            )
        elif et == "other":
            # Anything that doesn't match the above known patterns
            known = []
            known.append(
                or_(
                    TargetStatus.last_error.ilike("%DNS无法解析%"),
                    TargetStatus.last_error.ilike("%domain_not_found%"),
                    TargetStatus.last_error.ilike("%NXDOMAIN%"),
                )
            )
            for p in _DOMAIN_EXPIRED_PATTERNS:
                known.append(TargetStatus.last_error.ilike(f"%{p}%"))
            known.append(TargetStatus.last_error.ilike("%timeout%"))
            known.append(TargetStatus.last_error.ilike("%connect_error%"))
            known.append(TargetStatus.last_error.ilike("%ssl_error%"))
            known.append(TargetStatus.last_error.ilike("expected %"))
            known.append(
                and_(
                    TargetStatus.last_error.ilike("%keyword%"),
                    TargetStatus.last_error.ilike("%not found%"),
                )
            )
            known.append(
                or_(
                    TargetStatus.last_error.ilike("%ssl_cert%"),
                    TargetStatus.last_error.ilike("%证书%"),
                )
            )
            conditions.append(not_(or_(*known)))

    if conditions:
        stmt = stmt.where(or_(*conditions))
    return stmt


@router.get("/recent-failures")
async def recent_failures(
    group: str = None,
    error_types: Optional[List[str]] = Query(None, alias="error_type"),
    limit: Optional[int] = Query(200, ge=0, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """Get currently failed targets, optionally filtered by group and error type.

    error_type can be repeated:  ?error_type=timeout&error_type=connect_error
    Available types: domain_expired, timeout, connect_error, ssl_error,
                     status_mismatch, keyword_missing, other
    limit: Defaults to 200 for fast UI display. Use limit=0 for all.
    """
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
            TargetStatus.ssl_valid,
            TargetStatus.ssl_error,
            TargetStatus.ssl_days_left,
            TargetStatus.ssl_issuer,
        )
        .join(Target, Target.id == TargetStatus.target_id)
        .where(TargetStatus.is_ok == False)
    )
    if group:
        stmt = stmt.where(Target.group == group)
    stmt = _apply_error_type_filter(stmt, error_types)
    stmt = stmt.order_by(TargetStatus.last_check_at.desc())
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    rows = await db.execute(stmt)
    return [
        {
            "target_id": r[0], "url": r[1], "name": r[2], "group": r[3],
            "last_check_at": r[4], "status_code": r[5], "error": r[6],
            "consecutive_fails": r[7], "has_anomaly": r[8],
            "ssl_valid": r[9], "ssl_error": r[10],
            "ssl_days_left": r[11], "ssl_issuer": r[12],
            "error_type": _classify_error(r[6]),
        }
        for r in rows.all()
    ]


@router.get("/export-failures")
async def export_failures(
    group: str = None,
    error_types: Optional[List[str]] = Query(None, alias="error_type"),
):
    """Export failed sites as CSV with high-performance streaming response.
    Eliminates 30s timeouts by streaming directly from DB in chunks without in-memory buffering.
    """
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
    stmt = _apply_error_type_filter(stmt, error_types)
    stmt = stmt.order_by(TargetStatus.last_check_at.desc())

    async def generate_failures_csv():
        output = io.StringIO()
        # Write BOM for Excel compatibility and flush header immediately (<1ms)
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow([
            'ID', 'URL', '站点名称', '分组', '最后检测时间',
            '状态码', '错误信息', '连续失败次数', '渲染异常',
        ])
        yield output.getvalue().encode('utf-8')

        async with AsyncSessionLocal() as db:
            result = await db.stream(stmt)
            async for partition in result.partitions(1000):
                output = io.StringIO()
                writer = csv.writer(output)
                for r in partition:
                    writer.writerow([
                        r[0], r[1], r[2] or '', r[3] or '',
                        r[4].strftime('%Y-%m-%d %H:%M:%S') if r[4] else '',
                        r[5] or '', r[6] or '', r[7] or 0,
                        '是' if r[8] else '否',
                    ])
                yield output.getvalue().encode('utf-8')

    now_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"failures_{now_str}.csv"
    return StreamingResponse(
        generate_failures_csv(),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@router.get("/domain-detect")
async def domain_detect(url: str):
    """Run multi-signal domain status detection for a single URL."""
    from backend.app.domain_detector import detect_domain_status
    verdict = await detect_domain_status(url)
    return {
        "url": url,
        "is_parked_or_expired": verdict.is_parked_or_expired,
        "confidence": verdict.confidence,
        "category": verdict.category,
        "summary": verdict.summary,
        "signals": [
            {"type": s.signal_type, "confidence": s.confidence, "detail": s.detail}
            for s in verdict.signals
        ],
    }


def _ssl_status(url: str, ssl_valid, ssl_days_left, ssl_checked_at) -> str:
    """Compute ssl_status label from URL + DB fields."""
    if not url or not url.lower().startswith("https://"):
        return "no_https"        # HTTP site, no SSL
    if ssl_checked_at is None:
        return "unchecked"       # HTTPS but not checked yet
    if ssl_valid is False:
        return "invalid"         # cert error
    if ssl_valid is True:
        if ssl_days_left is not None and ssl_days_left <= 30:
            return "expiring"    # about to expire
        return "valid"           # all good
    return "unchecked"


def _apply_ssl_filter(stmt, status: str | None):
    """Apply WHERE clauses for SSL status filter."""
    if not status or status == "all":
        return stmt
    if status == "no_https":
        return stmt.where(not_(Target.url.ilike("https://%")))
    # Everything below is HTTPS-only
    stmt = stmt.where(Target.url.ilike("https://%"))
    if status == "valid":
        return stmt.where(
            TargetStatus.ssl_valid == True,
            or_(TargetStatus.ssl_days_left.is_(None), TargetStatus.ssl_days_left > 30),
        )
    if status == "invalid":
        return stmt.where(TargetStatus.ssl_valid == False)
    if status == "expiring":
        return stmt.where(
            TargetStatus.ssl_valid == True,
            TargetStatus.ssl_days_left.isnot(None),
            TargetStatus.ssl_days_left <= 30,
        )
    if status == "unchecked":
        return stmt.where(TargetStatus.ssl_checked_at.is_(None))
    return stmt


@router.get("/ssl-summary")
async def ssl_summary(
    group: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Return fast aggregated SSL counts directly from DB.
    Runs in <200ms without loading all domain records into memory.
    """
    stmt = (
        select(
            func.count(Target.id).label("total"),
            func.count(TargetStatus.target_id).filter(
                TargetStatus.ssl_valid == True,
                or_(TargetStatus.ssl_days_left.is_(None), TargetStatus.ssl_days_left > 30),
            ).label("valid"),
            func.count(TargetStatus.target_id).filter(
                TargetStatus.ssl_valid == False,
            ).label("invalid"),
            func.count(TargetStatus.target_id).filter(
                TargetStatus.ssl_valid == True,
                TargetStatus.ssl_days_left.isnot(None),
                TargetStatus.ssl_days_left <= 30,
            ).label("expiring"),
            func.count(Target.id).filter(
                not_(Target.url.ilike("https://%")),
            ).label("no_https"),
            func.count(Target.id).filter(
                Target.url.ilike("https://%"),
                TargetStatus.ssl_checked_at.is_(None),
            ).label("unchecked"),
        )
        .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
        .where(Target.enabled == True)
    )
    if group:
        stmt = stmt.where(Target.group == group)
    row = (await db.execute(stmt)).one()
    return {
        "total": row.total or 0,
        "valid": row.valid or 0,
        "invalid": row.invalid or 0,
        "expiring": row.expiring or 0,
        "noHttps": row.no_https or 0,
        "unchecked": row.unchecked or 0,
    }


@router.get("/ssl-overview")
async def ssl_overview(
    group: str = None,
    status: str = None,  # all, valid, invalid, expiring, no_https, unchecked
    limit: Optional[int] = Query(200, ge=0, le=5000),
    db: AsyncSession = Depends(get_db),
):
    """SSL certificate overview for targets (preview for dashboard).

    status filter:
      - valid:     HTTPS + cert OK + days>30
      - invalid:   HTTPS + cert error
      - expiring:  HTTPS + cert OK + days<=30
      - no_https:  HTTP sites (no SSL)
      - unchecked: HTTPS but not yet checked
    limit: Defaults to 200 for fast UI display. Use limit=0 to return all.
    """
    stmt = (
        select(
            TargetStatus.target_id,
            Target.url,
            Target.name,
            Target.group,
            TargetStatus.is_ok,
            TargetStatus.ssl_valid,
            TargetStatus.ssl_error,
            TargetStatus.ssl_issuer,
            TargetStatus.ssl_subject,
            TargetStatus.ssl_not_after,
            TargetStatus.ssl_days_left,
            TargetStatus.ssl_warning,
            TargetStatus.ssl_checked_at,
        )
        .join(Target, Target.id == TargetStatus.target_id)
        .where(Target.enabled == True)
    )
    if group:
        stmt = stmt.where(Target.group == group)
    stmt = _apply_ssl_filter(stmt, status)
    stmt = stmt.order_by(TargetStatus.ssl_days_left.asc().nullslast())
    if limit and limit > 0:
        stmt = stmt.limit(limit)
    rows = await db.execute(stmt)
    return [
        {
            "target_id": r[0], "url": r[1], "name": r[2], "group": r[3],
            "is_ok": r[4], "ssl_valid": r[5], "ssl_error": r[6],
            "ssl_issuer": r[7], "ssl_subject": r[8],
            "ssl_not_after": r[9], "ssl_days_left": r[10],
            "ssl_warning": r[11], "ssl_checked_at": r[12],
            "ssl_status": _ssl_status(r[1], r[5], r[10], r[12]),
        }
        for r in rows.all()
    ]


@router.get("/ssl-export")
async def ssl_export(
    group: str = None,
    status: str = None,
):
    """Export SSL certificate status as CSV with high-performance streaming response.
    Eliminates 30s timeouts by streaming directly from DB in chunks without in-memory buffering.
    """
    stmt = (
        select(
            TargetStatus.target_id,
            Target.url,
            Target.name,
            Target.group,
            TargetStatus.ssl_valid,
            TargetStatus.ssl_issuer,
            TargetStatus.ssl_subject,
            TargetStatus.ssl_not_after,
            TargetStatus.ssl_days_left,
            TargetStatus.ssl_error,
            TargetStatus.ssl_warning,
            TargetStatus.ssl_checked_at,
        )
        .join(Target, Target.id == TargetStatus.target_id)
        .where(Target.enabled == True)
    )
    if group:
        stmt = stmt.where(Target.group == group)
    stmt = _apply_ssl_filter(stmt, status)
    stmt = stmt.order_by(TargetStatus.ssl_days_left.asc().nullslast())

    _status_labels = {
        "valid": "证书正常",
        "invalid": "证书异常",
        "expiring": "即将过期",
        "no_https": "HTTP未加密",
        "unchecked": "未检测",
    }

    async def generate_ssl_csv():
        output = io.StringIO()
        # Write BOM for Excel compatibility and flush header immediately (<1ms)
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow([
            'ID', 'URL', '站点名称', '分组', 'SSL状态',
            '签发者', '证书主体',
            '到期时间', '剩余天数',
            '证书错误', '证书警告', '检测时间',
        ])
        yield output.getvalue().encode('utf-8')

        async with AsyncSessionLocal() as db:
            result = await db.stream(stmt)
            async for partition in result.partitions(1000):
                output = io.StringIO()
                writer = csv.writer(output)
                for r in partition:
                    ss = _ssl_status(r[1], r[4], r[8], r[11])
                    writer.writerow([
                        r[0], r[1], r[2] or '', r[3] or '',
                        _status_labels.get(ss, ss),
                        r[5] or '', r[6] or '',
                        r[7].strftime('%Y-%m-%d %H:%M:%S') if r[7] else '',
                        r[8] if r[8] is not None else '',
                        r[9] or '', r[10] or '',
                        r[11].strftime('%Y-%m-%d %H:%M:%S') if r[11] else '',
                    ])
                yield output.getvalue().encode('utf-8')

    now_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"ssl_certificates_{now_str}.csv"
    return StreamingResponse(
        generate_ssl_csv(),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )
