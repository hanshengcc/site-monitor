"""Playwright screenshot worker - captures screenshots with page metadata."""
import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.async_api import async_playwright, Browser, Error as PlaywrightError
from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Target, Screenshot, TargetStatus
from backend.app.analyzer import analyze_screenshot


_browser: Optional[Browser] = None
_playwright = None


async def get_browser() -> Browser:
    """Get or create a shared browser instance."""
    global _browser, _playwright
    if _browser is None or not _browser.is_connected():
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-extensions",
            ]
        )
        logger.info("Browser launched")
    return _browser


async def close_browser():
    global _browser, _playwright
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None


async def take_screenshot(target_id: int, url: str) -> Optional[dict]:
    """Take a screenshot of a URL, return metadata dict."""
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
            user_agent="SiteMonitor/1.0 (Screenshot)",
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

        # Navigate
        try:
            await page.goto(url, wait_until="networkidle", timeout=settings.screenshot_timeout * 1000)
        except PlaywrightError:
            # Fallback: at least wait for DOM
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.screenshot_timeout * 1000)
                await page.wait_for_timeout(3000)
            except PlaywrightError as e:
                logger.warning(f"[{target_id}] Navigation failed: {e}")
                return None

        # Inject: disable animations for stable screenshot
        await page.evaluate("""
            () => {
                const style = document.createElement('style');
                style.textContent = '*, *::before, *::after { animation: none !important; transition: none !important; }';
                document.head.appendChild(style);
            }
        """)
        await page.wait_for_timeout(500)

        # Get page info
        page_title = await page.title()
        dom_text_length = await page.evaluate("() => document.body ? document.body.innerText.length : 0")

        # Take screenshot
        await page.screenshot(path=str(filepath), full_page=False, type="jpeg", quality=80)

        # Generate thumbnail
        img = Image.open(filepath)
        width, height = img.size
        thumb = img.copy()
        thumb.thumbnail((320, 200))
        thumb.save(str(thumb_filepath), "JPEG", quality=60)

        file_size = os.path.getsize(filepath)

        # Relative paths for storage
        rel_path = str(filepath.relative_to(screenshots_dir))
        rel_thumb = str(thumb_filepath.relative_to(screenshots_dir))

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
            await page.close()
        if context:
            await context.close()


async def run_screenshots():
    """Run screenshot round for all enabled targets."""
    logger.info("Starting screenshot round...")

    async with AsyncSessionLocal() as session:
        stmt = select(Target).where(Target.enabled == True)
        rows = await session.execute(stmt)
        targets = rows.scalars().all()

    if not targets:
        logger.info("No targets for screenshots.")
        return

    logger.info(f"Taking screenshots for {len(targets)} targets (concurrency={settings.playwright_concurrency})")

    semaphore = asyncio.Semaphore(settings.playwright_concurrency)

    async def shot_with_sem(t):
        async with semaphore:
            return await take_screenshot(t.id, t.url)

    results = await asyncio.gather(*[shot_with_sem(t) for t in targets], return_exceptions=True)

    # Save results and run analysis
    screenshots_dir = Path(settings.screenshots_dir)
    saved = 0

    async with AsyncSessionLocal() as session:
        for r in results:
            if isinstance(r, Exception):
                logger.error(f"Screenshot task exception: {r}")
                continue
            if r is None:
                continue

            # Run anomaly analysis on the image
            img_path = screenshots_dir / r["file_path"]
            analysis = analyze_screenshot(
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

    logger.info(f"Screenshot round done: {saved}/{len(targets)} saved")
