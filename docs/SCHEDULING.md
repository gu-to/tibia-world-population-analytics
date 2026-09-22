# Local scheduling (optional)

The v0.2 public history uses GitHub Actions once per hour at `XX:30 UTC`; see
[the data pipeline guide](data_pipeline.md). The existing local `src.collector` remains a one-shot
SQLite collector. Use the operating system scheduler only if you also want independent local history.
The dashboard is a separate read-only process.

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
