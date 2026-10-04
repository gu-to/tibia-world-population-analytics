from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_batch

from src.database import store_batch
from src.health import WorkflowRun, compare_workflow_runs, fetch_workflow_runs, main
from src.monitoring import coverage_window


def _run(hour: int, conclusion: str, *, run_id: int | None = None) -> WorkflowRun:
    return WorkflowRun(
        run_id=run_id or hour,
        event="schedule",
        started_at=datetime(2026, 10, 2, hour, 31, tzinfo=UTC),
        status="completed",
        conclusion=conclusion,
    )


def test_health_distinguishes_failed_and_absent_runs(tmp_path) -> None:
    db = tmp_path / "health.db"
    start = datetime(2026, 10, 2, 10, 30, tzinfo=UTC)
    for hour in (10, 12):
        store_batch(make_batch(start.replace(hour=hour) + timedelta(minutes=5), {"Antica": 10}), db)
    coverage = coverage_window(db, start, start.replace(hour=13, minute=40))

    result = compare_workflow_runs(
        coverage, [_run(10, "success"), _run(11, "failure"), _run(12, "success")]
    )

    assert len(coverage.observed_slots) == 2
    assert result.scheduled_runs == 3
    assert result.successful_runs == 2
    assert result.failed_runs == 1
    assert result.failed_run_ids == (11,)
    assert [slot.hour for slot in result.no_visible_run_slots] == [13]
    assert result.success_without_nearby_audit == ()


def test_health_separates_newer_runs_from_missing_local_audit(tmp_path) -> None:
    db = tmp_path / "health.db"
    start = datetime(2026, 10, 2, 10, 30, tzinfo=UTC)
    store_batch(make_batch(start + timedelta(minutes=5), {"Antica": 10}), db)
    store_batch(make_batch(start + timedelta(hours=2, minutes=5), {"Antica": 20}), db)
    coverage = coverage_window(db, start, start + timedelta(hours=3, minutes=10))

    result = compare_workflow_runs(
        coverage,
        [_run(10, "success"), _run(11, "success"), _run(12, "success"), _run(13, "success")],
    )

    assert result.success_without_nearby_audit == (11,)
    assert result.newer_than_local_sync == (13,)


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self.payload


class _Session:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payload)


def test_github_run_fetch_is_bounded_and_parsed_without_network() -> None:
    start = datetime(2026, 10, 2, tzinfo=UTC)
    session = _Session(
        {
            "workflow_runs": [
                {
                    "id": 42,
                    "event": "schedule",
                    "created_at": "2026-10-02T10:30:00Z",
                    "run_started_at": "2026-10-02T10:31:00Z",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }
    )

    runs = fetch_workflow_runs(
        "gu-to/tibia-world-population-analytics",
        start,
        start + timedelta(days=1),
        token="secret-for-test",
        session=session,
    )

    assert runs == [_run(10, "success", run_id=42)]
    assert session.calls[0]["params"]["created"].startswith("2026-10-02T00:00:00Z..")
    assert session.calls[0]["headers"]["Authorization"] == "Bearer secret-for-test"
    with pytest.raises(ValueError, match="OWNER/REPO"):
        fetch_workflow_runs("invalid", start, start, session=session)
    with pytest.raises(ValueError, match="Unexpected"):
        fetch_workflow_runs("owner/repo", start, start, session=_Session({"bad": []}))


def test_health_cli_reports_local_data_without_github(tmp_path, capsys) -> None:
    db = tmp_path / "health.db"
    store_batch(make_batch(datetime(2026, 10, 2, 10, 35, tzinfo=UTC), {"Antica": 10}), db)

    assert main(["--db", str(db), "--days", "7"]) == 0
    output = capsys.readouterr().out
    assert "Observed / expected hourly slots" in output
    assert "Latest source observation" in output
