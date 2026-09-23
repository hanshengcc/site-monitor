"""Tests for group deletion.

These mock the DB session by dispatching on the SQL being executed rather than
on call order. The previous version pinned an ordered list of six side effects,
so it broke the moment delete_group was rewritten to count first and delete the
child tables in foreign-key order — it had been failing ever since.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.app.routers.targets import delete_group


def make_db(target_count, shot_rows=(), snap_rows=()):
    """A fake session that answers each statement by inspecting its SQL."""
    executed = []
    db = AsyncMock()

    def dispatch(statement, params=None):
        sql = " ".join(str(statement).split())
        executed.append(sql)
        result = MagicMock()

        is_select = sql.lstrip().lower().startswith("select")
        lowered = sql.lower()

        if is_select and "count(" in lowered:
            result.scalar.return_value = target_count
        elif is_select and "from screenshots" in lowered:
            result.all.return_value = list(shot_rows)
        elif is_select and "from snapshots" in lowered:
            result.all.return_value = list(snap_rows)
        else:
            result.all.return_value = []
            result.rowcount = 0
        return result

    db.execute.side_effect = dispatch
    return db, executed


def deletion_order(executed):
    """Tables targeted by DELETE statements, in execution order."""
    order = []
    for sql in executed:
        lowered = sql.lower()
        if lowered.startswith("delete from "):
            order.append(lowered.split("delete from ", 1)[1].split()[0].strip('"'))
    return order


class TestDeleteGroupWithTargets:
    def _run(self):
        db, executed = make_db(
            target_count=3,
            shot_rows=[("fake_shot_1.png", "fake_thumb_1.webp")],
            snap_rows=[("fake_snap_1.html.gz",)],
        )
        with patch("pathlib.Path.unlink") as unlink:
            result = asyncio.run(delete_group("test_group", db=db))
        return result, executed, unlink, db

    def test_reports_the_number_of_targets_removed(self):
        result, _, _, db = self._run()
        assert result["ok"] is True
        assert result["group"] == "test_group"
        assert result["deleted_targets"] == 3
        assert db.commit.called

    def test_removes_screenshot_thumbnail_and_snapshot_files(self):
        _, _, unlink, _ = self._run()
        # two screenshot files (full + thumb) and one snapshot archive
        assert unlink.call_count == 3

    def test_deletes_child_tables_before_their_parents(self):
        _, executed, _, _ = self._run()
        order = deletion_order(executed)

        for table in ("baselines", "anomalies", "screenshots", "snapshots",
                      "check_results", "target_status", "targets", "group_settings"):
            assert table in order, f"{table} was never deleted from (order={order})"

        # Rows referencing targets must go before targets itself, or PostgreSQL
        # rejects the delete on the foreign key.
        targets_at = order.index("targets")
        for child in ("baselines", "anomalies", "screenshots", "snapshots",
                      "check_results", "target_status"):
            assert order.index(child) < targets_at, f"{child} deleted after targets"

        # baselines references screenshots as well as targets.
        assert order.index("baselines") < order.index("screenshots")

    def test_selects_targets_by_subquery_not_by_id_list(self):
        # A literal IN (...) list overflows asyncpg's parameter limit on large
        # groups, which is what the raw-SQL subquery rewrite fixed.
        _, executed, _, _ = self._run()
        target_scoped = [s for s in executed if "target_id in" in s.lower()]
        assert target_scoped, "expected child deletes scoped by target_id"
        for sql in target_scoped:
            assert "select id from targets" in sql.lower(), f"not a subquery: {sql}"


class TestDeleteGroupEmpty:
    def _run(self):
        db, executed = make_db(target_count=0)
        result = asyncio.run(delete_group("empty_group", db=db))
        return result, executed, db

    def test_reports_zero_targets(self):
        result, _, db = self._run()
        assert result["ok"] is True
        assert result["group"] == "empty_group"
        assert result["deleted_targets"] == 0
        assert db.commit.called

    def test_still_cleans_up_the_orphaned_group_setting(self):
        _, executed, _ = self._run()
        assert deletion_order(executed) == ["group_settings"]

    def test_does_not_touch_target_data(self):
        _, executed, _ = self._run()
        joined = " ".join(executed).lower()
        for table in ("screenshots", "snapshots", "check_results", "target_status"):
            assert table not in joined, f"unexpectedly touched {table}"
