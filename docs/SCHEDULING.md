# Local scheduling (optional)

The v0.2 public history uses GitHub Actions once per hour at `XX:30 UTC`; see
[the data pipeline guide](data_pipeline.md). The existing local `src.collector` remains a one-shot
SQLite collector. Use the operating system scheduler only if you also want independent local history.
The dashboard is a separate read-only process.

## Public schedule audit (4 October 2026)

The checked-in workflow still specifies `30 * * * *`. A read-only comparison of the public
[workflow history](https://github.com/gu-to/tibia-world-population-analytics/actions/workflows/collect-hourly.yml)
with the [October run audit on the `data` branch](https://github.com/gu-to/tibia-world-population-analytics/blob/data/data/audit/2026-10.csv)
found 16 successful scheduled executions and 16 corresponding audit rows through 08:39 UTC on
4 October: four on 1 October, five on 2 October, five on 3 October, and two early on 4 October.
No failed workflow run was visible in that interval. The collection code successfully stored the
runs that GitHub started; the many other hourly slots had no visible run in the inspected history.
This does **not** prove why a scheduled event was absent. GitHub documents that scheduled runs
[may be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
The raw dataset leaves those slots missing rather than writing zeros or retrospectively sampling.

The Data Quality page can compare local coverage with public workflow runs on demand. The CLI
equivalent is `python -m src.health --days 7 --github`; omit `--github` for an offline local
summary. Refresh the local SQLite with `python -m src.sync` first when practical. The comparison
reports visible failed runs and runs newer than the last local sync separately. It uses run-start
times to *infer* slots; a delayed run can be assigned to a later slot, so the result is diagnostic,
not a causal verdict. Manual `workflow_dispatch` creates a new current observation and cannot
backfill historical gaps.

## Linux/macOS cron example

Run `crontab -e` and adapt both absolute paths. This example collects locally every five minutes,
independent of the public hourly dataset:

```cron
*/5 * * * * cd /path/to/tibia-world-analytics && /path/to/.venv/bin/python -m src.collector >> data/collector.log 2>&1
```

## Windows Task Scheduler

Create a basic task with a five-minute repeat interval and use:

- Program: `C:\path\to\tibia-world-analytics\.venv\Scripts\python.exe`
- Arguments: `-m src.collector`
- Start in: `C:\path\to\tibia-world-analytics`

The default database path is `data/tibia_worlds.db`. Set `TIBIA_ANALYTICS_DB` or pass `--db` to use
another location. Avoid overlapping invocations; SQLite serializes writers and duplicate source
timestamps are ignored safely.
