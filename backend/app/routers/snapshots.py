"""Snapshots router - Wayback Machine (网页时光机) preview, source, diff, and management."""
import gzip
import html
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import settings
from backend.app.database import get_db
from backend.app.models import Snapshot, Target
from backend.app.schemas import SnapshotOut

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


@router.get("/{target_id}", response_model=dict)
async def list_snapshots(
    target_id: int,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    changed_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """List historical snapshots for a specific target with pagination and filter."""
    base = select(Snapshot).where(Snapshot.target_id == target_id)
    if changed_only:
        base = base.where(Snapshot.has_changed == True)

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    stmt = base.order_by(Snapshot.taken_at.desc()).offset((page - 1) * size).limit(size)
    rows = await db.execute(stmt)
    items = [SnapshotOut.model_validate(r) for r in rows.scalars().all()]
    return {"total": total, "items": items, "page": page, "size": size}


@router.get("/detail/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot_detail(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Get metadata for a single snapshot."""
    stmt = select(Snapshot).where(Snapshot.id == snapshot_id)
    row = await db.execute(stmt)
    snapshot = row.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")
    return snapshot


def _read_snapshot_html(file_path: str) -> str:
    """Read and decompress snapshot HTML from disk."""
    full_path = Path(settings.snapshots_dir) / file_path
    if not full_path.exists():
        raise HTTPException(404, "Snapshot file not found on disk")

    try:
        if file_path.endswith(".gz"):
            with gzip.open(full_path, "rt", encoding="utf-8", errors="replace") as f:
                return f.read()
        else:
            return full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(500, f"Failed to read snapshot file: {e}")


@router.get("/view/{snapshot_id}")
async def view_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Wayback Machine interactive view - renders the archived page with injected <base> tag and time banner."""
    stmt = select(Snapshot, Target).join(Target, Snapshot.target_id == Target.id).where(Snapshot.id == snapshot_id)
    row = await db.execute(stmt)
    res = row.first()
    if not res:
        raise HTTPException(404, "Snapshot not found")
    snapshot, target = res

    # Find previous and next snapshot IDs for timeline navigation
    prev_stmt = (
        select(Snapshot.id)
        .where(Snapshot.target_id == target.id, Snapshot.taken_at < snapshot.taken_at)
        .order_by(Snapshot.taken_at.desc())
        .limit(1)
    )
    prev_id = (await db.execute(prev_stmt)).scalar_one_or_none()

    next_stmt = (
        select(Snapshot.id)
        .where(Snapshot.target_id == target.id, Snapshot.taken_at > snapshot.taken_at)
        .order_by(Snapshot.taken_at.asc())
        .limit(1)
    )
    next_id = (await db.execute(next_stmt)).scalar_one_or_none()

    raw_html = _read_snapshot_html(snapshot.file_path)

    # Format info
    taken_str = snapshot.taken_at.strftime("%Y-%m-%d %H:%M:%S")
    target_url = target.url
    target_name = target.name or target.url
    status_label = f"{snapshot.http_status or 200}"
    size_kb = f"{(snapshot.file_size or len(raw_html)) / 1024:.1f} KB"
    changed_badge = (
        '<span style="background: #e6a23c; color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 11px;">⚡ 内容变更</span>'
        if snapshot.has_changed
        else '<span style="background: #67c23a; color: #fff; padding: 2px 6px; border-radius: 4px; font-size: 11px;">⏺ 与上版一致</span>'
    )

    prev_btn = (
        f'<a href="/api/snapshots/view/{prev_id}" style="color: #409eff; text-decoration: none; margin-right: 8px;">◀ 上一个快照</a>'
        if prev_id
        else '<span style="color: #666; margin-right: 8px;">◀ 最早快照</span>'
    )
    next_btn = (
        f'<a href="/api/snapshots/view/{next_id}" style="color: #409eff; text-decoration: none; margin-left: 8px;">下一个快照 ▶</a>'
        if next_id
        else '<span style="color: #666; margin-left: 8px;">最新快照 ▶</span>'
    )

    # Construct Wayback Banner HTML
    banner_html = f"""
<!-- SITE MONITOR WAYBACK MACHINE BANNER -->
<div id="__sm_wayback_bar__" style="position: fixed; top: 0; left: 0; width: 100%; height: 42px; background: rgba(22, 27, 34, 0.95); backdrop-filter: blur(8px); border-bottom: 2px solid #30363d; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13px; z-index: 2147483647; display: flex; align-items: center; justify-content: space-between; padding: 0 16px; box-sizing: border-box; box-shadow: 0 4px 12px rgba(0,0,0,0.3);">
  <div style="display: flex; align-items: center; gap: 12px;">
    <strong style="color: #58a6ff; font-size: 14px; display: flex; align-items: center; gap: 4px;">
      🕰️ 网页时光机
    </strong>
    <span style="color: #8b949e;">|</span>
    <span>快照时间: <b style="color: #f0f6fc;">{taken_str}</b></span>
    {changed_badge}
    <span style="color: #8b949e;">|</span>
    <span>状态码: <b style="color: #7ee787;">{status_label}</b></span>
    <span style="color: #8b949e;">大小: {size_kb}</span>
  </div>
  <div style="display: flex; align-items: center; gap: 12px;">
    <div>{prev_btn} {next_btn}</div>
    <span style="color: #8b949e;">|</span>
    <a href="/api/snapshots/raw/{snapshot_id}" target="_blank" style="color: #8b949e; text-decoration: none; font-size: 12px;">查看源码</a>
    <a href="/api/snapshots/download/{snapshot_id}" style="color: #8b949e; text-decoration: none; font-size: 12px;">下载HTML</a>
    <a href="{html.escape(target_url)}" target="_blank" rel="noopener noreferrer" style="color: #58a6ff; text-decoration: none; font-size: 12px;">访问原站 ↗</a>
    <button onclick="document.getElementById('__sm_wayback_bar__').style.display='none'; document.getElementById('__sm_wayback_mini__').style.display='block';" style="background: none; border: 1px solid #30363d; color: #8b949e; border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 12px;" title="收起时光机条">✕</button>
  </div>
</div>
<div id="__sm_wayback_mini__" style="display: none; position: fixed; top: 10px; right: 10px; z-index: 2147483647; background: #161b22; border: 1px solid #30363d; border-radius: 20px; padding: 4px 12px; font-size: 12px; color: #58a6ff; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.4);" onclick="document.getElementById('__sm_wayback_bar__').style.display='flex'; this.style.display='none';">
  🕰️ 展开时光机条
</div>
<div style="height: 42px;" id="__sm_wayback_spacer__"></div>
"""

    base_tag = f'<base href="{html.escape(target_url)}" target="_blank">'

    # Inject base tag into <head>
    if "<head" in raw_html.lower():
        # Inject right after <head...>
        parts = re.split(r"(<head[^>]*>)", raw_html, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) >= 3:
            raw_html = parts[0] + parts[1] + "\n" + base_tag + "\n" + parts[2]
        else:
            raw_html = base_tag + "\n" + raw_html
    else:
        raw_html = f"<head>{base_tag}</head>\n" + raw_html

    # Inject banner right after <body...> or at beginning
    if "<body" in raw_html.lower():
        parts = re.split(r"(<body[^>]*>)", raw_html, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) >= 3:
            raw_html = parts[0] + parts[1] + "\n" + banner_html + "\n" + parts[2]
        else:
            raw_html = banner_html + raw_html
    else:
        raw_html = banner_html + raw_html

    return Response(
        content=raw_html,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


@router.get("/raw/{snapshot_id}")
async def get_raw_snapshot_html(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Get the raw uncompressed HTML source code of the snapshot."""
    stmt = select(Snapshot).where(Snapshot.id == snapshot_id)
    row = await db.execute(stmt)
    snapshot = row.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")

    raw_html = _read_snapshot_html(snapshot.file_path)
    return PlainTextResponse(content=raw_html, media_type="text/plain; charset=utf-8")


@router.get("/download/{snapshot_id}")
async def download_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Download the snapshot as an uncompressed .html file."""
    stmt = select(Snapshot, Target).join(Target, Snapshot.target_id == Target.id).where(Snapshot.id == snapshot_id)
    row = await db.execute(stmt)
    res = row.first()
    if not res:
        raise HTTPException(404, "Snapshot not found")
    snapshot, target = res

    raw_html = _read_snapshot_html(snapshot.file_path)
    date_str = snapshot.taken_at.strftime("%Y%m%d_%H%M%S")
    clean_name = re.sub(r"[^\w\-_\.]", "_", target.name or target.url)[:30]
    filename = f"snapshot_{clean_name}_{date_str}.html"

    return Response(
        content=raw_html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/diff/{snapshot_id}")
async def get_snapshot_diff(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Compare snapshot with its previous version for the same target."""
    stmt = select(Snapshot).where(Snapshot.id == snapshot_id)
    snapshot = (await db.execute(stmt)).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")

    prev_stmt = (
        select(Snapshot)
        .where(Snapshot.target_id == snapshot.target_id, Snapshot.taken_at < snapshot.taken_at)
        .order_by(Snapshot.taken_at.desc())
        .limit(1)
    )
    prev = (await db.execute(prev_stmt)).scalar_one_or_none()

    if not prev:
        return {
            "has_previous": False,
            "current": SnapshotOut.model_validate(snapshot),
            "previous": None,
            "has_changed": True,
            "size_diff": 0,
            "title_changed": False,
            "status_changed": False,
        }

    size_diff = (snapshot.file_size or 0) - (prev.file_size or 0)
    title_changed = snapshot.page_title != prev.page_title
    status_changed = snapshot.http_status != prev.http_status
    hash_changed = snapshot.content_hash != prev.content_hash

    return {
        "has_previous": True,
        "current": SnapshotOut.model_validate(snapshot),
        "previous": SnapshotOut.model_validate(prev),
        "has_changed": hash_changed,
        "size_diff": size_diff,
        "title_changed": title_changed,
        "status_changed": status_changed,
    }


@router.delete("/{snapshot_id}")
async def delete_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a single snapshot record and remove its file from disk."""
    stmt = select(Snapshot).where(Snapshot.id == snapshot_id)
    snapshot = (await db.execute(stmt)).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(404, "Snapshot not found")

    # Remove file on disk
    if snapshot.file_path:
        full_path = Path(settings.snapshots_dir) / snapshot.file_path
        if full_path.exists():
            try:
                full_path.unlink()
            except OSError:
                pass

    await db.delete(snapshot)
    await db.commit()
    return {"ok": True, "message": "Snapshot deleted"}
