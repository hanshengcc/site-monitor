"""End-to-end run_checks() against a local target server, plus DNS layer timing.

Throwaway database only — it truncates the tables it touches.
"""
import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import func, select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.database import AsyncSessionLocal, engine                  # noqa: E402
from backend.app.models import CheckResult, TargetStatus                    # noqa: E402
from backend.app import checker, domain_detector                            # noqa: E402

# A large body is required to measure this at all: loopback sockets buffer a
# megabyte or two, so with a small page the server finishes writing before the
# client's close is noticed and the saving is invisible regardless of behaviour.
TARGET_COUNT = 50
BODY = b"<html><body>" + b"padding " * 2_500_000 + b"</body></html>"   # ~20MB
bytes_served = {"n": 0}

# Loopback sockets buffer megabytes, so an unthrottled server pushes the whole
# body out before the client's close is noticed and nothing can be measured. A
# small delay between chunks makes TCP flow control behave like a real network,
# where the server genuinely stops sending once the client goes away.
CHUNK = 65536
CHUNK_DELAY_S = 0.002


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        try:
            for i in range(0, len(BODY), CHUNK):
                chunk = BODY[i:i + CHUNK]
                self.wfile.write(chunk)
                self.wfile.flush()
                bytes_served["n"] += len(chunk)
                time.sleep(CHUNK_DELAY_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, *args):
        pass


async def seed(port: int):
    async with AsyncSessionLocal() as session:
        await session.execute(text(
            "TRUNCATE anomalies, check_results, target_status, targets RESTART IDENTITY CASCADE"
        ))
        await session.execute(text("""
            INSERT INTO targets (url, name, "group", check_interval, expect_status)
            SELECT 'http://127.0.0.1:' || CAST(:port AS text) || '/p' || g, 'local' || g,
                   'group' || (g % 4), 300, 200
            FROM generate_series(1, CAST(:n AS integer)) AS g
        """), {"port": str(port), "n": TARGET_COUNT})
        await session.commit()


async def check_round():
    print(f"\n[1] Full run_checks() over {TARGET_COUNT} targets")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    await seed(port)
    bytes_served["n"] = 0

    started = time.monotonic()
    await checker.run_checks()
    elapsed = time.monotonic() - started
    await asyncio.sleep(0.5)
    served_mb = bytes_served["n"] / 1024 / 1024
    would_have_been_mb = len(BODY) * TARGET_COUNT / 1024 / 1024

    async with AsyncSessionLocal() as session:
        stored = (await session.execute(select(func.count()).select_from(CheckResult))).scalar()
        statuses = (await session.execute(select(func.count()).select_from(TargetStatus))).scalar()
        ok_count = (await session.execute(
            select(func.count()).select_from(TargetStatus).where(TargetStatus.is_ok == True)
        )).scalar()
        sample_error = (await session.execute(
            select(TargetStatus.last_error).where(TargetStatus.is_ok == False).limit(1)
        )).scalar()

    server.shutdown()

    print(f"      round took {elapsed:.1f}s | progress={checker.check_progress['done']}/{TARGET_COUNT}")
    print(f"      bytes transferred: {served_mb:.1f}MB "
          f"(full download would be {would_have_been_mb:.0f}MB)")
    print(f"      results stored={stored} statuses={statuses} ok={ok_count}")
    if sample_error:
        # The server locale may not be UTF-8; the reason text is Chinese.
        print(f"      sample non-ok reason: {sample_error[:90]}")

    ok = stored == TARGET_COUNT and statuses == TARGET_COUNT
    print(f"      [{'PASS' if ok else 'FAIL'}] every target checked and persisted")
    saved_pct = 100 * (1 - served_mb / would_have_been_mb)
    print(f"      [{'PASS' if saved_pct > 80 else 'FAIL'}] response bodies skipped "
          f"({saved_pct:.1f}% less traffic)")
    return ok and saved_pct > 80


async def dns_timing():
    print("\n[2] DNS layer: concurrent queries + shared apex NS cache")
    domains = [
        "example.com", "www.example.com", "cloudflare.com", "www.cloudflare.com",
        "wikipedia.org", "www.wikipedia.org", "github.com", "api.github.com",
    ]

    domain_detector._DNS_CACHE.clear()
    domain_detector._NS_CACHE.clear()
    started = time.monotonic()
    results = await asyncio.gather(*[domain_detector._resolve_dns(d, timeout=5.0) for d in domains])
    cold = time.monotonic() - started

    started = time.monotonic()
    await asyncio.gather(*[domain_detector._resolve_dns(d, timeout=5.0) for d in domains])
    warm = time.monotonic() - started

    resolved = sum(1 for r in results if r["ns_records"])
    apex_entries = len(domain_detector._NS_CACHE)

    print(f"      {len(domains)} hostnames cold in {cold * 1000:.0f}ms, warm in {warm * 1000:.0f}ms")
    print(f"      NS cache holds {apex_entries} apex entries for {len(domains)} hostnames")
    print(f"      [{'PASS' if resolved >= 6 else 'FAIL'}] nameservers resolved for {resolved}/{len(domains)}")
    print(f"      [{'PASS' if apex_entries < len(domains) else 'FAIL'}] subdomains share one apex NS entry")
    print(f"      [{'PASS' if warm < cold else 'FAIL'}] warm cache is faster")
    return resolved >= 6 and apex_entries < len(domains) and warm < cold


async def main():
    ok_round = await check_round()
    ok_dns = await dns_timing()
    await engine.dispose()

    print("\n" + "=" * 62)
    print("ALL CHECKS PASSED" if (ok_round and ok_dns) else "SOME CHECKS FAILED")
    print("=" * 62)
    return 0 if (ok_round and ok_dns) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
