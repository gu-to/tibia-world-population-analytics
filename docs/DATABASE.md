# SQLite data model

All timestamps are stored as ISO-8601 UTC strings ending in `Z`. The collector uses the TibiaData
`information.timestamp` as `observed_at` and records its own `collected_at` separately.

```text
worlds (1) ──────< population_snapshots >────── (1) collection_runs
  metadata          time-series observations          ingestion audit
```

## `worlds`

One row per case-insensitive world name. It holds current/last-seen metadata and `first_seen_at` /
`last_seen_at`. Nullable columns preserve resilience when the API omits optional fields.

## `population_snapshots`

One row per `(world_id, observed_at)`, with `players_online` and the dynamic `status`. The unique
constraint makes retries idempotent; indexes support time-window and per-world series queries.

## `collection_runs`

One row per unique source `observed_at`, containing collection time, API release, global values,
number of received worlds, number of inserted snapshots, and `data_mode` (`real` or `demo`). A run
and all its world rows are committed in one transaction, so partial failures roll back.

Real and synthetic data use separate database files by default. `data_mode` is an additional guard;
the dashboard refuses a database containing both modes.

The v0.2 public pipeline does not commit this SQLite file. `python -m src.rebuild` reconstructs the
same schema from monthly historical Parquets and, optionally, the current live CSV and reference
files. The rebuild writes a fresh target database and refuses to overwrite an existing one.
