"""Make collection gaps, freshness, and per-world coverage visible."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.common import configure_page, format_age, render_sidebar
from src.analytics import list_worlds
from src.monitoring import coverage_window

configure_page("Data Quality")
context = render_sidebar()
st.title("Data Quality & Coverage")

if context.is_demo:
    st.info(
        "Hourly collection coverage applies to real public data only. The synthetic demo "
        "has a different five-minute cadence and is not a measure of pipeline reliability."
    )
    st.stop()

days = st.selectbox("UTC calendar window", (7, 30), format_func=lambda value: f"Last {value} days")
now = datetime.now(UTC)
start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=days - 1)
report = coverage_window(context.db_path, start, now)
expected = len(report.expected_slots)
observed = len(report.observed_slots)
last = report.last_observed_at

columns = st.columns(4)
columns[0].metric("Hourly coverage", f"{observed / expected:.1%}" if expected else "—")
columns[1].metric("Observed / expected slots", f"{observed:,} / {expected:,}")
columns[2].metric("Missing slots", f"{len(report.missing_slots):,}")
columns[3].metric("Latest data age", format_age(now - last) if last else "—")
st.caption(
    "Last source observation: "
    + (last.strftime("%Y-%m-%d %H:%M:%S UTC") if last else "none")
    + f" · {report.successful_runs:,} successful runs in this window"
)
st.info(
    "Red means a scheduled observation is missing; blank cells are before monitoring began "
    "or are future hours not due yet. "
    "Neither represents zero players. "
    "Coverage excludes hours before the first locally recorded collection. "
    "Slots are inferred from collection time as the preceding XX:30 boundary; when GitHub "
    "delays a run beyond another boundary, its intended slot cannot be recovered. "
    "Global metrics ignore metadata filters; the per-world table respects them."
)

st.subheader("Collection calendar · UTC")
calendar_days = [(start + timedelta(days=index)).date() for index in range(days)]
matrix: dict[str, list[int | None]] = {day.isoformat(): [None] * 24 for day in calendar_days}
for slot in report.expected_slots:
    matrix[slot.date().isoformat()][slot.hour] = int(slot in report.observed_slots)
figure = go.Figure(
    data=go.Heatmap(
        x=list(range(24)),
        y=list(matrix),
        z=list(matrix.values()),
        text=[
            [
                "Observed"
                if value == 1
                else "Missing"
                if value == 0
                else "Outside monitored window"
                for value in row
            ]
            for row in matrix.values()
        ],
        zmin=0,
        zmax=1,
        colorscale=[[0, "#d77b7b"], [0.499, "#d77b7b"], [0.5, "#4aa583"], [1, "#4aa583"]],
        colorbar=dict(tickvals=[0, 1], ticktext=["Missing", "Observed"]),
        hovertemplate="%{y} · %{x}:30 UTC<br>%{text}<extra></extra>",
    )
)
figure.update_layout(xaxis_title="Hour (UTC)", yaxis_title="Day (UTC)", height=max(290, days * 23))
st.plotly_chart(figure, width="stretch")

st.subheader("Coverage by world")
selected_worlds = set(list_worlds(context.db_path, context.filters))
rows = [
    {
        "World": world,
        "Observed slots": len(slots),
        "Expected slots": expected,
        "Coverage": len(slots) / expected if expected else None,
        "Last observed (UTC)": (
            report.world_last_seen[world].strftime("%Y-%m-%d %H:%M")
            if report.world_last_seen[world]
            else "—"
        ),
    }
    for world, slots in report.world_slots.items()
    if world in selected_worlds
]
table = pd.DataFrame(rows)
if table.empty:
    st.info("No worlds match the selected metadata filters.")
else:
    table["Coverage (%)"] = table["Coverage"] * 100
    st.dataframe(
        table.drop(columns="Coverage").sort_values(["Coverage (%)", "World"]),
        column_config={
            "Coverage (%)": st.column_config.ProgressColumn(
                "Coverage (%)", min_value=0, max_value=100, format="%.1f%%"
            )
        },
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "The denominator is the full selected window for every world. A world introduced "
        "mid-window therefore has lower reported coverage; metadata changes are not inferred."
    )
