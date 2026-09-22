"""Central project configuration."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "tibia_worlds.db"
DEMO_DB_PATH = DATA_DIR / "demo_tibia_worlds.db"

TIBIADATA_WORLDS_URL = "https://api.tibiadata.com/v4/worlds"
HTTP_TIMEOUT_SECONDS = 15.0
HTTP_RETRIES = 3
USER_AGENT = "tibia-world-population-analytics/0.1"


def database_path(*, demo: bool = False) -> Path:
    """Return the configured database path, with separate real/demo defaults."""
    env_name = "TIBIA_ANALYTICS_DEMO_DB" if demo else "TIBIA_ANALYTICS_DB"
    configured = os.getenv(env_name)
    if configured:
        return Path(configured).expanduser()
    return DEMO_DB_PATH if demo else DEFAULT_DB_PATH
