"""Compare population series and descriptive statistics across worlds."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from app.common import configure_page, history_notice, render_sidebar
from src.analytics import list_worlds, world_series, world_statistics

configure_page("Compare Worlds")
context = render_sidebar()
st.title("Compare Worlds")

world_names = list_worlds(context.db_path, context.filters)
if not world_names:
    st.info("No worlds match the selected filters.")
    st.stop()

controls = st.columns([2, 1])
default_worlds = world_names[: min(3, len(world_names))]
selected_worlds = controls[0].multiselect(
    "Worlds", world_names, default=default_worlds, max_selections=8
)
period_label = controls[1].selectbox("Period", ("24 hours", "7 days", "30 days"))
hours = {"24 hours": 24, "7 days": 24 * 7, "30 days": 24 * 30}[period_label]

if not selected_worlds:
    st.info("Select at least one world to compare.")
    st.stop()

series = world_series(context.db_path, selected_worlds, hours=hours)
statistics = world_statistics(context.db_path, selected_worlds, hours=hours)
if series.empty or statistics.empty:
    st.info("No observations are available for this selection.")
    st.stop()

shortest = statistics.sort_values("first_observed_at", ascending=False).iloc[0]
history_notice(shortest["first_observed_at"], shortest["last_observed_at"], hours)

figure = px.line(
    series,
    x="observed_at",
    y="players_online",
    color="world",
    labels={
        "observed_at": "Observed at (UTC)",
        "players_online": "Players online",
        "world": "World",
    },
)
figure.update_layout(hovermode="x unified", height=540)
st.plotly_chart(figure, width="stretch")

st.subheader("Descriptive statistics")
display = statistics.rename(
    columns={
        "world": "World",
        "samples": "Samples",
        "current": "Current",
        "mean": "Mean",
        "median": "Median",
        "minimum": "Minimum",
        "maximum": "Maximum",
        "std_dev": "Std. Dev.",
        "p10": "P10",
        "p25": "P25",
        "p75": "P75",
        "p90": "P90",
        "peak_at": "Peak At (UTC)",
    }
)
numeric_columns = ["Mean", "Median", "Std. Dev.", "P10", "P25", "P75", "P90"]
display[numeric_columns] = display[numeric_columns].round(1)
st.dataframe(
    display[
        [
            "World",
            "Samples",
            "Current",
            "Mean",
            "Median",
            "Minimum",
            "Maximum",
            "Std. Dev.",
            "P10",
            "P25",
            "P75",
            "P90",
            "Peak At (UTC)",
        ]
    ],
    hide_index=True,
    width="stretch",
)
