"""Screenshots router."""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Screenshot
from backend.app.schemas import ScreenshotOut

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])


@router.get("/{target_id}", response_model=dict)
async def list_screenshots(
    target_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    anomaly_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    base = select(Screenshot).where(Screenshot.target_id == target_id)
    if anomaly_only:
        base = base.where(Screenshot.is_anomaly == True)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar()
    stmt = base.order_by(Screenshot.taken_at.desc()).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    items = [ScreenshotOut.model_validate(r) for r in rows.scalars().all()]
    return {"total": total, "items": items, "page": page, "size": size}


@router.get("/image/{screenshot_id}")
async def get_screenshot_image(screenshot_id: int, thumb: bool = False, db: AsyncSession = Depends(get_db)):
    stmt = select(Screenshot).where(Screenshot.id == screenshot_id)
    row = await db.execute(stmt)
    shot = row.scalar_one_or_none()
    if not shot:
        from fastapi import HTTPException
        raise HTTPException(404, "Screenshot not found")

    path = shot.thumb_path if (thumb and shot.thumb_path) else shot.file_path
    full_path = Path(settings.screenshots_dir) / path
    if not full_path.exists():
        from fastapi import HTTPException
        raise HTTPException(404, "File not found")

    return FileResponse(str(full_path), media_type="image/jpeg")


@router.get("/file/{file_path:path}")
async def serve_screenshot_file(file_path: str):
    """Serve screenshot by relative path."""
    full_path = Path(settings.screenshots_dir) / file_path
    if not full_path.exists() or not full_path.is_relative_to(Path(settings.screenshots_dir)):
        from fastapi import HTTPException
        raise HTTPException(404, "File not found")
    return FileResponse(str(full_path), media_type="image/jpeg")
