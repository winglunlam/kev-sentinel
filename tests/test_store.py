from __future__ import annotations

import json

from sentinel.core.fetch import synthetic_result
from sentinel.core.fingerprint import fingerprint_result
from sentinel.core.store import Store


def _snapshot(store: Store, endpoint: str = "jobs", n: int = 1):
    result = synthetic_result(endpoint, body=json.dumps({"items": [{"id": i} for i in range(n)]}).encode())
    return store.record_snapshot(endpoint, result, fingerprint_result(result))


def test_snapshot_and_baseline_round_trip(tmp_path):
    store = Store(tmp_path / "sentinel.db")
    try:
        snapshot = _snapshot(store, n=3)
        assert snapshot.item_count == 3
        assert store.baseline("jobs") is None
        store.set_baseline("jobs", snapshot.id)
        baseline = store.baseline("jobs")
        assert baseline is not None and baseline.id == snapshot.id
        assert store.baseline_set_at("jobs")
        assert store.latest_snapshot("jobs").id == snapshot.id
    finally:
        store.close()


def test_prune_keeps_baseline(tmp_path):
    store = Store(tmp_path / "sentinel.db")
    try:
        first = _snapshot(store, n=1)
        store.set_baseline("jobs", first.id)
        for index in range(3):
            _snapshot(store, n=index + 2)
        with store._db:
            count = store._db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        # KEEP_SNAPSHOTS is 200, so all four remain; the point is the baseline is never pruned
        assert count == 4
        assert store.baseline("jobs").id == first.id
    finally:
        store.close()


def test_runs_and_flush(tmp_path):
    store = Store(tmp_path / "sentinel.db")
    try:
        run_id = store.record_run("jobs", False, {"reasons": ["bad"]})
        assert store.last_run("jobs").ok is False
        store.mark_alerted(run_id)
        assert store.last_alert_at("jobs")
        assert store.recent_runs(5)[0].summary["reasons"] == ["bad"]
        snapshot = _snapshot(store)
        store.set_baseline("jobs", snapshot.id)
        store.flush_endpoint("jobs")
        assert store.baseline("jobs") is None
        assert store.recent_runs(5) == []
    finally:
        store.close()
