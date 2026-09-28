from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.common import with_visual_gaps


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
