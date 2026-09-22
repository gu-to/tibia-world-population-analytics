from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import requests
from conftest import make_batch

from src.api import TibiaDataClient, TibiaDataError, WorldObservation, WorldsBatch
from src.datasets import append_batch, audit_path, read_csv, reference_path, snapshot_path
from src.quality import REFERENCE_FIELDS, RUN_FIELDS, SNAPSHOT_FIELDS, DataQualityError


def test_append_rerun_and_metadata_changes(tmp_path) -> None:
    first_at = datetime(2026, 8, 3, 10, 29, tzinfo=UTC)
    first = make_batch(first_at, {"Antica": 100, "Belobra": 20})
    result = append_batch(first, tmp_path)
    assert result.snapshots_added == 2
    assert append_batch(first, tmp_path).snapshots_added == 0
    assert len(read_csv(audit_path(tmp_path, "2026-08"), RUN_FIELDS)) == 1
    second_at = first_at + timedelta(hours=1)
    changed = WorldsBatch(
        observed_at=second_at,
        collected_at=second_at + timedelta(minutes=2),
        worlds=(
            WorldObservation(
                name="Antica", players_online=110, location="Europe", pvp_type="Optional PvP"
            ),
            WorldObservation(name="Newera", players_online=5, location="Oceania"),
        ),
        total_players_online=115,
        record_players=None,
        record_date=None,
        api_version=4,
        api_release="test",
    )
    append_batch(changed, tmp_path)
    snapshots = read_csv(snapshot_path(tmp_path, "2026-08"), SNAPSHOT_FIELDS)
    metadata = {row["world"]: row for row in read_csv(reference_path(tmp_path), REFERENCE_FIELDS)}
    assert len(snapshots) == 4
    assert metadata["Antica"]["pvp_type"] == "Optional PvP"
    assert metadata["Antica"]["first_seen"] == "2026-08-03T10:29:00Z"
    assert metadata["Newera"]["active"] == "true"
    assert metadata["Belobra"]["active"] == "false"
    assert metadata["Belobra"]["last_seen"] == "2026-08-03T10:29:00Z"


def test_bad_batch_does_not_change_existing_files(tmp_path) -> None:
    start = datetime(2026, 8, 3, 10, 30, tzinfo=UTC)
    append_batch(make_batch(start, {"Antica": 10}), tmp_path)
    snap_file = snapshot_path(tmp_path, "2026-08")
    original = snap_file.read_bytes()
    with pytest.raises(DataQualityError):
        append_batch(make_batch(start + timedelta(hours=1), {"Antica": -1}), tmp_path)
    assert snap_file.read_bytes() == original
    assert len(read_csv(audit_path(tmp_path, "2026-08"), RUN_FIELDS)) == 1


def test_invalid_existing_schema_and_empty_file_fail_safely(tmp_path) -> None:
    start = datetime(2026, 8, 3, 10, 30, tzinfo=UTC)
    snap_file = snapshot_path(tmp_path, "2026-08")
    snap_file.parent.mkdir(parents=True)
    snap_file.write_text("wrong,columns\n", encoding="utf-8")
    with pytest.raises(DataQualityError):
        append_batch(make_batch(start, {"Antica": 10}), tmp_path)
    snap_file.write_text("", encoding="utf-8")
    with pytest.raises(DataQualityError):
        append_batch(make_batch(start, {"Antica": 10}), tmp_path)


def test_failed_api_leaves_no_dataset(tmp_path, monkeypatch) -> None:
    class BrokenSession(requests.Session):
        def get(self, *args, **kwargs):
            raise requests.Timeout("offline")

    from src import pipeline

    monkeypatch.setattr(
        pipeline, "TibiaDataClient", lambda: TibiaDataClient(session=BrokenSession())
    )
    with pytest.raises(TibiaDataError):
        pipeline.collect_public(tmp_path)
    assert not (tmp_path / "data").exists()


def test_mocked_public_collector_is_idempotent(tmp_path, monkeypatch) -> None:
    from src import pipeline

    batch = make_batch(datetime(2026, 8, 2, 12, 30, tzinfo=UTC), {"Antica": 71})

    class FakeClient:
        def fetch_worlds(self):
            return batch

    monkeypatch.setattr(pipeline, "TibiaDataClient", FakeClient)
    assert pipeline.collect_public(tmp_path) == 1
    assert pipeline.collect_public(tmp_path) == 0
    assert len(read_csv(snapshot_path(tmp_path, "2026-08"), SNAPSHOT_FIELDS)) == 1


def test_source_time_selects_month_and_is_not_rounded(tmp_path) -> None:
    source = datetime(2026, 8, 31, 23, 59, 5, tzinfo=UTC)
    batch = make_batch(source, {"Antica": 42})
    append_batch(batch, tmp_path)
    row = read_csv(snapshot_path(tmp_path, "2026-08"), SNAPSHOT_FIELDS)[0]
    assert row["observed_at"] == "2026-08-31T23:59:05Z"
    assert not snapshot_path(tmp_path, "2026-09").exists()


def test_subsecond_source_timestamps_remain_distinct(tmp_path) -> None:
    first = datetime(2026, 8, 3, 10, 30, 5, 100_000, tzinfo=UTC)
    second = first + timedelta(microseconds=100_000)
    append_batch(make_batch(first, {"Antica": 42}), tmp_path)
    append_batch(make_batch(second, {"Antica": 43}), tmp_path)
    rows = read_csv(snapshot_path(tmp_path, "2026-08"), SNAPSHOT_FIELDS)
    assert len(rows) == 2
    assert [row["observed_at"] for row in rows] == [
        "2026-08-03T10:30:05.100000Z",
        "2026-08-03T10:30:05.200000Z",
    ]


def test_rejects_late_closed_month_and_skipped_api_world(tmp_path) -> None:
    from dataclasses import replace

    source = datetime(2026, 8, 31, 23, 59, tzinfo=UTC)
    batch = make_batch(source, {"Antica": 42})
    with pytest.raises(DataQualityError, match="closed"):
        append_batch(
            replace(batch, collected_at=datetime(2026, 9, 1, 4, 30, tzinfo=UTC)),
            tmp_path,
        )
    with pytest.raises(DataQualityError, match="skipped"):
        append_batch(replace(batch, skipped_world_rows=1), tmp_path)
    assert not (tmp_path / "data").exists()
