from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from conftest import make_batch
from streamlit.testing.v1 import AppTest

from src.database import store_batch


def test_patterns_page_renders_sparse_real_history(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "patterns.db"
    friday = datetime(2026, 9, 25, 10, tzinfo=UTC)
    store_batch(make_batch(friday, {"Antica": 100}), db_path)
    store_batch(make_batch(friday + timedelta(days=1), {"Antica": 300}), db_path)
    monkeypatch.setenv("TIBIA_ANALYTICS_DB", str(db_path))
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "4_population_patterns.py"

    rendered = AppTest.from_file(str(page), default_timeout=30).run()
    timezone = next(widget for widget in rendered.selectbox if widget.label == "Display timezone")
    rendered = timezone.set_value("America/Sao_Paulo").run()

    assert not rendered.exception
    assert len(rendered.metric) == 4
    assert len(rendered.get("plotly_chart")) == 8
    assert any("America/Sao_Paulo" in item.value for item in rendered.get("caption"))
    assert any("Uneven collection times" in item.value for item in rendered.get("caption"))
    minimum = next(
        widget for widget in rendered.slider if widget.label == "Minimum distinct dates per cell"
    )
    rendered = minimum.set_value(2).run()
    assert not rendered.exception
    assert any(
        "are hidden by the selected minimum" in item.value for item in rendered.get("caption")
    )


def test_compare_page_supports_local_timezone_and_profile_comparison(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "compare.db"
    friday = datetime(2026, 9, 25, 10, tzinfo=UTC)
    store_batch(make_batch(friday, {"Antica": 100, "Belobra": 50}), db_path)
    store_batch(make_batch(friday + timedelta(days=1), {"Antica": 300, "Belobra": 60}), db_path)
    monkeypatch.setenv("TIBIA_ANALYTICS_DB", str(db_path))
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "2_compare_worlds.py"

    rendered = AppTest.from_file(str(page), default_timeout=30).run()
    period = next(widget for widget in rendered.selectbox if widget.label == "Period")
    rendered = period.set_value("7 days").run()
    timezone = next(widget for widget in rendered.selectbox if widget.label == "Display timezone")
    rendered = timezone.set_value("America/Sao_Paulo").run()

    assert not rendered.exception
    assert len(rendered.get("plotly_chart")) == 3
    assert any("America/Sao_Paulo" in item.value for item in rendered.get("subheader"))
    scale = next(widget for widget in rendered.radio if widget.label == "Comparison scale")
    rendered = scale.set_value("% of each world's observed mean").run()
    assert not rendered.exception
    assert any("100% is each world's mean" in item.value for item in rendered.get("info"))
    minimum = next(
        widget for widget in rendered.slider if widget.label == "Minimum distinct dates per point"
    )
    rendered = minimum.set_value(2).run()
    assert not rendered.exception


def test_relative_comparison_handles_zero_mean_world(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "zero.db"
    observed = datetime(2026, 9, 25, 10, tzinfo=UTC)
    store_batch(make_batch(observed, {"Antica": 0}), db_path)
    store_batch(make_batch(observed + timedelta(days=1), {"Antica": 0}), db_path)
    monkeypatch.setenv("TIBIA_ANALYTICS_DB", str(db_path))
    page = Path(__file__).resolve().parents[1] / "app" / "pages" / "2_compare_worlds.py"

    rendered = AppTest.from_file(str(page), default_timeout=30).run()
    period = next(widget for widget in rendered.selectbox if widget.label == "Period")
    rendered = period.set_value("7 days").run()
    scale = next(widget for widget in rendered.radio if widget.label == "Comparison scale")
    rendered = scale.set_value("% of each world's observed mean").run()

    assert not rendered.exception
    assert any("Relative values are undefined" in item.value for item in rendered.get("warning"))
