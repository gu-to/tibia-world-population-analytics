"""Explore observed population distributions without inferring missing hours."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.common import configure_page, coverage_notice, history_notice, render_sidebar
from src.analytics import (
    hourly_population_profile,
    list_worlds,
    weekday_weekend_summary,
    world_series,
    world_statistics,
)
from src.monitoring import trailing_coverage

configure_page("Population Patterns")
context = render_sidebar()
st.title("Population Patterns")
st.caption(
    "Exploratory descriptions of collected samples only. All hours and weekday labels are UTC; "
    "missing observations are neither zero nor interpolated."
)

world_names = list_worlds(context.db_path, context.filters)
if not world_names:
    st.info("No worlds match the selected filters.")
    st.stop()

controls = st.columns([2, 1])
selected_world = controls[0].selectbox("World", world_names)
period_label = controls[1].selectbox("Period", ("7 days", "30 days"), index=1)
hours = {"7 days": 24 * 7, "30 days": 24 * 30}[period_label]

series = world_series(context.db_path, [selected_world], hours=hours)
statistics = world_statistics(context.db_path, [selected_world], hours=hours)
if series.empty or statistics.empty:
    st.info("No observations are available for this world and period.")
    st.stop()

stats = statistics.iloc[0]
history_notice(stats["first_observed_at"], stats["last_observed_at"], hours)
coverage = None if context.is_demo else trailing_coverage(context.db_path, hours)
if coverage is not None:
    coverage_notice(coverage, [selected_world])

columns = st.columns(4)
columns[0].metric("Observed samples", f"{int(stats['samples']):,}")
columns[1].metric("Observed mean", f"{stats['mean']:.1f}")
columns[2].metric("Observed median", f"{stats['median']:.1f}")
if coverage is None:
    columns[3].metric("Hourly coverage", "Demo — not applicable")
else:
    expected = len(coverage.expected_slots)
    observed = len(coverage.world_slots.get(selected_world, frozenset()))
    columns[3].metric("Hourly coverage", f"{observed / expected:.1%}" if expected else "—")

st.subheader("Distribution of observed population")
histogram = px.histogram(
    series,
    x="players_online",
    nbins=24,
    labels={"players_online": "Players online", "count": "Observed samples"},
)
histogram.update_layout(height=350, yaxis_title="Observed samples")
st.plotly_chart(histogram, width="stretch")
st.caption(
    "Each sample counts once in this histogram; bar height is not the number of hours "
    "the world spent at that population."
)

st.subheader("Population by hour · UTC")
profile = hourly_population_profile(context.db_path, selected_world, hours=hours)
if profile.empty:
    st.info("No hourly samples are available in this period.")
else:
    charts = st.columns(2)
    hourly_mean = px.bar(
        profile,
        x="hour_utc",
        y="mean_players",
        custom_data=["samples"],
        labels={"hour_utc": "Hour (UTC)", "mean_players": "Observed-sample mean"},
    )
    hourly_mean.update_traces(
        hovertemplate="%{x}:00 UTC<br>Mean: %{y:.1f}<br>Samples: %{customdata[0]}<extra></extra>"
    )
    hourly_mean.update_xaxes(tickmode="linear", dtick=2, range=[-0.5, 23.5])
    hourly_mean.update_layout(height=360)
    charts[0].plotly_chart(hourly_mean, width="stretch")

    sample_counts = px.bar(
        profile,
        x="hour_utc",
        y="samples",
        labels={"hour_utc": "Hour (UTC)", "samples": "Observed samples"},
    )
    sample_counts.update_xaxes(tickmode="linear", dtick=2, range=[-0.5, 23.5])
    sample_counts.update_layout(height=360)
    charts[1].plotly_chart(sample_counts, width="stretch")
    st.caption(
        f"{len(profile)}/24 UTC hours contain observations. The mean uses only those samples; "
        "a missing hour has no bar, not a population of zero. Compare the sample counts "
        "before interpreting apparent peaks."
    )

st.subheader("Weekday versus weekend · UTC")
summary = weekday_weekend_summary(context.db_path, selected_world, hours=hours)
if summary.empty:
    st.info("No weekday or weekend samples are available in this period.")
else:
    display = summary.rename(
        columns={
            "day_type": "Day type (UTC)",
            "samples": "Samples",
            "mean_players": "Observed mean",
            "minimum": "Minimum",
            "maximum": "Maximum",
        }
    )
    display["Observed mean"] = display["Observed mean"].round(1)
    st.dataframe(display, hide_index=True, width="stretch")
    if len(summary) < 2:
        st.info("Only one day type has observations; a comparison is not yet possible.")
    else:
        observed = series.assign(
            day_type=pd.Categorical(
                series["observed_at"].dt.dayofweek.map(
                    lambda day: "Weekend" if day >= 5 else "Weekday"
                ),
                categories=["Weekday", "Weekend"],
                ordered=True,
            )
        )
        comparison = px.box(
            observed,
            x="day_type",
            y="players_online",
            points="all",
            labels={"day_type": "Day type (UTC)", "players_online": "Players online"},
        )
        comparison.update_layout(height=390)
        st.plotly_chart(comparison, width="stretch")
    st.caption(
        "Weekday/weekend summaries weight every collected snapshot equally. Uneven collection "
        "times can bias the comparison; these figures are descriptive, not evidence of a "
        "stable behavioral difference."
    )
