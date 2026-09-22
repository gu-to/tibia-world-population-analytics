"""Validation and hourly coverage for public population datasets."""

from __future__ import annotations

import calendar
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

SNAPSHOT_FIELDS = ("observed_at", "collected_at", "world", "players_online", "status")
RUN_FIELDS = (
    "observed_at",
    "collected_at",
    "slot_at",
    "worlds_returned",
    "reported_players_online",
    "api_version",
    "api_release",
)
REFERENCE_FIELDS = (
    "world",
    "location",
    "pvp_type",
    "battleye_protected",
    "battleye_date",
    "premium_only",
    "transfer_type",
    "game_world_type",
    "tournament_world_type",
    "first_seen",
    "last_seen",
    "active",
)


class DataQualityError(ValueError):
    """A public dataset failed validation and must not be published."""


def parse_utc(value: str) -> datetime:
    """Parse a timezone-aware ISO-8601 string and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        raise DataQualityError("Missing timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataQualityError(f"Invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise DataQualityError(f"Timestamp lacks timezone: {value!r}")
    return parsed.astimezone(UTC)


def utc_iso(value: datetime) -> str:
    """Render a timezone-aware UTC datetime for CSV and SQLite."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise DataQualityError("Timestamp must have a timezone")
    return value.astimezone(UTC).isoformat(timespec="auto").replace("+00:00", "Z")


def parse_month(month: str) -> tuple[int, int]:
    """Validate YYYY-MM and return year and month numbers."""
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise DataQualityError(f"Invalid month: {month!r}")
    try:
        year, number = int(month[:4]), int(month[5:])
        datetime(year, number, 1)
    except ValueError as exc:
        raise DataQualityError(f"Invalid month: {month!r}") from exc
    return year, number


def observation_slot(collected_at: datetime) -> datetime:
    """Assign a run to the most recent UTC :30 slot, without changing source time."""
    instant = collected_at.astimezone(UTC)
    slot = instant.replace(minute=30, second=0, microsecond=0)
    if instant < slot:
        slot -= timedelta(hours=1)
    return slot


def validate_schema(fieldnames: Iterable[str] | None, expected: tuple[str, ...]) -> None:
    if tuple(fieldnames or ()) != expected:
        raise DataQualityError(f"Expected CSV columns {expected}, got {tuple(fieldnames or ())}")


def validate_snapshot(row: Mapping[str, Any], *, month: str | None = None) -> tuple[str, str]:
    """Validate one raw row and return its logical (world, observed_at) key."""
    if tuple(row) != SNAPSHOT_FIELDS:
        raise DataQualityError("Unexpected snapshot schema")
    observed = parse_utc(row["observed_at"])
    parse_utc(row["collected_at"])
    world = row["world"]
    if not isinstance(world, str) or not world.strip() or world != world.strip():
        raise DataQualityError("World name must be nonempty and trimmed")
    if month and observed.strftime("%Y-%m") != month:
        raise DataQualityError(f"Snapshot is outside {month}: {row['observed_at']}")
    value = row["players_online"]
    if isinstance(value, bool):
        raise DataQualityError("Player count cannot be boolean")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(f"Invalid player count: {value!r}") from exc
    if str(count) != str(value) or count < 0:
        raise DataQualityError(f"Invalid player count: {value!r}")
    if row["status"] is not None and not isinstance(row["status"], str):
        raise DataQualityError("Invalid world status")
    return world.casefold(), utc_iso(observed)


def validate_run(row: Mapping[str, Any], *, month: str | None = None) -> str:
    """Validate a successful collection audit row."""
    if tuple(row) != RUN_FIELDS:
        raise DataQualityError("Unexpected run audit schema")
    observed = parse_utc(row["observed_at"])
    collected = parse_utc(row["collected_at"])
    slot = parse_utc(row["slot_at"])
    if observation_slot(collected) != slot:
        raise DataQualityError("Run slot disagrees with collected_at")
    if month and observed.strftime("%Y-%m") != month:
        raise DataQualityError("Run audit is outside its source observation month")
    count = row["worlds_returned"]
    if not str(count).isdigit() or int(count) < 1:
        raise DataQualityError("Run must contain at least one world")
    for field in ("reported_players_online", "api_version"):
        value = row[field]
        if value not in ("", None) and (not str(value).isdigit() or int(value) < 0):
            raise DataQualityError(f"Invalid {field}")
    return utc_iso(observed)


def validate_reference(row: Mapping[str, Any]) -> str:
    """Validate one last-seen world metadata row."""
    if tuple(row) != REFERENCE_FIELDS:
        raise DataQualityError("Unexpected world reference schema")
    world = row["world"]
    if not isinstance(world, str) or not world.strip():
        raise DataQualityError("Reference world must be nonempty")
    first = parse_utc(row["first_seen"])
    last = parse_utc(row["last_seen"])
    if first > last:
        raise DataQualityError("first_seen exceeds last_seen")
    for field in ("battleye_protected", "premium_only", "active"):
        if row[field] not in ("", "true", "false"):
            raise DataQualityError(f"Invalid {field} boolean")
    return world.casefold()


def expected_slots(month: str, *, through: datetime | None = None) -> list[datetime]:
    """Return all :30 UTC schedule slots for a closed or elapsed month."""
    year, number = parse_month(month)
    first = datetime(year, number, 1, 0, 30, tzinfo=UTC)
    days = calendar.monthrange(year, number)[1]
    end = datetime(year, number, days, 23, 30, tzinfo=UTC)
    if through is not None:
        end = min(end, through.astimezone(UTC))
    if end < first:
        return []
    count = int((end - first) / timedelta(hours=1)) + 1
    return [first + timedelta(hours=index) for index in range(count)]


def coverage_report(
    month: str,
    snapshots: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    *,
    through: datetime | None = None,
) -> dict[str, Any]:
    """Calculate collection and per-world coverage from unique scheduled slots."""
    expected = {utc_iso(slot) for slot in expected_slots(month, through=through)}
    runs_by_observation: dict[str, str] = {}
    for row in runs:
        observed = validate_run(row, month=month)
        if observed in runs_by_observation:
            raise DataQualityError(f"Duplicate collection run: {observed}")
        runs_by_observation[observed] = utc_iso(parse_utc(row["slot_at"]))
    actual = {slot for slot in runs_by_observation.values() if slot in expected}
    world_slots: dict[str, set[str]] = {}
    for row in snapshots:
        world, observed = validate_snapshot(row, month=month)
        slot = runs_by_observation.get(observed)
        if slot is None:
            raise DataQualityError(f"Snapshot without audit run: {observed}")
        if slot in expected:
            world_slots.setdefault(world, set()).add(slot)
    denominator = len(expected)
    missing = sorted(expected - actual)
    return {
        "expected_collection_slots": denominator,
        "successful_observation_slots": len(actual),
        "coverage": len(actual) / denominator if denominator else None,
        "missing_slots": missing,
        "world_coverage": {
            world: {
                "observed_slots": len(slots),
                "expected_slots": denominator,
                "coverage": len(slots) / denominator if denominator else None,
            }
            for world, slots in sorted(world_slots.items())
        },
    }


def validate_month(
    month: str,
    snapshots: list[dict[str, str]],
    runs: list[dict[str, str]],
    *,
    deduplicate: bool = False,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Validate a full month, optionally dropping identical duplicate rows."""
    if not snapshots:
        raise DataQualityError(f"Month {month} has no snapshot rows")
    unique: dict[tuple[str, str], dict[str, str]] = {}
    duplicates = 0
    for row in snapshots:
        key = validate_snapshot(row, month=month)
        if key in unique:
            if not deduplicate or unique[key] != row:
                raise DataQualityError(f"Duplicate or conflicting snapshot: {key}")
            duplicates += 1
        else:
            unique[key] = row
    ordered = sorted(
        unique.values(),
        key=lambda row: (parse_utc(row["observed_at"]), row["world"].casefold()),
    )
    counts = Counter(utc_iso(parse_utc(row["observed_at"])) for row in ordered)
    run_counts = Counter(validate_run(row, month=month) for row in runs)
    if any(count != 1 for count in run_counts.values()):
        raise DataQualityError("Duplicate run audit rows")
    run_order = [parse_utc(row["observed_at"]) for row in runs]
    if run_order != sorted(run_order):
        raise DataQualityError("Run audit is not sorted by source observation time")
    if set(counts) != set(run_counts):
        raise DataQualityError("Snapshot and audit observation timestamps differ")
    reported_counts = {
        utc_iso(parse_utc(row["observed_at"])): int(row["worlds_returned"]) for row in runs
    }
    if any(counts[time] != reported_counts[time] for time in counts):
        raise DataQualityError("World count differs from run audit")
    coverage = coverage_report(month, ordered, runs)
    report = {
        "month": month,
        "snapshot_rows": len(ordered),
        "deduplicated_rows": duplicates,
        "collection_runs": len(runs),
        "worlds_observed": len({row["world"].casefold() for row in ordered}),
        "min_worlds_per_run": min(counts.values()),
        "max_worlds_per_run": max(counts.values()),
        **coverage,
    }
    return ordered, report
