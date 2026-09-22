"""SQL-first read model and descriptive analytics for the dashboard."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.database import connect


@dataclass(frozen=True, slots=True)
class WorldFilters:
    """Optional world metadata filters used by analytical queries."""

    locations: tuple[str, ...] = ()
    pvp_types: tuple[str, ...] = ()
    battleye: tuple[bool, ...] = ()
    premium_only: tuple[bool, ...] = ()
    transfer_types: tuple[str, ...] = ()
    game_world_types: tuple[str, ...] = ()


EMPTY_FILTERS = WorldFilters()


def database_has_data(db_path: Path | str) -> bool:
    """Return whether a valid database has at least one snapshot."""
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        with connect(path) as connection:
            row = connection.execute("SELECT EXISTS(SELECT 1 FROM population_snapshots)").fetchone()
        return bool(row[0])
    except sqlite3.DatabaseError:
        return False


def _in_clause(column: str, values: Sequence[Any]) -> tuple[str, list[Any]]:
    if not values:
        return "", []
    placeholders = ", ".join("?" for _ in values)
    normalized = [int(value) if isinstance(value, bool) else value for value in values]
    return f" AND {column} IN ({placeholders})", normalized


def _filter_sql(filters: WorldFilters, alias: str = "w") -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, values in (
        ("location", filters.locations),
        ("pvp_type", filters.pvp_types),
        ("battleye_protected", filters.battleye),
        ("premium_only", filters.premium_only),
        ("transfer_type", filters.transfer_types),
        ("game_world_type", filters.game_world_types),
    ):
        sql, sql_params = _in_clause(f"{alias}.{column}", values)
        clauses.append(sql)
        params.extend(sql_params)
    return "".join(clauses), params


def latest_observed_at(db_path: Path | str) -> str | None:
    """Return the most recent source observation timestamp."""
    with connect(db_path) as connection:
        row = connection.execute("SELECT MAX(observed_at) FROM collection_runs").fetchone()
    return row[0] if row else None


def data_mode(db_path: Path | str) -> str | None:
    """Return the database mode, rejecting accidental mixed-mode databases."""
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT data_mode FROM collection_runs ORDER BY data_mode"
        ).fetchall()
    modes = [row[0] for row in rows]
    if len(modes) > 1:
        return "mixed"
    return modes[0] if modes else None


def filter_options(db_path: Path | str) -> dict[str, list[Any]]:
    """Load filter values from metadata instead of hardcoding API categories."""
    columns = (
        "location",
        "pvp_type",
        "battleye_protected",
        "premium_only",
        "transfer_type",
        "game_world_type",
    )
    result: dict[str, list[Any]] = {}
    with connect(db_path) as connection:
        for column in columns:
            rows = connection.execute(
                f"SELECT DISTINCT {column} FROM worlds "  # noqa: S608 - fixed allowlist
                f"WHERE {column} IS NOT NULL ORDER BY {column}"
            ).fetchall()
            values = [row[0] for row in rows]
            if column in {"battleye_protected", "premium_only"}:
                values = [bool(value) for value in values]
            result[column] = values
    return result


def overview_kpis(db_path: Path | str, filters: WorldFilters = EMPTY_FILTERS) -> dict[str, Any]:
    """Calculate latest and trailing-24-hour global KPIs in SQL."""
    filter_sql, params = _filter_sql(filters)
    query = f"""
        WITH latest AS (SELECT MAX(observed_at) AS ts FROM collection_runs),
        totals AS (
            SELECT ps.observed_at, SUM(ps.players_online) AS total_players
            FROM population_snapshots ps
            JOIN worlds w ON w.id = ps.world_id
            CROSS JOIN latest
            WHERE datetime(ps.observed_at) >= datetime(latest.ts, '-24 hours')
            {filter_sql}
            GROUP BY ps.observed_at
        )
        SELECT
            latest.ts AS latest_at,
            COALESCE(SUM(CASE WHEN ps.observed_at = latest.ts THEN ps.players_online END), 0)
                AS players_now,
            COUNT(DISTINCT CASE WHEN ps.observed_at = latest.ts THEN ps.world_id END)
                AS worlds_monitored,
            AVG(CASE WHEN ps.observed_at = latest.ts THEN ps.players_online END)
                AS average_now,
            (SELECT MAX(total_players) FROM totals) AS peak_24h,
            (SELECT AVG(total_players) FROM totals) AS average_24h,
            (SELECT COUNT(*) FROM totals) AS observations_24h
        FROM latest
        LEFT JOIN population_snapshots ps ON ps.observed_at = latest.ts
        LEFT JOIN worlds w ON w.id = ps.world_id
        WHERE 1 = 1 {filter_sql}
    """
    with connect(db_path) as connection:
        row = connection.execute(query, params + params).fetchone()
    return dict(row) if row else {}


def latest_world_table(db_path: Path | str, filters: WorldFilters = EMPTY_FILTERS) -> pd.DataFrame:
    """Return latest population plus per-world 24-hour mean and maximum."""
    filter_sql, params = _filter_sql(filters)
    query = f"""
        WITH latest AS (SELECT MAX(observed_at) AS ts FROM collection_runs),
        trailing AS (
            SELECT world_id, AVG(players_online) AS average_24h,
                   MAX(players_online) AS peak_24h, COUNT(*) AS samples_24h
            FROM population_snapshots, latest
            WHERE datetime(observed_at) >= datetime(latest.ts, '-24 hours')
            GROUP BY world_id
        )
        SELECT w.name AS world, w.location AS region, w.pvp_type,
               w.battleye_protected, w.premium_only, w.transfer_type,
               w.game_world_type, ps.status, ps.players_online AS players_now,
               t.average_24h, t.peak_24h, t.samples_24h
        FROM latest
        JOIN population_snapshots ps ON ps.observed_at = latest.ts
        JOIN worlds w ON w.id = ps.world_id
        LEFT JOIN trailing t ON t.world_id = w.id
        WHERE 1 = 1 {filter_sql}
        ORDER BY ps.players_online DESC, w.name
    """
    with connect(db_path) as connection:
        return pd.read_sql_query(query, connection, params=params)


def total_population_series(
    db_path: Path | str,
    *,
    hours: int | None = None,
    filters: WorldFilters = EMPTY_FILTERS,
) -> pd.DataFrame:
    """Aggregate total population by observation time in SQL."""
    filter_sql, params = _filter_sql(filters)
    time_sql = ""
    if hours is not None:
        time_sql = "AND datetime(ps.observed_at) >= datetime(latest.ts, ?)"
        params = [f"-{int(hours)} hours", *params]
    query = f"""
        WITH latest AS (SELECT MAX(observed_at) AS ts FROM collection_runs)
        SELECT ps.observed_at, SUM(ps.players_online) AS players_online
        FROM population_snapshots ps
        JOIN worlds w ON w.id = ps.world_id
        CROSS JOIN latest
        WHERE 1 = 1 {time_sql} {filter_sql}
        GROUP BY ps.observed_at
        ORDER BY ps.observed_at
    """
    with connect(db_path) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    if not frame.empty:
        frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    return frame


def list_worlds(db_path: Path | str, filters: WorldFilters = EMPTY_FILTERS) -> list[str]:
    """List world names matching metadata filters."""
    filter_sql, params = _filter_sql(filters)
    with connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT w.name FROM worlds w WHERE 1 = 1 {filter_sql} ORDER BY w.name",
            params,
        ).fetchall()
    return [row[0] for row in rows]


def world_series(db_path: Path | str, worlds: Sequence[str], *, hours: int) -> pd.DataFrame:
    """Retrieve one or more world time series over a trailing period."""
    if not worlds:
        return pd.DataFrame(columns=["observed_at", "world", "players_online", "status"])
    placeholders = ", ".join("?" for _ in worlds)
    query = f"""
        WITH latest AS (SELECT MAX(observed_at) AS ts FROM collection_runs)
        SELECT ps.observed_at, w.name AS world, ps.players_online, ps.status
        FROM population_snapshots ps
        JOIN worlds w ON w.id = ps.world_id
        CROSS JOIN latest
        WHERE w.name IN ({placeholders})
          AND datetime(ps.observed_at) >= datetime(latest.ts, ?)
        ORDER BY ps.observed_at, w.name
    """
    params = [*worlds, f"-{int(hours)} hours"]
    with connect(db_path) as connection:
        frame = pd.read_sql_query(query, connection, params=params)
    if not frame.empty:
        frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    return frame


def world_statistics(db_path: Path | str, worlds: Sequence[str], *, hours: int) -> pd.DataFrame:
    """Compute reliable descriptive statistics using SQL plus quantiles in pandas."""
    if not worlds:
        return pd.DataFrame()
    placeholders = ", ".join("?" for _ in worlds)
    query = f"""
        WITH latest AS (SELECT MAX(observed_at) AS ts FROM collection_runs),
        scoped AS (
            SELECT w.name AS world, ps.observed_at, ps.players_online
            FROM population_snapshots ps
            JOIN worlds w ON w.id = ps.world_id
            CROSS JOIN latest
            WHERE w.name IN ({placeholders})
              AND datetime(ps.observed_at) >= datetime(latest.ts, ?)
        ),
        peak AS (
            SELECT world, observed_at AS peak_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY world ORDER BY players_online DESC, observed_at ASC
                   ) AS rank
            FROM scoped
        )
        SELECT s.world, COUNT(*) AS samples, AVG(s.players_online) AS mean,
               MIN(s.players_online) AS minimum, MAX(s.players_online) AS maximum,
               MIN(s.observed_at) AS first_observed_at,
               MAX(s.observed_at) AS last_observed_at,
               MAX(CASE WHEN p.rank = 1 THEN p.peak_at END) AS peak_at
        FROM scoped s
        LEFT JOIN peak p ON p.world = s.world AND p.peak_at = s.observed_at AND p.rank = 1
        GROUP BY s.world
        ORDER BY s.world
    """
    params = [*worlds, f"-{int(hours)} hours"]
    with connect(db_path) as connection:
        base = pd.read_sql_query(query, connection, params=params)
    series = world_series(db_path, worlds, hours=hours)
    if base.empty or series.empty:
        return base
    extras = (
        series.groupby("world")["players_online"]
        .agg(
            median="median",
            std_dev="std",
            p10=lambda values: values.quantile(0.10),
            p25=lambda values: values.quantile(0.25),
            p75=lambda values: values.quantile(0.75),
            p90=lambda values: values.quantile(0.90),
            current="last",
        )
        .reset_index()
    )
    return base.merge(extras, on="world", how="left")
