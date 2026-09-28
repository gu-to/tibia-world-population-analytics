"""Coverage and freshness calculations for sparse local observations."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.database import connect
from src.quality import observation_slot, parse_utc, utc_iso


@dataclass(frozen=True, slots=True)
class CoverageWindow:
    """Observed hourly slots in a bounded UTC window; missing never means zero."""

    expected_slots: tuple[datetime, ...]
    observed_slots: frozenset[datetime]
    world_slots: dict[str, frozenset[datetime]]
    world_last_seen: dict[str, datetime | None]
    successful_runs: int
    last_collected_at: datetime | None
    last_observed_at: datetime | None

    @property
    def coverage(self) -> float | None:
        return len(self.observed_slots) / len(self.expected_slots) if self.expected_slots else None

    @property
    def missing_slots(self) -> tuple[datetime, ...]:
        return tuple(slot for slot in self.expected_slots if slot not in self.observed_slots)


def scheduled_slots(start: datetime, end: datetime) -> tuple[datetime, ...]:
    """List XX:30 UTC slots within an inclusive time range."""
    first = observation_slot(start)
    if first < start.astimezone(UTC):
        first += timedelta(hours=1)
    finish = end.astimezone(UTC)
    if first > finish:
        return ()
    count = int((finish - first) / timedelta(hours=1)) + 1
    return tuple(first + timedelta(hours=index) for index in range(count))


def coverage_window(db_path: Path | str, start: datetime, end: datetime) -> CoverageWindow:
    """Read bounded run/world observations from SQLite and compare hourly slots."""
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError("Coverage window requires ordered timezone-aware timestamps")
    # A source timestamp can precede collection time, so scope by collection time.
    with closing(connect(db_path)) as connection:
        first_run = connection.execute(
            "SELECT MIN(collected_at) AS collected_at FROM collection_runs"
        ).fetchone()
        effective_start = (
            max(start, observation_slot(parse_utc(first_run["collected_at"])))
            if first_run["collected_at"]
            else end + timedelta(hours=1)
        )
        expected = scheduled_slots(effective_start, end)
        expected_set = set(expected)
        runs = connection.execute(
            """
            SELECT id, observed_at, collected_at
            FROM collection_runs
            WHERE datetime(collected_at) >= datetime(?)
              AND datetime(collected_at) <= datetime(?)
            ORDER BY collected_at
            """,
            (utc_iso(start), utc_iso(end)),
        ).fetchall()
        names = [row[0] for row in connection.execute("SELECT name FROM worlds ORDER BY name")]
        snapshots = connection.execute(
            """
            SELECT cr.collected_at, w.name, ps.observed_at
            FROM collection_runs cr
            JOIN population_snapshots ps ON ps.collection_run_id = cr.id
            JOIN worlds w ON w.id = ps.world_id
            WHERE datetime(cr.collected_at) >= datetime(?)
              AND datetime(cr.collected_at) <= datetime(?)
            ORDER BY cr.collected_at
            """,
            (utc_iso(start), utc_iso(end)),
        ).fetchall()
        observed: set[datetime] = set()
        world_slots: dict[str, set[datetime]] = {name: set() for name in names}
        world_last: dict[str, datetime | None] = dict.fromkeys(names)
        for run in runs:
            slot = observation_slot(parse_utc(run["collected_at"]))
            if slot in expected_set:
                observed.add(slot)
        for row in snapshots:
            slot = observation_slot(parse_utc(row["collected_at"]))
            if slot not in expected_set:
                continue
            name = row["name"]
            world_slots[name].add(slot)
            source_time = parse_utc(row["observed_at"])
            if world_last[name] is None or source_time > world_last[name]:
                world_last[name] = source_time
        latest = connection.execute(
            "SELECT MAX(observed_at) AS observed_at, MAX(collected_at) AS collected_at "
            "FROM collection_runs"
        ).fetchone()
    return CoverageWindow(
        expected_slots=expected,
        observed_slots=frozenset(observed),
        world_slots={name: frozenset(slots) for name, slots in world_slots.items()},
        world_last_seen=world_last,
        successful_runs=len(runs),
        last_collected_at=parse_utc(latest["collected_at"]) if latest["collected_at"] else None,
        last_observed_at=parse_utc(latest["observed_at"]) if latest["observed_at"] else None,
    )


def trailing_coverage(db_path: Path | str, hours: int) -> CoverageWindow:
    """Coverage for the last available observation's hourly window."""
    if hours < 1:
        raise ValueError("hours must be positive")
    with closing(connect(db_path)) as connection:
        row = connection.execute(
            "SELECT MAX(collected_at) AS collected_at FROM collection_runs"
        ).fetchone()
    if row["collected_at"] is None:
        raise ValueError("Database has no collection runs")
    end = parse_utc(row["collected_at"])
    start = observation_slot(end) - timedelta(hours=hours - 1)
    return coverage_window(db_path, start, end)
