"""Targets CRUD router."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from backend.app.database import get_db
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

    total = (await db.execute(count_stmt)).scalar()

    stmt = stmt.order_by(Target.id).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    targets = rows.unique().scalars().all()

    # Post-filter by status if needed
    items = [TargetOut.model_validate(t) for t in targets]
    if status == "ok":
        items = [t for t in items if t.status and t.status.is_ok is True]
    elif status == "fail":
        items = [t for t in items if t.status and t.status.is_ok is False]
    elif status == "unknown":
        items = [t for t in items if t.status is None or t.status.is_ok is None]

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
    """Batch import URLs."""
    created = 0
    skipped = 0
    for url in body.urls:
        url = url.strip()
        if not url:
            continue
        # Add http:// if missing
        if not url.startswith("http"):
            url = "https://" + url
        # Skip duplicates
        exists = await db.execute(select(Target.id).where(Target.url == url))
        if exists.scalar():
            skipped += 1
            continue
        target = Target(
            url=url,
            name=url,
            group=body.group,
            check_interval=body.check_interval,
            shot_interval=body.shot_interval,
        )
        db.add(target)
        created += 1

    await db.commit()
    return {"created": created, "skipped": skipped, "total": len(body.urls)}


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


@router.delete("/{target_id}")
async def delete_target(target_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(Target).where(Target.id == target_id)
    row = await db.execute(stmt)
    target = row.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target not found")

    # check_results 是分区表没有外键级联，需要手动删
    from sqlalchemy import delete as sa_delete
    from backend.app.models import CheckResult
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
