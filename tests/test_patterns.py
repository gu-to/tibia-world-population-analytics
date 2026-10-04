from __future__ import annotations

import pandas as pd
import pytest

from src.patterns import (
    comparison_profile,
    display_series_timezone,
    population_patterns,
    relative_population_series,
    weekday_hour_grid,
)


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


def test_weekday_hour_grid_preserves_missing_cells_and_masks_weak_support() -> None:
    series = _series(
        ["2026-09-25T10:30:00Z", "2026-10-02T10:30:00Z", "2026-10-03T10:30:00Z"],
        [100, 300, 25],
    )
    patterns = population_patterns(series, "UTC")

    grid = weekday_hour_grid(patterns.weekday_hour, min_dates=2)

    assert len(grid) == 168
    friday = grid.loc[(grid["weekday"] == "Friday") & (grid["hour"] == 10)].iloc[0]
    saturday = grid.loc[(grid["weekday"] == "Saturday") & (grid["hour"] == 10)].iloc[0]
    missing = grid.loc[(grid["weekday"] == "Monday") & (grid["hour"] == 10)].iloc[0]
    assert (friday["mean_players"], friday["samples"], friday["days_observed"]) == (200, 2, 2)
    assert friday["display_mean"] == 200
    assert saturday["samples"] == 1 and pd.isna(saturday["display_mean"])
    assert missing["samples"] == 0 and pd.isna(missing["mean_players"])
    assert pd.isna(missing["display_mean"])
    with pytest.raises(ValueError, match="positive"):
        weekday_hour_grid(patterns.weekday_hour, min_dates=0)


def test_relative_comparison_uses_each_world_observed_baseline() -> None:
    antica = _series(["2026-09-25T10:30:00Z", "2026-09-25T11:30:00Z"], [100, 300])
    belobra = antica.assign(world="Belobra", players_online=[10, 30])
    patterns = population_patterns(pd.concat([antica, belobra]), "UTC")

    profile = comparison_profile(patterns.hourly, patterns.observations, min_dates=1)

    low = profile.loc[profile["hour"] == 10].set_index("world")
    assert low.loc["Antica", "mean_players"] == 100
    assert low.loc["Belobra", "mean_players"] == 10
    assert low.loc["Antica", "world_mean"] == 200
    assert low.loc["Belobra", "world_mean"] == 20
    assert low.loc["Antica", "relative_mean_percent"] == 50
    assert low.loc["Belobra", "relative_mean_percent"] == 50
    assert low.loc["Antica", "samples"] == 1
    assert low.loc["Antica", "days_observed"] == 1


def test_relative_comparison_does_not_impute_or_divide_by_zero() -> None:
    series = _series(["2026-09-25T10:30:00Z"], [0])
    patterns = population_patterns(series, "UTC")

    profile = comparison_profile(patterns.hourly, patterns.observations, min_dates=2)

    assert profile.iloc[0]["mean_players"] == 0
    assert pd.isna(profile.iloc[0]["relative_mean_percent"])
    assert pd.isna(profile.iloc[0]["display_mean"])
    assert pd.isna(profile.iloc[0]["display_relative_percent"])
    with pytest.raises(ValueError, match="positive"):
        comparison_profile(patterns.hourly, patterns.observations, min_dates=0)


def test_relative_time_series_preserves_absolute_observations() -> None:
    antica = _series(["2026-09-25T10:30:00Z", "2026-09-25T11:30:00Z"], [100, 300])
    belobra = antica.assign(world="Belobra", players_online=[0, 0])
    source = pd.concat([antica, belobra], ignore_index=True)

    relative = relative_population_series(source)

    assert relative.loc[relative["world"] == "Antica", "relative_mean_percent"].tolist() == [
        50,
        150,
    ]
    assert relative.loc[relative["world"] == "Antica", "world_mean"].eq(200).all()
    assert relative.loc[relative["world"] == "Belobra", "relative_mean_percent"].isna().all()
    pd.testing.assert_frame_equal(source, pd.concat([antica, belobra], ignore_index=True))
