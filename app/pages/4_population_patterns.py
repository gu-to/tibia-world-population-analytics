"""Explore observed population distributions without inferring missing hours."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.common import configure_page, coverage_notice, history_notice, render_sidebar
from src.analytics import list_worlds, world_series, world_statistics
from src.monitoring import trailing_coverage
from src.patterns import WEEKDAYS, population_patterns, weekday_hour_grid

configure_page("Population Patterns")
context = render_sidebar()
st.title("Population Patterns")
st.caption(
    f"Exploratory descriptions of collected samples only. Hours and weekdays use "
    f"{context.timezone}; stored source timestamps remain UTC. Missing observations are "
    "neither zero nor interpolated."
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
patterns = population_patterns(series, context.timezone)
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

st.subheader(f"Population by hour · {context.timezone}")
profile = patterns.hourly
if profile.empty:
    st.info("No hourly samples are available in this period.")
else:
    charts = st.columns(2)
    hourly_mean = px.bar(
        profile,
        x="hour",
        y="mean_players",
        custom_data=["samples", "days_observed"],
        labels={"hour": f"Hour ({context.timezone})", "mean_players": "Observed-sample mean"},
    )
    hourly_mean.update_traces(
        hovertemplate=(
            "%{x}:00<br>Mean: %{y:.1f}<br>Samples: %{customdata[0]}"
            "<br>Distinct days: %{customdata[1]}<extra></extra>"
        )
    )
    hourly_mean.update_xaxes(tickmode="linear", dtick=2, range=[-0.5, 23.5])
    hourly_mean.update_layout(height=360)
    charts[0].plotly_chart(hourly_mean, width="stretch")

    sample_counts = px.bar(
        profile,
        x="hour",
        y="samples",
        custom_data=["days_observed"],
        labels={"hour": f"Hour ({context.timezone})", "samples": "Observed samples"},
    )
    sample_counts.update_traces(
        hovertemplate="%{x}:00<br>Samples: %{y}<br>Distinct days: %{customdata[0]}<extra></extra>"
    )
    sample_counts.update_xaxes(tickmode="linear", dtick=2, range=[-0.5, 23.5])
    sample_counts.update_layout(height=360)
    charts[1].plotly_chart(sample_counts, width="stretch")
    st.caption(
        f"{len(profile)}/24 local hours contain observations. The mean uses only those samples; "
        "a missing hour has no bar, not a population of zero. Compare the sample counts "
        "and distinct represented dates before interpreting apparent peaks."
    )

st.subheader(f"Population by weekday · {context.timezone}")
weekday = patterns.weekday
if weekday.empty:
    st.info("No weekday samples are available in this period.")
else:
    weekday_charts = st.columns(2)
    day_mean = px.bar(
        weekday,
        x="weekday_index",
        y="mean_players",
        custom_data=["samples", "days_observed", "weekday"],
        labels={"weekday_index": "Local weekday", "mean_players": "Observed-sample mean"},
    )
    day_mean.update_traces(
        hovertemplate=(
            "%{customdata[2]}<br>Mean: %{y:.1f}<br>Samples: %{customdata[0]}"
            "<br>Distinct dates: %{customdata[1]}<extra></extra>"
        )
    )
    day_mean.update_xaxes(tickmode="array", tickvals=list(range(7)), ticktext=list(WEEKDAYS))
    day_mean.update_layout(height=360)
    weekday_charts[0].plotly_chart(day_mean, width="stretch")

    day_counts = px.bar(
        weekday,
        x="weekday_index",
        y="samples",
        custom_data=["days_observed", "weekday"],
        labels={"weekday_index": "Local weekday", "samples": "Observed samples"},
    )
    day_counts.update_traces(
        hovertemplate=(
            "%{customdata[1]}<br>Samples: %{y}<br>Distinct dates: %{customdata[0]}<extra></extra>"
        )
    )
    day_counts.update_xaxes(tickmode="array", tickvals=list(range(7)), ticktext=list(WEEKDAYS))
    day_counts.update_layout(height=360)
    weekday_charts[1].plotly_chart(day_counts, width="stretch")
    st.caption(
        f"{len(weekday)}/7 local weekdays are represented. Each mean weights snapshots, "
        "not entire days. Compare samples and distinct dates before interpreting differences."
    )

st.subheader(f"Weekday × hour · {context.timezone}")
minimum_dates = st.slider(
    "Minimum distinct dates per cell",
    min_value=1,
    max_value=4,
    value=1,
    help="Cells below this support remain in the export but their population color is hidden.",
)
grid = weekday_hour_grid(patterns.weekday_hour, min_dates=minimum_dates)
values = grid["display_mean"].to_numpy().reshape(7, 24)
counts = grid["samples"].to_numpy().reshape(7, 24)
support = grid[["samples", "days_observed"]].to_numpy().reshape(7, 24, 2)
heatmaps = st.columns(2)
population_heatmap = go.Figure(
    go.Heatmap(
        z=values,
        x=list(range(24)),
        y=list(WEEKDAYS),
        customdata=support,
        colorscale="Viridis",
        colorbar={"title": "Players"},
        hoverongaps=False,
        hovertemplate=(
            "%{y} %{x}:00<br>Observed mean: %{z:.1f}"
            "<br>Samples: %{customdata[0]}<br>Distinct dates: %{customdata[1]}<extra></extra>"
        ),
    )
)
population_heatmap.update_layout(height=360, title="Observed population mean")
population_heatmap.update_xaxes(dtick=2, title=f"Hour ({context.timezone})")
heatmaps[0].plotly_chart(population_heatmap, width="stretch")
count_heatmap = go.Figure(
    go.Heatmap(
        z=counts,
        x=list(range(24)),
        y=list(WEEKDAYS),
        customdata=support,
        colorscale="Blues",
        colorbar={"title": "Samples"},
        hovertemplate="%{y} %{x}:00<br>Samples: %{z}<br>Dates: %{customdata[1]}<extra></extra>",
    )
)
count_heatmap.update_layout(height=360, title="Observation count")
count_heatmap.update_xaxes(dtick=2, title=f"Hour ({context.timezone})")
heatmaps[1].plotly_chart(count_heatmap, width="stretch")
observed_cells = int((grid["samples"] > 0).sum())
masked_cells = int(((grid["samples"] > 0) & ~grid["meets_min_dates"]).sum())
single_date_cells = int((grid["days_observed"] == 1).sum())
if observed_cells == masked_cells:
    st.info("No weekday-hour cell meets this date minimum. Lower it or collect more dates.")
st.caption(
    f"{observed_cells}/168 weekday-hour cells have observations; {single_date_cells} rest on "
    f"only one date; {masked_cells} are hidden by the selected minimum. Blank population "
    "cells are missing or masked, never zero. The count chart uses zero only to mean "
    "zero collected samples. Uneven collection times can bias apparent peaks."
)
st.download_button(
    "Download weekday-hour support (CSV)",
    data=grid.drop(columns="display_mean").to_csv(index=False).encode("utf-8"),
    file_name=f"{selected_world.lower().replace(' ', '_')}_weekday_hour_{hours}h.csv",
    mime="text/csv",
)

st.subheader(f"Weekday versus weekend · {context.timezone}")
summary = patterns.day_type
if summary.empty:
    st.info("No weekday or weekend samples are available in this period.")
else:
    display = summary.rename(
        columns={
            "day_type": f"Day type ({context.timezone})",
            "samples": "Samples",
            "days_observed": "Distinct dates",
            "mean_players": "Observed mean",
            "median_players": "Observed median",
            "minimum": "Minimum",
            "maximum": "Maximum",
        }
    )
    display[["Observed mean", "Observed median"]] = display[
        ["Observed mean", "Observed median"]
    ].round(1)
    display = display.drop(columns="world")
    st.dataframe(display, hide_index=True, width="stretch")
    if len(summary) < 2:
        st.info("Only one day type has observations; a comparison is not yet possible.")
    else:
        observed = patterns.observations.assign(
            day_type=pd.Categorical(
                patterns.observations["day_type"],
                categories=["Weekday", "Weekend"],
                ordered=True,
            )
        )
        comparison = px.box(
            observed,
            x="day_type",
            y="players_online",
            points="all",
            labels={
                "day_type": f"Day type ({context.timezone})",
                "players_online": "Players online",
            },
        )
        comparison.update_layout(height=390)
        st.plotly_chart(comparison, width="stretch")
    st.caption(
        "Weekday/weekend summaries weight every collected snapshot equally. Uneven collection "
        "times can bias the comparison; these figures are descriptive, not evidence of a "
        "stable behavioral difference."
    )
