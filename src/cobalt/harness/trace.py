"""Structured traces: every LLM call and tool call, as JSONL and in SQLite.

``runs/traces/<run_id>/<task_id>.jsonl`` holds the full event stream of one
agent task (one JSON object per line). The same events are indexed in
``runs/cobalt.db`` (table ``trace_events``) with token and cost columns so
metrics are a SQL query away. If it isn't traced, it didn't happen.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cobalt.logging_setup import make_redactor

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    run_id        TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    agent         TEXT NOT NULL,
    step          INTEGER NOT NULL,
    kind          TEXT NOT NULL,
    name          TEXT,
    duration_ms   REAL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read    INTEGER DEFAULT 0,
    cache_write   INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    ok            INTEGER,
    data          TEXT
);
CREATE INDEX IF NOT EXISTS trace_run ON trace_events(run_id, task_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (and initialise) the cobalt SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


class Tracer:
    """Writes events for one (run, task, agent)."""

    def __init__(
        self,
        runs_dir: Path,
        run_id: str,
        task_id: str,
        agent: str,
        secrets: list[str] | None = None,
    ) -> None:
        self.run_id = run_id
        self.task_id = task_id
        self.agent = agent
        self.path = runs_dir / "traces" / run_id / f"{task_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = connect(runs_dir / "cobalt.db")
        self._redact = make_redactor(secrets or [])
        self._lock = threading.Lock()

    def event(
        self,
        kind: str,
        step: int,
        *,
        name: str | None = None,
        duration_s: float | None = None,
        usage: dict[str, Any] | None = None,
        ok: bool | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        """Record one event and return it."""
        usage = usage or {}
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "step": step,
            "kind": kind,
            "name": name,
            "duration_ms": round(duration_s * 1000, 1) if duration_s is not None else None,
            **({"usage": usage} if usage else {}),
            **({"ok": ok} if ok is not None else {}),
            **data,
        }
        record = dict(self._redact(None, kind, record))
        line = json.dumps(record, default=str, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._db.execute(
                "INSERT INTO trace_events (ts, run_id, task_id, agent, step, kind, name, "
                "duration_ms, input_tokens, output_tokens, cache_read, cache_write, cost_usd, "
                "ok, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record["ts"],
                    self.run_id,
                    self.task_id,
                    self.agent,
                    step,
                    kind,
                    name,
                    record["duration_ms"],
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cache_read_tokens", 0),
                    usage.get("cache_write_tokens", 0),
                    usage.get("cost_usd", 0.0),
                    None if ok is None else int(ok),
                    line,
                ),
            )
            self._db.commit()
        return record

    def close(self) -> None:
        self._db.close()


def read_trace(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL trace."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class Stopwatch:
    """``with Stopwatch() as sw: ...; sw.elapsed``"""

    def __enter__(self) -> Stopwatch:
        self.start = time.monotonic()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc: object) -> None:
        self.elapsed = time.monotonic() - self.start
