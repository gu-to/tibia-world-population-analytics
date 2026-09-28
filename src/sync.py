"""Safely refresh local SQLite from reviewed history and the public data branch."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from src.config import PROJECT_ROOT, database_path
from src.quality import SNAPSHOT_FIELDS, DataQualityError, validate_schema
from src.rebuild import rebuild_database

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceMonth:
    """One public monthly partition selected for the local rebuild."""

    month: str
    kind: str
    snapshot_rows: int


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Summary of a dry-run or completed local database refresh."""

    sources: tuple[SourceMonth, ...]
    runs: int
    snapshots: int
    backup_path: Path | None
    dry_run: bool


def _count_live_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        validate_schema(reader.fieldnames, SNAPSHOT_FIELDS)
        return sum(1 for _ in reader)


def discover_sources(
    main_root: Path, data_root: Path | None, *, include_live: bool
) -> tuple[SourceMonth, ...]:
    """List bounded monthly inputs without loading all historical rows."""
    historical = sorted((main_root / "data" / "historical").glob("????/????-??.parquet"))
    months = {path.stem for path in historical}
    selected = [
        SourceMonth(path.stem, "Parquet", pq.read_metadata(path).num_rows) for path in historical
    ]
    if include_live:
        if data_root is None:
            raise ValueError("A data-branch checkout is required for live data")
        for path in sorted((data_root / "data" / "live").glob("????-??.csv")):
            if path.stem not in months:
                selected.append(SourceMonth(path.stem, "live CSV", _count_live_rows(path)))
    if not selected:
        raise DataQualityError("No public monthly datasets available for synchronization")
    return tuple(sorted(selected, key=lambda item: item.month))


def _backup_database(source: Path, destination: Path) -> None:
    """Create a consistent SQLite backup after active sidecars are ruled out."""
    if destination.exists():
        raise FileExistsError(f"Backup already exists: {destination}")
    try:
        # mode=rw avoids accidentally creating a missing DB and lets SQLite
        # checkpoint/remove its own transient WAL sidecars on close (Windows).
        uri = f"file:{source.resolve().as_posix()}?mode=rw"
        with (
            closing(sqlite3.connect(uri, uri=True)) as original,
            closing(sqlite3.connect(destination)) as backup,
        ):
            original.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DataQualityError("SQLite backup failed integrity_check")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def sync_database(
    db_path: Path,
    main_root: Path,
    *,
    data_root: Path | None,
    include_live: bool = True,
    replace: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Rebuild off to the side; publish only after validation and an optional backup."""
    db_path = Path(db_path)
    if db_path.resolve() == database_path(demo=True).resolve():
        raise ValueError("Refusing to replace the separate synthetic-demo database")
    sources = discover_sources(main_root, data_root, include_live=include_live)
    if dry_run:
        return SyncResult(sources, 0, 0, None, True)
    if db_path.exists() and not replace:
        raise FileExistsError(f"{db_path} exists; pass --replace to back it up and refresh it")
    if db_path.exists():
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(db_path) + suffix).exists():
                raise RuntimeError("Close the dashboard and SQLite writers before replacing the DB")
        with closing(
            sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        ) as old:
            modes = {
                row[0] for row in old.execute("SELECT DISTINCT data_mode FROM collection_runs")
            }
        if "demo" in modes:
            raise ValueError("Refusing to replace a database containing synthetic-demo runs")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{db_path.stem}.", suffix=".db", dir=db_path.parent
    )
    os.close(descriptor)
    staged = Path(name)
    staged.unlink()
    backup_path: Path | None = None
    try:
        runs, snapshots = rebuild_database(
            staged,
            main_root,
            include_live=include_live,
            live_root=data_root,
        )
        if db_path.exists():
            if not replace:
                raise FileExistsError(f"{db_path} appeared during synchronization")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = db_path.with_name(f"{db_path.stem}.backup-{stamp}.db")
            _backup_database(db_path, backup_path)
            if any(Path(str(db_path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                raise RuntimeError(
                    "SQLite writer opened during synchronization; original DB retained"
                )
        os.replace(staged, db_path)
        return SyncResult(sources, runs, snapshots, backup_path, False)
    finally:
        staged.unlink(missing_ok=True)


def _clone_data_branch(main_root: Path, destination: Path) -> None:
    """Read the public data branch into a temporary checkout, without touching local Git refs."""
    try:
        remote = subprocess.run(
            ["git", "-C", str(main_root), "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not inspect the Git origin; use --data-root") from exc
    if remote.returncode != 0 or not remote.stdout.strip():
        raise RuntimeError("Cannot locate origin; pass --data-root for an existing data checkout")
    try:
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--depth",
                "1",
                "--single-branch",
                "--branch",
                "data",
                remote.stdout.strip(),
                str(destination),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not read the data branch in time; use --data-root") from exc
    if clone.returncode != 0:
        raise RuntimeError(
            "Could not read the data branch; pass --data-root for an offline checkout"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT, help="Checkout containing main history"
    )
    parser.add_argument("--data-root", type=Path, help="Existing checkout of the data branch")
    parser.add_argument("--db", type=Path, default=database_path())
    parser.add_argument("--historical-only", action="store_true", help="Skip current-month CSV")
    parser.add_argument("--replace", action="store_true", help="Back up and replace an existing DB")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show selected sources without writing"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        with tempfile.TemporaryDirectory(prefix="tibia-public-data-") as temporary:
            data_root = args.data_root
            if not args.historical_only and data_root is None:
                data_root = Path(temporary) / "data-branch"
                _clone_data_branch(args.root, data_root)
            sources = discover_sources(args.root, data_root, include_live=not args.historical_only)
            for source in sources:
                LOGGER.info(
                    "Import: %s %s (%d rows)", source.month, source.kind, source.snapshot_rows
                )
            LOGGER.info("Target: %s", args.db)
            result = sync_database(
                args.db,
                args.root,
                data_root=data_root,
                include_live=not args.historical_only,
                replace=args.replace,
                dry_run=args.dry_run,
            )
    except (DataQualityError, FileExistsError, ValueError, RuntimeError, OSError, sqlite3.Error):
        LOGGER.exception("Local synchronization failed; the existing database was not replaced")
        return 1
    if result.dry_run:
        LOGGER.info("Dry run complete; no database changed")
    else:
        LOGGER.info("Synchronized %d runs and %d snapshots", result.runs, result.snapshots)
        if result.backup_path:
            LOGGER.info("Previous database backup: %s", result.backup_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
