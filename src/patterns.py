"""Timezone-aware descriptions of observed population samples only."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@dataclass(frozen=True, slots=True)
class PopulationPatterns:
    """Profiles with both snapshot counts and distinct represented local dates."""

    observations: pd.DataFrame
    hourly: pd.DataFrame
    weekday: pd.DataFrame
    weekday_hour: pd.DataFrame
    day_type: pd.DataFrame


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown IANA timezone: {name}") from exc


def display_series_timezone(series: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Return a chart-only copy with timestamps converted from stored UTC."""
    zone = _zone(timezone)
    displayed = series.copy()
    if not displayed.empty:
        displayed["observed_at"] = pd.to_datetime(displayed["observed_at"], utc=True).dt.tz_convert(
            zone
        )
    return displayed


def _summary(frame: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(by, observed=True, sort=True)
        .agg(
            samples=("players_online", "size"),
            days_observed=("local_date", "nunique"),
            mean_players=("players_online", "mean"),
            median_players=("players_online", "median"),
            minimum=("players_online", "min"),
            maximum=("players_online", "max"),
        )
        .reset_index()
    )


def population_patterns(series: pd.DataFrame, timezone: str) -> PopulationPatterns:
    """Group real samples by local hour/day; never create values for missing times."""
    zone = _zone(timezone)
    required = {"world", "observed_at", "players_online"}
    if not required.issubset(series.columns):
        raise ValueError(f"Series requires columns: {', '.join(sorted(required))}")
    observations = series[["world", "observed_at", "players_online"]].copy()
    observations["local_at"] = pd.to_datetime(observations["observed_at"], utc=True).dt.tz_convert(
        zone
    )
    observations["local_date"] = observations["local_at"].dt.date
    observations["hour"] = observations["local_at"].dt.hour
    observations["weekday_index"] = observations["local_at"].dt.dayofweek
    observations["weekday"] = observations["weekday_index"].map(dict(enumerate(WEEKDAYS)))
    observations["day_type"] = observations["weekday_index"].map(
        lambda weekday: "Weekend" if weekday >= 5 else "Weekday"
    )
    hourly = _summary(observations, ["world", "hour"])
    weekday = _summary(observations, ["world", "weekday_index", "weekday"])
    weekday_hour = _summary(observations, ["world", "weekday_index", "weekday", "hour"])
    day_type = _summary(observations, ["world", "day_type"])
    return PopulationPatterns(observations, hourly, weekday, weekday_hour, day_type)


def weekday_hour_grid(profile: pd.DataFrame, *, min_dates: int = 1) -> pd.DataFrame:
    """Build a one-world display grid; missing population cells stay null."""
    if min_dates < 1:
        raise ValueError("min_dates must be positive")
    if profile.empty or profile["world"].nunique() != 1:
        raise ValueError("A nonempty single-world weekday-hour profile is required")
    grid = pd.MultiIndex.from_product(
        [range(7), range(24)], names=["weekday_index", "hour"]
    ).to_frame(index=False)
    values = profile.drop(columns="weekday").copy()
    grid = grid.merge(values, on=["weekday_index", "hour"], how="left", validate="one_to_one")
    grid["world"] = profile["world"].iloc[0]
    grid["weekday"] = grid["weekday_index"].map(dict(enumerate(WEEKDAYS)))
    grid[["samples", "days_observed"]] = grid[["samples", "days_observed"]].fillna(0).astype(int)
    grid["meets_min_dates"] = grid["days_observed"] >= min_dates
    grid["display_mean"] = grid["mean_players"].where(grid["meets_min_dates"])
    return grid


def comparison_profile(
    profile: pd.DataFrame, observations: pd.DataFrame, *, min_dates: int = 1
) -> pd.DataFrame:
    """Add relative observed means and explicit support without inferring gaps."""
    if min_dates < 1:
        raise ValueError("min_dates must be positive")
    result = profile.copy()
    world_means = observations.groupby("world")["players_online"].mean()
    result["world_mean"] = result["world"].map(world_means)
    result["relative_mean_percent"] = (
        result["mean_players"] / result["world_mean"].where(result["world_mean"] > 0) * 100
    )
    result["meets_min_dates"] = result["days_observed"] >= min_dates
    result["display_mean"] = result["mean_players"].where(result["meets_min_dates"])
    result["display_relative_percent"] = result["relative_mean_percent"].where(
        result["meets_min_dates"]
    )
    return result


def relative_population_series(series: pd.DataFrame) -> pd.DataFrame:
    """Add each world's observed-mean percentage without changing raw populations."""
    result = series.copy()
    world_means = result.groupby("world")["players_online"].mean()
    result["world_mean"] = result["world"].map(world_means)
    result["relative_mean_percent"] = (
        result["players_online"] / result["world_mean"].where(result["world_mean"] > 0) * 100
    )
    return result
