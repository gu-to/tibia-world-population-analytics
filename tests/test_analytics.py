from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_batch

from src.analytics import (
    WorldFilters,
    latest_world_table,
    overview_kpis,
    total_population_series,
    world_statistics,
)
from src.database import store_batch


@pytest.fixture
def analytics_db(tmp_path):
    db_path = tmp_path / "analytics.db"
    start = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    store_batch(make_batch(start, {"Antica": 100, "Belobra": 50}), db_path)
    store_batch(
        make_batch(start + timedelta(hours=1), {"Antica": 200, "Belobra": 70}),
        db_path,
    )
    store_batch(
        make_batch(start + timedelta(hours=2), {"Antica": 300, "Belobra": 60}),
        db_path,
    )
    return db_path


def test_overview_aggregations(analytics_db) -> None:
    kpis = overview_kpis(analytics_db)

    assert kpis["players_now"] == 360
    assert kpis["worlds_monitored"] == 2
    assert kpis["average_now"] == 180
    assert kpis["peak_24h"] == 360
    assert kpis["average_24h"] == pytest.approx(260)


def test_overview_filters_are_applied(analytics_db) -> None:
    kpis = overview_kpis(analytics_db, WorldFilters(locations=("South America",)))

    assert kpis["players_now"] == 60
    assert kpis["worlds_monitored"] == 1
    assert kpis["peak_24h"] == 70


def test_latest_world_table_contains_trailing_statistics(analytics_db) -> None:
    table = latest_world_table(analytics_db)
    antica = table.loc[table["world"] == "Antica"].iloc[0]

    assert antica["players_now"] == 300
    assert antica["average_24h"] == pytest.approx(200)
    assert antica["peak_24h"] == 300


def test_world_statistics_and_series(analytics_db) -> None:
    stats = world_statistics(analytics_db, ["Antica"], hours=24).iloc[0]
    series = total_population_series(analytics_db, hours=24)

    assert stats["samples"] == 3
    assert stats["mean"] == pytest.approx(200)
    assert stats["median"] == 200
    assert stats["minimum"] == 100
    assert stats["maximum"] == 300
    assert stats["p90"] == pytest.approx(280)
    assert stats["peak_at"] == "2026-09-22T12:00:00Z"
    assert series["players_online"].tolist() == [150, 270, 360]
