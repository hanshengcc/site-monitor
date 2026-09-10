"""Playwright screenshot worker - captures screenshots with page metadata."""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from loguru import logger
from playwright.async_api import async_playwright, Browser, Error as PlaywrightError
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Target, Screenshot, TargetStatus, Anomaly
from backend.app.analyzer import analyze_screenshot


_browser: Optional[Browser] = None
_playwright = None
_render_count: int = 0
_MAX_RENDERS_BEFORE_RECYCLE: int = 100
_browser_lock = asyncio.Lock()


async def _stop_playwright_safely(pw, br):
    """Safely stop a browser and playwright instance."""
    if br is not None:
        try:
            await br.close()
        except Exception as e:
            logger.debug(f"Error closing browser: {e}")
    if pw is not None:
        try:
            await pw.stop()
        except Exception as e:
            logger.debug(f"Error stopping playwright: {e}")


async def _close_browser_unlocked():
    global _browser, _playwright, _render_count
    br, pw = _browser, _playwright
    _browser = None
    _playwright = None
    _render_count = 0
    await _stop_playwright_safely(pw, br)


async def close_browser():
    """Explicitly close global shared browser and stop playwright process."""
    async with _browser_lock:
        await _close_browser_unlocked()
        logger.info("Shared browser and Playwright stopped.")


async def get_browser() -> Browser:
    """Get or create a shared browser instance with strict concurrency lock and automatic recycling."""
    global _browser, _playwright, _render_count
    async with _browser_lock:
        # 1. Recycle if render threshold exceeded
        if _browser is not None and _render_count >= _MAX_RENDERS_BEFORE_RECYCLE:
            logger.info(f"Recycling browser after {_render_count} screenshots to release memory...")
            await _close_browser_unlocked()

        # 2. Check if existing browser is still connected
        if _browser is not None:
            try:
                if not _browser.is_connected():
                    logger.warning("Shared browser disconnected, resetting...")
                    await _close_browser_unlocked()
            except Exception:
                await _close_browser_unlocked()

        # 3. Launch new browser if none exists
        if _browser is None:
            # Clean up any dangling playwright driver first
            if _playwright is not None:
                await _stop_playwright_safely(_playwright, None)
                _playwright = None

            logger.info("Starting shared Playwright browser instance...")
            _playwright = await async_playwright().start()
            _browser = await _playwright.chromium.launch(
                args=[
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                ]
            )
            _render_count = 0
            logger.info("Shared browser launched successfully.")

        return _browser


async def _do_take_screenshot(target_id: int, url: str) -> Optional[dict]:
    """Execute single screenshot capture."""
    browser = await get_browser()
    context = None
    page = None

    screenshots_dir = Path(settings.screenshots_dir)
    now = datetime.now(timezone.utc)
    date_dir = screenshots_dir / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{target_id}_{now.strftime('%H%M%S')}.jpeg"
    filepath = date_dir / filename
    thumb_filename = f"{target_id}_{now.strftime('%H%M%S')}_thumb.jpeg"
    thumb_filepath = date_dir / thumb_filename

    console_errors = 0
    failed_requests = 0
    page_title = ""
    dom_text_length = 0

    try:
        context = await browser.new_context(
            viewport={"width": settings.viewport_width, "height": settings.viewport_height},
            device_scale_factor=1,
            ignore_https_errors=True,
            user_agent=settings.default_user_agent or "SiteMonitor/1.0 (Screenshot)",
        )
        context.set_default_timeout(settings.screenshot_timeout * 1000)

        page = await context.new_page()

        # Track console errors
        def on_console(msg):
            nonlocal console_errors
            if msg.type == "error":
                console_errors += 1

        page.on("console", on_console)

        # Track failed requests
        def on_response(response):
            nonlocal failed_requests
            if response.status >= 400:
                failed_requests += 1

        page.on("response", on_response)

        # Navigate (first try networkidle, then fallback to domcontentloaded)
        try:
            await page.goto(url, wait_until="networkidle", timeout=settings.screenshot_timeout * 1000)
        except PlaywrightError:
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.screenshot_timeout * 1000)
                await page.wait_for_timeout(2000)
            except PlaywrightError as e:
                logger.warning(f"[{target_id}] Navigation failed: {e}")
                return None

        # Inject: disable animations for stable screenshot
        try:
            await page.evaluate("""
                () => {
                    const style = document.createElement('style');
                    style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
                    document.head.appendChild(style);
                }
            """)
            await page.wait_for_timeout(300)
        except Exception:
            pass

        # Get page info
        page_title = await page.title()
        dom_text_length = await page.evaluate("() => document.body ? document.body.innerText.length : 0")

        # Take screenshot
        await page.screenshot(path=str(filepath), full_page=False, type="jpeg", quality=80)

        # Offload image processing to threadpool to avoid blocking asyncio event loop
        def _process_image():
            img = Image.open(filepath)
            w, h = img.size
            thumb = img.copy()
            thumb.thumbnail((320, 200))
            thumb.save(str(thumb_filepath), "JPEG", quality=60)
            sz = os.path.getsize(filepath)
            return w, h, sz

        width, height, file_size = await asyncio.to_thread(_process_image)

        rel_path = str(filepath.relative_to(screenshots_dir))
        rel_thumb = str(thumb_filepath.relative_to(screenshots_dir))

        global _render_count
        async with _browser_lock:
            _render_count += 1

        return {
            "target_id": target_id,
            "taken_at": now,
            "file_path": rel_path,
            "thumb_path": rel_thumb,
            "file_size": file_size,
            "width": width,
            "height": height,
            "page_title": page_title,
            "console_errors": console_errors,
            "failed_requests": failed_requests,
            "dom_text_length": dom_text_length,
        }

    except Exception as e:
        logger.error(f"[{target_id}] Screenshot error: {type(e).__name__}: {e}")
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


async def take_screenshot(target_id: int, url: str) -> Optional[dict]:
    """Take a screenshot with outer timeout protection."""
    if not url.startswith("http"):
        url = f"https://{url}"
    try:
        # Guard with outer timeout so bad sites cannot hang a coroutine indefinitely
        return await asyncio.wait_for(
            _do_take_screenshot(target_id, url),
            timeout=settings.screenshot_timeout * 2 + 10,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{target_id}] Screenshot timed out")
        return None
    except Exception as e:
        logger.error(f"[{target_id}] Outer screenshot error: {e}")
        return None


async def _save_and_analyze_batch(batch_results: List[dict]) -> int:
    """Save a batch of screenshot results and record anomalies."""
    if not batch_results:
        return 0

    screenshots_dir = Path(settings.screenshots_dir)
    saved = 0

    async with AsyncSessionLocal() as session:
        for r in batch_results:
            if not r or isinstance(r, Exception):
                continue

            # Run anomaly analysis in worker thread
            img_path = screenshots_dir / r["file_path"]
            analysis = await asyncio.to_thread(
                analyze_screenshot,
                str(img_path),
                page_title=r.get("page_title", ""),
                console_errors=r.get("console_errors", 0),
                failed_requests=r.get("failed_requests", 0),
                dom_text_length=r.get("dom_text_length", 0),
            )

            r["is_anomaly"] = analysis["is_anomaly"]
            r["anomaly_score"] = analysis["score"]
            r["anomaly_reasons"] = analysis["reasons"]

            shot = Screenshot(**r)
            session.add(shot)
            await session.flush()

            # Record or resolve Anomaly
            if analysis["is_anomaly"]:
                existing = await session.execute(
                    select(Anomaly).where(
                        Anomaly.target_id == r["target_id"],
                        Anomaly.anomaly_type == "render_anomaly",
                        Anomaly.state == "open",
                    )
                )
                if not existing.scalar_one_or_none():
                    anomaly = Anomaly(
                        target_id=r["target_id"],
                        detected_at=r["taken_at"],
                        anomaly_type="render_anomaly",
                        score=analysis["score"],
                        reasons=analysis["reasons"],
                        screenshot_id=shot.id,
                        state="open",
                        notified=False,
                    )
                    session.add(anomaly)
            else:
                open_anomalies = await session.execute(
                    select(Anomaly).where(
                        Anomaly.target_id == r["target_id"],
                        Anomaly.anomaly_type == "render_anomaly",
                        Anomaly.state == "open",
                    )
                )
                for oa in open_anomalies.scalars().all():
                    was_notified = oa.notified
                    oa.state = "resolved"
                    oa.resolved_at = r["taken_at"]
                    oa.notified = False if was_notified else True

            # Update target_status with latest screenshot
            stmt = insert(TargetStatus).values(
                target_id=r["target_id"],
                last_screenshot_id=shot.id,
                last_screenshot_at=r["taken_at"],
                has_anomaly=analysis["is_anomaly"],
            ).on_conflict_do_update(
                index_elements=["target_id"],
                set_={
                    "last_screenshot_id": shot.id,
                    "last_screenshot_at": r["taken_at"],
                    "has_anomaly": analysis["is_anomaly"],
                },
            )
            await session.execute(stmt)
            saved += 1

        await session.commit()
    return saved


async def run_screenshots(max_targets: Optional[int] = None):
    """Run screenshot round in batches to prevent memory bloat and process leaks."""
    logger.info("Starting screenshot round...")

    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(Target)
                .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
                .where(Target.enabled == True)
                .order_by(
                    TargetStatus.has_anomaly.desc().nulls_last(),
                    TargetStatus.last_screenshot_at.asc().nulls_first(),
                    Target.id.asc(),
                )
            )
            if max_targets:
                stmt = stmt.limit(max_targets)
            rows = await session.execute(stmt)
            targets = rows.scalars().all()

        if not targets:
            logger.info("No targets for screenshots.")
            return

        total = len(targets)
        concurrency = min(max(1, settings.playwright_concurrency), 8)
        logger.info(f"Taking screenshots for {total} targets (concurrency={concurrency})")

        semaphore = asyncio.Semaphore(concurrency)

        async def shot_with_sem(t):
            async with semaphore:
                return await take_screenshot(t.id, t.url)

        # Process in chunks to prevent unbounded memory and coroutine growth
        chunk_size = max(10, concurrency * 2)
        total_saved = 0

        for i in range(0, total, chunk_size):
            chunk = targets[i:i + chunk_size]
            chunk_results = await asyncio.gather(*[shot_with_sem(t) for t in chunk], return_exceptions=True)
            valid_results = [r for r in chunk_results if r and not isinstance(r, Exception)]
            saved = await _save_and_analyze_batch(valid_results)
            total_saved += saved
            logger.info(f"Screenshot progress: {min(i + chunk_size, total)}/{total} processed, {total_saved} saved")

        logger.info(f"Screenshot round done: {total_saved}/{total} saved")
    except Exception as e:
        logger.exception(f"Unexpected error in run_screenshots: {e}")
    finally:
        # ALWAYS close shared browser and stop playwright at the end of the round
        await close_browser()
