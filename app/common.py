"""Shared Streamlit controls and presentation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from src.analytics import WorldFilters, data_mode, database_has_data, filter_options
from src.config import database_path


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


def history_notice(first_at: str, last_at: str, requested_hours: int) -> None:
    """Warn when the available window is materially shorter than requested."""
    import pandas as pd

    first = pd.to_datetime(first_at, utc=True)
    last = pd.to_datetime(last_at, utc=True)
    actual_hours = max(0.0, (last - first).total_seconds() / 3600)
    if actual_hours < requested_hours * 0.9:
        st.info(
            f"Limited history: this view requests {requested_hours / 24:g} day(s), "
            f"but only {actual_hours:.1f} hours are available. Statistics describe "
            "the available samples, not the full selected period."
        )
