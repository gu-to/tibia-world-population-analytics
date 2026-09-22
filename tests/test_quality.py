from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.quality import (
    DataQualityError,
    coverage_report,
    expected_slots,
    observation_slot,
    validate_month,
    validate_snapshot,
)


def _snapshot(time: str, world: str = "Antica", players: str = "12") -> dict[str, str]:
    return {
        "observed_at": time,
        "collected_at": time,
        "world": world,
        "players_online": players,
        "status": "online",
    }


def _run(observed: str, collected: str, slot: str, worlds: str = "1") -> dict[str, str]:
    return {
        "observed_at": observed,
        "collected_at": collected,
        "slot_at": slot,
        "worlds_returned": worlds,
        "reported_players_online": "12",
        "api_version": "4",
        "api_release": "test",
    }


@pytest.mark.parametrize("players", ["-1", "abc", "1.5", "True", ""])
def test_reject_invalid_populations(players: str) -> None:
    with pytest.raises(DataQualityError):
        validate_snapshot(_snapshot("2026-08-01T00:30:00Z", players=players))


@pytest.mark.parametrize("timestamp", ["not-a-date", "2026-08-01T00:30:00", ""])
def test_reject_invalid_timestamps(timestamp: str) -> None:
    with pytest.raises(DataQualityError):
        validate_snapshot(_snapshot(timestamp))


def test_identical_duplicates_are_removed_only_during_finalization() -> None:
    row = _snapshot("2026-08-01T00:30:00Z")
    run = _run(row["observed_at"], row["collected_at"], row["observed_at"])
    with pytest.raises(DataQualityError):
        validate_month("2026-08", [row, row], [run])
    ordered, report = validate_month("2026-08", [row, row], [run], deduplicate=True)
    assert len(ordered) == 1
    assert report["deduplicated_rows"] == 1


def test_conflicting_duplicates_are_rejected() -> None:
    row = _snapshot("2026-08-01T00:30:00Z")
    other = {**row, "players_online": "13"}
    run = _run(row["observed_at"], row["collected_at"], row["observed_at"])
    with pytest.raises(DataQualityError):
        validate_month("2026-08", [row, other], [run], deduplicate=True)


def test_coverage_counts_slots_not_world_rows() -> None:
    first = "2026-08-01T00:28:00Z"
    second = "2026-08-01T02:29:00Z"
    rows = [
        _snapshot(first, "Antica"),
        _snapshot(first, "Belobra"),
        _snapshot(second, "Antica"),
    ]
    runs = [
        _run(first, "2026-08-01T00:35:00Z", "2026-08-01T00:30:00Z", "2"),
        _run(second, "2026-08-01T02:35:00Z", "2026-08-01T02:30:00Z", "1"),
    ]
    through = datetime(2026, 8, 1, 2, 40, tzinfo=UTC)
    report = coverage_report("2026-08", rows, runs, through=through)
    assert report["expected_collection_slots"] == 3
    assert report["successful_observation_slots"] == 2
    assert report["coverage"] == pytest.approx(2 / 3)
    assert report["missing_slots"] == ["2026-08-01T01:30:00Z"]
    assert report["world_coverage"]["antica"]["observed_slots"] == 2
    assert report["world_coverage"]["belobra"]["observed_slots"] == 1
    assert len(expected_slots("2026-08")) == 31 * 24
    assert observation_slot(datetime(2026, 8, 1, 2, 10, tzinfo=UTC)).hour == 1
