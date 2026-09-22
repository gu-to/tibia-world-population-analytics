"""Explicit, immutable monthly Parquet finalization."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.config import PROJECT_ROOT
from src.datasets import audit_path, read_csv, reference_path, snapshot_path
from src.quality import (
    REFERENCE_FIELDS,
    RUN_FIELDS,
    SNAPSHOT_FIELDS,
    DataQualityError,
    parse_month,
    parse_utc,
    validate_month,
    validate_reference,
)

LOGGER = logging.getLogger(__name__)

PARQUET_SCHEMA = pa.schema(
    [
        pa.field("observed_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("collected_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("world", pa.string(), nullable=False),
        pa.field("players_online", pa.int32(), nullable=False),
        pa.field("status", pa.string(), nullable=True),
    ]
)


def historical_directory(root: Path, month: str) -> Path:
    year, _ = parse_month(month)
    return root / "data" / "historical" / str(year)


def historical_paths(root: Path, month: str) -> dict[str, Path]:
    directory = historical_directory(root, month)
    return {
        "parquet": directory / f"{month}.parquet",
        "quality": directory / f"{month}.quality.json",
        "runs": directory / f"{month}.runs.csv",
        "worlds": directory / f"{month}.worlds.csv",
    }


def closed_months(
    source_root: Path, output_root: Path, *, now: datetime | None = None
) -> list[str]:
    """List live months older than the current UTC month and not yet published."""
    current = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")
    pending = []
    for path in sorted((source_root / "data" / "live").glob("????-??.csv")):
        month = path.stem
        parse_month(month)
        if month < current and not historical_paths(output_root, month)["parquet"].exists():
            pending.append(month)
    return pending


def _table(rows: list[dict[str, str]]) -> pa.Table:
    return pa.Table.from_pydict(
        {
            "observed_at": [parse_utc(row["observed_at"]) for row in rows],
            "collected_at": [parse_utc(row["collected_at"]) for row in rows],
            "world": [row["world"] for row in rows],
            "players_online": [int(row["players_online"]) for row in rows],
            "status": [row["status"] or None for row in rows],
        },
        schema=PARQUET_SCHEMA,
    )


def _temporary_path(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(descriptor)
    return Path(name)


def finalize_month(
    month: str,
    source_root: Path,
    output_root: Path,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate a closed live month and publish immutable Parquet plus audit files."""
    parse_month(month)
    current = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m")
    if month >= current:
        raise DataQualityError(f"Month {month} is still open")
    paths = historical_paths(output_root, month)
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(f"Historical month {month} already has output: {existing}")
    live = snapshot_path(source_root, month)
    audit = audit_path(source_root, month)
    reference = reference_path(source_root)
    for required in (live, audit, reference):
        if not required.exists():
            raise FileNotFoundError(f"Monthly finalization requires {required}")
    snapshots = read_csv(live, SNAPSHOT_FIELDS)
    runs = read_csv(audit, RUN_FIELDS)
    worlds = read_csv(reference, REFERENCE_FIELDS)
    reference_keys = set()
    for row in worlds:
        key = validate_reference(row)
        if key in reference_keys:
            raise DataQualityError(f"Duplicate reference world: {key}")
        reference_keys.add(key)
    ordered, report = validate_month(month, snapshots, runs, deduplicate=True)
    missing_worlds = {row["world"].casefold() for row in ordered} - reference_keys
    if missing_worlds:
        raise DataQualityError(f"Missing world metadata: {sorted(missing_worlds)}")
    table = _table(ordered)
    staged: dict[str, Path] = {}
    try:
        staged["parquet"] = _temporary_path(paths["parquet"])
        pq.write_table(table, staged["parquet"], compression="zstd", version="2.6")
        reloaded = pq.read_table(staged["parquet"])
        if reloaded.schema != PARQUET_SCHEMA or not reloaded.equals(table):
            raise DataQualityError("Parquet read-back validation failed")
        report["parquet_compression"] = "zstd"
        report["source"] = "TibiaData API v4 via hourly GitHub Actions"
        staged["quality"] = _temporary_path(paths["quality"])
        staged["quality"].write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        for key, source in (("runs", audit), ("worlds", reference)):
            staged[key] = _temporary_path(paths[key])
            shutil.copyfile(source, staged[key])
        # The existence check is repeated immediately before publishing.
        if any(path.exists() for path in paths.values()):
            raise FileExistsError(f"Historical month {month} became available during finalization")
        for key in ("parquet", "quality", "runs", "worlds"):
            os.replace(staged[key], paths[key])
        LOGGER.info(
            "Finalized %s: %d rows, %d runs, %.1f%% coverage",
            month,
            report["snapshot_rows"],
            report["collection_runs"],
            100 * report["coverage"],
        )
        return report
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    pending_parser = subparsers.add_parser("pending", help="Print closed, unfinalized months")
    pending_parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
    pending_parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT)
    finalize_parser = subparsers.add_parser("finalize", help="Finalize one closed month")
    finalize_parser.add_argument("--month", required=True)
    finalize_parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT)
    finalize_parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "pending":
            for month in closed_months(args.source_root, args.output_root):
                print(month)
        else:
            finalize_month(args.month, args.source_root, args.output_root)
    except (DataQualityError, FileNotFoundError, FileExistsError, OSError):
        LOGGER.exception("Monthly finalization failed safely")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
