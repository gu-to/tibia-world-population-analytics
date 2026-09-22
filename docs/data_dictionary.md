# Public dataset data dictionary

All timestamps are timezone-aware UTC ISO-8601. CSV uses `YYYY-MM-DDTHH:MM:SSZ`, with fractional
seconds retained when provided; Parquet uses Arrow `timestamp[us, tz=UTC]`.

## Monthly population snapshots

Live: `data/live/YYYY-MM.csv` on `data`. Closed:
`data/historical/YYYY/YYYY-MM.parquet` on `main` after review.

| Field | Type | Meaning |
|---|---|---|
| `observed_at` | UTC timestamp | Canonical source observation from TibiaData `information.timestamp`, when present |
| `collected_at` | UTC timestamp | Time our collector received/parsed the API response |
| `world` | string | Tibia world name; case-insensitive with `observed_at` for uniqueness |
| `players_online` | nonnegative integer | Observed online players; zero is a real API value, never a missing-run marker |
| `status` | nullable string | World status at observation time |

The source timestamp determines the `YYYY-MM` partition. No analytic aggregates or static world
metadata are repeated in these rows.

## Collection audit

Live: `data/audit/YYYY-MM.csv`. Closed: `YYYY-MM.runs.csv` beside the Parquet.

| Field | Meaning |
|---|---|
| `observed_at` | Source observation time; one successful run record per unique source time |
| `collected_at` | Actual collector time |
| `slot_at` | Most recent scheduled UTC `XX:30` boundary before `collected_at`, for coverage only |
| `worlds_returned` | Number of valid world rows in this run |
| `reported_players_online` | API envelope global population, if supplied |
| `api_version` | TibiaData major API version, if supplied |
| `api_release` | TibiaData release, if supplied |

Failed runs are recorded by GitHub Actions logs and do not create success audit rows or zero-valued
snapshot rows.

## World reference

Live: `data/reference/worlds.csv`. Closed monthly copy: `YYYY-MM.worlds.csv` beside the Parquet.

| Field | Meaning |
|---|---|
| `world` | Case-insensitive world identity/name |
| `location` | Server location/region |
| `pvp_type` | PvP ruleset |
| `battleye_protected` | `true`, `false`, or blank if unavailable |
| `battleye_date` | Date or source marker such as `release` |
| `premium_only` | `true`, `false`, or blank if unavailable |
| `transfer_type` | Source transfer restriction |
| `game_world_type` | Source world type, such as regular or experimental |
| `tournament_world_type` | Tournament subtype, often blank |
| `first_seen` | First observation in this reference dataset |
| `last_seen` | Latest observation in this reference dataset |
| `active` | `true` if present in latest accepted API batch; otherwise `false` |

This is last-seen metadata, not a complete event history. A monthly copy records what was known
when the month was finalized.

## Quality report

`YYYY-MM.quality.json` includes row count, number of successful runs, unique world count, smallest
and largest world counts per run, deduplicated identical rows, expected hourly slots, successful
slots, missing slots, overall coverage, and per-world coverage. A full closed month has
`days_in_month × 24` expected slots. A scheduled run delayed past a later `XX:30` boundary may be
assigned to the later slot because GitHub does not supply its exact intended execution time.
