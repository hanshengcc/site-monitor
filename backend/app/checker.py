"""HTTP health checker - async checking with realtime DB updates via write queue."""
import asyncio
import ssl
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from loguru import logger
from sqlalchemy import case, func, select, text
from sqlalchemy import insert as sa_insert
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Target, CheckResult, TargetStatus, Anomaly

# Realtime progress (read by /api/tasks/progress)
check_progress = {"running": False, "total": 0, "done": 0, "ok": 0, "fail": 0, "group": None}


# Building this context loads the system CA bundle from disk, so it is built once
# and shared. The context is stateless after configuration and safe to reuse.
_PERMISSIVE_CTX: Optional[ssl.SSLContext] = None

# Upper bound on response body read when a keyword must be matched. The keyword
# check itself only ever looked at the first 50k characters.
MAX_BODY_BYTES = 256 * 1024


def _make_ssl_context() -> ssl.SSLContext:
    """Get the shared permissive SSL context that works with broken/legacy TLS servers."""
    global _PERMISSIVE_CTX
    if _PERMISSIVE_CTX is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        # Allow legacy renegotiation for old servers
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT') else 0
        # Set minimum TLS to 1.0 for maximum compatibility
        ctx.minimum_version = ssl.TLSVersion.TLSv1
        # Permissive ciphers
        ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        _PERMISSIVE_CTX = ctx
    return _PERMISSIVE_CTX


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
    expect_dns_server: Optional[str] = None,
    request_timeout: int = 15,
    follow_redirects: bool = True,
    custom_headers: Optional[dict] = None,
    retry_http_on_ssl_error: bool = True,
    check_ssl_cert: bool = True,
) -> dict:
    """Check a single URL with custom parameters.
    If HTTPS fails due to SSL error, automatically retry with HTTP.

    check_ssl_cert=False skips the extra verifying TLS handshake; callers use it
    when a recent certificate result is already on record (see settings.ssl_recheck_hours).
    """
    result = await _do_check(
        client, target_id, url,
        expect_status=expect_status,
        expect_keyword=expect_keyword,
        expect_dns_server=expect_dns_server,
        request_timeout=request_timeout,
        follow_redirects=follow_redirects,
        custom_headers=custom_headers,
    )

    # ---- SSL certificate check (for HTTPS URLs that got HTTP 200) ----
    if check_ssl_cert and result["is_ok"] and url.startswith("https://"):
        try:
            from backend.app.ssl_checker import check_ssl_for_url
            ssl_info = await check_ssl_for_url(url, timeout=10, warn_days=30)
            if ssl_info:
                result["ssl_info"] = ssl_info
        except Exception as e:
            logger.debug(f"[{target_id}] SSL cert check failed: {e}")

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
            expect_status=expect_status,
            expect_keyword=expect_keyword,
            expect_dns_server=expect_dns_server,
            request_timeout=request_timeout,
            follow_redirects=follow_redirects,
            custom_headers=custom_headers,
        )
        if fallback["is_ok"]:
            fallback["error"] = None
            return fallback
        else:
            # Both failed: keep original SSL error but note fallback attempted
            result["error"] = f"ssl_error (HTTP fallback also failed): {result['error']}"

    return result


async def _fetch_status_and_body(
    client: httpx.AsyncClient,
    url: str,
    follow_redirects: bool,
    request_timeout: int,
    headers: dict,
    need_body: bool,
) -> tuple[int, str]:
    """Fetch a URL, reading only as much of the body as the check actually needs.

    Without a keyword to match, the body is never inspected, so it is never read:
    the response is closed right after the headers arrive. Across tens of thousands
    of targets this removes the bulk of the bandwidth, memory and time of a round.
    """
    async with client.stream(
        "GET", url,
        follow_redirects=follow_redirects,
        timeout=request_timeout,
        headers=headers,
    ) as resp:
        if not need_body:
            return resp.status_code, ""

        chunks: list[bytes] = []
        read = 0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            read += len(chunk)
            if read >= MAX_BODY_BYTES:
                break
        body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
        return resp.status_code, body


async def _do_check(
    client: httpx.AsyncClient,
    target_id: int,
    url: str,
    expect_status: int = 200,
    expect_keyword: Optional[str] = None,
    expect_dns_server: Optional[str] = None,
    request_timeout: int = 15,
    follow_redirects: bool = True,
    custom_headers: Optional[dict] = None,
) -> dict:
    """Perform health check based on domain DNS server and HTTP status.
    Cancels body string matching and relies on authoritative DNS servers.
    """
    start = time.monotonic()
    result = {
        "target_id": target_id,
        "checked_at": datetime.now(timezone.utc),
        "status_code": None,
        "latency_ms": None,
        "is_ok": False,
        "error": None,
        "dns_server": None,
    }
    headers = dict(custom_headers) if custom_headers else {}

    from backend.app.domain_detector import detect_domain_status, DomainVerdict

    # Perform HTTP request and authoritative DNS server inspection concurrently
    http_task = _fetch_status_and_body(
        client, url,
        follow_redirects=follow_redirects,
        request_timeout=request_timeout,
        headers=headers,
        need_body=bool(expect_keyword),
    )
    dns_task = detect_domain_status(
        url,
        expect_dns_server=expect_dns_server,
        dns_timeout=min(5.0, max(2.0, float(request_timeout) / 2)),
    )

    http_res, dns_verdict = await asyncio.gather(
        http_task, dns_task, return_exceptions=True
    )
    elapsed_ms = int((time.monotonic() - start) * 1000)
    result["latency_ms"] = elapsed_ms

    if isinstance(dns_verdict, DomainVerdict):
        result["dns_server"] = dns_verdict.dns_server

        # 1. DNS does not exist (NXDOMAIN)
        if dns_verdict.is_dns_not_found:
            result["is_ok"] = False
            result["error"] = dns_verdict.summary or "DNS无法解析 (域名不存在/NXDOMAIN)"
            return result

        # 2. DNS server matches known parking / expired nameserver, or CNAME/IP parking
        if dns_verdict.is_parked_or_expired:
            result["is_ok"] = False
            result["error"] = dns_verdict.summary or "域名已过期或停放"
            return result

        # 3. Target / Group expected DNS server mismatch
        if dns_verdict.category == "dns_mismatch":
            result["is_ok"] = False
            result["error"] = dns_verdict.summary
            return result

    # 4. HTTP response / connection failure evaluation
    if isinstance(http_res, Exception):
        result["is_ok"] = False
        err_str = str(http_res)
        if isinstance(http_res, httpx.TimeoutException):
            result["error"] = "timeout"
        elif isinstance(http_res, httpx.ConnectError):
            if _is_ssl_error(http_res):
                result["error"] = f"ssl_error: {err_str[:200]}"
            else:
                result["error"] = f"connect_error: {err_str[:200]}"
        elif isinstance(http_res, ssl.SSLError):
            result["error"] = f"ssl_error: {err_str[:200]}"
        else:
            result["error"] = f"{type(http_res).__name__}: {err_str[:200]}"
        return result

    status_code, body = http_res
    result["status_code"] = status_code

    if status_code != expect_status:
        result["is_ok"] = False
        result["error"] = f"expected {expect_status}, got {status_code}"
        return result

    if expect_keyword:
        if expect_keyword not in body[:50000]:
            result["is_ok"] = False
            result["error"] = f"keyword '{expect_keyword}' not found"
            return result

    # Domain DNS is healthy and HTTP returned expected status!
    result["is_ok"] = True
    result["error"] = None
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


SSL_STATUS_COLUMNS = (
    "ssl_valid", "ssl_error", "ssl_issuer", "ssl_subject",
    "ssl_not_after", "ssl_days_left", "ssl_warning", "ssl_checked_at",
)


def _parse_ssl_not_after(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _build_status_values(result: dict, dns_server, ssl_info) -> dict:
    """Build one row of target_status upsert values from a check result.

    Every row carries the same columns so the whole batch can go up as a single
    multi-row INSERT ... ON CONFLICT. Columns with no new reading are left NULL
    and preserved by the conflict clause rather than overwriting good data.
    """
    values = {
        "target_id": result["target_id"],
        "is_ok": result["is_ok"],
        "last_check_at": result["checked_at"],
        "last_status_code": result["status_code"],
        "last_latency_ms": result["latency_ms"],
        "last_error": result["error"],
        "consecutive_fails": 0 if result["is_ok"] else 1,
        "dns_server": dns_server,
    }
    values.update({col: None for col in SSL_STATUS_COLUMNS})
    if ssl_info:
        values.update({
            "ssl_valid": ssl_info.get("ssl_valid"),
            "ssl_error": ssl_info.get("ssl_error"),
            "ssl_issuer": ssl_info.get("ssl_issuer"),
            "ssl_subject": ssl_info.get("ssl_subject"),
            "ssl_not_after": _parse_ssl_not_after(ssl_info.get("ssl_not_after")),
            "ssl_days_left": ssl_info.get("ssl_days_left"),
            "ssl_warning": ssl_info.get("ssl_warning"),
            "ssl_checked_at": result["checked_at"],
        })
    return values


def _status_upsert_statement(rows: list[dict]):
    """Multi-row upsert into target_status, returning each row's new fail streak."""
    stmt = insert(TargetStatus).values(rows)
    excluded = stmt.excluded
    set_ = {
        "is_ok": excluded.is_ok,
        "last_check_at": excluded.last_check_at,
        "last_status_code": excluded.last_status_code,
        "last_latency_ms": excluded.last_latency_ms,
        "last_error": excluded.last_error,
        "consecutive_fails": case(
            (excluded.is_ok.is_(True), 0),
            else_=TargetStatus.consecutive_fails + 1,
        ),
        # A round without a DNS answer must not erase the last known nameserver.
        "dns_server": func.coalesce(excluded.dns_server, TargetStatus.dns_server),
    }
    # ssl_checked_at acts as the "this row carries fresh cert data" marker: when it
    # is NULL the existing certificate columns are kept untouched.
    for col in SSL_STATUS_COLUMNS:
        set_[col] = case(
            (excluded.ssl_checked_at.isnot(None), getattr(excluded, col)),
            else_=getattr(TargetStatus, col),
        )
    return stmt.on_conflict_do_update(
        index_elements=["target_id"], set_=set_,
    ).returning(TargetStatus.target_id, TargetStatus.consecutive_fails)


# Resolving open anomalies for every recovered target, in one statement.
_RESOLVE_ANOMALIES_SQL = text("""
    UPDATE anomalies AS a
       SET state = 'resolved',
           resolved_at = v.ts,
           notified = NOT a.notified
      FROM (
        SELECT unnest(CAST(:target_ids AS integer[]))     AS target_id,
               unnest(CAST(:timestamps AS timestamptz[])) AS ts
      ) AS v
     WHERE a.target_id = v.target_id
       AND a.state = 'open'
""")


async def _resolve_open_anomalies(session, recovered: list[tuple[int, datetime]]):
    """Close every open anomaly for targets that just came back healthy.

    Targets that had already been alerted on get notified=False so the recovery
    notice goes out; ones that never alerted are marked notified to stay quiet.
    """
    if not recovered:
        return
    await session.execute(_RESOLVE_ANOMALIES_SQL, {
        "target_ids": [tid for tid, _ in recovered],
        "timestamps": [ts for _, ts in recovered],
    })


async def _open_http_error_anomalies(session, failing: list[tuple[dict, int]]):
    """Open an http_error anomaly for each failing target that has none yet."""
    if not failing:
        return
    target_ids = [values["target_id"] for values, _ in failing]
    already_open = set((await session.execute(
        select(Anomaly.target_id).where(
            Anomaly.target_id.in_(target_ids),
            Anomaly.anomaly_type == "http_error",
            Anomaly.state == "open",
        )
    )).scalars().all())

    new_anomalies = []
    for values, fails in failing:
        if values["target_id"] in already_open:
            continue
        error_desc = values["last_error"] or f"HTTP {values['last_status_code']}"
        new_anomalies.append({
            "target_id": values["target_id"],
            "detected_at": values["last_check_at"],
            "anomaly_type": "http_error",
            "score": min(100.0, 30.0 + fails * 10),
            "reasons": [{
                "rule": "consecutive_fails",
                "detail": f"连续失败 {fails} 次: {error_desc}",
            }],
            "state": "open",
            "notified": False,
        })

    if new_anomalies:
        await session.execute(sa_insert(Anomaly), new_anomalies)


async def _flush_buffer(buffer: list):
    """Write a batch of results to DB using a handful of set-based statements.

    The previous implementation issued three or more round trips per result, which
    at tens of thousands of targets per round dominated the check duration. This
    version costs a fixed number of statements per batch regardless of batch size.
    """
    if not buffer:
        return
    try:
        check_rows: list[dict] = []
        # Last reading wins if a target somehow appears twice: PostgreSQL rejects an
        # ON CONFLICT DO UPDATE that would touch the same row twice in one statement.
        status_rows: dict[int, dict] = {}

        for r in buffer:
            ssl_info = r.pop("ssl_info", None)
            dns_server = r.pop("dns_server", None)
            check_rows.append(dict(r))
            status_rows[r["target_id"]] = _build_status_values(r, dns_server, ssl_info)

        async with AsyncSessionLocal() as session:
            await session.execute(sa_insert(CheckResult), check_rows)

            result = await session.execute(_status_upsert_statement(list(status_rows.values())))
            fails_by_target = dict(result.all())

            recovered = [
                (values["target_id"], values["last_check_at"])
                for values in status_rows.values() if values["is_ok"]
            ]
            failing = [
                (values, fails_by_target.get(values["target_id"], 1))
                for values in status_rows.values() if not values["is_ok"]
            ]
            failing = [
                (values, fails) for values, fails in failing
                if fails >= settings.consecutive_fails_threshold
            ]

            await _resolve_open_anomalies(session, recovered)
            await _open_http_error_anomalies(session, failing)

            await session.commit()
    except Exception as e:
        logger.error(f"DB flush failed for {len(buffer)} results: {e}")


from collections import defaultdict, deque


class GroupRateLimiter:
    """Token Bucket rate limiter for per-group QPS control."""
    def __init__(self, rate: float):
        self.rate = max(0.1, float(rate))
        self.capacity = max(1.0, float(rate))
        self.tokens = self.capacity
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.updated_at
            self.updated_at = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens < 1.0:
                needed = 1.0 - self.tokens
                wait_time = needed / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.updated_at = time.monotonic()
            else:
                self.tokens -= 1.0


def interleave_by_group(targets: list) -> list:
    """Interleave targets by group in round-robin fashion.
    Ensures fair scheduling across different groups and avoids starvation.
    Example:
      Group A: [A1, A2, A3]
      Group B: [B1, B2]
      Group C: [C1]
      Result:  [A1, B1, C1, A2, B2, A3]
    """
    if not targets or len(targets) <= 1:
        return targets

    grouped = defaultdict(deque)
    for t in targets:
        grouped[t.group].append(t)

    interleaved = []
    while grouped:
        for k in list(grouped.keys()):
            interleaved.append(grouped[k].popleft())
            if not grouped[k]:
                del grouped[k]
    return interleaved


async def _load_group_settings() -> dict:
    """Load per-group concurrency/rate_limit/timeout/ua settings from DB."""
    from backend.app.models import GroupSetting
    async with AsyncSessionLocal() as session:
        rows = await session.execute(select(GroupSetting))
        return {
            gs.group_name: {
                "max_concurrency": gs.max_concurrency or 10,
                "rate_limit": gs.rate_limit or 0,
                "request_timeout": gs.request_timeout or settings.check_timeout,
                "user_agent": gs.user_agent,
                "enabled": gs.enabled if gs.enabled is not None else True,
            }
            for gs in rows.scalars().all()
        }


async def run_checks(group: Optional[str] = None):
    """Run HTTP checks with fair round-robin scheduling, concurrency limits, and per-group QPS rate limits:
    - Round-robin interleaving across groups to prevent large groups from blocking smaller ones
    - Per-group semaphore: limits in-flight connections per group (e.g. 10)
    - Global semaphore: limits total concurrent requests across all groups (e.g. 200)
    - Per-group rate limiter (Token Bucket): limits requests per second (QPS)
    - Semaphore acquisition order: group_sem -> global_sem (prevents starvation)
    """
    if check_progress.get("running"):
        logger.warning("Previous check round still running, skip.")
        return

    group_desc = f"group [{group}]" if group else "ALL groups"
    logger.info(f"Starting HTTP check round for {group_desc}...")
    check_progress.update({"running": True, "total": 0, "done": 0, "ok": 0, "fail": 0, "group": group})

    # Load targets along with when each one's certificate was last inspected, so
    # the extra verifying TLS handshake can be skipped for recently checked certs.
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Target, TargetStatus.ssl_checked_at)
            .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
            .where(Target.enabled == True)
        )
        if group:
            stmt = stmt.where(Target.group == group)
        rows = (await session.execute(stmt)).all()
        targets = [row[0] for row in rows]
        ssl_checked_at_by_target = {row[0].id: row[1] for row in rows}

    ssl_recheck_cutoff = datetime.now(timezone.utc) - timedelta(hours=settings.ssl_recheck_hours)

    if not targets:
        logger.info(f"No targets to check for {group_desc}.")
        check_progress.update({"running": False, "group": group})
        return

    # Load group settings
    group_cfg = await _load_group_settings()
    default_group_concurrency = 10

    # Fair round-robin interleave across groups
    if not group:
        targets = interleave_by_group(targets)

    total = len(targets)
    max_concurrent = settings.max_concurrent_checks

    # Log group plan
    groups_in_use = set(t.group for t in targets)
    for g in sorted(groups_in_use):
        gc = group_cfg.get(g, {})
        rl_info = f", QPS={gc.get('rate_limit')}" if gc.get("rate_limit") else ", QPS=unlimited"
        logger.info(f"  Group [{g}]: concurrency={gc.get('max_concurrency', default_group_concurrency)}"
                    f"{rl_info}, timeout={gc.get('request_timeout', settings.check_timeout)}s")

    logger.info(f"Checking {total} targets (global max={max_concurrent}, round-robin interleaved)")
    check_progress.update({"running": True, "total": total, "done": 0, "ok": 0, "fail": 0, "group": group})

    # Write queue
    write_queue = asyncio.Queue(maxsize=500)
    writer_task = asyncio.create_task(_db_writer(write_queue))

    # Concurrency and Rate Limiting controls
    global_sem = asyncio.Semaphore(max_concurrent)
    group_sems: dict[str, asyncio.Semaphore] = {}
    group_rate_limiters: dict[str, GroupRateLimiter] = {}

    for g in groups_in_use:
        gc = group_cfg.get(g, {})
        limit = gc.get("max_concurrency", default_group_concurrency)
        group_sems[g] = asyncio.Semaphore(limit)
        qps = gc.get("rate_limit") or 0
        if qps > 0:
            group_rate_limiters[g] = GroupRateLimiter(rate=qps)

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
        expect_dns = (getattr(target, 'expect_dns_server', None)
                      or gc.get('expect_dns_server'))
        extra_headers = getattr(target, 'request_headers', None) or {}
        headers = {"User-Agent": ua}
        headers.update(extra_headers)

        group_sem = group_sems.get(target.group, asyncio.Semaphore(default_group_concurrency))
        limiter = group_rate_limiters.get(target.group)

        # Acquisition order: group_sem FIRST, then global_sem.
        # This prevents one large group from monopolizing all global permits while waiting on its own limit.
        async with group_sem:
            async with global_sem:
                if limiter:
                    await limiter.acquire()

                last_ssl_check = ssl_checked_at_by_target.get(target.id)
                needs_ssl_check = last_ssl_check is None or last_ssl_check < ssl_recheck_cutoff

                r = await check_single(
                    client, target.id, url,
                    expect_status=target.expect_status,
                    expect_keyword=target.expect_keyword,
                    expect_dns_server=expect_dns,
                    request_timeout=timeout,
                    follow_redirects=follow if follow is not None else True,
                    custom_headers=headers,
                    check_ssl_cert=needs_ssl_check,
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
        chunk_size = max(500, max_concurrent * 2)
        for i in range(0, total, chunk_size):
            chunk = targets[i:i + chunk_size]
            tasks = [check_and_enqueue(t) for t in chunk]
            await asyncio.gather(*tasks, return_exceptions=True)

    await write_queue.put(None)
    await writer_task

    check_progress["running"] = False
    logger.info(
        f"Check round done [{group_desc}]: {check_progress['ok']} ok, "
        f"{check_progress['fail']} failed out of {total}"
    )
