"""Append-friendly monthly CSV storage and world reference updates."""

from __future__ import annotations

import csv
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.api import WorldsBatch
from src.quality import (
    REFERENCE_FIELDS,
    RUN_FIELDS,
    SNAPSHOT_FIELDS,
    DataQualityError,
    observation_slot,
    parse_utc,
    utc_iso,
    validate_reference,
    validate_run,
    validate_schema,
    validate_snapshot,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Public data files modified by one collection."""

    month: str
    snapshots_added: int
    metadata_changed: bool
    audit_added: bool


def snapshot_path(root: Path, month: str) -> Path:
    return root / "data" / "live" / f"{month}.csv"


def audit_path(root: Path, month: str) -> Path:
    return root / "data" / "audit" / f"{month}.csv"


def reference_path(root: Path) -> Path:
    return root / "data" / "reference" / "worlds.csv"


def read_csv(path: Path, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """Read one monthly/reference CSV with exact schema validation."""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_schema(reader.fieldnames, fields)
        return list(reader)


def _stage_csv(
    path: Path,
    fields: tuple[str, ...],
    rows: Iterable[dict[str, str]],
    *,
    append: bool,
) -> Path:
    """Write into a sibling temp file, streaming existing data if appending."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
            if append and path.exists():
                with path.open("r", encoding="utf-8", newline="") as source:
                    shutil.copyfileobj(source, target)
            else:
                csv.writer(target, lineterminator="\n").writerow(fields)
            writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
            writer.writerows(rows)
            target.flush()
            os.fsync(target.fileno())
        return temp_path
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _existing_snapshot_state(path: Path, month: str) -> tuple[set[str], str | None]:
    """Stream one monthly file, checking ordering and duplicate keys."""
    if not path.exists():
        return set(), None
    observed_keys: set[str] = set()
    previous: tuple[datetime, str] | None = None
    latest: str | None = None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_schema(reader.fieldnames, SNAPSHOT_FIELDS)
        for row in reader:
            world, observed = validate_snapshot(row, month=month)
            position = parse_utc(observed), world
            if previous is not None and position <= previous:
                raise DataQualityError(f"Unsorted or duplicate live CSV: {path}")
            previous = position
            if latest != observed:
                latest = observed
                observed_keys.clear()
            observed_keys.add(world)
    return observed_keys, latest


def _reference_rows(batch: WorldsBatch, path: Path) -> tuple[list[dict[str, str]], bool]:
    existing = read_csv(path, REFERENCE_FIELDS)
    by_world: dict[str, dict[str, str]] = {}
    for row in existing:
        key = validate_reference(row)
        if key in by_world:
            raise DataQualityError(f"Duplicate world reference: {key}")
        by_world[key] = row
    latest_reference_time = max((row["last_seen"] for row in by_world.values()), default=None)
    if latest_reference_time and batch.observed_at < parse_utc(latest_reference_time):
        raise DataQualityError("Source observation predates the latest accepted world reference")
    previously_active = sum(row["active"] == "true" for row in by_world.values())
    if previously_active >= 10 and len(batch.worlds) * 2 < previously_active:
        raise DataQualityError(
            f"World count dropped from {previously_active} to {len(batch.worlds)}; "
            "refusing to mark most worlds inactive"
        )
    changed = False
    seen: set[str] = set()
    time = utc_iso(batch.observed_at)
    for world in batch.worlds:
        key = world.name.casefold()
        if key in seen:
            raise DataQualityError(f"Duplicate world in API batch: {world.name}")
        seen.add(key)
        old = by_world.get(key)
        row = dict(old) if old else dict.fromkeys(REFERENCE_FIELDS, "")
        row["world"] = world.name
        for field in (
            "location",
            "pvp_type",
            "battleye_date",
            "transfer_type",
            "game_world_type",
            "tournament_world_type",
        ):
            value = getattr(world, field)
            if value is not None:
                row[field] = value
        for field in ("battleye_protected", "premium_only"):
            value = getattr(world, field)
            if value is not None:
                row[field] = "true" if value else "false"
        row["first_seen"] = old["first_seen"] if old else time
        row["last_seen"] = (
            utc_iso(max(parse_utc(old["last_seen"]), batch.observed_at)) if old else time
        )
        row["active"] = "true"
        validate_reference(row)
        if old != row:
            changed = True
        by_world[key] = row
    for key, row in by_world.items():
        if key not in seen and row["active"] != "false":
            row["active"] = "false"
            changed = True
    return [by_world[key] for key in sorted(by_world)], changed


def _audit_row(batch: WorldsBatch) -> dict[str, str]:
    row = {
        "observed_at": utc_iso(batch.observed_at),
        "collected_at": utc_iso(batch.collected_at),
        "slot_at": utc_iso(observation_slot(batch.collected_at)),
        "worlds_returned": str(len(batch.worlds)),
        "reported_players_online": (
            "" if batch.total_players_online is None else str(batch.total_players_online)
        ),
        "api_version": "" if batch.api_version is None else str(batch.api_version),
        "api_release": batch.api_release or "",
    }
    validate_run(row, month=batch.observed_at.strftime("%Y-%m"))
    return row


def append_batch(
    batch: WorldsBatch, root: Path, *, historical_root: Path | None = None
) -> AppendResult:
    """Validate and append one API batch to public monthly CSVs.

    The source observation month selects the file. Each file is replaced from a
    complete sibling temp file; a rerun heals a missing audit/reference file.
    """
    root = Path(root)
    observed = utc_iso(batch.observed_at)
    month = observed[:7]
    collected = parse_utc(utc_iso(batch.collected_at))
    if month < collected.strftime("%Y-%m") and (collected.day > 1 or collected.hour >= 3):
        raise DataQualityError(f"Month {month} is closed to late observations")
    snap_file = snapshot_path(root, month)
    audit_file = audit_path(root, month)
    ref_file = reference_path(root)
    published_root = historical_root or root
    if (published_root / "data" / "historical" / observed[:4] / f"{month}.parquet").exists():
        raise DataQualityError(f"Month {month} is finalized and immutable")
    if not batch.worlds:
        raise DataQualityError("Cannot append an empty API batch")
    if batch.skipped_world_rows:
        raise DataQualityError(
            f"API response skipped {batch.skipped_world_rows} malformed world rows"
        )
    rows = [
        {
            "observed_at": observed,
            "collected_at": utc_iso(batch.collected_at),
            "world": world.name,
            "players_online": str(world.players_online),
            "status": world.status or "",
        }
        for world in batch.worlds
    ]
    rows.sort(key=lambda row: row["world"].casefold())
    new_keys: set[str] = set()
    for row in rows:
        world_key, _ = validate_snapshot(row, month=month)
        if world_key in new_keys:
            raise DataQualityError(f"Duplicate world in API batch: {row['world']}")
        new_keys.add(world_key)
    existing_keys, latest = _existing_snapshot_state(snap_file, month)
    if latest is not None and parse_utc(observed) < parse_utc(latest):
        raise DataQualityError("Source timestamp is older than the latest live observation")
    if latest == observed:
        if new_keys != existing_keys:
            raise DataQualityError("Rerun conflicts with existing source observation")
        rows = []
    reference, reference_changed = _reference_rows(batch, ref_file)
    audits = read_csv(audit_file, RUN_FIELDS)
    audit_keys = set()
    previous_audit: datetime | None = None
    for item in audits:
        key = validate_run(item, month=month)
        if key in audit_keys or (previous_audit is not None and parse_utc(key) <= previous_audit):
            raise DataQualityError(f"Duplicate or unsorted run audit: {key}")
        audit_keys.add(key)
        previous_audit = parse_utc(key)
    audit_added = observed not in audit_keys
    if audit_added and audits and parse_utc(observed) < parse_utc(audits[-1]["observed_at"]):
        raise DataQualityError("Run audit is not chronologically appendable")
    if not rows and not audit_added and not reference_changed:
        LOGGER.info("Observation %s is already present", observed)
        return AppendResult(month, 0, False, False)
    staged: list[tuple[Path, Path]] = []
    try:
        if rows:
            staged.append((snap_file, _stage_csv(snap_file, SNAPSHOT_FIELDS, rows, append=True)))
        if reference_changed:
            staged.append(
                (ref_file, _stage_csv(ref_file, REFERENCE_FIELDS, reference, append=False))
            )
        if audit_added:
            staged.append(
                (audit_file, _stage_csv(audit_file, RUN_FIELDS, [_audit_row(batch)], append=True))
            )
        for destination, temporary in staged:
            os.replace(temporary, destination)
    finally:
        for _, temporary in staged:
            temporary.unlink(missing_ok=True)
    LOGGER.info(
        "Observation %s: %d snapshots, metadata_changed=%s, audit_added=%s",
        observed,
        len(rows),
        reference_changed,
        audit_added,
    )
    return AppendResult(month, len(rows), reference_changed, audit_added)
