from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import make_batch

from src.database import store_batch
from src.monitoring import coverage_window, scheduled_slots, trailing_coverage


def test_hourly_coverage_keeps_missing_slots_missing(tmp_path) -> None:
    db = tmp_path / "coverage.db"
    start = datetime(2026, 9, 22, 10, 30, tzinfo=UTC)
    for offset, worlds in (
        (0, {"Antica": 10, "Belobra": 20}),
        (2, {"Antica": 15}),
        (4, {"Antica": 30, "Belobra": 0}),
    ):
        store_batch(make_batch(start + timedelta(hours=offset, minutes=5), worlds), db)
    report = coverage_window(db, start, start + timedelta(hours=4, minutes=10))
    assert len(report.expected_slots) == 5
    assert len(report.observed_slots) == 3
    assert report.coverage == 0.6
    assert [slot.hour for slot in report.missing_slots] == [11, 13]
    assert len(report.world_slots["Antica"]) == 3
    assert len(report.world_slots["Belobra"]) == 2
    assert report.world_last_seen["Belobra"] == start + timedelta(hours=4, minutes=5)
    assert trailing_coverage(db, 5).coverage == 0.6
    wider = coverage_window(db, start - timedelta(days=3), start + timedelta(hours=4, minutes=10))
    assert len(wider.expected_slots) == 5  # no penalty before monitoring began


def test_slot_generation_excludes_future_and_rejects_bad_window(tmp_path) -> None:
    start = datetime(2026, 9, 22, 10, 31, tzinfo=UTC)
    end = datetime(2026, 9, 22, 12, 29, tzinfo=UTC)
    assert [slot.hour for slot in scheduled_slots(start, end)] == [11]
    try:
        coverage_window(tmp_path / "empty.db", end, start)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected a bad-window error")


def test_multiple_runs_in_one_hour_count_as_one_slot(tmp_path) -> None:
    db = tmp_path / "coverage.db"
    start = datetime(2026, 9, 22, 10, 30, tzinfo=UTC)
    store_batch(make_batch(start + timedelta(minutes=5), {"Antica": 10}), db)
    store_batch(make_batch(start + timedelta(minutes=20), {"Antica": 20}), db)
    report = coverage_window(db, start, start + timedelta(minutes=50))
    assert report.successful_runs == 2
    assert len(report.observed_slots) == 1
    assert len(report.world_slots["Antica"]) == 1
