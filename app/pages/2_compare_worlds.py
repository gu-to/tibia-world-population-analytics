"""Compare population series and descriptive statistics across worlds."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.common import (
    configure_page,
    coverage_notice,
    history_notice,
    population_line_chart,
    render_sidebar,
)
from src.analytics import list_worlds, world_series, world_statistics
from src.monitoring import trailing_coverage
from src.patterns import (
    comparison_profile,
    display_series_timezone,
    population_patterns,
    relative_population_series,
)

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
coverage = None if context.is_demo else trailing_coverage(context.db_path, hours)
if coverage is not None:
    coverage_notice(coverage, selected_worlds)

scale = st.radio(
    "Comparison scale", ("Players online", "% of each world's observed mean"), horizontal=True
)
relative = scale.startswith("%")
plotted_series = display_series_timezone(series, context.timezone)
if relative:
    plotted_series = relative_population_series(plotted_series)
    zero_worlds = sorted(plotted_series.loc[plotted_series["world_mean"] <= 0, "world"].unique())
    if zero_worlds:
        st.warning(
            "Relative values are undefined for worlds with a zero observed mean: "
            + ", ".join(zero_worlds)
        )
    plotted_series = plotted_series.assign(players_online=plotted_series["relative_mean_percent"])
    st.info(
        "100% is each world's mean across its own observed snapshots in this period. "
        "This compares observed pattern shape, not absolute population or missing hours."
    )

figure = population_line_chart(
    plotted_series,
    cadence_minutes=5 if context.is_demo else 60,
    height=540,
    group="world",
    labels={
        "observed_at": f"Observed at ({context.timezone})",
        "players_online": "% of world's observed mean" if relative else "Players online",
        "world": "World",
    },
)
st.plotly_chart(figure, width="stretch")
st.caption(
    "Dashed connections bridge unobserved intervals for each world. Missing hours "
    "remain missing and are excluded from statistics."
)

st.subheader("Descriptive statistics")
display = statistics.rename(
    columns={
        "world": "World",
        "samples": "Samples",
        "current": "Last Observed",
        "mean": "Mean",
        "median": "Median",
        "minimum": "Minimum",
        "maximum": "Maximum",
        "std_dev": "Std. Dev.",
        "p10": "P10",
        "p25": "P25",
        "p75": "P75",
        "p90": "P90",
        "peak_at": f"Peak At ({context.timezone})",
    }
)
if coverage is not None:
    expected = len(coverage.expected_slots)
    display["Observed Slots"] = display["World"].map(
        lambda world: len(coverage.world_slots.get(world, frozenset()))
    )
    display["Coverage"] = display["Observed Slots"].map(
        lambda observed: f"{observed / expected:.0%}" if expected else "—"
    )
numeric_columns = ["Mean", "Median", "Std. Dev.", "P10", "P25", "P75", "P90"]
display[numeric_columns] = display[numeric_columns].round(1)
display[f"Peak At ({context.timezone})"] = (
    pd.to_datetime(display[f"Peak At ({context.timezone})"], utc=True)
    .dt.tz_convert(context.timezone)
    .dt.strftime("%Y-%m-%d %H:%M")
)
st.dataframe(
    display[
        [
            "World",
            "Samples",
            "Last Observed",
            *(["Observed Slots", "Coverage"] if coverage is not None else []),
            "Mean",
            "Median",
            "Minimum",
            "Maximum",
            "Std. Dev.",
            "P10",
            "P25",
            "P75",
            "P90",
            f"Peak At ({context.timezone})",
        ]
    ],
    hide_index=True,
    width="stretch",
)

st.subheader(f"Observed patterns · {context.timezone}")
patterns = population_patterns(series, context.timezone)
st.caption(
    "Comparison uses collected samples only. Hover over each point for sample and distinct-day "
    "support; missing hours/days have no inferred population. Uneven collection may bias means."
)
if period_label == "24 hours":
    st.info("A 24-hour window is too short to compare recurring weekday patterns.")
else:
    minimum_dates = st.slider(
        "Minimum distinct dates per point",
        min_value=1,
        max_value=4,
        value=1,
        help="Points with fewer represented local dates are hidden, not estimated.",
    )
    value_column = "display_relative_percent" if relative else "display_mean"
    value_label = "% of world's observed mean" if relative else "Observed mean (players)"
    hourly_profile = comparison_profile(
        patterns.hourly, patterns.observations, min_dates=minimum_dates
    )
    weekday_profile = comparison_profile(
        patterns.weekday, patterns.observations, min_dates=minimum_dates
    )
    hidden_hourly = int((~hourly_profile["meets_min_dates"]).sum())
    hidden_weekday = int((~weekday_profile["meets_min_dates"]).sum())
    st.caption(
        f"Support filter hides {hidden_hourly} hourly and {hidden_weekday} weekday points "
        f"with fewer than {minimum_dates} distinct local date(s). The underlying observations "
        "and exported summaries are unchanged. A point supported by one date is only a "
        "description of that date, not a recurring pattern."
    )
    hourly_visible = hourly_profile.dropna(subset=[value_column])
    if hourly_visible.empty:
        st.info(
            "No hourly profile point meets this selection. "
            "Lower the date minimum or collect more data."
        )
    hourly = px.scatter(
        hourly_visible,
        x="hour",
        y=value_column,
        color="world",
        custom_data=["mean_players", "samples", "days_observed", "world_mean"],
        labels={
            "hour": f"Hour ({context.timezone})",
            value_column: value_label,
            "world": "World",
        },
    )
    hourly.update_traces(
        hovertemplate=(
            "%{fullData.name}<br>%{x}:00<br>Shown value: %{y:.1f}"
            "<br>Observed mean: %{customdata[0]:.1f} players"
            "<br>World baseline: %{customdata[3]:.1f} players"
            "<br>Samples: %{customdata[1]}"
            "<br>Distinct dates: %{customdata[2]}<extra></extra>"
        )
    )
    hourly.update_xaxes(tickmode="linear", dtick=2, range=[-0.5, 23.5])
    hourly.update_layout(height=420)
    st.plotly_chart(hourly, width="stretch")

    weekday_visible = weekday_profile.dropna(subset=[value_column])
    if weekday_visible.empty:
        st.info(
            "No weekday profile point meets this selection. "
            "Lower the date minimum or collect more data."
        )
    weekday = px.scatter(
        weekday_visible,
        x="weekday_index",
        y=value_column,
        color="world",
        custom_data=["mean_players", "samples", "days_observed", "weekday", "world_mean"],
        labels={
            "weekday_index": "Local weekday",
            value_column: value_label,
            "world": "World",
        },
    )
    weekday.update_traces(
        hovertemplate=(
            "%{fullData.name}<br>%{customdata[3]}<br>Shown value: %{y:.1f}"
            "<br>Observed mean: %{customdata[0]:.1f} players"
            "<br>World baseline: %{customdata[4]:.1f} players"
            "<br>Samples: %{customdata[1]}"
            "<br>Distinct dates: %{customdata[2]}<extra></extra>"
        )
    )
    weekday.update_xaxes(
        tickmode="array",
        tickvals=list(range(7)),
        ticktext=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    )
    weekday.update_layout(height=420)
    st.plotly_chart(weekday, width="stretch")

    with st.expander("Download selected data"):
        st.download_button(
            "Download observed snapshots (CSV)",
            data=series.to_csv(index=False).encode("utf-8"),
            file_name=f"world_observations_{hours}h.csv",
            mime="text/csv",
        )
        st.download_button(
            "Download hourly profile with support (CSV)",
            data=hourly_profile.drop(columns=["display_mean", "display_relative_percent"])
            .to_csv(index=False)
            .encode("utf-8"),
            file_name=f"world_hourly_profile_{hours}h.csv",
            mime="text/csv",
        )
        st.caption(
            "Snapshot timestamps are UTC. Profile hours use the selected display timezone. "
            "The profile includes all observed bins and their sample/date counts, including "
            "points hidden by the chart support filter."
        )

    with st.expander("View sample support by hour and weekday"):
        support_columns = st.columns(2)
        hourly_support = patterns.hourly[["world", "hour", "samples", "days_observed"]].rename(
            columns={
                "world": "World",
                "hour": "Hour",
                "samples": "Samples",
                "days_observed": "Distinct dates",
            }
        )
        support_columns[0].dataframe(hourly_support, hide_index=True, width="stretch")
        weekday_support = patterns.weekday[["world", "weekday", "samples", "days_observed"]].rename(
            columns={
                "world": "World",
                "weekday": "Weekday",
                "samples": "Samples",
                "days_observed": "Distinct dates",
            }
        )
        support_columns[1].dataframe(weekday_support, hide_index=True, width="stretch")
