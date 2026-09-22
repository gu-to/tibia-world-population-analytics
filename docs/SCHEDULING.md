# Scheduling collections

The collector intentionally performs one request and exits. Use the operating system scheduler to
run it approximately every five minutes; the dashboard is an independent read-only process.

## Linux/macOS cron example

Run `crontab -e` and adapt both absolute paths:

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

