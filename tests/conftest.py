from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api import WorldObservation, WorldsBatch


@pytest.fixture
def valid_payload() -> dict:
    return {
        "worlds": {
            "players_online": 150,
            "record_players": 64028,
            "record_date": "2007-11-28T18:26:00Z",
            "regular_worlds": [
                {
                    "name": "Antica",
                    "status": "online",
                    "players_online": 150,
                    "location": "Europe",
                    "pvp_type": "Open PvP",
                    "premium_only": False,
                    "transfer_type": "regular",
                    "battleye_protected": True,
                    "battleye_date": "2017-08-29",
                    "game_world_type": "regular",
                    "tournament_world_type": "",
                }
            ],
            "tournament_worlds": None,
        },
        "information": {
            "api": {"version": 4, "release": "4.10.0"},
            "timestamp": "2026-09-22T12:34:56Z",
            "status": {"http_code": 200},
        },
    }


def make_batch(timestamp: datetime, values: dict[str, int]) -> WorldsBatch:
    worlds = tuple(
        WorldObservation(
            name=name,
            players_online=players,
            status="online",
            location="Europe" if name == "Antica" else "South America",
            pvp_type="Open PvP" if name == "Antica" else "Optional PvP",
            premium_only=False,
            transfer_type="regular",
            battleye_protected=True,
            battleye_date="release",
            game_world_type="regular",
            tournament_world_type="",
        )
        for name, players in values.items()
    )
    return WorldsBatch(
        observed_at=timestamp,
        collected_at=timestamp,
        worlds=worlds,
        total_players_online=sum(values.values()),
        record_players=64028,
        record_date="2007-11-28T18:26:00Z",
        api_version=4,
        api_release="test",
    )


@pytest.fixture
def sample_batch() -> WorldsBatch:
    return make_batch(datetime(2026, 9, 22, 12, 0, tzinfo=UTC), {"Antica": 100})
