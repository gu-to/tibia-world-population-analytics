from __future__ import annotations

import pandas as pd
import pytest

from src.patterns import display_series_timezone, population_patterns


def _series(timestamps: list[str], populations: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "observed_at": pd.to_datetime(timestamps, utc=True),
            "world": "Antica",
            "players_online": populations,
        }
    )


def test_local_hour_skips_nonexistent_dst_hour_without_inventing_samples() -> None:
    series = _series(["2026-03-08T06:30:00Z", "2026-03-08T07:30:00Z"], [100, 300])

    patterns = population_patterns(series, "America/New_York")

    assert patterns.hourly["hour"].tolist() == [1, 3]
    assert patterns.hourly["samples"].tolist() == [1, 1]
    assert patterns.weekday["weekday"].tolist() == ["Sunday"]
    assert patterns.weekday["days_observed"].tolist() == [1]
    assert patterns.day_type["day_type"].tolist() == ["Weekend"]
    assert patterns.observations["observed_at"].equals(series["observed_at"])


def test_local_weekday_and_distinct_date_support() -> None:
    series = _series(
        ["2026-09-28T01:00:00Z", "2026-09-28T01:20:00Z", "2026-09-28T12:00:00Z"],
        [100, 200, 300],
    )

    patterns = population_patterns(series, "America/Sao_Paulo")

    assert patterns.hourly["hour"].tolist() == [9, 22]
    assert patterns.hourly["samples"].tolist() == [1, 2]
    assert patterns.hourly["days_observed"].tolist() == [1, 1]
    assert patterns.weekday["weekday"].tolist() == ["Monday", "Sunday"]
    assert patterns.weekday["samples"].tolist() == [1, 2]
    assert patterns.day_type["day_type"].tolist() == ["Weekday", "Weekend"]


def test_timezone_display_is_copy_and_rejects_unknown_zone() -> None:
    series = _series(["2026-09-28T01:00:00Z"], [100])

    display = display_series_timezone(series, "America/Sao_Paulo")

    assert display.iloc[0]["observed_at"].hour == 22
    assert series.iloc[0]["observed_at"].hour == 1
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        population_patterns(series, "Unknown/Somewhere")
