# Public historical data pipeline

## Branches and cadence

- `main`: application code and reviewed, immutable `data/historical/YYYY/YYYY-MM.parquet` partitions.
- `data`: append-friendly `data/live/YYYY-MM.csv`, `data/audit/YYYY-MM.csv`, and
  `data/reference/worlds.csv`. It is created on the first successful hourly workflow run if absent.
- `finalize/YYYY-MM`: one review branch per closed month, based on `main`; contains the Parquet,
  quality report, run audit, and world reference copy for that month.

The collection workflow runs at minute 30 each hour in UTC and also supports manual dispatch. It
checks out current code from `main` and a separate `data` worktree, then commits only public CSV
changes to `data`. It never commits SQLite. Both workflows share a concurrency group; GitHub will not
run them simultaneously. A default `GITHUB_TOKEN` with workflow-level `contents: write` permission
pushes the branches. No PAT is needed.

The finalization workflow checks daily at 03:10 UTC for live months older than the current month.
It may also be manually dispatched with a month. It stops on invalid data and does not overwrite an
existing historical output. A successful run pushes a `finalize/YYYY-MM` branch and prints a compare
link in the run summary. Open a PR from that branch into `main` and review the quality report before
merging. The workflow does not need `pull-requests: write` or the optional repository setting that
allows Actions to create PRs.

## Initial GitHub setup

1. Review and push the v0.2 code to `main` yourself. No data branch must be created manually.
2. In repository **Settings → Actions → General**, allow GitHub Actions and ensure workflow tokens
   can write repository contents. Branch rules must permit `github-actions[bot]` to push `data` and
   `finalize/*` (or the workflow will fail visibly). `main` may stay protected.
3. Under **Actions**, enable the workflows if GitHub presents an enable button.
4. Run **Collect world population hourly → Run workflow** once on `main`; inspect the log and the
   newly created `data` branch. Scheduled runs then occur at `XX:30 UTC`.
5. After a month ends, run **Prepare monthly historical dataset** manually if you do not want to wait
   for the daily schedule. Inspect the run summary's compare link, create a PR, review, and merge.

Scheduled workflows run from the default branch and may be delayed or skipped by GitHub. They may
also be disabled after repository inactivity. See the [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Validation and missing observations

The public collector rejects malformed API rows, invalid timestamps and populations, duplicate
world names, stale source times, unexpected file schemas, and a world count falling below half of a
previously active population of at least ten worlds. It uses bounded HTTP retries inherited from the
existing API client. If the call or validation fails, the workflow fails and creates no new snapshot.
The next hourly run resumes normally; no interpolation or synthetic zero is written.

`observed_at` comes from the API, never the cron expression. A public CSV is keyed by
`(world, observed_at)` and reruns with the same source observation add nothing. The last known world
metadata is updated separately; absent worlds are retained with `active=false`. A source observation
from the preceding month may be recorded through 02:59 UTC on the first day of the next month. Later
arrivals fail visibly rather than changing a month already entering finalization.

## Local use and rebuild

Create a local checkout of the `data` branch next to the `main` checkout:

```bash
git fetch origin data
git worktree add ../tibia-data origin/data
```

From the `main` checkout:

```bash
python -m src.historical pending --source-root ../tibia-data --output-root .
python -m src.rebuild --db data/rebuilt.db --include-live --live-root ../tibia-data
```

The rebuilt SQLite is private/local and can be selected by the dashboard using
`TIBIA_ANALYTICS_DB`. Monthly Parquets can also be queried directly by pandas or future DuckDB
without consolidating years into one file.

The old live CSV remains on `data` after finalization. Retention/cleanup can be designed once the
monthly PR is safely merged; the v0.2 automation does not delete or rewrite it.
