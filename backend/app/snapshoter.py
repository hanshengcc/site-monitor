"""Webpage Snapshot worker (网页时光机) - captures HTML DOM archives with change detection."""
import asyncio
import gzip
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

from loguru import logger
from playwright.async_api import Error as PlaywrightError
from sqlalchemy import or_, select, update
import httpx

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Target, Snapshot, TargetStatus
from backend.app.screenshoter import get_browser
from backend.app.intervals import due_cutoff
from backend.app.checker import _make_ssl_context


# Progress tracking
snapshot_progress: Dict[str, Any] = {
    "running": False,
    "total": 0,
    "processed": 0,
    "saved": 0,
    "changed": 0,
    "started_at": None,
    "ended_at": None,
}


async def unlink_unreferenced_snapshot_files(session, file_paths: List[str]) -> int:
    """Delete snapshot files from disk that no remaining snapshot row points at.

    Unchanged snapshots reuse the previously stored archive instead of writing a
    byte-identical copy, so one file can back several rows. Call this only after
    the owning rows have been deleted and flushed.
    """
    candidates = {p for p in file_paths if p}
    if not candidates:
        return 0

    still_referenced = set((await session.execute(
        select(Snapshot.file_path).where(Snapshot.file_path.in_(candidates))
    )).scalars().all())

    snapshots_dir = Path(settings.snapshots_dir)
    deleted = 0
    for rel_path in candidates - still_referenced:
        try:
            (snapshots_dir / rel_path).unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass
    return deleted


def _extract_title_from_html(html: str) -> str:
    """Fallback title extractor from HTML string."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


async def _save_snapshot_bytes(
    target_id: int,
    url: str,
    html_content: str,
    page_title: str = "",
    http_status: int = 200,
    dom_text_length: int = 0,
    headers: dict = None,
) -> Optional[dict]:
    """Compress HTML, check hash change against last snapshot, and persist to disk and DB."""
    if not html_content:
        return None

    now = datetime.now(timezone.utc)
    content_bytes = html_content.encode("utf-8", errors="replace")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    file_size = len(content_bytes)

    snapshots_dir = Path(settings.snapshots_dir)

    async with AsyncSessionLocal() as session:
        # Compare against the previous snapshot BEFORE compressing and writing.
        # Most rounds find the page unchanged, and the old order paid full gzip
        # plus a disk write for every one of them, storing a byte-identical copy.
        prev_row = await session.execute(
            select(Snapshot.content_hash, Snapshot.file_path, Snapshot.compressed_size)
            .where(Snapshot.target_id == target_id)
            .order_by(Snapshot.taken_at.desc())
            .limit(1)
        )
        prev = prev_row.first()
        has_changed = not (prev and prev[0] == content_hash)

        if has_changed:
            compressed_bytes = gzip.compress(content_bytes, compresslevel=6)
            compressed_size = len(compressed_bytes)

            date_dir = snapshots_dir / now.strftime("%Y-%m-%d")
            date_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{target_id}_{now.strftime('%H%M%S')}_{content_hash[:8]}.html.gz"
            filepath = date_dir / filename
            await asyncio.to_thread(filepath.write_bytes, compressed_bytes)
            rel_path = str(filepath.relative_to(snapshots_dir))
        else:
            # Unchanged: record the observation, but point at the stored copy.
            rel_path = prev[1]
            compressed_size = prev[2]

        snapshot_obj = Snapshot(
            target_id=target_id,
            taken_at=now,
            file_path=rel_path,
            file_size=file_size,
            compressed_size=compressed_size,
            content_hash=content_hash,
            page_title=page_title or "",
            http_status=http_status,
            dom_text_length=dom_text_length or len(html_content),
            has_changed=has_changed,
            headers=headers or {},
        )
        session.add(snapshot_obj)
        await session.flush()

        # Update TargetStatus
        await session.execute(
            update(TargetStatus)
            .where(TargetStatus.target_id == target_id)
            .values(
                last_snapshot_id=snapshot_obj.id,
                last_snapshot_at=now,
            )
        )
        await session.commit()

        snapshot_id = snapshot_obj.id

    return {
        "id": snapshot_id,
        "target_id": target_id,
        "taken_at": now,
        "file_path": rel_path,
        "file_size": file_size,
        "compressed_size": compressed_size,
        "content_hash": content_hash,
        "page_title": page_title,
        "http_status": http_status,
        "dom_text_length": dom_text_length,
        "has_changed": has_changed,
    }


async def _capture_with_playwright(target_id: int, url: str) -> Optional[dict]:
    """Capture snapshot with dynamic DOM rendering using Playwright."""
    browser = await get_browser()
    context = None
    page = None

    try:
        context = await browser.new_context(
            viewport={"width": settings.viewport_width, "height": settings.viewport_height},
            device_scale_factor=1,
            ignore_https_errors=True,
            user_agent=settings.default_user_agent or "SiteMonitor/1.0 (WaybackSnapshot)",
        )
        context.set_default_timeout(settings.screenshot_timeout * 1000)
        page = await context.new_page()

        http_status = 200
        def on_response(response):
            nonlocal http_status
            if response.url == url or response.url.rstrip("/") == url.rstrip("/"):
                http_status = response.status

        page.on("response", on_response)

        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=settings.screenshot_timeout * 1000)
            if resp:
                http_status = resp.status
        except PlaywrightError:
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=settings.screenshot_timeout * 1000)
                await page.wait_for_timeout(2000)
                if resp:
                    http_status = resp.status
            except PlaywrightError as e:
                logger.warning(f"[{target_id}] Playwright snapshot navigation failed: {e}")
                return None

        page_title = await page.title()
        html_content = await page.content()
        dom_text_length = await page.evaluate("() => document.body ? document.body.innerText.length : 0")

        return await _save_snapshot_bytes(
            target_id=target_id,
            url=url,
            html_content=html_content,
            page_title=page_title,
            http_status=http_status,
            dom_text_length=dom_text_length,
        )
    except Exception as e:
        logger.error(f"[{target_id}] Playwright snapshot error: {e}")
        return None
    finally:
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass


async def _capture_with_httpx(target_id: int, url: str) -> Optional[dict]:
    """Fallback snapshot capture using HTTP client."""
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.default_user_agent or "SiteMonitor/1.0 (WaybackSnapshot)"},
            verify=_make_ssl_context(),
            timeout=settings.check_timeout,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            html_content = resp.text
            page_title = _extract_title_from_html(html_content)
            http_status = resp.status_code
            dom_text_length = len(html_content)

            return await _save_snapshot_bytes(
                target_id=target_id,
                url=url,
                html_content=html_content,
                page_title=page_title,
                http_status=http_status,
                dom_text_length=dom_text_length,
                headers=dict(resp.headers),
            )
    except Exception as e:
        logger.warning(f"[{target_id}] HTTP snapshot fallback failed: {e}")
        return None


async def take_snapshot(target_id: int, url: str, prefer_playwright: bool = True) -> Optional[dict]:
    """Take a webpage snapshot with timeout protection and HTTP fallback."""
    if not url.startswith("http"):
        url = f"https://{url}"

    if prefer_playwright:
        try:
            result = await asyncio.wait_for(
                _capture_with_playwright(target_id, url),
                timeout=settings.screenshot_timeout * 2 + 5,
            )
            if result:
                return result
        except asyncio.TimeoutError:
            logger.warning(f"[{target_id}] Playwright snapshot timed out, trying HTTP fallback...")
        except Exception as e:
            logger.warning(f"[{target_id}] Playwright snapshot error ({e}), trying HTTP fallback...")

    # Fallback to HTTP fetch
    return await _capture_with_httpx(target_id, url)


async def run_snapshots(group: Optional[str] = None, force: bool = False):
    """Run a snapshot round over the targets whose snapshot_interval has elapsed.

    Like screenshots, archiving every enabled target on every round cannot finish
    within the scheduled interval at scale. Pass force=True for a manual full sweep.
    """
    global snapshot_progress
    if snapshot_progress["running"]:
        logger.warning("Snapshot round already running, skipping...")
        return

    logger.info("Starting snapshot round (网页时光机)...")
    now_utc = datetime.now(timezone.utc)
    snapshot_progress.update({
        "running": True,
        "total": 0,
        "processed": 0,
        "saved": 0,
        "changed": 0,
        "started_at": now_utc.isoformat(),
        "ended_at": None,
    })

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(Target).where(Target.enabled == True)
            if group:
                stmt = stmt.where(Target.group == group)
            if not force:
                stmt = stmt.outerjoin(
                    TargetStatus, Target.id == TargetStatus.target_id
                ).where(or_(
                    TargetStatus.last_snapshot_at.is_(None),
                    TargetStatus.last_snapshot_at < due_cutoff("snapshot_interval"),
                ))
            stmt = stmt.order_by(Target.id)
            rows = await session.execute(stmt)
            targets = rows.scalars().all()

        total = len(targets)
        snapshot_progress["total"] = total
        logger.info(f"Targets to snapshot: {total}")

        if not total:
            return

        concurrency = max(1, settings.playwright_concurrency // 2)
        sem = asyncio.Semaphore(concurrency)

        async def worker(t: Target):
            async with sem:
                try:
                    res = await take_snapshot(t.id, t.url)
                    snapshot_progress["processed"] += 1
                    if res:
                        snapshot_progress["saved"] += 1
                        if res.get("has_changed"):
                            snapshot_progress["changed"] += 1
                except Exception as e:
                    snapshot_progress["processed"] += 1
                    logger.error(f"Snapshot worker error on target {t.id}: {e}")

        chunk_size = concurrency * 4
        for i in range(0, total, chunk_size):
            chunk = targets[i : i + chunk_size]
            await asyncio.gather(*(worker(t) for t in chunk), return_exceptions=True)
            logger.info(
                f"Snapshot progress: {min(i + chunk_size, total)}/{total} processed, "
                f"{snapshot_progress['saved']} saved ({snapshot_progress['changed']} changed)"
            )

        logger.info(
            f"Snapshot round finished: {snapshot_progress['saved']}/{total} saved "
            f"({snapshot_progress['changed']} changed)"
        )
    finally:
        snapshot_progress["running"] = False
        snapshot_progress["ended_at"] = datetime.now(timezone.utc).isoformat()
