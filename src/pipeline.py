"""One-shot public dataset collection for GitHub Actions or local dry runs."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from src.api import TibiaDataClient, TibiaDataError
from src.config import PROJECT_ROOT
from src.datasets import append_batch
from src.quality import DataQualityError

LOGGER = logging.getLogger(__name__)


def collect_public(root: Path = PROJECT_ROOT, *, historical_root: Path | None = None) -> int:
    """Fetch once, validate, and append to the current source observation month."""
    batch = TibiaDataClient().fetch_worlds()
    result = append_batch(batch, root, historical_root=historical_root)
    LOGGER.info(
        "Collected %d worlds: month=%s, new_rows=%d, audit_added=%s",
        len(batch.worlds),
        result.month,
        result.snapshots_added,
        result.audit_added,
    )
    return result.snapshots_added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--historical-root", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        collect_public(args.root, historical_root=args.historical_root)
    except (TibiaDataError, DataQualityError, OSError):
        LOGGER.exception("Public collection failed; no synthetic/zero snapshot was recorded")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
