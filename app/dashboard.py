"""Overview page for Tibia World Population Analytics."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.common import configure_page, render_sidebar
from src.analytics import latest_world_table, overview_kpis, total_population_series

configure_page("Overview")
context = render_sidebar()

st.title("Tibia World Population Analytics")
st.caption(
    "Historical population snapshots collected from the public TibiaData API. "
    "The project is independent and unofficial."
)

kpis = overview_kpis(context.db_path, context.filters)
columns = st.columns(4)
columns[0].metric("Players online now", f"{int(kpis.get('players_now') or 0):,}")
columns[1].metric("Worlds monitored", f"{int(kpis.get('worlds_monitored') or 0):,}")
columns[2].metric("Average per world", f"{float(kpis.get('average_now') or 0):,.1f}")
latest = pd.to_datetime(kpis.get("latest_at"), utc=True)
columns[3].metric(
    "Last snapshot (UTC)", latest.strftime("%Y-%m-%d %H:%M") if pd.notna(latest) else "—"
)

columns = st.columns(2)
columns[0].metric("Global peak · available last 24h", f"{int(kpis.get('peak_24h') or 0):,}")
columns[1].metric(
    "Global average · available last 24h", f"{float(kpis.get('average_24h') or 0):,.1f}"
)
if int(kpis.get("observations_24h") or 0) < 2:
    st.info(
        "The 24-hour metrics currently contain fewer than two collection instants. "
        "They will become representative as the historical dataset grows."
    )

st.subheader("Total population over time")
series = total_population_series(context.db_path, hours=24 * 30, filters=context.filters)
if series.empty:
    st.info("No data matches the selected filters.")
else:
    figure = px.line(
        series,
        x="observed_at",
        y="players_online",
        labels={"observed_at": "Observed at (UTC)", "players_online": "Players online"},
    )
    figure.update_layout(hovermode="x unified", height=430)
    st.plotly_chart(figure, width="stretch")

st.subheader("Worlds at the latest snapshot")
worlds = latest_world_table(context.db_path, context.filters)
if worlds.empty:
    st.info("No worlds match the selected filters.")
else:
    display = worlds.rename(
        columns={
            "world": "World",
            "region": "Region",
            "pvp_type": "PvP Type",
            "battleye_protected": "BattlEye",
            "players_now": "Players Now",
            "average_24h": "Average 24h",
            "peak_24h": "Peak 24h",
            "premium_only": "Premium Only",
            "transfer_type": "Transfer Type",
            "status": "Status",
        }
    )
    display["BattlEye"] = display["BattlEye"].map({1: "Yes", 0: "No"}).fillna("Unknown")
    display["Premium Only"] = display["Premium Only"].map({1: "Yes", 0: "No"}).fillna("Unknown")
    display["Average 24h"] = display["Average 24h"].round(1)
    st.dataframe(
        display[
            [
                "World",
                "Region",
                "PvP Type",
                "BattlEye",
                "Players Now",
                "Average 24h",
                "Peak 24h",
                "Status",
                "Premium Only",
                "Transfer Type",
            ]
        ],
        hide_index=True,
        width="stretch",
    )
