"""Read-only reconciliation of local collection coverage and GitHub workflow runs."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from src.config import DEFAULT_DB_PATH
from src.monitoring import CoverageWindow, coverage_window
from src.quality import observation_slot, parse_utc, utc_iso

DEFAULT_REPOSITORY = "gu-to/tibia-world-population-analytics"
WORKFLOW_FILE = "collect-hourly.yml"
MATCH_WINDOW = timedelta(minutes=45)
FAILED_CONCLUSIONS = {"failure", "timed_out", "action_required"}


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """One GitHub Actions run, without credentials or log contents."""

    run_id: int
    event: str
    started_at: datetime
    status: str
    conclusion: str | None


@dataclass(frozen=True, slots=True)
class HealthComparison:
    """What workflow history can and cannot explain about missing local data."""

    scheduled_runs: int
    successful_runs: int
    failed_runs: int
    cancelled_runs: int
    pending_runs: int
    no_visible_run_slots: tuple[datetime, ...]
    success_without_nearby_audit: tuple[int, ...]
    newer_than_local_sync: tuple[int, ...]
    failed_run_ids: tuple[int, ...]


def fetch_workflow_runs(
    repository: str,
    start: datetime,
    end: datetime,
    *,
    token: str | None = None,
    session: requests.Session | None = None,
) -> list[WorkflowRun]:
    """List public workflow runs via GitHub REST; paginate bounded 30-day windows."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be OWNER/REPO")
    if start.tzinfo is None or end.tzinfo is None or start > end:
        raise ValueError("start/end must be ordered timezone-aware timestamps")
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{WORKFLOW_FILE}/runs"
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    client = session or requests.Session()
    try:
        runs: list[WorkflowRun] = []
        for page in range(1, 11):
            response = client.get(
                url,
                headers=headers,
                params={
                    "created": f"{utc_iso(start)}..{utc_iso(end)}",
                    "per_page": 100,
                    "page": page,
                },
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
                raise ValueError("Unexpected GitHub workflow-runs response")
            items = payload["workflow_runs"]
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("Unexpected GitHub workflow-run entry")
                started = item.get("run_started_at") or item.get("created_at")
                runs.append(
                    WorkflowRun(
                        run_id=int(item["id"]),
                        event=str(item["event"]),
                        started_at=parse_utc(started),
                        status=str(item["status"]),
                        conclusion=item.get("conclusion"),
                    )
                )
            if len(items) < 100:
                return sorted(runs, key=lambda run: run.started_at)
        raise ValueError("GitHub returned more than 1,000 runs; select a shorter window")
    finally:
        if session is None:
            client.close()


def compare_workflow_runs(coverage: CoverageWindow, runs: list[WorkflowRun]) -> HealthComparison:
    """Compare run starts to observed slots; matching is approximate, never causal proof."""
    expected = set(coverage.expected_slots)
    scheduled = [run for run in runs if run.event == "schedule"]
    visible_slots = {observation_slot(run.started_at) for run in scheduled} & expected
    available_audits = list(coverage.run_collected_at)
    unmatched_success: list[int] = []
    newer: list[int] = []
    for run in sorted(runs, key=lambda item: item.started_at):
        if run.conclusion != "success":
            continue
        match = next(
            (
                index
                for index, collected in enumerate(available_audits)
                if timedelta(0) <= collected - run.started_at <= MATCH_WINDOW
            ),
            None,
        )
        if match is not None:
            available_audits.pop(match)
        elif coverage.last_collected_at is None or run.started_at > coverage.last_collected_at:
            newer.append(run.run_id)
        else:
            unmatched_success.append(run.run_id)
    return HealthComparison(
        scheduled_runs=len(scheduled),
        successful_runs=sum(run.conclusion == "success" for run in runs),
        failed_runs=sum(run.conclusion in FAILED_CONCLUSIONS for run in runs),
        cancelled_runs=sum(run.conclusion == "cancelled" for run in runs),
        pending_runs=sum(run.status != "completed" for run in runs),
        no_visible_run_slots=tuple(
            slot for slot in coverage.expected_slots if slot not in visible_slots
        ),
        success_without_nearby_audit=tuple(unmatched_success),
        newer_than_local_sync=tuple(newer),
        failed_run_ids=tuple(run.run_id for run in runs if run.conclusion in FAILED_CONCLUSIONS),
    )


def main(argv: list[str] | None = None) -> int:
    """Print a local coverage summary and optionally reconcile public Actions runs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--days", type=int, choices=(7, 30), default=7)
    parser.add_argument("--github", action="store_true", help="Compare public workflow runs")
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=args.days - 1)
    try:
        coverage = coverage_window(args.db, start, now)
        observed, expected = len(coverage.observed_slots), len(coverage.expected_slots)
        print(f"UTC window: {utc_iso(start)} to {utc_iso(now)}")
        print(f"Observed / expected hourly slots: {observed} / {expected}")
        print(f"Missing slots: {len(coverage.missing_slots)}")
        print(
            "Latest source observation: "
            + (utc_iso(coverage.last_observed_at) if coverage.last_observed_at else "none")
        )
        print(
            "Latest local collection: "
            + (utc_iso(coverage.last_collected_at) if coverage.last_collected_at else "none")
        )
        if args.github:
            runs = fetch_workflow_runs(args.repo, start, now, token=os.getenv("GITHUB_TOKEN"))
            comparison = compare_workflow_runs(coverage, runs)
            print(
                "GitHub scheduled / successful / failed runs: "
                f"{comparison.scheduled_runs} / {comparison.successful_runs} / "
                f"{comparison.failed_runs}"
            )
            print(
                "Expected slots without a visible scheduled run: "
                f"{len(comparison.no_visible_run_slots)}"
            )
            print(
                "Successful runs without a nearby local audit: "
                f"{len(comparison.success_without_nearby_audit)}"
            )
            print(f"Runs newer than local sync: {len(comparison.newer_than_local_sync)}")
            print(
                f"Cancelled / pending runs: {comparison.cancelled_runs} / {comparison.pending_runs}"
            )
            if comparison.failed_run_ids:
                print("Failed run IDs: " + ", ".join(map(str, comparison.failed_run_ids)))
            print("Run-to-slot matching is inferred; no-run slots do not establish a root cause.")
    except (OSError, ValueError, requests.RequestException) as exc:
        parser.exit(1, f"Collection health check failed: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main() tests
    raise SystemExit(main())
