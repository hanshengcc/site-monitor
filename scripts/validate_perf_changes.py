"""Exercise the optimised write/read paths against a real PostgreSQL instance.

Run against a throwaway database only — it truncates every table it touches.

    DATABASE_URL=postgresql+asyncpg://... python scripts/validate_perf_changes.py
"""
import asyncio
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.database import AsyncSessionLocal, engine          # noqa: E402
from backend.app.models import Anomaly, CheckResult, Target, TargetStatus  # noqa: E402
from backend.app import checker                                      # noqa: E402

SEED_TARGETS = 8000
PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = ""):
    (PASSED if condition else FAILED).append(label)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}{(' -> ' + detail) if detail else ''}")


def result_row(target_id: int, is_ok: bool, when: datetime, error=None, status=200):
    return {
        "target_id": target_id,
        "checked_at": when,
        "status_code": status if is_ok else None,
        "latency_ms": 42,
        "is_ok": is_ok,
        "error": error,
        "dns_server": "ns1.example.com" if is_ok else None,
    }


async def reset(session):
    await session.execute(text(
        "TRUNCATE anomalies, check_results, target_status, targets RESTART IDENTITY CASCADE"
    ))
    await session.commit()


async def seed_targets(session, count: int):
    await session.execute(text("""
        INSERT INTO targets (url, name, "group", check_interval, shot_interval, snapshot_interval)
        SELECT 'https://host' || g || '.example.com', 'host' || g,
               'group' || (g % 5), 300, 21600, 21600
        FROM generate_series(1, :n) AS g
    """), {"n": count})
    await session.commit()


# ---------------------------------------------------------------------------
async def test_auto_migration():
    print("\n[1] Startup auto-migration (init_db_schema)")
    from backend.app.main import init_db_schema
    await init_db_schema()

    async with AsyncSessionLocal() as session:
        indexes = set((await session.execute(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
        ))).scalars().all())

    check("partial index on open anomalies created", "idx_anomalies_open_by_target" in indexes)
    check("last_screenshot_at index created", "idx_target_status_last_screenshot_at" in indexes)
    check("last_snapshot_at index created", "idx_target_status_last_snapshot_at" in indexes)
    check("pg_trgm search index created", "idx_targets_url_trgm" in indexes,
          "optional; skipped when the extension is unavailable")


# ---------------------------------------------------------------------------
async def test_flush_semantics():
    print("\n[2] Batched _flush_buffer semantics")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        await reset(session)
        await seed_targets(session, 10)

    # --- failures accumulate a streak and open an anomaly at the threshold ---
    threshold = checker.settings.consecutive_fails_threshold
    for i in range(threshold):
        await checker._flush_buffer([result_row(1, False, now + timedelta(seconds=i), "timeout")])

    async with AsyncSessionLocal() as session:
        streak = (await session.execute(
            select(TargetStatus.consecutive_fails).where(TargetStatus.target_id == 1)
        )).scalar()
        open_anomalies = (await session.execute(
            select(func.count(Anomaly.id)).where(Anomaly.target_id == 1, Anomaly.state == "open")
        )).scalar()
        rows = (await session.execute(
            select(func.count()).select_from(CheckResult).where(CheckResult.target_id == 1)
        )).scalar()

    check("fail streak increments across batches", streak == threshold, f"{streak} == {threshold}")
    check("one anomaly opened at threshold", open_anomalies == 1, f"count={open_anomalies}")
    check("every check result persisted", rows == threshold, f"{rows} rows")

    # --- a further failure must not open a duplicate anomaly ---
    await checker._flush_buffer([result_row(1, False, now + timedelta(seconds=9), "timeout")])
    async with AsyncSessionLocal() as session:
        open_anomalies = (await session.execute(
            select(func.count(Anomaly.id)).where(Anomaly.target_id == 1, Anomaly.state == "open")
        )).scalar()
    check("no duplicate anomaly while one is open", open_anomalies == 1, f"count={open_anomalies}")

    # --- recovery resolves the anomaly, flips notified, resets the streak ---
    async with AsyncSessionLocal() as session:
        await session.execute(text("UPDATE anomalies SET notified = TRUE WHERE target_id = 1"))
        await session.commit()

    recovered_at = now + timedelta(seconds=20)
    await checker._flush_buffer([result_row(1, True, recovered_at)])

    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(Anomaly.state, Anomaly.notified, Anomaly.resolved_at).where(Anomaly.target_id == 1)
        )).one()
        status = (await session.execute(
            select(TargetStatus.consecutive_fails, TargetStatus.is_ok, TargetStatus.dns_server)
            .where(TargetStatus.target_id == 1)
        )).one()

    check("anomaly resolved on recovery", row.state == "resolved", row.state)
    check("notified flipped so recovery is announced", row.notified is False, str(row.notified))
    check("resolved_at uses the check timestamp, not DB now()",
          row.resolved_at == recovered_at, str(row.resolved_at))
    check("streak reset to zero", status.consecutive_fails == 0, str(status.consecutive_fails))
    check("dns_server recorded", status.dns_server == "ns1.example.com", str(status.dns_server))


# ---------------------------------------------------------------------------
async def test_ssl_column_preservation():
    print("\n[3] Certificate columns survive rounds that carry no cert data")
    now = datetime.now(timezone.utc)

    cert_round = result_row(2, True, now)
    cert_round["ssl_info"] = {
        "ssl_valid": True, "ssl_issuer": "Let's Encrypt R3", "ssl_subject": "example.com",
        "ssl_not_after": "2027-01-01T00:00:00+00:00", "ssl_days_left": 120, "ssl_warning": None,
    }
    await checker._flush_buffer([cert_round])

    # A later round with the cert check throttled off must not wipe the stored cert.
    await checker._flush_buffer([result_row(2, True, now + timedelta(seconds=30))])

    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(TargetStatus.ssl_valid, TargetStatus.ssl_issuer,
                   TargetStatus.ssl_days_left, TargetStatus.ssl_checked_at)
            .where(TargetStatus.target_id == 2)
        )).one()

    check("ssl_valid preserved", row.ssl_valid is True, str(row.ssl_valid))
    check("ssl_issuer preserved", row.ssl_issuer == "Let's Encrypt R3", str(row.ssl_issuer))
    check("ssl_days_left preserved", row.ssl_days_left == 120, str(row.ssl_days_left))
    check("ssl_checked_at keeps the original check time", row.ssl_checked_at == now, str(row.ssl_checked_at))

    # dns_server must likewise survive a round that resolved nothing.
    await checker._flush_buffer([result_row(2, False, now + timedelta(seconds=60), "timeout")])
    async with AsyncSessionLocal() as session:
        dns_server = (await session.execute(
            select(TargetStatus.dns_server).where(TargetStatus.target_id == 2)
        )).scalar()
    check("dns_server preserved when a round resolves nothing",
          dns_server == "ns1.example.com", str(dns_server))


# ---------------------------------------------------------------------------
async def test_duplicate_target_in_one_batch():
    print("\n[4] Duplicate target in a single batch (ON CONFLICT would otherwise abort)")
    now = datetime.now(timezone.utc)
    try:
        await checker._flush_buffer([
            result_row(3, False, now, "timeout"),
            result_row(3, False, now + timedelta(seconds=1), "timeout"),
        ])
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(func.count()).select_from(CheckResult).where(CheckResult.target_id == 3)
            )).scalar()
            streak = (await session.execute(
                select(TargetStatus.consecutive_fails).where(TargetStatus.target_id == 3)
            )).scalar()
        check("batch with a duplicate target does not abort", rows == 2, f"{rows} results stored")
        check("status row written once for the duplicate", streak == 1, f"streak={streak}")
    except Exception as e:
        check("batch with a duplicate target does not abort", False, repr(e))


# ---------------------------------------------------------------------------
async def test_target_deleted_mid_round():
    print("\n[4b] Target deleted mid-round must not sink the rest of the batch")
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as session:
        await reset(session)
        await seed_targets(session, 10)

    # Delete target 5 while results for 1..10 are already in flight.
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM targets WHERE id = 5"))
        await session.commit()

    batch = [result_row(i, True, now) for i in range(1, 11)]
    await checker._flush_buffer(batch)

    async with AsyncSessionLocal() as session:
        stored = set((await session.execute(select(CheckResult.target_id))).scalars().all())
        statuses = set((await session.execute(select(TargetStatus.target_id))).scalars().all())

    survivors = {1, 2, 3, 4, 6, 7, 8, 9, 10}
    check("results for the nine live targets survived", stored == survivors,
          f"stored={sorted(stored)}")
    check("status rows written for the nine live targets", statuses == survivors,
          f"statuses={sorted(statuses)}")
    check("no orphan result for the deleted target", 5 not in stored, "target 5 absent")


# ---------------------------------------------------------------------------
async def test_write_throughput():
    print(f"\n[5] Write throughput at production scale ({SEED_TARGETS} targets)")
    async with AsyncSessionLocal() as session:
        await reset(session)
        await seed_targets(session, SEED_TARGETS)

    now = datetime.now(timezone.utc)
    # 85% healthy / 15% failing, roughly matching the production mix.
    results = [
        result_row(i, i % 7 != 0, now, None if i % 7 != 0 else "timeout")
        for i in range(1, SEED_TARGETS + 1)
    ]

    batch_size = 20
    started = time.monotonic()
    for i in range(0, len(results), batch_size):
        await checker._flush_buffer([dict(r) for r in results[i:i + batch_size]])
    elapsed = time.monotonic() - started

    async with AsyncSessionLocal() as session:
        stored = (await session.execute(select(func.count()).select_from(CheckResult))).scalar()
        statuses = (await session.execute(select(func.count()).select_from(TargetStatus))).scalar()

    print(f"      {SEED_TARGETS} results in {elapsed:.2f}s "
          f"({SEED_TARGETS / elapsed:,.0f} results/s, {len(results) // batch_size} batches)")
    check("all results stored", stored == SEED_TARGETS, f"{stored}")
    check("all status rows upserted", statuses == SEED_TARGETS, f"{statuses}")
    return elapsed


async def test_legacy_write_throughput():
    """Replay the pre-optimisation write path for a like-for-like comparison."""
    print("\n[6] Same workload on the previous per-row write path")
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    async with AsyncSessionLocal() as session:
        await reset(session)
        await seed_targets(session, SEED_TARGETS)

    now = datetime.now(timezone.utc)
    results = [
        result_row(i, i % 7 != 0, now, None if i % 7 != 0 else "timeout")
        for i in range(1, SEED_TARGETS + 1)
    ]

    async def legacy_flush(buffer):
        async with AsyncSessionLocal() as session:
            for r in buffer:
                r = dict(r)
                r.pop("ssl_info", None)
                dns_server = r.pop("dns_server", None)
                session.add(CheckResult(**r))
                vals = {
                    "target_id": r["target_id"], "is_ok": r["is_ok"],
                    "last_check_at": r["checked_at"], "last_status_code": r["status_code"],
                    "last_latency_ms": r["latency_ms"], "last_error": r["error"],
                    "consecutive_fails": 0 if r["is_ok"] else 1,
                }
                update_set = dict(vals)
                update_set.pop("target_id")
                update_set["consecutive_fails"] = (
                    0 if r["is_ok"] else text("target_status.consecutive_fails + 1")
                )
                if dns_server:
                    vals["dns_server"] = dns_server
                    update_set["dns_server"] = dns_server
                stmt = pg_insert(TargetStatus).values(**vals).on_conflict_do_update(
                    index_elements=["target_id"], set_=update_set,
                ).returning(TargetStatus.consecutive_fails)
                res = await session.execute(stmt)
                curr = res.fetchone()[0]
                if not r["is_ok"]:
                    if curr >= checker.settings.consecutive_fails_threshold:
                        existing = await session.execute(select(Anomaly).where(
                            Anomaly.target_id == r["target_id"],
                            Anomaly.anomaly_type == "http_error", Anomaly.state == "open",
                        ))
                        if not existing.scalar_one_or_none():
                            session.add(Anomaly(
                                target_id=r["target_id"], detected_at=r["checked_at"],
                                anomaly_type="http_error", score=50.0, reasons=[],
                                state="open", notified=False,
                            ))
                else:
                    open_anomalies = await session.execute(select(Anomaly).where(
                        Anomaly.target_id == r["target_id"], Anomaly.state == "open",
                    ))
                    for oa in open_anomalies.scalars().all():
                        oa.state = "resolved"
                        oa.resolved_at = r["checked_at"]
                        oa.notified = not oa.notified
            await session.commit()

    batch_size = 20
    started = time.monotonic()
    for i in range(0, len(results), batch_size):
        await legacy_flush(results[i:i + batch_size])
    elapsed = time.monotonic() - started

    print(f"      {SEED_TARGETS} results in {elapsed:.2f}s ({SEED_TARGETS / elapsed:,.0f} results/s)")
    return elapsed


# ---------------------------------------------------------------------------
async def test_read_queries():
    print("\n[7] Optimised read endpoints")
    from backend.app.routers.dashboard import get_dashboard_stats
    from backend.app.routers.results import get_stats

    async with AsyncSessionLocal() as session:
        started = time.monotonic()
        stats = await get_dashboard_stats(db=session)
        dash_ms = (time.monotonic() - started) * 1000

        started = time.monotonic()
        uptime = await get_stats(target_id=1, hours=24, db=session)
        stats_ms = (time.monotonic() - started) * 1000

    print(f"      /dashboard/stats {dash_ms:.0f}ms | /results/1/stats {stats_ms:.0f}ms")
    check("dashboard totals correct", stats.total_targets == SEED_TARGETS, str(stats.total_targets))
    check("healthy + unhealthy covers every target",
          stats.healthy + stats.unhealthy == SEED_TARGETS,
          f"{stats.healthy}+{stats.unhealthy}")
    check("dashboard unknown count is non-negative", stats.unknown >= 0, str(stats.unknown))
    check("uptime stats aggregate in one pass", uptime["total_checks"] >= 1, str(uptime["total_checks"]))


# ---------------------------------------------------------------------------
async def test_check_interval_filter():
    print("\n[7b] Per-target check_interval decides who is due")
    from sqlalchemy import or_
    from backend.app.intervals import due_cutoff

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        # Target 1: 5-minute cadence, checked 1 minute ago    -> not due
        # Target 2: 5-minute cadence, checked 10 minutes ago  -> due
        # Target 3: 1-hour cadence, checked 10 minutes ago    -> not due
        # Target 4: never checked                             -> always due
        await session.execute(text("UPDATE targets SET check_interval = 300 WHERE id IN (1, 2)"))
        await session.execute(text("UPDATE targets SET check_interval = 3600 WHERE id = 3"))
        await session.execute(text(
            "UPDATE target_status SET last_check_at = :t WHERE target_id = 1"
        ), {"t": now - timedelta(minutes=1)})
        await session.execute(text(
            "UPDATE target_status SET last_check_at = :t WHERE target_id IN (2, 3)"
        ), {"t": now - timedelta(minutes=10)})
        await session.execute(text("DELETE FROM target_status WHERE target_id = 4"))
        await session.commit()

        due = set((await session.execute(
            select(Target.id)
            .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
            .where(Target.enabled == True)
            .where(or_(
                TargetStatus.last_check_at.is_(None),
                TargetStatus.last_check_at < due_cutoff("check_interval"),
            ))
            .where(Target.id.in_([1, 2, 3, 4]))
        )).scalars().all())

    check("recently checked target is skipped", 1 not in due, f"due={sorted(due)}")
    check("target past its 5-minute interval is due", 2 in due, f"due={sorted(due)}")
    check("target on a 1-hour interval is not yet due", 3 not in due, f"due={sorted(due)}")
    check("never-checked target is always due", 4 in due, f"due={sorted(due)}")


# ---------------------------------------------------------------------------
async def test_due_filters():
    print("\n[8] Screenshot / snapshot interval filters")
    from sqlalchemy import or_
    from backend.app.intervals import due_cutoff as _due_cutoff

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        # Target 1 shot 10 minutes ago, target 2 shot 10 hours ago; interval is 6h.
        await session.execute(text(
            "UPDATE target_status SET last_screenshot_at = :recent WHERE target_id = 1"
        ), {"recent": now - timedelta(minutes=10)})
        await session.execute(text(
            "UPDATE target_status SET last_screenshot_at = :old WHERE target_id = 2"
        ), {"old": now - timedelta(hours=10)})
        await session.commit()

        due = (await session.execute(
            select(Target.id)
            .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
            .where(Target.enabled == True)
            .where(or_(
                TargetStatus.last_screenshot_at.is_(None),
                TargetStatus.last_screenshot_at < _due_cutoff("shot_interval"),
            ))
            .where(Target.id.in_([1, 2]))
        )).scalars().all()

    check("recently captured target is skipped", 1 not in due, f"due={sorted(due)}")
    check("stale target is due for capture", 2 in due, f"due={sorted(due)}")


# ---------------------------------------------------------------------------
async def test_partition_drop():
    print("\n[9] Partition retention")
    from backend.app.scheduler import _drop_expired_partitions

    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS check_results_2024_01
            PARTITION OF check_results FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')
        """))
        await session.commit()

        before = set((await session.execute(text("""
            SELECT c.relname FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'check_results'
        """))).scalars().all())

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        dropped = await _drop_expired_partitions(session, cutoff)

        after = set((await session.execute(text("""
            SELECT c.relname FROM pg_inherits i
              JOIN pg_class c ON c.oid = i.inhrelid
              JOIN pg_class p ON p.oid = i.inhparent
             WHERE p.relname = 'check_results'
        """))).scalars().all())

    current_month = f"check_results_{datetime.now(timezone.utc):%Y_%m}"
    check("stale 2024 partition dropped", "check_results_2024_01" in dropped, str(dropped))
    check("current month partition retained", current_month in after, current_month)
    check("only expired partitions removed", before - after == set(dropped), str(before - after))


# ---------------------------------------------------------------------------
async def main():
    print(f"Validating against {os.environ.get('DATABASE_URL', '<default>').split('@')[-1]}")
    await test_auto_migration()
    await test_flush_semantics()
    await test_ssl_column_preservation()
    await test_duplicate_target_in_one_batch()
    await test_target_deleted_mid_round()
    new_elapsed = await test_write_throughput()
    old_elapsed = await test_legacy_write_throughput()
    # Re-seed the optimised state for the read tests.
    await test_write_throughput()
    await test_read_queries()
    await test_check_interval_filter()
    await test_due_filters()
    await test_partition_drop()

    print("\n" + "=" * 62)
    print(f"Write path: {old_elapsed:.2f}s before -> {new_elapsed:.2f}s after "
          f"({old_elapsed / new_elapsed:.1f}x faster for {SEED_TARGETS} results)")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAILED: {name}")
    print("=" * 62)

    await engine.dispose()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
