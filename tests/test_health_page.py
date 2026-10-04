from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
from conftest import make_batch
from streamlit.testing.v1 import AppTest

from src.database import store_batch
from src.health import WorkflowRun


def test_data_quality_compares_github_runs_only_on_request(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "health-page.db"
    collected = datetime.now(UTC) - timedelta(days=1)
    store_batch(make_batch(collected, {"Antica": 100}), db_path)
    monkeypatch.setenv("TIBIA_ANALYTICS_DB", str(db_path))
    calls: list[tuple] = []

    def fake_fetch(*args, **kwargs) -> list[WorkflowRun]:
        calls.append((args, kwargs))
        return [
            WorkflowRun(
                run_id=42,
                event="schedule",
                started_at=collected - timedelta(minutes=2),
                status="completed",
                conclusion="success",
            )
        ]

    monkeypatch.setattr("src.health.fetch_workflow_runs", fake_fetch)
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "3_data_quality.py"

    rendered = AppTest.from_file(str(page), default_timeout=30).run()
    assert not rendered.exception
    assert calls == []
    button = next(item for item in rendered.button if item.label == "Compare with GitHub Actions")
    rendered = button.click().run()

    assert not rendered.exception
    assert len(calls) == 1
    assert any(item.label == "Scheduled runs visible" for item in rendered.metric)


def test_data_quality_keeps_local_report_when_github_is_unavailable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "health-page.db"
    store_batch(make_batch(datetime.now(UTC) - timedelta(hours=1), {"Antica": 100}), db_path)
    monkeypatch.setenv("TIBIA_ANALYTICS_DB", str(db_path))

    def fail_fetch(*args, **kwargs):
        raise requests.Timeout("offline for test")

    monkeypatch.setattr("src.health.fetch_workflow_runs", fail_fetch)
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "3_data_quality.py"
    rendered = AppTest.from_file(str(page), default_timeout=30).run()
    button = next(item for item in rendered.button if item.label == "Compare with GitHub Actions")
    rendered = button.click().run()

    assert not rendered.exception
    assert len(rendered.get("plotly_chart")) == 1
    assert any("GitHub comparison unavailable" in item.value for item in rendered.warning)
