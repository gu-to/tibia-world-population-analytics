"""Build a fresh local SQLite database from public monthly datasets."""

from __future__ import annotations

import argparse
import csv
import itertools
import logging
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

from src.api import WorldObservation, WorldsBatch
from src.config import PROJECT_ROOT, database_path
from src.database import connect, initialize_database, store_batch
from src.datasets import read_csv, reference_path
from src.historical import PARQUET_SCHEMA, historical_paths
from src.quality import (
    REFERENCE_FIELDS,
    RUN_FIELDS,
    SNAPSHOT_FIELDS,
    DataQualityError,
    parse_utc,
    utc_iso,
    validate_reference,
    validate_run,
    validate_schema,
    validate_snapshot,
)

LOGGER = logging.getLogger(__name__)


def _bool(value: str) -> bool | None:
    return {"true": True, "false": False}.get(value)


def _metadata(path: Path) -> dict[str, dict[str, str]]:
    rows = read_csv(path, REFERENCE_FIELDS)
    if not rows:
        raise DataQualityError(f"Missing or empty world reference: {path}")
    result = {}
    for row in rows:
        key = validate_reference(row)
        if key in result:
            raise DataQualityError(f"Duplicate world reference: {key}")
        result[key] = row
    return result


def _runs(path: Path, month: str) -> dict[str, dict[str, str]]:
    rows = read_csv(path, RUN_FIELDS)
    if not rows:
        raise DataQualityError(f"Missing or empty run audit: {path}")
    result = {}
    for row in rows:
        key = validate_run(row, month=month)
        if key in result:
            raise DataQualityError(f"Duplicate run audit: {key}")
        result[key] = row
    return result


def _parquet_rows(path: Path) -> Iterator[dict[str, str]]:
    parquet = pq.ParquetFile(path)
    if parquet.schema_arrow != PARQUET_SCHEMA:
        raise DataQualityError(f"Unexpected Parquet schema: {path}")
    for batch in parquet.iter_batches(batch_size=8192):
        for item in batch.to_pylist():
            yield {
                "observed_at": utc_iso(item["observed_at"]),
                "collected_at": utc_iso(item["collected_at"]),
                "world": item["world"],
                "players_online": str(item["players_online"]),
                "status": item["status"] or "",
            }


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_schema(reader.fieldnames, SNAPSHOT_FIELDS)
        yield from reader


def _import_month(
    source: Iterator[dict[str, str]],
    month: str,
    runs: dict[str, dict[str, str]],
    metadata: dict[str, dict[str, str]],
    connection: sqlite3.Connection,
) -> tuple[int, int]:
    previous: tuple[datetime, str] | None = None
    run_count = 0
    snapshot_count = 0

    def checked_rows() -> Iterator[dict[str, str]]:
        nonlocal previous
        for row in source:
            world, observed = validate_snapshot(row, month=month)
            key = parse_utc(observed), world
            if previous is not None and key <= previous:
                raise DataQualityError(f"Month {month} is unsorted or contains duplicates")
            previous = key
            yield row

    for observed, group in itertools.groupby(
        checked_rows(), key=lambda row: utc_iso(parse_utc(row["observed_at"]))
    ):
        rows = list(group)
        audit = runs.get(observed)
        if audit is None:
            raise DataQualityError(f"No audit record for {observed}")
        if len(rows) != int(audit["worlds_returned"]):
            raise DataQualityError(f"World count differs from audit for {observed}")
        worlds = []
        for row in rows:
            attributes = metadata.get(row["world"].casefold())
            if attributes is None:
                raise DataQualityError(f"No world metadata for {row['world']}")
            worlds.append(
                WorldObservation(
                    name=row["world"],
                    players_online=int(row["players_online"]),
                    status=row["status"] or None,
                    location=attributes["location"] or None,
                    pvp_type=attributes["pvp_type"] or None,
                    premium_only=_bool(attributes["premium_only"]),
                    transfer_type=attributes["transfer_type"] or None,
                    battleye_protected=_bool(attributes["battleye_protected"]),
                    battleye_date=attributes["battleye_date"] or None,
                    game_world_type=attributes["game_world_type"] or None,
                    tournament_world_type=attributes["tournament_world_type"],
                )
            )
        batch = WorldsBatch(
            observed_at=parse_utc(observed),
            collected_at=parse_utc(audit["collected_at"]),
            worlds=tuple(worlds),
            total_players_online=(
                int(audit["reported_players_online"]) if audit["reported_players_online"] else None
            ),
            record_players=None,
            record_date=None,
            api_version=int(audit["api_version"]) if audit["api_version"] else None,
            api_release=audit["api_release"] or None,
        )
        result = store_batch(batch, connection=connection)
        run_count += 1
        snapshot_count += result.snapshots_inserted
    if not run_count:
        raise DataQualityError(f"Empty month: {month}")
    if run_count != len(runs):
        raise DataQualityError(f"Audit contains runs without snapshots for {month}")
    return run_count, snapshot_count


def rebuild_database(
    db_path: Path,
    root: Path = PROJECT_ROOT,
    *,
    include_live: bool = False,
    live_root: Path | None = None,
) -> tuple[int, int]:
    """Stream monthly files into a new SQLite DB, publishing it only when complete."""
    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing local database: {db_path}")
    historical = sorted((root / "data" / "historical").glob("????/????-??.parquet"))
    historical_months = {path.stem for path in historical}
    current_root = live_root or root
    live = sorted((current_root / "data" / "live").glob("????-??.csv")) if include_live else []
    live = [path for path in live if path.stem not in historical_months]
    if not historical and not live:
        raise DataQualityError("No public monthly datasets found for rebuild")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{db_path.name}.", suffix=".db", dir=db_path.parent
    )
    os.close(descriptor)
    staged_db = Path(temporary)
    staged_db.unlink()
    total_runs = 0
    total_snapshots = 0
    try:
        initialize_database(staged_db)
        with closing(connect(staged_db)) as connection:
            for path in sorted([*historical, *live], key=lambda item: item.stem):
                month = path.stem
                if path.suffix == ".parquet":
                    paths = historical_paths(root, month)
                    metadata = _metadata(paths["worlds"])
                    runs = _runs(paths["runs"], month)
                    rows = _parquet_rows(path)
                else:
                    metadata = _metadata(reference_path(current_root))
                    runs = _runs(current_root / "data" / "audit" / f"{month}.csv", month)
                    rows = _csv_rows(path)
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    run_count, snapshot_count = _import_month(
                        rows, month, runs, metadata, connection
                    )
                total_runs += run_count
                total_snapshots += snapshot_count
                LOGGER.info("Imported %s: %d runs, %d snapshots", month, run_count, snapshot_count)
        # Collapse WAL changes into the main DB file before publication.
        with closing(sqlite3.connect(staged_db)) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DataQualityError("Rebuilt SQLite failed integrity_check")
        if db_path.exists():
            raise FileExistsError(f"Database appeared during rebuild: {db_path}")
        os.replace(staged_db, db_path)
        return total_runs, total_snapshots
    finally:
        staged_db.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(staged_db) + suffix).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--db", type=Path, default=database_path())
    parser.add_argument("--include-live", action="store_true")
    parser.add_argument("--live-root", type=Path, help="Separate checkout of the data branch")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        runs, snapshots = rebuild_database(
            args.db, args.root, include_live=args.include_live, live_root=args.live_root
        )
    except (DataQualityError, FileExistsError, FileNotFoundError, OSError):
        LOGGER.exception("SQLite rebuild failed; target database was not replaced")
        return 1
    LOGGER.info("Rebuilt %s with %d runs and %d snapshots", args.db, runs, snapshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
