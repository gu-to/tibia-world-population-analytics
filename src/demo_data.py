"""Generate a clearly separated synthetic database for dashboard development."""

from __future__ import annotations

import argparse
import math
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.api import WorldObservation, WorldsBatch
from src.config import database_path
from src.database import store_batch

DEMO_WORLDS = (
    ("Steadia", "Europe", "Optional PvP", False, "stable", 340),
    ("Rushibra", "South America", "Open PvP", False, "peak", 90),
    ("Quietera", "North America", "Optional PvP", False, "low", 18),
    ("Weekendra", "Europe", "Open PvP", True, "weekend", 115),
    ("Oceanica", "Oceania", "Optional PvP", False, "peak", 45),
    ("Retrovia", "South America", "Retro Open PvP", True, "low", 28),
    ("Constanta", "North America", "Optional PvP", False, "stable", 220),
    ("Nocturna", "Europe", "Retro Hardcore PvP", False, "night", 55),
)


def _population(
    pattern: str, baseline: int, timestamp: datetime, rng: random.Random, phase: float
) -> int:
    hour = timestamp.hour + timestamp.minute / 60
    daily = max(0.0, math.sin((hour - 12 + phase) * math.pi / 12))
    weekend = timestamp.weekday() >= 5
    if pattern == "stable":
        value = baseline + 15 * daily
    elif pattern == "peak":
        value = baseline + 260 * daily**4
    elif pattern == "weekend":
        value = baseline + 100 * daily + (150 if weekend else 0)
    elif pattern == "night":
        night_curve = max(0.0, math.sin((hour + 2) * math.pi / 12))
        value = baseline + 180 * night_curve**3
    else:
        value = baseline + 15 * daily
    return max(0, round(value + rng.gauss(0, max(2, baseline * 0.04))))


def generate_demo_database(
    db_path: Path, *, days: int = 14, seed: int = 42, replace: bool = False
) -> int:
    """Generate deterministic five-minute demo snapshots in a separate database."""
    if db_path.exists():
        if not replace:
            raise FileExistsError(f"{db_path} already exists; pass --replace to recreate demo data")
        db_path.unlink()
    rng = random.Random(seed)
    end = datetime.now(UTC).replace(second=0, microsecond=0)
    end -= timedelta(minutes=end.minute % 5)
    start = end - timedelta(days=days)
    steps = int((end - start) / timedelta(minutes=5)) + 1

    for step in range(steps):
        observed_at = start + timedelta(minutes=5 * step)
        observations = []
        for index, (name, region, pvp, premium, pattern, baseline) in enumerate(DEMO_WORLDS):
            players = _population(pattern, baseline, observed_at, rng, index * 1.7)
            observations.append(
                WorldObservation(
                    name=name,
                    players_online=players,
                    status="online",
                    location=region,
                    pvp_type=pvp,
                    premium_only=premium,
                    transfer_type="blocked" if pattern == "low" else "regular",
                    battleye_protected=True,
                    battleye_date="release",
                    game_world_type="regular",
                    tournament_world_type="",
                )
            )
        batch = WorldsBatch(
            observed_at=observed_at,
            collected_at=observed_at,
            worlds=tuple(observations),
            total_players_online=sum(world.players_online for world in observations),
            record_players=None,
            record_date=None,
            api_version=4,
            api_release="synthetic-demo",
        )
        store_batch(batch, db_path, data_mode="demo")
    return steps * len(DEMO_WORLDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=database_path(demo=True))
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    if args.days < 2:
        parser.error("--days must be at least 2")
    count = generate_demo_database(args.db, days=args.days, seed=args.seed, replace=args.replace)
    print(f"Generated {count:,} synthetic snapshots in {args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
