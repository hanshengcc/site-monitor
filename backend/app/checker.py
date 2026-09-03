"""HTTP health checker - async checking with realtime DB updates via write queue."""
import asyncio
import ssl
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Target, CheckResult, TargetStatus

# Realtime progress (read by /api/tasks/progress)
check_progress = {"running": False, "total": 0, "done": 0, "ok": 0, "fail": 0}


def _make_ssl_context() -> ssl.SSLContext:
    """Create a permissive SSL context that works with broken/legacy TLS servers."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Allow legacy renegotiation for old servers
    ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT') else 0
    # Set minimum TLS to 1.0 for maximum compatibility
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    # Permissive ciphers
    ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
    return ctx


def _is_ssl_error(exc: Exception) -> bool:
    """Check if an exception is SSL-related."""
    err_str = str(exc).lower()
    ssl_keywords = [
        'ssl', 'tls', 'certificate', 'handshake', 'tlsv1',
        'sslv3', 'cert', 'ssl_error', 'ssl:',
    ]
    return any(kw in err_str for kw in ssl_keywords)


async def check_single(
    client: httpx.AsyncClient,
    target_id: int,
    url: str,
    expect_status: int = 200,
    expect_keyword: Optional[str] = None,
    request_timeout: int = 15,
    follow_redirects: bool = True,
    custom_headers: Optional[dict] = None,
    retry_http_on_ssl_error: bool = True,
) -> dict:
    """Check a single URL with custom parameters.
    If HTTPS fails due to SSL error, automatically retry with HTTP.
    """
    result = await _do_check(
        client, target_id, url,
        expect_status, expect_keyword,
        request_timeout, follow_redirects, custom_headers,
    )

    # Auto-fallback: HTTPS SSL error → try HTTP
    if (
        not result["is_ok"]
        and result["error"]
        and retry_http_on_ssl_error
        and url.startswith("https://")
        and _is_ssl_error(Exception(result["error"]))
    ):
        http_url = url.replace("https://", "http://", 1)
        logger.debug(f"[{target_id}] SSL error, retrying with HTTP: {http_url}")
        fallback = await _do_check(
            client, target_id, http_url,
            expect_status, expect_keyword,
            request_timeout, follow_redirects, custom_headers,
        )
        if fallback["is_ok"]:
            fallback["error"] = None
            return fallback
        else:
            # Both failed: keep original SSL error but note fallback attempted
            result["error"] = f"ssl_error (HTTP fallback also failed): {result['error']}"

    return result


async def _do_check(
    client: httpx.AsyncClient,
    target_id: int,
    url: str,
    expect_status: int = 200,
    expect_keyword: Optional[str] = None,
    request_timeout: int = 15,
    follow_redirects: bool = True,
    custom_headers: Optional[dict] = None,
) -> dict:
    """Perform a single HTTP check."""
    start = time.monotonic()
    result = {
        "target_id": target_id,
        "checked_at": datetime.now(timezone.utc),
        "status_code": None,
        "latency_ms": None,
        "is_ok": False,
        "error": None,
    }
    headers = dict(custom_headers) if custom_headers else {}
    try:
        resp = await client.get(
            url, follow_redirects=follow_redirects,
            timeout=request_timeout, headers=headers,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        result["status_code"] = resp.status_code
        result["latency_ms"] = elapsed_ms
        if resp.status_code != expect_status:
            result["error"] = f"expected {expect_status}, got {resp.status_code}"
            return result
        if expect_keyword:
            if expect_keyword not in resp.text[:50000]:
                result["error"] = f"keyword '{expect_keyword}' not found"
                return result
        result["is_ok"] = True
    except httpx.TimeoutException:
        result["latency_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = "timeout"
    except httpx.ConnectError as e:
        result["latency_ms"] = int((time.monotonic() - start) * 1000)
        err_str = str(e)
        if _is_ssl_error(e):
            result["error"] = f"ssl_error: {err_str[:200]}"
        else:
            result["error"] = f"connect_error: {err_str[:200]}"
    except ssl.SSLError as e:
        result["latency_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = f"ssl_error: {str(e)[:200]}"
    except Exception as e:
        result["latency_ms"] = int((time.monotonic() - start) * 1000)
        result["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    return result


async def _db_writer(queue: asyncio.Queue, batch_size: int = 20):
    """Background writer: reads results from queue, batch-saves to DB."""
    buffer = []
    while True:
        try:
            # Wait for first item (blocks until available or None sentinel)
            item = await asyncio.wait_for(queue.get(), timeout=2.0)
            if item is None:
                # Sentinel: flush remaining and exit
                if buffer:
                    await _flush_buffer(buffer)
                break
            buffer.append(item)
            # Drain any additional items already in queue
            while not queue.empty() and len(buffer) < batch_size:
                item = queue.get_nowait()
                if item is None:
                    await _flush_buffer(buffer)
                    return
                buffer.append(item)
            # Flush batch
            await _flush_buffer(buffer)
            buffer = []
        except asyncio.TimeoutError:
            # Flush partial buffer on timeout
            if buffer:
                await _flush_buffer(buffer)
                buffer = []


async def _flush_buffer(buffer: list):
    """Write a batch of results to DB in a single session."""
    if not buffer:
        return
    try:
        async with AsyncSessionLocal() as session:
            for r in buffer:
                session.add(CheckResult(**r))
                stmt = insert(TargetStatus).values(
                    target_id=r["target_id"],
                    is_ok=r["is_ok"],
                    last_check_at=r["checked_at"],
                    last_status_code=r["status_code"],
                    last_latency_ms=r["latency_ms"],
                    last_error=r["error"],
                    consecutive_fails=0 if r["is_ok"] else 1,
                ).on_conflict_do_update(
                    index_elements=["target_id"],
                    set_={
                        "is_ok": r["is_ok"],
                        "last_check_at": r["checked_at"],
                        "last_status_code": r["status_code"],
                        "last_latency_ms": r["latency_ms"],
                        "last_error": r["error"],
                        "consecutive_fails": (
                            0 if r["is_ok"]
                            else text("target_status.consecutive_fails + 1")
                        ),
                    },
                )
                await session.execute(stmt)
            await session.commit()
    except Exception as e:
        logger.error(f"DB flush failed for {len(buffer)} results: {e}")


from collections import defaultdict


async def _load_group_settings() -> dict:
    """Load per-group concurrency/timeout/ua settings from DB."""
    from backend.app.models import GroupSetting
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(GroupSetting))
        return {
            gs.group_name: {
                "max_concurrency": gs.max_concurrency or 10,
                "request_timeout": gs.request_timeout or settings.check_timeout,
                "user_agent": gs.user_agent,
                "enabled": gs.enabled if gs.enabled is not None else True,
            }
            for gs in rows.scalars().all()
        }


async def run_checks():
    """Run HTTP checks with two-level concurrency:
    - Global semaphore: total max concurrent (e.g. 200)
    - Per-group semaphore: each group has its own limit (e.g. Taky=10, Danny=50)
    """
    logger.info("Starting HTTP check round...")
    check_progress.update({"running": True, "total": 0, "done": 0, "ok": 0, "fail": 0})

    # Load targets
    async with AsyncSessionLocal() as session:
        stmt = select(Target).where(Target.enabled == True)
        rows = await session.execute(stmt)
        targets = rows.scalars().all()

    if not targets:
        logger.info("No targets to check.")
        check_progress.update({"running": False})
        return

    # Load group settings
    group_cfg = await _load_group_settings()
    default_group_concurrency = 10

    total = len(targets)
    max_concurrent = settings.max_concurrent_checks

    # Log group plan
    groups_in_use = set(t.group for t in targets)
    for g in sorted(groups_in_use):
        gc = group_cfg.get(g, {})
        logger.info(f"  Group [{g}]: concurrency={gc.get('max_concurrency', default_group_concurrency)}, "
                    f"timeout={gc.get('request_timeout', settings.check_timeout)}s")

    logger.info(f"Checking {total} targets (global max={max_concurrent})")
    check_progress.update({"running": True, "total": total, "done": 0, "ok": 0, "fail": 0})

    # Write queue
    write_queue = asyncio.Queue(maxsize=500)
    writer_task = asyncio.create_task(_db_writer(write_queue))

    # Concurrency controls
    global_sem = asyncio.Semaphore(max_concurrent)
    group_sems: dict[str, asyncio.Semaphore] = {}
    for g in groups_in_use:
        gc = group_cfg.get(g, {})
        limit = gc.get("max_concurrency", default_group_concurrency)
        group_sems[g] = asyncio.Semaphore(limit)

    default_ua = settings.default_user_agent

    async def check_and_enqueue(target):
        url = target.url
        if not url.startswith("http"):
            protocol = getattr(target, 'protocol', 'https') or 'https'
            url = f"{protocol}://{url}"

        # Per-group settings override
        gc = group_cfg.get(target.group, {})
        ua = (getattr(target, 'user_agent', None)
              or gc.get('user_agent')
              or default_ua)
        timeout = (getattr(target, 'request_timeout', None)
                   or gc.get('request_timeout')
                   or settings.check_timeout)
        follow = getattr(target, 'follow_redirects', True)
        extra_headers = getattr(target, 'request_headers', None) or {}
        headers = {"User-Agent": ua}
        headers.update(extra_headers)

        group_sem = group_sems.get(target.group, asyncio.Semaphore(default_group_concurrency))

        async with global_sem:
            async with group_sem:
                r = await check_single(
                    client, target.id, url,
                    expect_status=target.expect_status,
                    expect_keyword=target.expect_keyword,
                    request_timeout=timeout,
                    follow_redirects=follow if follow is not None else True,
                    custom_headers=headers,
                )

        await write_queue.put(r)
        check_progress["done"] += 1
        if r["is_ok"]:
            check_progress["ok"] += 1
        else:
            check_progress["fail"] += 1

    async with httpx.AsyncClient(
        verify=_make_ssl_context(),
        limits=httpx.Limits(
            max_connections=max_concurrent,
            max_keepalive_connections=50,
        ),
    ) as client:
        tasks = [check_and_enqueue(t) for t in targets]
        await asyncio.gather(*tasks, return_exceptions=True)

    await write_queue.put(None)
    await writer_task

    check_progress["running"] = False
    logger.info(
        f"Check round done: {check_progress['ok']} ok, "
        f"{check_progress['fail']} failed out of {total}"
    )
