"""Shared SQL helpers for per-target interval scheduling.

Each target carries its own cadence (check_interval, shot_interval,
snapshot_interval, in seconds). A round should only pick up the targets whose
own interval has elapsed, rather than processing everything every time.

This lives in its own module so the checker can use it without importing the
screenshot worker, which would drag Playwright into the check import path.
"""
from sqlalchemy import text


def due_cutoff(interval_column: str):
    """SQL expression for "older than this target's own interval", in seconds.

    Compare a target_status timestamp against it, e.g.

        TargetStatus.last_check_at < due_cutoff("check_interval")

    The column name is interpolated into SQL, so it must be a literal from the
    call site and never anything derived from user input.
    """
    return text(f"now() - (targets.{interval_column} * interval '1 second')")
