"""SQLite persistence for metadata, collection runs, and population history."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.api import WorldsBatch
from src.config import database_path

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS worlds (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    location TEXT,
    pvp_type TEXT,
    premium_only INTEGER CHECK (premium_only IN (0, 1) OR premium_only IS NULL),
    transfer_type TEXT,
    battleye_protected INTEGER CHECK (battleye_protected IN (0, 1) OR battleye_protected IS NULL),
    battleye_date TEXT,
    game_world_type TEXT,
    tournament_world_type TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY,
    observed_at TEXT NOT NULL UNIQUE,
    collected_at TEXT NOT NULL,
    data_mode TEXT NOT NULL CHECK (data_mode IN ('real', 'demo')),
    api_version INTEGER,
    api_release TEXT,
    reported_players_online INTEGER,
    record_players INTEGER,
    record_date TEXT,
    worlds_received INTEGER NOT NULL,
    snapshots_inserted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS population_snapshots (
    id INTEGER PRIMARY KEY,
    collection_run_id INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    world_id INTEGER NOT NULL REFERENCES worlds(id),
    observed_at TEXT NOT NULL,
    players_online INTEGER NOT NULL CHECK (players_online >= 0),
    status TEXT,
    UNIQUE (world_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_observed_at
    ON population_snapshots(observed_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_world_time
    ON population_snapshots(world_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_worlds_filters
    ON worlds(location, pvp_type, battleye_protected, premium_only);

PRAGMA user_version = 1;
"""


@dataclass(frozen=True, slots=True)
class StoreResult:
    """Outcome of an atomic batch insertion."""

    worlds_received: int
    snapshots_inserted: int
    duplicate_batch: bool


def utc_text(value: datetime) -> str:
    """Serialize a timezone-aware datetime as canonical UTC ISO-8601."""
    utc_value = value.astimezone(UTC).replace(tzinfo=None)
    return utc_value.isoformat(timespec="seconds") + "Z"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open an SQLite connection configured for integrity and concurrency."""
    path = Path(db_path) if db_path is not None else database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(db_path: Path | str | None = None) -> None:
    """Create the database schema if it does not exist."""
    with connect(db_path) as connection:
        connection.executescript(SCHEMA_SQL)


def _bool_as_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def store_batch(
    batch: WorldsBatch,
    db_path: Path | str | None = None,
    *,
    data_mode: str = "real",
) -> StoreResult:
    """Atomically upsert world metadata and insert one historical batch."""
    if data_mode not in {"real", "demo"}:
        raise ValueError("data_mode must be 'real' or 'demo'")
    initialize_database(db_path)
    observed_at = utc_text(batch.observed_at)
    collected_at = utc_text(batch.collected_at)

    with connect(db_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            INSERT INTO collection_runs (
                observed_at, collected_at, data_mode, api_version, api_release,
                reported_players_online, record_players, record_date,
                worlds_received, snapshots_inserted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(observed_at) DO NOTHING
            """,
            (
                observed_at,
                collected_at,
                data_mode,
                batch.api_version,
                batch.api_release,
                batch.total_players_online,
                batch.record_players,
                batch.record_date,
                len(batch.worlds),
            ),
        )
        if cursor.rowcount == 0:
            return StoreResult(len(batch.worlds), 0, True)
        run_id = cursor.lastrowid

        inserted = 0
        for world in batch.worlds:
            connection.execute(
                """
                INSERT INTO worlds (
                    name, location, pvp_type, premium_only, transfer_type,
                    battleye_protected, battleye_date, game_world_type,
                    tournament_world_type, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    location = COALESCE(excluded.location, worlds.location),
                    pvp_type = COALESCE(excluded.pvp_type, worlds.pvp_type),
                    premium_only = COALESCE(excluded.premium_only, worlds.premium_only),
                    transfer_type = COALESCE(excluded.transfer_type, worlds.transfer_type),
                    battleye_protected = COALESCE(
                        excluded.battleye_protected, worlds.battleye_protected
                    ),
                    battleye_date = COALESCE(excluded.battleye_date, worlds.battleye_date),
                    game_world_type = COALESCE(excluded.game_world_type, worlds.game_world_type),
                    tournament_world_type = COALESCE(
                        excluded.tournament_world_type, worlds.tournament_world_type
                    ),
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    world.name,
                    world.location,
                    world.pvp_type,
                    _bool_as_int(world.premium_only),
                    world.transfer_type,
                    _bool_as_int(world.battleye_protected),
                    world.battleye_date,
                    world.game_world_type,
                    world.tournament_world_type,
                    observed_at,
                    observed_at,
                ),
            )
            world_id = connection.execute(
                "SELECT id FROM worlds WHERE name = ? COLLATE NOCASE", (world.name,)
            ).fetchone()["id"]
            snapshot_cursor = connection.execute(
                """
                INSERT INTO population_snapshots (
                    collection_run_id, world_id, observed_at, players_online, status
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(world_id, observed_at) DO NOTHING
                """,
                (run_id, world_id, observed_at, world.players_online, world.status),
            )
            inserted += max(snapshot_cursor.rowcount, 0)

        connection.execute(
            "UPDATE collection_runs SET snapshots_inserted = ? WHERE id = ?",
            (inserted, run_id),
        )
        return StoreResult(len(batch.worlds), inserted, False)
