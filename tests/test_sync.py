from __future__ import annotations

from datetime import UTC, datetime

import pytest
from conftest import make_batch

from src.database import connect, store_batch
from src.datasets import append_batch, snapshot_path
from src.sync import discover_sources, sync_database


def test_sync_preview_create_replace_and_backup(tmp_path) -> None:
    main = tmp_path / "main"
    data = tmp_path / "data_branch"
    target = tmp_path / "real.db"
    observed = datetime(2026, 9, 22, 12, 30, tzinfo=UTC)
    append_batch(make_batch(observed, {"Antica": 50}), data)
    preview = sync_database(target, main, data_root=data, dry_run=True)
    assert preview.dry_run
    assert [(item.month, item.kind, item.snapshot_rows) for item in preview.sources] == [
        ("2026-09", "live CSV", 1)
    ]
    assert not target.exists()
    first = sync_database(target, main, data_root=data)
    assert (first.runs, first.snapshots) == (1, 1)
    assert first.backup_path is None
    with pytest.raises(FileExistsError):
        sync_database(target, main, data_root=data)
    old_manual = make_batch(observed.replace(hour=13), {"Antica": 99})
    store_batch(old_manual, target)
    replacement = sync_database(target, main, data_root=data, replace=True)
    assert replacement.backup_path is not None
    assert replacement.backup_path.exists()
    with connect(target) as connection:
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 1
    with connect(replacement.backup_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM population_snapshots").fetchone()[0] == 2


def test_sync_invalid_live_file_preserves_existing_database(tmp_path) -> None:
    main = tmp_path / "main"
    data = tmp_path / "data_branch"
    target = tmp_path / "real.db"
    observed = datetime(2026, 9, 22, 12, 30, tzinfo=UTC)
    append_batch(make_batch(observed, {"Antica": 50}), data)
    store_batch(make_batch(observed, {"Antica": 100}), target)
    live = snapshot_path(data, "2026-09")
    live.write_text("wrong,header\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sync_database(target, main, data_root=data, replace=True)
    with connect(target) as connection:
        assert (
            connection.execute("SELECT players_online FROM population_snapshots").fetchone()[0]
            == 100
        )
    assert not list(tmp_path.glob("real.backup-*.db"))


def test_historical_partition_takes_precedence_over_live(tmp_path) -> None:
    from src.historical import finalize_month

    main = tmp_path / "main"
    data = tmp_path / "data_branch"
    observed = datetime(2026, 8, 22, 12, 30, tzinfo=UTC)
    append_batch(make_batch(observed, {"Antica": 50}), data)
    finalize_month("2026-08", data, main, now=datetime(2026, 9, 1, tzinfo=UTC))
    sources = discover_sources(main, data, include_live=True)
    assert [(item.month, item.kind) for item in sources] == [("2026-08", "Parquet")]


def test_sync_refuses_demo_and_open_sqlite_sidecars(tmp_path) -> None:
    main = tmp_path / "main"
    data = tmp_path / "data_branch"
    target = tmp_path / "real.db"
    observed = datetime(2026, 9, 22, 12, 30, tzinfo=UTC)
    batch = make_batch(observed, {"Antica": 50})
    append_batch(batch, data)
    store_batch(batch, target, data_mode="demo")
    with pytest.raises(ValueError, match="synthetic-demo"):
        sync_database(target, main, data_root=data, replace=True)
    target.unlink()
    store_batch(batch, target)
    sidecar = tmp_path / "real.db-wal"
    sidecar.touch()
    assert sync_database(target, main, data_root=data, dry_run=True).dry_run
    with pytest.raises(RuntimeError, match="Close the dashboard"):
        sync_database(target, main, data_root=data, replace=True)
    assert target.exists()
