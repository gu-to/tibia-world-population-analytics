# Tibia World Population Analytics

A reproducible local data project that turns successive snapshots from the TibiaData API into a
historical dataset for understanding how the population of each Tibia world changes over time.

The central question is not only *“How many players are online now?”*, but:

> **How does the population of each Tibia world behave over time?**

Version 0.5 adds weekday-hour heatmaps, support-aware profile comparisons, optional relative
normalization, and CSV exports. It retains the v0.4 gap-aware charts, v0.3 safe local refresh,
v0.1 collector, and v0.2 public pipeline.
It does not assign profile labels, scores, or clusters before enough real data exists.

## Pipeline and architecture

```text
TibiaData API v4
    → GitHub Actions hourly at XX:30 UTC
    → data branch / data/live/YYYY-MM.csv + reference + audit
    → monthly validation and coverage
    → finalize/YYYY-MM review branch → PR → main historical Parquet
    → local SQLite / future DuckDB / pandas
    → Streamlit dashboard
```

Three layers are kept distinct:

1. **Public datasets:** the current month's text CSV and closed monthly Parquet partitions.
2. **Local analytical database:** rebuildable SQLite, never committed to Git.
3. **Application:** SQL, pandas, Plotly, and an independent Streamlit dashboard.

The existing `python -m src.collector` command still collects one observation directly into local
SQLite. The new `python -m src.pipeline` command writes public CSV/reference/audit files and is what
the hourly workflow runs. Both use the same TibiaData API client.

## Data source discovery

The schema was based on a real live request and the current Swagger definition, not on older
examples. On 2026-09-22, the live endpoint reported TibiaData API v4 release `4.10.0`.

Each overview world currently exposes:

- name, current player count, and status;
- location and PvP type;
- premium-only and transfer restriction;
- BattlEye protection flag and date/release marker;
- game-world type and tournament-world type.

The envelope also exposes the source timestamp, reported global population, historical global record,
and API metadata. `tournament_worlds` may be `null`. See [the full field mapping](docs/API_FIELDS.md).

Source: [TibiaData documentation](https://docs.tibiadata.com/) and
[`GET /v4/worlds`](https://api.tibiadata.com/v4/worlds).

## Stack and Python version

- Python **3.11** recommended (project range: 3.11–3.13)
- requests
- SQLite (Python standard library)
- pandas
- Plotly
- Streamlit
- PyArrow for monthly Parquet generation and local rebuild
- pytest and Ruff for quality checks

## Quick start

### 0. Clone the repository

```bash
git clone https://github.com/gu-to/tibia-world-population-analytics.git
cd tibia-world-population-analytics
```

### 1. Create an environment

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Linux/macOS:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Collect one real snapshot

```bash
python -m src.collector
```

This creates `data/tibia_worlds.db`. Repeating the same TibiaData source timestamp is safe and does
not duplicate the run or snapshots. This manual command is independent of the public hourly workflow.

Useful collector options:

```bash
python -m src.collector --help
python -m src.collector --db data/custom.db --log-level DEBUG
```

### 3. Open the dashboard

```bash
python -m streamlit run app/dashboard.py
```

Closing Streamlit does not affect scheduled collections. All stored/displayed timestamps are UTC.

### 4. Refresh from public history (v0.3)

The local SQLite above does **not** update automatically when GitHub Actions commits new data.
To inspect the public inputs and build a separate, current analytical database:

```bash
python -m src.sync --dry-run
python -m src.sync --db data/public_history.db
```

The command temporarily reads the public `data` branch, combines its live CSV with any reviewed
Parquets already present in this `main` checkout, and removes the temporary checkout. Point the
dashboard to the new database with `TIBIA_ANALYTICS_DB` (PowerShell example):

```powershell
$env:TIBIA_ANALYTICS_DB = "data/public_history.db"
python -m streamlit run app/dashboard.py
```

For later refreshes, close the dashboard first, then explicitly request replacement:

```bash
python -m src.sync --db data/public_history.db --replace
```

The previous SQLite is backed up as a timestamped `*.backup-*.db` file before replacement.
On Windows, PyArrow needs the `tzdata` package to read UTC timestamps from Parquet. If this
environment was installed before `tzdata` was added to `requirements.txt`, run
`python -m pip install -r requirements.txt` and retry synchronization.
`--replace` reconstructs from public datasets; manually collected snapshots absent from those
datasets remain in the backup, not in the refreshed database. Without `--replace`, an existing
target is never overwritten. Use `--data-root ../tibia-data` for an existing/offline data-branch
checkout, or `--historical-only` to skip the live CSV. Run `python -m src.sync --help` for all options.

## Automated public collection

The [hourly workflow](.github/workflows/collect-hourly.yml) runs on `30 * * * *` in UTC and can also
be started manually through **GitHub → Actions → Collect world population hourly → Run workflow**.
GitHub may start it late or skip a scheduled run. The dataset preserves the TibiaData
`information.timestamp` as `observed_at` and the actual ingestion time as `collected_at`; neither is
rounded to the theoretical `XX:30` schedule. Missing runs remain gaps, never zero-valued rows.

The workflow checks out current application code from `main` and writes only public datasets in a
separate checkout of `data`. If `data` does not exist, the first successful run creates it from
`main` without rewriting history. The workflow makes a commit only when public files change.
The default `GITHUB_TOKEN` is sufficient; no personal token or secret is required.

`data` contains current mutable CSVs and last-seen world metadata. `main` contains code and, after
review/merge, immutable closed-month Parquets. Hourly commits never go to `main`. See
[pipeline operations](docs/data_pipeline.md).

For a local test using the real API, without committing anything:

```bash
python -m src.pipeline --root .
```

Use a temporary directory with a mocked API for tests; this command performs a live request and
creates `data/live`, `data/audit`, and `data/reference` in the selected root.

## Public data storage and quality

| Location | Branch | Meaning |
|---|---|---|
| `data/live/YYYY-MM.csv` | `data` | Append-friendly population observations for the source observation month |
| `data/audit/YYYY-MM.csv` | `data` | Successful collection times, assigned schedule slots, API release and world counts |
| `data/reference/worlds.csv` | `data` | Last-seen metadata, first/last seen and active flag |
| `data/historical/YYYY/YYYY-MM.parquet` | `main` after PR | Immutable closed-month population history |
| `data/historical/YYYY/YYYY-MM.{runs,worlds}.csv` | `main` after PR | Monthly audit and reference snapshot for rebuild |
| `data/historical/YYYY/YYYY-MM.quality.json` | `main` after PR | Coverage and validation report |
| `data/*.db` | local only | SQLite analytics database; ignored by Git |

Raw snapshot fields are only `observed_at`, `collected_at`, `world`, `players_online`, and `status`.
World metadata is not repeated on every snapshot. The logical key is `(world, observed_at)`; reruns
do not add a second copy. Invalid timestamps/counts, duplicate keys, unexpected columns, missing
audit records, or inconsistent world counts stop publication. A drastic world-count drop also stops
the hourly pipeline before it can mark most worlds inactive. See the
[data dictionary](docs/data_dictionary.md).

Coverage is calculated from unique hourly `XX:30` slots assigned by actual `collected_at`, not from
the number of world rows. A completed month's expected slots equal days × 24. The quality report also
lists missing slots and per-world coverage. Since GitHub does not expose the exact intended timestamp
of each delayed scheduled event, a delay crossing the next `XX:30` boundary may be assigned to the
later slot; the source observation timestamp is still preserved unchanged.

## Monthly finalization and review

The [finalization workflow](.github/workflows/finalize-month.yml) checks for closed months daily at
03:10 UTC and supports **workflow_dispatch** with an optional `YYYY-MM` month. It validates the
complete monthly CSV and audit, defensively removes identical duplicate rows, sorts observations,
calculates coverage, writes compressed Parquet, reads it back for verification, and publishes a
`finalize/YYYY-MM` branch. The run summary includes a compare link. Open a PR from that branch into
`main`, review the report, and merge it manually. The workflow never silently commits to `main`.

If a finalization branch or historical Parquet already exists, the automation skips or refuses to
overwrite it. The old live CSV remains on `data` as source material until a separate retention policy
is defined. Newly collected observations for an old month are accepted only through 02:59 UTC on the
first day of the following month; after that they fail visibly to protect the review snapshot.

Local finalization example with a source checkout of `data` and an output checkout of `main`:

```bash
python -m src.historical pending --source-root ../tibia-data --output-root .
python -m src.historical finalize --month 2026-09 --source-root ../tibia-data --output-root .
```

Finalization requires a month that has actually ended. It will not overwrite existing output files.

## Rebuild the local SQLite database

After obtaining historical Parquets from `main`, and optionally the current live/reference/audit
files from `data`, build a *new* SQLite file:

```bash
python -m src.rebuild --db data/rebuilt.db
python -m src.rebuild --db data/rebuilt-with-live.db --include-live --live-root ../tibia-data
```

`--include-live` reads `data/live/*.csv` only for months without a historical Parquet. Use
`--live-root` for a separate checkout of `data`, while `--root` (default: this checkout) supplies
published historical partitions from `main`. The rebuild reads one monthly partition at a time and
streams Parquet batches. It refuses to overwrite an existing SQLite file and publishes the new file
only after validation. To use it in the dashboard, set `TIBIA_ANALYTICS_DB` to its path.

## Dashboard

Latest values are labeled as the *last observation*, not necessarily the current live population.
The sidebar warns when local real data is stale. Means are means of **observed samples**, not
time-weighted estimates of missing hours. Charts use solid lines between nearby observations and
dashed visual guides across longer gaps; no zero or interpolated raw rows are created. Dashed
segments indicate an unknown interval, not a measured trajectory.

### Overview

- latest total, monitored-world count, mean per world, and last collection time;
- global mean and peak across available samples in the trailing 24 hours;
- total-population time series;
- sortable current-world table with region, PvP, BattlEye, current, 24h average, and 24h peak.

### World Explorer

- 24-hour, 7-day, and 30-day windows;
- current, mean, median, min, max, sample standard deviation, P10/P25/P75/P90;
- observed peak time and population series;
- an explicit limited-history notice when the selected period is not covered.

### Compare Worlds

- up to eight series on one chart;
- the same basic statistics side by side;
- timezone-aware hourly and weekday comparisons with observed-sample and distinct-date support;
- optional normalization to each world's own observed mean (100%) to compare pattern shape;
- a configurable distinct-date minimum that hides weakly supported chart points without changing
  the underlying data, plus exports of selected observations and hourly profile support;
- global filters derived from database values for region, PvP type, BattlEye, premium-only,
  transfer type, and game-world type.

### Data Quality & Coverage

- last source observation and its age;
- successful versus expected hourly collection slots over 7 or 30 UTC calendar days;
- missing-slot calendar by day/hour and per-world coverage table;
- on-demand, read-only comparison with public GitHub Actions runs: visible failures, slots with
  no visible scheduled run, and successful runs without a nearby local audit;
- explicit warning that delayed GitHub runs can be assigned only to an *inferred* hourly slot.

The calendar uses the local SQLite. Refresh it with `python -m src.sync` to see newly published
CSV observations. Global coverage concerns the collection pipeline; metadata filters apply to the
per-world table, not to the global hourly slot count. Hours before the first local collection are
excluded from coverage; missing hours after that remain visible.

The GitHub comparison needs an internet connection, is never required to open the dashboard, and
does not write data. It can use `GITHUB_TOKEN` to avoid anonymous API rate limits. Forks can set
`TIBIA_GITHUB_REPO=owner/repository`. Run the same read-only check from the terminal with:

```bash
python -m src.health --days 7 --github
```

Without `--github`, the command reports local coverage and freshness only. Matching a workflow
start to a local audit uses a short time window, so its classifications are diagnostic clues, not
proof of a missing run's root cause. A recently successful GitHub run may simply be newer than the
last local `src.sync`.

### Population Patterns

- one-world observed-population histogram for 7- or 30-day windows;
- selected-IANA-timezone hourly and weekday means alongside sample counts and distinct dates;
- weekday-by-hour population and sample-count heatmaps; missing population cells remain blank;
- a configurable minimum of distinct local dates per heatmap cell and a CSV export containing
  all 168 cells, observed means, sample counts, and support status;
- weekday/weekend sample counts, distinct dates, means, and box plot;
- coverage and limited-history notices. These are descriptive views, not scores or claims about
  missing hours. Uneven collection times can bias apparent peaks and weekday/weekend differences.

Select a display timezone in the sidebar. Source timestamps remain in UTC; time-series charts and
population-pattern groupings are converted for display. The collection calendar always uses UTC
because the public schedule is defined in UTC. Timezone conversion handles daylight-saving changes
without inserting an observation for a skipped local hour.
The relative comparison uses the mean of each world's *collected* snapshots in the selected
period. It is undefined when that mean is zero and does not estimate population in missing hours.
Support thresholds affect display only; CSV summaries retain observed values even for hidden
points. Sparse or unevenly sampled periods should not be interpreted as stable recurring patterns.

## Synthetic demo data

When real history is still short, generate 14 days of five-minute synthetic data:

```bash
python -m src.demo_data
```

The demo includes stable, low-population, pronounced-peak, weekend-sensitive, and shifted-hour worlds.
It is saved to `data/demo_tibia_worlds.db`, while real data remains in `data/tibia_worlds.db`. Choose
**Synthetic demo** in the dashboard sidebar; a persistent warning identifies every displayed value as
synthetic. Existing demo data is never overwritten unless explicitly requested:

```bash
python -m src.demo_data --replace --days 30 --seed 42
```

Synthetic names and values do not represent real Tibia worlds.

## Exploratory notebook

The notebook is an optional workspace for ad-hoc analysis outside the dashboard. Its dependencies
are intentionally separate from the minimal dashboard runtime. Install them and start Jupyter from
the repository root:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-notebook.txt
.\.venv\Scripts\python.exe -m jupyter lab
```

Then open `notebooks/exploratory_analysis.ipynb`. In VS Code, use **Select Kernel → Python
Environments → `.venv`**. To register an explicit kernel named `Python (Tibia World Analytics)`, run:

```powershell
.\.venv\Scripts\python.exe -m ipykernel install --user --name tibia-world-analytics --display-name "Python (Tibia World Analytics)"
```

If the environment was created with `uv` and does not contain `pip`, install with:

```powershell
uv pip install --python .venv\Scripts\python.exe -r requirements-notebook.txt
```

## Database design

| Table | Purpose | Key integrity rule |
|---|---|---|
| `worlds` | Last-seen relatively static metadata | unique case-insensitive world name |
| `population_snapshots` | Historical population and status | unique `(world_id, observed_at)` |
| `collection_runs` | Source/ingestion audit and global totals | unique source `observed_at` |

The API-provided timestamp is the canonical observation time; local ingestion time is stored
separately. Every timestamp is normalized to ISO-8601 UTC with `Z`. A complete collection is written
inside one transaction. See [the detailed schema](docs/DATABASE.md).

The SQLite tables remain compatible with v0.1/v0.2. New rebuilds include an index on
`population_snapshots.collection_run_id` for bounded coverage queries. The public run audit is a
monthly CSV; the rebuild maps it back into `collection_runs`.

## Project structure

```text
app/                    Streamlit Overview and multipage views, including Population Patterns
src/api.py              Shared HTTP client and defensive response parser
src/collector.py        Existing one-shot local SQLite collection
src/database.py         Existing normalized SQLite schema and persistence
src/pipeline.py         One-shot public hourly collection CLI
src/datasets.py         Monthly live CSV, run audit, and world reference
src/quality.py          Validation and hourly coverage
src/historical.py       Monthly immutable Parquet finalization
src/rebuild.py          Stream public monthly datasets into fresh SQLite
src/sync.py             Safe local public-data refresh, with backup on explicit replacement
src/monitoring.py       Bounded freshness and coverage calculations
src/health.py           Optional GitHub Actions / local-audit health comparison
src/analytics.py        Existing SQL read model and statistics
src/patterns.py         Timezone-aware observed-sample profiles
src/demo_data.py        Separate deterministic synthetic dataset
tests/                  Offline data, presentation, and health validation tests
.github/workflows/       Hourly collection and monthly finalization
notebooks/              Optional exploratory starting point
docs/                   API discovery, schema, and scheduling notes
data/                   Local DB ignored; public datasets intentionally versioned
```

## Tests and quality checks

Install development dependencies and run:

```bash
python -m pip install -r requirements-dev.txt
pytest
ruff check .
ruff format --check .
```

The test suite is offline: it uses fixtures and temporary SQLite databases, never requiring the API.
`requirements-collector.txt` and `requirements-finalize.txt` keep GitHub Actions installations small.
The project also maintains `uv.lock` for users who prefer `uv`.

## Configuration

- `TIBIA_ANALYTICS_DB`: optional path for the real database.
- `TIBIA_ANALYTICS_DEMO_DB`: optional path for the demo database.
- `--db`: per-command collector/demo database override.

No credentials or secrets are required by the public endpoint.

## Limitations

- The API returns current state, so public history begins only when the automation starts running.
- GitHub Actions may start late, skip a scheduled run, or be disabled after inactivity in a public
  repository. Failed collections create gaps rather than fabricated/interpolated points.
- Metadata is last-seen observational state; monthly reference snapshots capture the state known at
  finalization, not necessarily every intermediate metadata change.
- The overview endpoint does not include all fields available from a per-world request; the MVP avoids
  N+1 requests and stores only verified overview fields.
- SQLite is appropriate for a local single-writer MVP, not a distributed ingestion service.
- Finalization branches require human review and a PR into `main`; old live CSVs remain on `data`
  until a future retention policy exists.
- A short or sparse history cannot support reliable weekday/weekend or seasonal conclusions. The
  dashboard shows sample support and coverage but does not infer unobserved population.
- GitHub run-to-audit matching is approximate because the raw public audit predates an explicit
  workflow-run identifier. A successful rerun may add no new observation; an unseen scheduled
  run does not by itself establish why GitHub did not start it.
- Public-data refresh reads the latest `data` branch, while reviewed Parquets come from the local
  `main` checkout; pull `main` first if a monthly PR was merged elsewhere.

## Possible next versions

- coverage-aware comparison of hourly and weekday/weekend patterns over longer histories;
- optional external, independent collection monitor if GitHub scheduling remains sparse;
- metadata event history and lifecycle/merge handling for worlds;
- archival/retention for closed CSVs on `data` after PR merge;
- optional DuckDB reads over monthly Parquets and live CSV;
- only after enough data: stability, peak intensity, and clustering validated against real
  distributions rather than arbitrary thresholds.

## Disclaimer

Tibia is a registered trademark of **CipSoft GmbH**. This is an independent, unofficial portfolio
project and is not affiliated with, endorsed by, or sponsored by CipSoft. Data is accessed through
the community-operated TibiaData API, which is not an official CipSoft API; follow its terms and
operational guidance.
