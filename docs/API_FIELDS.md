# TibiaData v4 discovery

Discovery performed on **2026-09-22** against the live
[`GET /v4/worlds`](https://api.tibiadata.com/v4/worlds) endpoint and the current
[Swagger document](https://docs.tibiadata.com/swagger.json). The live response reported API release
`4.10.0` at that time.

## Response envelope

- `information.timestamp`: source timestamp in UTC. It is the canonical observation time.
- `information.api.version`, `information.api.release`, `information.api.commit`: API metadata.
- `information.status.http_code`: status repeated in the JSON envelope.
- `information.tibia_urls`: upstream Tibia URLs used by TibiaData.
- `worlds.players_online`: total players reported across all worlds.
- `worlds.record_players`, `worlds.record_date`: all-time global record information.
- `worlds.regular_worlds`: regular and experimental world overview rows.
- `worlds.tournament_worlds`: tournament rows, observed as `null` when none exist.

## Fields available for each overview world

| API field | Meaning | Stored in |
|---|---|---|
| `name` | World name | `worlds.name` |
| `players_online` | Current online count | `population_snapshots.players_online` |
| `status` | Current world status | `population_snapshots.status` |
| `location` | Server region/location | `worlds.location` |
| `pvp_type` | PvP ruleset | `worlds.pvp_type` |
| `premium_only` | Whether only premium accounts may enter | `worlds.premium_only` |
| `transfer_type` | Transfer restriction (`regular`, `blocked`, or `locked` observed/documented) | `worlds.transfer_type` |
| `battleye_protected` | Whether BattlEye protection is enabled | `worlds.battleye_protected` |
| `battleye_date` | Protection date or `release` in the live response | `worlds.battleye_date` |
| `game_world_type` | Such as `regular` or `experimental` | `worlds.game_world_type` |
| `tournament_world_type` | Tournament subtype; empty for ordinary worlds | `worlds.tournament_world_type` |

The single-world endpoint documents additional fields, but the collector deliberately does not call
it: the MVP obtains every monitored world with one `/worlds` request. Fields absent from the overview
are therefore not assumed or synthesized.

Unknown extra fields are ignored for forward compatibility. Missing optional fields become SQL
`NULL`; a row without a valid `name` or non-negative integer `players_online` is skipped and logged.
The batch is rejected when no valid world remains.

