from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.common import population_line_chart, with_visual_gaps


def test_long_missing_period_breaks_visual_line_only() -> None:
    start = datetime(2026, 9, 22, 10, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "observed_at": [start, start + timedelta(hours=1), start + timedelta(hours=5)],
            "world": ["Antica"] * 3,
            "players_online": [10, 20, 30],
        }
    )
    display = with_visual_gaps(frame, cadence_minutes=60, group="world")
    assert len(display) == 4
    assert pd.isna(display.iloc[2]["players_online"])
    assert frame["players_online"].tolist() == [10, 20, 30]


def test_dashed_bridge_connects_only_real_endpoints() -> None:
    start = datetime(2026, 9, 22, 10, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "observed_at": [start, start + timedelta(hours=1), start + timedelta(hours=5)],
            "players_online": [10, 20, 30],
        }
    )
    original = frame.copy(deep=True)

    figure = population_line_chart(frame, cadence_minutes=60, height=400)

    assert len(figure.data) == 2
    assert figure.data[0].connectgaps is False
    assert figure.data[1].line.dash == "dash"
    assert list(figure.data[1].x) == [start + timedelta(hours=1), start + timedelta(hours=5), None]
    assert list(figure.data[1].y) == [20, 30, None]
    assert figure.data[1].showlegend is False
    pd.testing.assert_frame_equal(frame, original)


def test_dashed_bridges_do_not_join_different_worlds() -> None:
    start = datetime(2026, 9, 22, 10, tzinfo=UTC)
    frame = pd.DataFrame(
        {
            "observed_at": [start, start + timedelta(hours=3), start, start + timedelta(hours=1)],
            "world": ["Antica", "Antica", "Belobra", "Belobra"],
            "players_online": [100, 300, 10, 20],
        }
    )

    figure = population_line_chart(frame, cadence_minutes=60, height=400, group="world")

    dashed = [trace for trace in figure.data if trace.line.dash == "dash"]
    assert len(dashed) == 1
    assert list(dashed[0].y) == [100, 300, None]
    assert dashed[0].line.color == figure.data[0].line.color
