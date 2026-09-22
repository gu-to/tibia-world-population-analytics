from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.api import TibiaDataResponseError, parse_worlds_payload


def test_parse_current_worlds_shape(valid_payload: dict) -> None:
    batch = parse_worlds_payload(valid_payload)

    assert batch.observed_at == datetime(2026, 9, 22, 12, 34, 56, tzinfo=UTC)
    assert batch.total_players_online == 150
    assert batch.api_release == "4.10.0"
    assert len(batch.worlds) == 1
    world = batch.worlds[0]
    assert world.name == "Antica"
    assert world.battleye_protected is True
    assert world.tournament_world_type == ""


def test_missing_optional_fields_become_none(valid_payload: dict) -> None:
    valid_payload["worlds"]["regular_worlds"] = [{"name": "Minimal", "players_online": 7}]

    world = parse_worlds_payload(valid_payload).worlds[0]

    assert world.location is None
    assert world.premium_only is None
    assert world.status is None


def test_malformed_world_is_skipped_when_another_is_valid(valid_payload: dict) -> None:
    valid_payload["worlds"]["regular_worlds"].insert(0, {"name": "Broken", "players_online": None})

    batch = parse_worlds_payload(valid_payload)

    assert [world.name for world in batch.worlds] == ["Antica"]
    assert batch.skipped_world_rows == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"worlds": {}},
        {"worlds": {"regular_worlds": "not-a-list"}},
        {"worlds": {"regular_worlds": [{"name": "NoCount"}]}},
    ],
)
def test_unexpected_payloads_raise(payload: dict) -> None:
    with pytest.raises(TibiaDataResponseError):
        parse_worlds_payload(payload)


def test_payload_level_error_is_rejected(valid_payload: dict) -> None:
    valid_payload["information"]["status"]["http_code"] = 503

    with pytest.raises(TibiaDataResponseError, match="503"):
        parse_worlds_payload(valid_payload)
