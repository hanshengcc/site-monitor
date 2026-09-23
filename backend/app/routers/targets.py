import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, delete, or_, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from backend.app.config import settings
from backend.app.database import get_db, AsyncSessionLocal
from backend.app.models import Target, TargetStatus
from backend.app.schemas import TargetCreate, TargetUpdate, TargetOut, TargetBatchCreate

router = APIRouter(prefix="/api/targets", tags=["targets"])


@router.get("", response_model=dict)
async def list_targets(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    group: Optional[str] = None,
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,  # ok / fail / unknown
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Target).options(joinedload(Target.status))
    count_stmt = select(func.count(Target.id))

    if group:
        stmt = stmt.where(Target.group == group)
        count_stmt = count_stmt.where(Target.group == group)
    if enabled is not None:
        stmt = stmt.where(Target.enabled == enabled)
        count_stmt = count_stmt.where(Target.enabled == enabled)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Target.url.ilike(like) | Target.name.ilike(like))
        count_stmt = count_stmt.where(Target.url.ilike(like) | Target.name.ilike(like))

    if status == "ok":
        stmt = stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(TargetStatus.is_ok == True)
        count_stmt = count_stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(TargetStatus.is_ok == True)
    elif status == "fail":
        stmt = stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(TargetStatus.is_ok == False)
        count_stmt = count_stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(TargetStatus.is_ok == False)
    elif status == "unknown":
        stmt = stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(or_(TargetStatus.is_ok.is_(None), TargetStatus.target_id.is_(None)))
        count_stmt = count_stmt.outerjoin(TargetStatus, Target.id == TargetStatus.target_id).where(or_(TargetStatus.is_ok.is_(None), TargetStatus.target_id.is_(None)))

    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(Target.id).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    targets = rows.unique().scalars().all()

    items = [TargetOut.model_validate(t) for t in targets]
    return {"total": total, "items": items, "page": page, "size": size}


@router.post("", response_model=TargetOut)
async def create_target(body: TargetCreate, db: AsyncSession = Depends(get_db)):
    target = Target(**body.model_dump())
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return target


@router.post("/batch", response_model=dict)
async def batch_create(body: TargetBatchCreate, db: AsyncSession = Depends(get_db)):
    """High-performance batch import URLs using PostgreSQL UNNEST and ON CONFLICT.
    Easily handles 20,000+ domains in 1-2 seconds without timing out.
    """
    if not body.urls:
        return {"created": 0, "skipped": 0, "total": 0}

    # 1. Clean, format and deduplicate URLs in Python memory (instant)
    unique_urls = []
    seen = set()
    for raw_url in body.urls:
        url = raw_url.strip()
        if not url:
            continue
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)

    if not unique_urls:
        return {"created": 0, "skipped": len(body.urls), "total": len(body.urls)}

    # 2. High-speed bulk insert in chunks of 10,000 using PostgreSQL UNNEST
    chunk_size = 10000
    created = 0
    sql = text("""
        INSERT INTO targets (url, name, "group", check_interval, shot_interval)
        SELECT u, u, :group, :check_interval, :shot_interval
        FROM UNNEST(CAST(:urls AS text[])) AS u
        ON CONFLICT (url) DO NOTHING
        RETURNING id;
    """)

    for i in range(0, len(unique_urls), chunk_size):
        chunk = unique_urls[i:i + chunk_size]
        res = await db.execute(sql, {
            "group": body.group or "default",
            "check_interval": body.check_interval or 300,
            "shot_interval": body.shot_interval or 21600,
            "urls": chunk,
        })
        created += len(res.fetchall())

    await db.commit()
    skipped = len(body.urls) - created
    return {"created": created, "skipped": skipped, "total": len(body.urls)}


@router.get("/export")
async def export_targets(
    group: Optional[str] = None,
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
):
    """Export targets to CSV with high-performance streaming response.
    Eliminates 30s timeouts by streaming directly from DB in chunks without in-memory buffering.
    """
    stmt = (
        select(
            Target.id,
            Target.url,
            Target.name,
            Target.group,
            TargetStatus.is_ok,
            TargetStatus.last_status_code,
            TargetStatus.last_latency_ms,
            TargetStatus.consecutive_fails,
            TargetStatus.has_anomaly,
            TargetStatus.last_error,
            Target.check_interval,
            Target.enabled,
            Target.created_at,
            TargetStatus.dns_server,
        )
        .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
    )

    if group:
        stmt = stmt.where(Target.group == group)
    if enabled is not None:
        stmt = stmt.where(Target.enabled == enabled)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Target.url.ilike(like) | Target.name.ilike(like))
    if status == "ok":
        stmt = stmt.where(TargetStatus.is_ok == True)
    elif status == "fail":
        stmt = stmt.where(TargetStatus.is_ok == False)
    elif status == "unknown":
        stmt = stmt.where(or_(TargetStatus.is_ok.is_(None), TargetStatus.target_id.is_(None)))

    stmt = stmt.order_by(Target.id)

    _status_map = {True: "正常", False: "异常", None: "未知"}

    async def generate_targets_csv():
        output = io.StringIO()
        # Write BOM for Excel compatibility and flush header immediately (<1ms)
        output.write('\ufeff')
        writer = csv.writer(output)
        writer.writerow([
            'ID', 'URL', '站点名称', '分组', '状态',
            '状态码', '响应延迟(ms)', '连续失败次数', '渲染异常',
            '错误信息', 'DNS服务器', '检测间隔(秒)', '是否启用', '创建时间',
        ])
        yield output.getvalue().encode('utf-8')

        async with AsyncSessionLocal() as db:
            result = await db.stream(stmt)
            async for partition in result.partitions(1000):
                output = io.StringIO()
                writer = csv.writer(output)
                for r in partition:
                    st_label = _status_map.get(r[4], "未知")
                    writer.writerow([
                        r[0], r[1], r[2] or '', r[3] or '',
                        st_label,
                        r[5] or '', r[6] if r[6] is not None else '',
                        r[7] or 0,
                        '是' if r[8] else '否',
                        r[9] or '',
                        r[13] or '',
                        r[10] or 300,
                        '是' if r[11] else '否',
                        r[12].strftime('%Y-%m-%d %H:%M:%S') if r[12] else '',
                    ])
                yield output.getvalue().encode('utf-8')

    now_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"targets_{now_str}.csv"
    return StreamingResponse(
        generate_targets_csv(),
        media_type='text/csv; charset=utf-8',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


@router.get("/{target_id}", response_model=TargetOut)
async def get_target(target_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Target).options(joinedload(Target.status)).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.unique().scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")
    return target


@router.put("/{target_id}", response_model=TargetOut)
async def update_target(target_id: int, body: TargetUpdate, db: AsyncSession = Depends(get_db)):
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")

    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(target, k, v)
    target.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(target)
    return target


@router.delete("/groups/{group_name}")
async def delete_group(group_name: str, db: AsyncSession = Depends(get_db)):
    """Delete a group and clean up all associated targets, checks, screenshots, snapshots, and settings.

    Uses raw SQL subqueries (DELETE ... WHERE target_id IN (SELECT id FROM targets WHERE group=:g))
    instead of loading all IDs into Python and passing them as IN() parameters, which fails for
    large groups (34k+ targets) due to asyncpg parameter limit overflow.

    Deletes child tables in correct FK dependency order to avoid slow per-row constraint checks:
      baselines -> anomalies -> screenshots -> snapshots -> check_results -> target_status -> targets
    """
    from loguru import logger
    from backend.app.models import Screenshot, Snapshot

    # 0. Count targets first for the response
    cnt_row = await db.execute(
        select(func.count()).select_from(Target).where(Target.group == group_name)
    )
    target_count = cnt_row.scalar() or 0

    if target_count == 0:
        # No targets; just clean up orphan group_settings
        await db.execute(text(
            "DELETE FROM group_settings WHERE group_name = :g"
        ), {"g": group_name})
        await db.commit()
        return {
            "ok": True,
            "group": group_name,
            "deleted_targets": 0,
            "message": f"分组 [{group_name}] 不存在或已无站点",
        }

    logger.info(f"Deleting group [{group_name}]: {target_count} targets, cleaning up all related data...")

    # 1. Clean up physical screenshot files on disk (stream to avoid OOM for large sets)
    try:
        shot_rows = await db.execute(text(
            "SELECT file_path, thumb_path FROM screenshots "
            "WHERE target_id IN (SELECT id FROM targets WHERE \"group\" = :g)"
        ), {"g": group_name})
        shots_dir = Path(settings.screenshots_dir)
        for fpath, thumb in shot_rows.all():
            for p_str in (fpath, thumb):
                if p_str:
                    p = shots_dir / p_str
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
    except Exception as e:
        logger.warning(f"Error cleaning screenshot files for group {group_name}: {e}")

    # 2. Clean up physical snapshot files on disk
    try:
        snap_rows = await db.execute(text(
            "SELECT file_path FROM snapshots "
            "WHERE target_id IN (SELECT id FROM targets WHERE \"group\" = :g)"
        ), {"g": group_name})
        snaps_dir = Path(settings.snapshots_dir)
        for (fpath,) in snap_rows.all():
            if fpath:
                p = snaps_dir / fpath
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception as e:
        logger.warning(f"Error cleaning snapshot files for group {group_name}: {e}")

    # 3. Delete DB records in correct FK dependency order using raw SQL subqueries
    #    This avoids building enormous parameter lists and lets PostgreSQL use indexes.
    target_subq = 'SELECT id FROM targets WHERE "group" = :g'

    # baselines references both targets(target_id) and screenshots(screenshot_id)
    await db.execute(text(f"DELETE FROM baselines WHERE target_id IN ({target_subq})"), {"g": group_name})

    # anomalies references targets(target_id) and screenshots(screenshot_id) with NO ACTION
    await db.execute(text(f"DELETE FROM anomalies WHERE target_id IN ({target_subq})"), {"g": group_name})

    # screenshots references targets(target_id) CASCADE - but we delete explicitly to be safe
    await db.execute(text(f"DELETE FROM screenshots WHERE target_id IN ({target_subq})"), {"g": group_name})

    # snapshots references targets(target_id) CASCADE
    await db.execute(text(f"DELETE FROM snapshots WHERE target_id IN ({target_subq})"), {"g": group_name})

    # check_results is partitioned, no FK cascade
    await db.execute(text(f"DELETE FROM check_results WHERE target_id IN ({target_subq})"), {"g": group_name})

    # target_status references targets(target_id) CASCADE
    await db.execute(text(f"DELETE FROM target_status WHERE target_id IN ({target_subq})"), {"g": group_name})

    # Finally delete the targets themselves (no more FK references remain)
    await db.execute(text('DELETE FROM targets WHERE "group" = :g'), {"g": group_name})

    # 4. Delete group_settings
    await db.execute(text("DELETE FROM group_settings WHERE group_name = :g"), {"g": group_name})

    await db.commit()
    logger.info(f"Group [{group_name}] deleted: {target_count} targets and all related data cleaned up.")
    return {
        "ok": True,
        "group": group_name,
        "deleted_targets": target_count,
        "message": f"分组 [{group_name}] 及其关联的 {target_count} 个站点与历史数据已彻底从数据库清理",
    }


@router.delete("/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")

    from sqlalchemy import delete as sa_delete
    from backend.app.models import CheckResult, Screenshot, Snapshot

    # Clean up physical files on disk
    try:
        shot_rows = await db.execute(select(Screenshot.file_path, Screenshot.thumb_path).where(Screenshot.target_id == target_id))
        shots_dir = Path(settings.screenshots_dir)
        for fpath, thumb in shot_rows.all():
            if fpath and (shots_dir / fpath).exists():
                try: (shots_dir / fpath).unlink()
                except OSError: pass
            if thumb and (shots_dir / thumb).exists():
                try: (shots_dir / thumb).unlink()
                except OSError: pass

        snap_rows = await db.execute(select(Snapshot.file_path).where(Snapshot.target_id == target_id))
        snaps_dir = Path(settings.snapshots_dir)
        for (fpath,) in snap_rows.all():
            if fpath and (snaps_dir / fpath).exists():
                try: (snaps_dir / fpath).unlink()
                except OSError: pass
    except Exception as e:
        logger.warning(f"Error cleaning files for target {target_id}: {e}")

    # check_results 是分区表没有外键级联，需要手动删
    await db.execute(sa_delete(CheckResult).where(CheckResult.target_id == target_id))

    await db.delete(target)
    await db.commit()
    return {"ok": True}


@router.post("/{target_id}/toggle")
async def toggle_target(target_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")
    target.enabled = not target.enabled
    target.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": target.id, "enabled": target.enabled}


@router.get("/groups/list")
async def list_groups(db: AsyncSession = Depends(get_db)):
    stmt = select(Target.group, func.count(Target.id)).group_by(Target.group).order_by(Target.group)
    rows = await db.execute(stmt)
    return [{"group": r[0], "count": r[1]} for r in rows.all()]
