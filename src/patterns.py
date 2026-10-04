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
    day_type = _summary(observations, ["world", "day_type"])
    return PopulationPatterns(observations, hourly, weekday, day_type)
