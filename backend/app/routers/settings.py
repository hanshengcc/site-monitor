"""Global settings and group settings router."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import GlobalSetting, GroupSetting

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ===================== Global Settings =====================

@router.get("/global")
async def get_global_settings(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(GlobalSetting))
    return {r.key: r.value for r in rows.scalars().all()}


@router.put("/global")
async def update_global_settings(body: dict, db: AsyncSession = Depends(get_db)):
    for key, value in body.items():
        stmt = insert(GlobalSetting).values(key=key, value=value).on_conflict_do_update(
            index_elements=["key"], set_={"value": value},
        )
        await db.execute(stmt)
    await db.commit()

    from backend.app.config import settings
    mapping = {
        "check_interval": "check_interval_minutes",
        "screenshot_interval": "screenshot_interval_minutes",
        "default_timeout": "check_timeout",
        "max_concurrent_checks": "max_concurrent_checks",
        "playwright_concurrency": "playwright_concurrency",
        "consecutive_fails_threshold": "consecutive_fails_threshold",
    }
    for db_key, attr in mapping.items():
        if db_key in body:
            val = body[db_key]
            if isinstance(val, str) and val.isdigit():
                val = int(val)
            if db_key in ("check_interval", "screenshot_interval"):
                val = max(1, int(val) // 60) if int(val) >= 60 else 1
            else:
                val = int(val)
            setattr(settings, attr, val)

    if "check_interval" in body or "screenshot_interval" in body:
        from backend.app.scheduler import restart_scheduler
        restart_scheduler()

    return {"ok": True}


# ===================== Group Settings =====================

class GroupSettingUpdate(BaseModel):
    max_concurrency: Optional[int] = None
    rate_limit: Optional[int] = None
    request_timeout: Optional[int] = None
    user_agent: Optional[str] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None


@router.get("/groups")
async def list_group_settings(db: AsyncSession = Depends(get_db)):
    """List all group settings with target counts."""
    from backend.app.models import Target, TargetStatus
    from sqlalchemy import func

    # Get group settings
    try:
        rows = await db.execute(select(GroupSetting).order_by(GroupSetting.group_name))
        group_settings = {gs.group_name: gs for gs in rows.scalars().all()}
    except Exception as e:
        logger.warning(f"Failed to query group settings: {e}")
        group_settings = {}

    # Get group stats
    stmt = (
        select(
            Target.group,
            func.count(Target.id).label("total"),
        )
        .group_by(Target.group)
        .order_by(Target.group)
    )
    group_rows = await db.execute(stmt)

    result = []
    for r in group_rows.all():
        gs = group_settings.get(r.group)
        result.append({
            "group_name": r.group,
            "total_targets": r.total,
            "max_concurrency": gs.max_concurrency if gs else 10,
            "rate_limit": gs.rate_limit if (gs and gs.rate_limit is not None) else 0,
            "request_timeout": gs.request_timeout if gs else 15,
            "user_agent": gs.user_agent if gs else None,
            "enabled": gs.enabled if gs else True,
            "note": gs.note if gs else None,
        })

    return result


@router.put("/groups/{group_name}")
async def update_group_setting(
    group_name: str,
    body: GroupSettingUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update settings for a specific group."""
    data = body.model_dump(exclude_unset=True)
    data["updated_at"] = datetime.now(timezone.utc)

    stmt = insert(GroupSetting).values(
        group_name=group_name, **data
    ).on_conflict_do_update(
        index_elements=["group_name"],
        set_=data,
    )
    await db.execute(stmt)
    await db.commit()
    return {"ok": True}


@router.put("/groups-batch")
async def batch_update_group_settings(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Batch update: {"group_name": {max_concurrency: N, ...}, ...}"""
    for group_name, settings_dict in body.items():
        settings_dict["updated_at"] = datetime.now(timezone.utc)
        stmt = insert(GroupSetting).values(
            group_name=group_name, **settings_dict
        ).on_conflict_do_update(
            index_elements=["group_name"],
            set_=settings_dict,
        )
        await db.execute(stmt)
    await db.commit()
    return {"ok": True, "updated": len(body)}
