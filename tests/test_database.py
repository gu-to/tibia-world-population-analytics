from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_batch

from src.api import WorldObservation, WorldsBatch
from src.database import connect, store_batch


def test_batch_insert_and_duplicate_prevention(tmp_path, sample_batch) -> None:
    db_path = tmp_path / "test.db"

    first = store_batch(sample_batch, db_path)
    second = store_batch(sample_batch, db_path)

    assert first.snapshots_inserted == 1
    assert first.duplicate_batch is False
    assert second.snapshots_inserted == 0
    assert second.duplicate_batch is True
    with connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM worlds").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 1


def test_metadata_is_updated_without_duplicating_world(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    first_at = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    store_batch(make_batch(first_at, {"Antica": 100}), db_path)
    changed_world = WorldObservation(
        name="Antica",
        players_online=110,
        status="online",
        location="Europe",
        pvp_type="Optional PvP",
        premium_only=True,
        transfer_type="blocked",
        battleye_protected=True,
        game_world_type="regular",
    )
    second = WorldsBatch(
        observed_at=first_at + timedelta(minutes=5),
        collected_at=first_at + timedelta(minutes=5),
        worlds=(changed_world,),
        total_players_online=110,
        record_players=None,
        record_date=None,
        api_version=4,
        api_release="test",
    )

    store_batch(second, db_path)

    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT pvp_type, premium_only, transfer_type FROM worlds"
        ).fetchone()
        assert tuple(row) == ("Optional PvP", 1, "blocked")
        assert connection.execute("SELECT COUNT(*) FROM worlds").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 2


def test_failed_snapshot_rolls_back_entire_batch(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    timestamp = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    invalid = make_batch(timestamp, {"Antica": -1})

    with pytest.raises(sqlite3.IntegrityError):
        store_batch(invalid, db_path)

    with connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 0


def test_timestamp_is_normalized_to_utc(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    local_time = datetime.fromisoformat("2026-09-22T09:00:00-03:00")
    store_batch(make_batch(local_time, {"Antica": 10}), db_path)

    with connect(db_path) as connection:
        stored = connection.execute("SELECT observed_at FROM collection_runs").fetchone()[0]
    assert stored == "2026-09-22T12:00:00Z"
