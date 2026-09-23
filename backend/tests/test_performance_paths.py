"""Tests for the hot-path optimisations in the check/maintenance pipeline."""
import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.app import domain_detector
from backend.app.checker import (
    SSL_STATUS_COLUMNS,
    _build_status_values,
    _drop_missing_targets,
    _status_upsert_statement,
)
from backend.app.scheduler import is_partition_expired


def _check_result(target_id=1, is_ok=True, error=None, status_code=200):
    return {
        "target_id": target_id,
        "checked_at": datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),
        "status_code": status_code,
        "latency_ms": 42,
        "is_ok": is_ok,
        "error": error,
    }


# ---------------------------------------------------------------------------
# target_status batch upsert
# ---------------------------------------------------------------------------
class TestStatusUpsertValues:
    def test_row_without_cert_data_leaves_ssl_columns_null(self):
        # Arrange / Act
        values = _build_status_values(_check_result(), dns_server="ns1.example.com", ssl_info=None)

        # Assert: NULL is the marker the conflict clause uses to keep existing certs
        for column in SSL_STATUS_COLUMNS:
            assert values[column] is None
        assert values["dns_server"] == "ns1.example.com"
        assert values["consecutive_fails"] == 0

    def test_failed_row_starts_fail_streak_at_one(self):
        values = _build_status_values(
            _check_result(is_ok=False, error="timeout", status_code=None),
            dns_server=None, ssl_info=None,
        )
        assert values["consecutive_fails"] == 1
        assert values["last_error"] == "timeout"

    def test_cert_data_is_parsed_and_stamped(self):
        values = _build_status_values(
            _check_result(),
            dns_server=None,
            ssl_info={
                "ssl_valid": True,
                "ssl_issuer": "R3",
                "ssl_not_after": "2027-01-01T00:00:00+00:00",
                "ssl_days_left": 100,
            },
        )
        assert values["ssl_valid"] is True
        assert values["ssl_not_after"] == datetime(2027, 1, 1, tzinfo=timezone.utc)
        # ssl_checked_at doubles as the "fresh cert data" flag
        assert values["ssl_checked_at"] == values["last_check_at"]

    def test_unparseable_cert_expiry_becomes_none(self):
        values = _build_status_values(
            _check_result(), dns_server=None,
            ssl_info={"ssl_valid": False, "ssl_not_after": "not-a-date"},
        )
        assert values["ssl_not_after"] is None

    def test_every_row_carries_identical_columns(self):
        # A multi-row INSERT requires a uniform column set across all rows.
        with_cert = _build_status_values(_check_result(1), None, {"ssl_valid": True})
        without_cert = _build_status_values(_check_result(2, is_ok=False), "ns1", None)
        assert with_cert.keys() == without_cert.keys()

    def test_upsert_preserves_existing_data_when_new_reading_is_absent(self):
        from sqlalchemy.dialects import postgresql

        rows = [_build_status_values(_check_result(), "ns1.example.com", None)]
        sql = str(_status_upsert_statement(rows).compile(dialect=postgresql.dialect()))

        # Fail streak increments off the stored row, not the incoming one
        assert "target_status.consecutive_fails + " in sql
        # Missing DNS/cert readings fall back to what is already stored
        assert "coalesce(excluded.dns_server, target_status.dns_server)" in sql
        assert "ELSE target_status.ssl_issuer" in sql
        # The new fail streak comes back without a follow-up query
        assert "RETURNING target_status.target_id, target_status.consecutive_fails" in sql


# ---------------------------------------------------------------------------
# Targets deleted mid-round
# ---------------------------------------------------------------------------
class TestDropMissingTargets:
    def _batch(self, target_ids):
        check_rows = [
            dict(_check_result(tid), **{}) for tid in target_ids
        ]
        for row in check_rows:
            row.pop("dns_server", None)
        status_rows = {
            tid: _build_status_values(_check_result(tid), None, None) for tid in target_ids
        }
        return check_rows, status_rows

    def test_deleted_target_is_dropped_and_the_rest_survive(self):
        # Arrange: a batch of 3, where target 2 was deleted mid-round
        check_rows, status_rows = self._batch([1, 2, 3])

        # Act
        kept_checks, kept_status = _drop_missing_targets(check_rows, status_rows, {1, 3})

        # Assert: the deleted one is gone, the other two are untouched
        assert [r["target_id"] for r in kept_checks] == [1, 3]
        assert set(kept_status) == {1, 3}

    def test_whole_batch_dropped_when_every_target_is_gone(self):
        check_rows, status_rows = self._batch([1, 2])
        kept_checks, kept_status = _drop_missing_targets(check_rows, status_rows, set())
        assert kept_checks == []
        assert kept_status == {}

    def test_nothing_dropped_when_all_targets_are_alive(self):
        check_rows, status_rows = self._batch([1, 2, 3])
        kept_checks, kept_status = _drop_missing_targets(check_rows, status_rows, {1, 2, 3})
        assert len(kept_checks) == 3
        assert set(kept_status) == {1, 2, 3}

    def test_orphan_check_results_are_not_written_either(self):
        # check_results has no foreign key, so orphan rows would insert happily
        # and linger after the target's history was deleted.
        check_rows, status_rows = self._batch([7, 8])
        kept_checks, _ = _drop_missing_targets(check_rows, status_rows, {7})
        assert [r["target_id"] for r in kept_checks] == [7]


# ---------------------------------------------------------------------------
# DNS cache bounding
# ---------------------------------------------------------------------------
class TestDnsCachePruning:
    def test_expired_entries_are_dropped_once_over_capacity(self):
        # Arrange: one stale entry plus fresh ones, just over the limit
        now = time.monotonic()
        cache = {"stale": (now - 9999, {}), "fresh1": (now, {}), "fresh2": (now, {})}

        # Act
        domain_detector._prune_cache(cache, ttl=300.0, max_entries=2)

        # Assert
        assert "stale" not in cache
        assert len(cache) == 2

    def test_oldest_entries_are_evicted_when_all_are_fresh(self):
        now = time.monotonic()
        cache = {f"d{i}": (now + i, {}) for i in range(10)}

        domain_detector._prune_cache(cache, ttl=300.0, max_entries=4)

        assert len(cache) == 4
        assert set(cache) == {"d6", "d7", "d8", "d9"}

    def test_cache_under_capacity_is_untouched(self):
        now = time.monotonic()
        cache = {"stale": (now - 9999, {})}
        domain_detector._prune_cache(cache, ttl=300.0, max_entries=100)
        assert "stale" in cache


class TestSharedResolver:
    def test_resolver_instance_is_reused(self):
        # Constructing a resolver re-reads /etc/resolv.conf with blocking IO
        assert domain_detector._get_resolver() is domain_detector._get_resolver()


class TestApexNsSharing:
    def test_subdomains_of_one_apex_issue_a_single_ns_query(self, monkeypatch):
        # Arrange
        domain_detector._NS_CACHE.clear()
        domain_detector._DNS_CACHE.clear()
        queried = []

        class FakeResolver:
            async def resolve(self, name, rdtype, lifetime=None):
                queried.append((name, rdtype))
                raise domain_detector.dns.resolver.NoAnswer()

        monkeypatch.setattr(domain_detector, "_get_resolver", lambda: FakeResolver())

        async def run():
            await domain_detector._resolve_dns("www.example.com", timeout=1.0)
            await domain_detector._resolve_dns("shop.example.com", timeout=1.0)

        # Act
        asyncio.run(run())

        # Assert: NS resolved once for the shared apex, not once per hostname
        apex_ns_queries = [q for q in queried if q == ("example.com", "NS")]
        assert len(apex_ns_queries) == 1

    def test_concurrent_lookups_of_one_domain_are_deduplicated(self, monkeypatch):
        domain_detector._NS_CACHE.clear()
        domain_detector._DNS_CACHE.clear()
        a_queries = []

        class SlowResolver:
            async def resolve(self, name, rdtype, lifetime=None):
                if rdtype == "A":
                    a_queries.append(name)
                await asyncio.sleep(0.01)
                raise domain_detector.dns.resolver.NoAnswer()

        monkeypatch.setattr(domain_detector, "_get_resolver", lambda: SlowResolver())

        async def run():
            await asyncio.gather(*[
                domain_detector._resolve_dns("example.org", timeout=1.0) for _ in range(5)
            ])

        asyncio.run(run())

        assert a_queries == ["example.org"]


# ---------------------------------------------------------------------------
# Partition retention
# ---------------------------------------------------------------------------
class TestPartitionExpiry:
    # What run_daily_maintenance passes: 30 days before "now" (2026-09-23).
    CUTOFF = datetime(2026, 8, 24, tzinfo=timezone.utc)

    @pytest.mark.parametrize("name", [
        "check_results_2026_07",   # month ended 2026-08-01, before the cutoff
        "check_results_2025_12",   # year rollover
    ])
    def test_fully_elapsed_months_are_expired(self, name):
        assert is_partition_expired(name, self.CUTOFF) is True

    @pytest.mark.parametrize("name", [
        "check_results_2026_08",   # ends 2026-09-01, still holds retained rows
        "check_results_2026_09",   # the current month, still being written
        "check_results_2026_10",   # future partition
    ])
    def test_recent_and_future_months_are_kept(self, name):
        assert is_partition_expired(name, self.CUTOFF) is False

    @pytest.mark.parametrize("name", [
        "check_results",
        "check_results_default",
        "check_results_2026_13",   # impossible month
        "some_other_table_2020_01",
    ])
    def test_unrecognised_names_are_never_dropped(self, name):
        assert is_partition_expired(name, self.CUTOFF) is False

    def test_december_partition_rolls_into_next_year(self):
        # Guards the month % 12 arithmetic against an off-by-one at the boundary
        cutoff = datetime(2027, 1, 15, tzinfo=timezone.utc)
        assert is_partition_expired("check_results_2026_12", cutoff) is True
        cutoff_same_month = datetime(2026, 12, 31, tzinfo=timezone.utc)
        assert is_partition_expired("check_results_2026_12", cutoff_same_month) is False


# ---------------------------------------------------------------------------
# Per-target interval scheduling
# ---------------------------------------------------------------------------
class TestDueCutoff:
    def _sql(self, column):
        from sqlalchemy import or_, select
        from sqlalchemy.dialects import postgresql
        from backend.app.intervals import due_cutoff
        from backend.app.models import Target, TargetStatus

        stmt = (
            select(Target.id)
            .outerjoin(TargetStatus, Target.id == TargetStatus.target_id)
            .where(or_(
                TargetStatus.last_check_at.is_(None),
                TargetStatus.last_check_at < due_cutoff(column),
            ))
        )
        return str(stmt.compile(dialect=postgresql.dialect()))

    def test_cutoff_is_relative_to_each_targets_own_column(self):
        sql = self._sql("check_interval")
        # The interval comes from the target row, not from a bound constant,
        # so every target gets its own cadence in a single query.
        assert "targets.check_interval * interval '1 second'" in sql
        assert "now() - (targets.check_interval" in sql

    def test_never_checked_targets_are_always_due(self):
        sql = self._sql("check_interval")
        assert "target_status.last_check_at IS NULL" in sql

    def test_same_helper_serves_screenshot_and_snapshot_cadence(self):
        assert "targets.shot_interval" in self._sql("shot_interval")
        assert "targets.snapshot_interval" in self._sql("snapshot_interval")


# ---------------------------------------------------------------------------
# Certificate recheck throttling
# ---------------------------------------------------------------------------
def _needs_ssl_check(last_checked, cutoff):
    """Mirror of the predicate run_checks uses to gate the extra TLS handshake."""
    return last_checked is None or last_checked < cutoff


class TestSslRecheckWindow:
    def test_recent_certificate_check_is_skipped(self):
        from backend.app.config import settings

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=settings.ssl_recheck_hours)

        assert _needs_ssl_check(None, cutoff) is True
        assert _needs_ssl_check(now - timedelta(minutes=5), cutoff) is False
        assert _needs_ssl_check(
            now - timedelta(hours=settings.ssl_recheck_hours + 1), cutoff
        ) is True
