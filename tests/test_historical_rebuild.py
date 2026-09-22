from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pyarrow.parquet as pq
import pytest
from conftest import make_batch

from src.database import connect
from src.datasets import append_batch, snapshot_path
from src.historical import closed_months, finalize_month, historical_paths
from src.quality import DataQualityError
from src.rebuild import rebuild_database


def _month(root):
    first = datetime(2026, 8, 1, 0, 28, tzinfo=UTC)
    second = first + timedelta(hours=2)
    append_batch(
        replace(
            make_batch(first, {"Antica": 10, "Belobra": 20}),
            collected_at=first + timedelta(minutes=7),
        ),
        root,
    )
    append_batch(
        replace(
            make_batch(second, {"Antica": 30, "Belobra": 40}),
            collected_at=second + timedelta(minutes=7),
        ),
        root,
    )
    return first, second


def test_monthly_finalization_parquet_coverage_and_immutability(tmp_path) -> None:
    _month(tmp_path)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    assert closed_months(tmp_path, tmp_path, now=now) == ["2026-08"]
    report = finalize_month("2026-08", tmp_path, tmp_path, now=now)
    paths = historical_paths(tmp_path, "2026-08")
    assert pq.read_metadata(paths["parquet"]).num_rows == 4
    assert report["expected_collection_slots"] == 31 * 24
    assert report["successful_observation_slots"] == 2
    assert report["coverage"] == pytest.approx(2 / (31 * 24))
    assert json.loads(paths["quality"].read_text(encoding="utf-8"))["snapshot_rows"] == 4
    assert paths["runs"].exists() and paths["worlds"].exists()
    previous_bytes = paths["parquet"].read_bytes()
    with pytest.raises(FileExistsError):
        finalize_month("2026-08", tmp_path, tmp_path, now=now)
    assert paths["parquet"].read_bytes() == previous_bytes
    assert closed_months(tmp_path, tmp_path, now=now) == []


def test_finalization_deduplicates_identical_rows(tmp_path) -> None:
    _month(tmp_path)
    live = snapshot_path(tmp_path, "2026-08")
    with live.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with live.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writerow(rows[0])
    report = finalize_month("2026-08", tmp_path, tmp_path, now=datetime(2026, 9, 1, tzinfo=UTC))
    assert report["deduplicated_rows"] == 1
    assert report["snapshot_rows"] == 4


def test_finalization_rejects_empty_and_open_month(tmp_path) -> None:
    _month(tmp_path)
    live = snapshot_path(tmp_path, "2026-08")
    live.write_text("observed_at,collected_at,world,players_online,status\n", encoding="utf-8")
    with pytest.raises(DataQualityError, match="no snapshot rows"):
        finalize_month("2026-08", tmp_path, tmp_path, now=datetime(2026, 9, 1, tzinfo=UTC))
    _month(tmp_path.parent / "other")
    with pytest.raises(DataQualityError):
        finalize_month(
            "2026-08",
            tmp_path.parent / "other",
            tmp_path.parent / "other",
            now=datetime(2026, 8, 31, tzinfo=UTC),
        )


def test_rebuild_from_historical_and_live_without_overwriting(tmp_path) -> None:
    live_root = tmp_path / "data_branch"
    historical_root = tmp_path / "main_branch"
    _month(live_root)
    finalize_month("2026-08", live_root, historical_root, now=datetime(2026, 9, 1, tzinfo=UTC))
    september = datetime(2026, 9, 1, 0, 35, tzinfo=UTC)
    append_batch(make_batch(september, {"Antica": 50}), live_root)
    rebuilt = tmp_path / "rebuilt.db"
    runs, snapshots = rebuild_database(
        rebuilt, historical_root, include_live=True, live_root=live_root
    )
    assert (runs, snapshots) == (3, 5)
    with connect(rebuilt) as connection:
        assert connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 5
        assert (
            connection.execute(
                "SELECT players_online FROM population_snapshots ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()[0]
            == 50
        )
    with pytest.raises(FileExistsError):
        rebuild_database(rebuilt, historical_root, include_live=True, live_root=live_root)


def test_rebuild_rejects_empty_sources_without_creating_target(tmp_path) -> None:
    target = tmp_path / "rebuilt.db"
    with pytest.raises(DataQualityError):
        rebuild_database(target, tmp_path, include_live=True)
    assert not target.exists()
