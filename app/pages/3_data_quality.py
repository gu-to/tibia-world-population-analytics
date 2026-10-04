"""Make collection gaps, freshness, and per-world coverage visible."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from app.common import configure_page, format_age, render_sidebar
from src.analytics import list_worlds
from src.health import DEFAULT_REPOSITORY, compare_workflow_runs, fetch_workflow_runs
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

st.subheader("Collection health")
st.caption(
    "Local SQLite contains successful recorded collections, not failed or never-started "
    "workflow runs. Check GitHub on demand to distinguish visible failures from missing "
    "workflow executions. This does not alter the database."
)
if st.button("Compare with GitHub Actions"):
    repository = os.getenv("TIBIA_GITHUB_REPO", DEFAULT_REPOSITORY)
    try:
        runs = fetch_workflow_runs(repository, start, now, token=os.getenv("GITHUB_TOKEN"))
        comparison = compare_workflow_runs(report, runs)
    except (ValueError, requests.RequestException) as exc:
        st.warning(f"GitHub comparison unavailable: {exc}. Local coverage remains valid.")
    else:
        health_columns = st.columns(4)
        health_columns[0].metric("Scheduled runs visible", comparison.scheduled_runs)
        health_columns[1].metric("Successful runs", comparison.successful_runs)
        health_columns[2].metric("Failed runs", comparison.failed_runs)
        health_columns[3].metric("Slots without visible run", len(comparison.no_visible_run_slots))
        st.caption(
            f"{len(comparison.success_without_nearby_audit)} successful run(s) without a nearby "
            f"local audit; {len(comparison.newer_than_local_sync)} run(s) newer than the local "
            f"sync; {comparison.cancelled_runs} cancelled and {comparison.pending_runs} pending. "
            "A successful rerun can legitimately add no new audit row."
        )
        if comparison.failed_run_ids:
            links = ", ".join(
                f"[#{run_id}](https://github.com/{repository}/actions/runs/{run_id})"
                for run_id in comparison.failed_run_ids
            )
            st.warning(f"Failed workflow runs to inspect: {links}")
        if comparison.no_visible_run_slots:
            st.info(
                "No GitHub run is visible for some inferred XX:30 slots. Delayed runs can be "
                "assigned to a later slot, so this identifies a scheduling gap to investigate, "
                "not its proven cause. Missing population remains missing, never zero."
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
