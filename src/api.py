"""Resilient client and parser for the TibiaData v4 worlds endpoint."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.config import (
    HTTP_RETRIES,
    HTTP_TIMEOUT_SECONDS,
    TIBIADATA_WORLDS_URL,
    USER_AGENT,
)

LOGGER = logging.getLogger(__name__)


class TibiaDataError(RuntimeError):
    """Base error for TibiaData retrieval or validation failures."""


class TibiaDataResponseError(TibiaDataError):
    """Raised when TibiaData returns an unusable payload."""


@dataclass(frozen=True, slots=True)
class WorldObservation:
    """One world as represented by the `/v4/worlds` overview response."""

    name: str
    players_online: int
    status: str | None = None
    location: str | None = None
    pvp_type: str | None = None
    premium_only: bool | None = None
    transfer_type: str | None = None
    battleye_protected: bool | None = None
    battleye_date: str | None = None
    game_world_type: str | None = None
    tournament_world_type: str | None = None


@dataclass(frozen=True, slots=True)
class WorldsBatch:
    """Validated snapshot batch returned by TibiaData."""

    observed_at: datetime
    collected_at: datetime
    worlds: tuple[WorldObservation, ...]
    total_players_online: int | None
    record_players: int | None
    record_date: str | None
    api_version: int | None
    api_release: str | None
    skipped_world_rows: int = 0


def _utc_datetime(value: Any, fallback: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        LOGGER.warning("API timestamp missing; using the local UTC collection time")
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TibiaDataResponseError(f"Invalid information.timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_str(item: Mapping[str, Any], key: str) -> str | None:
    value = item.get(key)
    return value if isinstance(value, str) else None


def _optional_bool(item: Mapping[str, Any], key: str) -> bool | None:
    value = item.get(key)
    return value if isinstance(value, bool) else None


def _optional_int(item: Mapping[str, Any], key: str) -> int | None:
    value = item.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_worlds_payload(
    payload: Mapping[str, Any], *, collected_at: datetime | None = None
) -> WorldsBatch:
    """Validate and normalize a TibiaData `/v4/worlds` response.

    Rows without the two essential analytical fields (`name`, `players_online`)
    are skipped. Optional missing fields are stored as null. The function fails
    if the response envelope is invalid or no valid world remains.
    """
    if not isinstance(payload, Mapping):
        raise TibiaDataResponseError("Expected a JSON object at the response root")

    worlds_node = payload.get("worlds")
    if not isinstance(worlds_node, Mapping):
        raise TibiaDataResponseError("Missing or invalid 'worlds' object")

    now = (collected_at or datetime.now(UTC)).astimezone(UTC)
    information = payload.get("information")
    information = information if isinstance(information, Mapping) else {}
    status = information.get("status")
    if isinstance(status, Mapping):
        api_http_code = status.get("http_code")
        if api_http_code not in (None, 200):
            raise TibiaDataResponseError(f"TibiaData payload reports HTTP status {api_http_code}")

    observed_at = _utc_datetime(information.get("timestamp"), now)
    api = information.get("api")
    api = api if isinstance(api, Mapping) else {}

    raw_worlds: list[Any] = []
    for list_name in ("regular_worlds", "tournament_worlds"):
        value = worlds_node.get(list_name)
        if value is None:
            continue
        if not isinstance(value, list):
            raise TibiaDataResponseError(f"'{list_name}' must be a list or null")
        raw_worlds.extend(value)

    parsed: list[WorldObservation] = []
    skipped_world_rows = 0
    for index, item in enumerate(raw_worlds):
        if not isinstance(item, Mapping):
            LOGGER.warning("Skipping world row %d: expected an object", index)
            skipped_world_rows += 1
            continue
        name = item.get("name")
        players = _optional_int(item, "players_online")
        if not isinstance(name, str) or not name.strip() or players is None or players < 0:
            LOGGER.warning("Skipping malformed world row %d", index)
            skipped_world_rows += 1
            continue
        parsed.append(
            WorldObservation(
                name=name.strip(),
                players_online=players,
                status=_optional_str(item, "status"),
                location=_optional_str(item, "location"),
                pvp_type=_optional_str(item, "pvp_type"),
                premium_only=_optional_bool(item, "premium_only"),
                transfer_type=_optional_str(item, "transfer_type"),
                battleye_protected=_optional_bool(item, "battleye_protected"),
                battleye_date=_optional_str(item, "battleye_date"),
                game_world_type=_optional_str(item, "game_world_type"),
                tournament_world_type=_optional_str(item, "tournament_world_type"),
            )
        )

    if not parsed:
        raise TibiaDataResponseError("Response contains no valid world observations")

    return WorldsBatch(
        observed_at=observed_at,
        collected_at=now,
        worlds=tuple(parsed),
        total_players_online=_optional_int(worlds_node, "players_online"),
        record_players=_optional_int(worlds_node, "record_players"),
        record_date=_optional_str(worlds_node, "record_date"),
        api_version=_optional_int(api, "version"),
        api_release=_optional_str(api, "release"),
        skipped_world_rows=skipped_world_rows,
    )


class TibiaDataClient:
    """HTTP client with bounded retries for temporary API failures."""

    def __init__(
        self,
        *,
        url: str = TIBIADATA_WORLDS_URL,
        timeout: float = HTTP_TIMEOUT_SECONDS,
        retries: int = HTTP_RETRIES,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.session = session or requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})

    def fetch_worlds(self) -> WorldsBatch:
        """Fetch and parse all worlds using one endpoint request."""
        try:
            response = self.session.get(self.url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TibiaDataError(f"Failed to fetch TibiaData worlds: {exc}") from exc
        try:
            payload = response.json()
        except requests.JSONDecodeError as exc:
            raise TibiaDataResponseError("TibiaData returned invalid JSON") from exc
        return parse_worlds_payload(payload)
