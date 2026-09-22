"""Command-line collector for one TibiaData world population snapshot."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.api import TibiaDataClient, TibiaDataError
from src.config import database_path
from src.database import store_batch

LOGGER = logging.getLogger(__name__)


def collect_once(db_path: Path | str | None = None) -> int:
    """Fetch and persist one batch; return the inserted snapshot count."""
    target = Path(db_path) if db_path is not None else database_path()
    LOGGER.info("Starting TibiaData collection into %s", target)
    batch = TibiaDataClient().fetch_worlds()
    result = store_batch(batch, target, data_mode="real")
    if result.duplicate_batch:
        LOGGER.info("Batch %s already exists; nothing inserted", batch.observed_at.isoformat())
    else:
        LOGGER.info(
            "Stored %d/%d world snapshots observed at %s",
            result.snapshots_inserted,
            result.worlds_received,
            batch.observed_at.isoformat(),
        )
    return result.snapshots_inserted


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Override the SQLite database path")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        collect_once(args.db)
    except TibiaDataError:
        LOGGER.exception("Collection failed; the database transaction was not changed")
        return 1
    except Exception:
        LOGGER.exception("Unexpected collection failure; the transaction was rolled back")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
