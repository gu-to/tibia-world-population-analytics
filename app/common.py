"""Shared Streamlit controls and presentation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from src.analytics import (
    WorldFilters,
    data_mode,
    database_has_data,
    filter_options,
    latest_observed_at,
)
from src.config import database_path
from src.monitoring import CoverageWindow
from src.quality import parse_utc


@dataclass(frozen=True, slots=True)
class DashboardContext:
    db_path: Path
    filters: WorldFilters
    is_demo: bool


def configure_page(title: str) -> None:
    st.set_page_config(
        page_title=f"{title} · Tibia World Analytics",
        page_icon="📊",
        layout="wide",
    )


def _boolean_label(value: bool) -> str:
    return "Yes" if value else "No"


def render_sidebar() -> DashboardContext:
    """Render source selection and data-driven global metadata filters."""
    st.sidebar.header("Data source")
    source = st.sidebar.radio(
        "Dataset",
        ("Real snapshots", "Synthetic demo"),
        help="Real and demo data are stored in separate SQLite databases.",
    )
    is_demo = source == "Synthetic demo"
    db_path = database_path(demo=is_demo)

    if not database_has_data(db_path):
        st.warning(
            "No snapshots are available in this dataset yet. "
            + (
                "Run `python -m src.demo_data` to create clearly labeled demo data."
                if is_demo
                else "Run `python -m src.collector` to collect a real snapshot."
            )
        )
        st.caption(f"Expected database: `{db_path}`")
        st.stop()

    mode = data_mode(db_path)
    if mode == "mixed":
        st.error("This database mixes real and synthetic runs. Use separate database files.")
        st.stop()
    if is_demo or mode == "demo":
        st.error(
            "DEMO MODE — Every value shown is synthetic and must not be interpreted "
            "as real Tibia population data."
        )
    else:
        latest_source = latest_observed_at(db_path)
        if latest_source:
            age = datetime.now(UTC) - parse_utc(latest_source)
            st.sidebar.caption(f"Latest source observation: {latest_source}")
            if age > timedelta(hours=2):
                st.sidebar.warning(
                    f"Local data is {format_age(age)} old. Run `python -m src.sync --replace` "
                    "to refresh it from public datasets."
                )

    options = filter_options(db_path)
    st.sidebar.header("Filters")
    locations = st.sidebar.multiselect("Region", options["location"])
    pvp_types = st.sidebar.multiselect("PvP type", options["pvp_type"])
    battleye = st.sidebar.multiselect(
        "BattlEye", options["battleye_protected"], format_func=_boolean_label
    )
    premium = st.sidebar.multiselect(
        "Premium only", options["premium_only"], format_func=_boolean_label
    )
    transfers = st.sidebar.multiselect("Transfer type", options["transfer_type"])
    game_types = st.sidebar.multiselect("Game world type", options["game_world_type"])
    filters = WorldFilters(
        locations=tuple(locations),
        pvp_types=tuple(pvp_types),
        battleye=tuple(battleye),
        premium_only=tuple(premium),
        transfer_types=tuple(transfers),
        game_world_types=tuple(game_types),
    )
    st.sidebar.caption("All timestamps are stored and displayed in UTC.")
    return DashboardContext(db_path=db_path, filters=filters, is_demo=is_demo)


def format_age(age: timedelta) -> str:
    """Display a nonnegative age without suggesting that stale data is live."""
    minutes = max(0, int(age.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} h {minutes % 60} min"
    return f"{hours // 24} d {hours % 24} h"


def history_notice(first_at: str, last_at: str, requested_hours: int) -> None:
    """Warn when the available window is materially shorter than requested."""
    first = pd.to_datetime(first_at, utc=True)
    last = pd.to_datetime(last_at, utc=True)
    actual_hours = max(0.0, (last - first).total_seconds() / 3600)
    if actual_hours < requested_hours * 0.9:
        st.info(
            f"Limited history: this view requests {requested_hours / 24:g} day(s), "
            f"but only {actual_hours:.1f} hours are available. Statistics describe "
            "the available samples, not the full selected period."
        )


def coverage_notice(coverage: CoverageWindow, worlds: list[str]) -> None:
    """Explain sample support for a view, even when its endpoints span the period."""
    expected = len(coverage.expected_slots)
    if not expected:
        st.info("No hourly collection slot has elapsed in this selected window.")
        return
    parts = []
    for world in worlds:
        observed = len(coverage.world_slots.get(world, frozenset()))
        parts.append(f"{world}: {observed}/{expected} ({observed / expected:.0%})")
    st.info(
        "Hourly observation coverage — "
        + "; ".join(parts)
        + ". Statistics use only observed samples; missing hours are not zeros or interpolated."
    )


def with_visual_gaps(
    frame: pd.DataFrame, *, cadence_minutes: int, group: str | None = None
) -> pd.DataFrame:
    """Insert NaN display points across long gaps, leaving stored data unchanged."""
    if frame.empty:
        return frame.copy()
    threshold = pd.Timedelta(minutes=cadence_minutes * 1.5)
    groups = frame.groupby(group, sort=False) if group else [(None, frame)]
    result: list[dict[str, object]] = []
    for _, values in groups:
        previous = None
        for row in values.sort_values("observed_at").to_dict("records"):
            current = pd.Timestamp(row["observed_at"])
            if previous is not None and current - previous > threshold:
                gap = dict(row)
                gap["observed_at"] = previous + (current - previous) / 2
                gap["players_online"] = None
                result.append(gap)
            result.append(row)
            previous = current
    return pd.DataFrame.from_records(result, columns=frame.columns)
