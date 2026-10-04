"""Explore descriptive statistics for one Tibia world."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from app.common import (
    configure_page,
    coverage_notice,
    format_age,
    history_notice,
    population_line_chart,
    render_sidebar,
)
from src.analytics import list_worlds, world_series, world_statistics
from src.monitoring import trailing_coverage
from src.quality import parse_utc

configure_page("World Explorer")
context = render_sidebar()
st.title("World Explorer")

world_names = list_worlds(context.db_path, context.filters)
if not world_names:
    st.info("No worlds match the selected filters.")
    st.stop()

controls = st.columns([2, 1])
selected_world = controls[0].selectbox("World", world_names)
period_label = controls[1].selectbox("Period", ("24 hours", "7 days", "30 days"))
hours = {"24 hours": 24, "7 days": 24 * 7, "30 days": 24 * 30}[period_label]

series = world_series(context.db_path, [selected_world], hours=hours)
statistics = world_statistics(context.db_path, [selected_world], hours=hours)
if series.empty or statistics.empty:
    st.info("No observations are available for this world and period.")
    st.stop()

row = statistics.iloc[0]
history_notice(row["first_observed_at"], row["last_observed_at"], hours)
if not context.is_demo:
    coverage_notice(trailing_coverage(context.db_path, hours), [selected_world])

first_row = st.columns(5)
first_row[0].metric("Last observed", f"{int(row['current']):,}")
first_row[1].metric("Mean", f"{row['mean']:.1f}")
first_row[2].metric("Median", f"{row['median']:.1f}")
first_row[3].metric("Minimum", f"{int(row['minimum']):,}")
first_row[4].metric("Maximum", f"{int(row['maximum']):,}")

second_row = st.columns(5)
std_value = row["std_dev"]
second_row[0].metric("Std. deviation", "—" if pd.isna(std_value) else f"{std_value:.1f}")
second_row[1].metric("P10", f"{row['p10']:.1f}")
second_row[2].metric("P25", f"{row['p25']:.1f}")
second_row[3].metric("P75", f"{row['p75']:.1f}")
second_row[4].metric("P90", f"{row['p90']:.1f}")

peak_at = pd.to_datetime(row["peak_at"], utc=True)
age = format_age(datetime.now(UTC) - parse_utc(row["last_observed_at"]))
st.caption(
    f"{int(row['samples']):,} observed samples · last sample {age} old · "
    f"observed peak at {peak_at.strftime('%Y-%m-%d %H:%M UTC')}"
)

figure = population_line_chart(
    series,
    cadence_minutes=5 if context.is_demo else 60,
    height=520,
    labels={"observed_at": "Observed at (UTC)", "players_online": "Players online"},
)
st.plotly_chart(figure, width="stretch")
st.caption(
    "Dashed connections cross hours without observations; they are visual guides, not "
    "interpolated population data."
)
