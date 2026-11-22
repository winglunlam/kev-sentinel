"""SQLite snapshot store: baselines, response history, and check runs.

Everything lives in one file under ``.sentinel/`` (gitignored) next to the
config, so a repo checkout carries the monitoring definition and the data stays
local.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .fetch import FetchResult

MAX_STORED_BODY = 1_000_000
KEEP_SNAPSHOTS = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    ts TEXT NOT NULL,
    status INTEGER,
    content_type TEXT NOT NULL,
    body TEXT NOT NULL,
    body_truncated INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_endpoint_id ON snapshots(endpoint, id DESC);

CREATE TABLE IF NOT EXISTS baselines (
    endpoint TEXT PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    set_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL,
    summary TEXT NOT NULL,
    alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_endpoint_id ON runs(endpoint, id DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Snapshot:
    id: int
    endpoint: str
    ts: str
    status: int | None
    content_type: str
    body: str
    body_truncated: bool
    sha256: str
    fingerprint: dict[str, Any]
    source: str

    @property
    def item_count(self) -> int | None:
        value = self.fingerprint.get("item_count")
        return value if isinstance(value, int) else None

    @property
    def nbytes(self) -> int:
        stats = self.fingerprint.get("stats", {})
        return int(stats.get("bytes", len(self.body)))


@dataclass(frozen=True)
class RunRecord:
    id: int
    endpoint: str
    ts: str
    ok: bool
    summary: dict[str, Any]
    alerted: bool


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @classmethod
    def for_home(cls, home: Path) -> "Store":
        return cls(Path(home) / "sentinel.db")

    # -- snapshots ---------------------------------------------------------

    def record_snapshot(self, endpoint: str, result: FetchResult, fingerprint: dict[str, Any]) -> Snapshot:
        body = result.text
        truncated = len(body) > MAX_STORED_BODY
        if truncated:
            body = body[:MAX_STORED_BODY]
        sha = hashlib.sha256(result.body).hexdigest()
        ts = utcnow()
        cursor = self._db.execute(
            """INSERT INTO snapshots
               (endpoint, ts, status, content_type, body, body_truncated, sha256, fingerprint, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                endpoint,
                ts,
                result.status,
                result.content_type,
                body,
                int(truncated),
                sha,
                json.dumps(fingerprint, ensure_ascii=False, sort_keys=True),
                result.source,
            ),
        )
        self._db.commit()
        snapshot = Snapshot(
            id=int(cursor.lastrowid),
            endpoint=endpoint,
            ts=ts,
            status=result.status,
            content_type=result.content_type,
            body=body,
            body_truncated=truncated,
            sha256=sha,
            fingerprint=fingerprint,
            source=result.source,
        )
        self._prune(endpoint)
        return snapshot

    def _prune(self, endpoint: str) -> None:
        baseline_id = self._db.execute(
            "SELECT snapshot_id FROM baselines WHERE endpoint = ?", (endpoint,)
        ).fetchone()
        keep = {baseline_id["snapshot_id"]} if baseline_id else set()
        rows = self._db.execute(
            "SELECT id FROM snapshots WHERE endpoint = ? ORDER BY id DESC LIMIT ?",
            (endpoint, KEEP_SNAPSHOTS),
        ).fetchall()
        keep.update(row["id"] for row in rows)
        placeholders = ",".join("?" for _ in keep)
        self._db.execute(
            f"DELETE FROM snapshots WHERE endpoint = ? AND id NOT IN ({placeholders})",
            (endpoint, *sorted(keep)),
        )
        self._db.commit()

    def latest_snapshot(self, endpoint: str) -> Snapshot | None:
        row = self._db.execute(
            "SELECT * FROM snapshots WHERE endpoint = ? ORDER BY id DESC LIMIT 1", (endpoint,)
        ).fetchone()
        return _snapshot(row) if row else None

    # -- baselines ---------------------------------------------------------

    def set_baseline(self, endpoint: str, snapshot_id: int) -> None:
        self._db.execute(
            """INSERT INTO baselines (endpoint, snapshot_id, set_at) VALUES (?, ?, ?)
               ON CONFLICT(endpoint) DO UPDATE SET snapshot_id = excluded.snapshot_id,
                                                   set_at = excluded.set_at""",
            (endpoint, snapshot_id, utcnow()),
        )
        self._db.commit()

    def baseline(self, endpoint: str) -> Snapshot | None:
        row = self._db.execute(
            """SELECT s.* FROM baselines b JOIN snapshots s ON s.id = b.snapshot_id
               WHERE b.endpoint = ?""",
            (endpoint,),
        ).fetchone()
        return _snapshot(row) if row else None

    def baseline_set_at(self, endpoint: str) -> str | None:
        row = self._db.execute(
            "SELECT set_at FROM baselines WHERE endpoint = ?", (endpoint,)
        ).fetchone()
        return str(row["set_at"]) if row else None

    # -- runs --------------------------------------------------------------

    def record_run(self, endpoint: str, ok: bool, summary: dict[str, Any]) -> int:
        cursor = self._db.execute(
            "INSERT INTO runs (endpoint, ts, ok, summary) VALUES (?, ?, ?, ?)",
            (endpoint, utcnow(), int(ok), json.dumps(summary, ensure_ascii=False, default=str)),
        )
        self._db.commit()
        return int(cursor.lastrowid)

    def last_run(self, endpoint: str) -> RunRecord | None:
        row = self._db.execute(
            "SELECT * FROM runs WHERE endpoint = ? ORDER BY id DESC LIMIT 1", (endpoint,)
        ).fetchone()
        return _run(row) if row else None

    def recent_runs(self, limit: int = 20) -> list[RunRecord]:
        rows = self._db.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_run(row) for row in rows]

    def last_alert_at(self, endpoint: str) -> str | None:
        row = self._db.execute(
            "SELECT ts FROM runs WHERE endpoint = ? AND alerted = 1 ORDER BY id DESC LIMIT 1",
            (endpoint,),
        ).fetchone()
        return str(row["ts"]) if row else None

    def mark_alerted(self, run_id: int) -> None:
        self._db.execute("UPDATE runs SET alerted = 1 WHERE id = ?", (run_id,))
        self._db.commit()

    def flush_endpoint(self, endpoint: str) -> None:
        """Forget snapshots and runs for an endpoint (baselines included)."""
        self._db.execute("DELETE FROM snapshots WHERE endpoint = ?", (endpoint,))
        self._db.execute("DELETE FROM baselines WHERE endpoint = ?", (endpoint,))
        self._db.execute("DELETE FROM runs WHERE endpoint = ?", (endpoint,))
        self._db.commit()


def _snapshot(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=row["id"],
        endpoint=row["endpoint"],
        ts=row["ts"],
        status=row["status"],
        content_type=row["content_type"],
        body=row["body"],
        body_truncated=bool(row["body_truncated"]),
        sha256=row["sha256"],
        fingerprint=json.loads(row["fingerprint"]),
        source=row["source"],
    )


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=row["id"],
        endpoint=row["endpoint"],
        ts=row["ts"],
        ok=bool(row["ok"]),
        summary=json.loads(row["summary"]),
        alerted=bool(row["alerted"]),
    )