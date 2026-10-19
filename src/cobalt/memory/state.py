"""Persistent migration state (SQLite, ``runs/cobalt.db``).

Per run: one row per module (status, attempts, spend, verdict), every agent
attempt, the artifacts agents hand each other (analysis, review, failure
notes, folded summaries) and every supervisor decision. Because everything
the supervisor needs is here, a run can be killed at any point and resumed:
accepted modules are never redone.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id   TEXT PRIMARY KEY,
    corpus   TEXT NOT NULL,
    created  TEXT NOT NULL,
    config   TEXT NOT NULL,
    status   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS modules (
    run_id        TEXT NOT NULL,
    program       TEXT NOT NULL,
    status        TEXT NOT NULL,     -- pending | in_progress | accepted | failed | blocked
    attempts      INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    wall_s        REAL NOT NULL DEFAULT 0,
    verdict       TEXT,
    failure       TEXT,              -- failure-taxonomy category of the final attempt
    updated       TEXT NOT NULL,
    PRIMARY KEY (run_id, program)
);
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    program       TEXT NOT NULL,
    attempt       INTEGER NOT NULL,
    role          TEXT NOT NULL,
    status        TEXT NOT NULL,
    steps         INTEGER NOT NULL,
    cost_usd      REAL NOT NULL,
    input_tokens  INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    wall_s        REAL NOT NULL,
    summary       TEXT,
    reason        TEXT,
    ended         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    run_id   TEXT NOT NULL,
    program  TEXT NOT NULL,
    kind     TEXT NOT NULL,          -- analysis | tests | review | failure | summary
    content  TEXT NOT NULL,
    created  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL,
    program  TEXT NOT NULL,
    decision TEXT NOT NULL,          -- start | retry | escalate | accept | fail | block | skip
    detail   TEXT,
    ts       TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ModuleRow:
    """One module's state."""

    program: str
    status: str
    attempts: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    wall_s: float
    verdict: str | None
    failure: str | None

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class MigrationState:
    """Read/write access to the state tables."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def close(self) -> None:
        self._db.close()

    # runs ------------------------------------------------------------- #
    def start_run(self, run_id: str, corpus: str, config: dict[str, Any]) -> bool:
        """Create the run if new; returns True when resuming an existing run."""
        existing = self._exec("SELECT run_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            self._exec("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
            return True
        self._exec(
            "INSERT INTO runs VALUES (?,?,?,?,?)",
            (run_id, corpus, _now(), json.dumps(config, sort_keys=True), "running"),
        )
        return False

    def finish_run(self, run_id: str, status: str) -> None:
        self._exec("UPDATE runs SET status=? WHERE run_id=?", (status, run_id))

    def run_config(self, run_id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT config FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def runs(self) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT run_id, corpus, created, status, config FROM runs ORDER BY created"
        ).fetchall()
        return [
            {
                "run_id": r[0],
                "corpus": r[1],
                "created": r[2],
                "status": r[3],
                "config": json.loads(r[4]),
            }
            for r in rows
        ]

    # modules ---------------------------------------------------------- #
    def ensure_module(self, run_id: str, program: str) -> None:
        self._exec(
            "INSERT OR IGNORE INTO modules (run_id, program, status, updated) VALUES (?,?,?,?)",
            (run_id, program, "pending", _now()),
        )

    def set_status(
        self,
        run_id: str,
        program: str,
        status: str,
        *,
        verdict: str | None = None,
        failure: str | None = None,
    ) -> None:
        self._exec(
            "UPDATE modules SET status=?, verdict=COALESCE(?, verdict), failure=?, "
            "updated=? WHERE run_id=? AND program=?",
            (status, verdict, failure, _now(), run_id, program),
        )

    def module(self, run_id: str, program: str) -> ModuleRow | None:
        row = self._exec(
            "SELECT program, status, attempts, cost_usd, input_tokens, output_tokens, wall_s, "
            "verdict, failure FROM modules WHERE run_id=? AND program=?",
            (run_id, program),
        ).fetchone()
        return ModuleRow(*row) if row else None

    def modules(self, run_id: str) -> list[ModuleRow]:
        rows = self._exec(
            "SELECT program, status, attempts, cost_usd, input_tokens, output_tokens, wall_s, "
            "verdict, failure FROM modules WHERE run_id=? ORDER BY rowid",
            (run_id,),
        ).fetchall()
        return [ModuleRow(*r) for r in rows]

    # attempts --------------------------------------------------------- #
    def record_attempt(
        self,
        run_id: str,
        program: str,
        attempt: int,
        role: str,
        status: str,
        steps: int,
        usage: dict[str, Any],
        wall_s: float,
        summary: str,
        reason: str,
        *,
        counts_as_attempt: bool,
    ) -> None:
        tokens_in = (
            usage.get("input_tokens", 0)
            + usage.get("cache_read_tokens", 0)
            + usage.get("cache_write_tokens", 0)
        )
        tokens_out = usage.get("output_tokens", 0)
        cost = usage.get("cost_usd", 0.0)
        self._exec(
            "INSERT INTO attempts (run_id, program, attempt, role, status, steps, cost_usd, "
            "input_tokens, output_tokens, wall_s, summary, reason, ended) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                program,
                attempt,
                role,
                status,
                steps,
                cost,
                tokens_in,
                tokens_out,
                wall_s,
                summary[:4000],
                reason[:2000],
                _now(),
            ),
        )
        self._exec(
            "UPDATE modules SET attempts = attempts + ?, cost_usd = cost_usd + ?, "
            "input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
            "wall_s = wall_s + ?, updated=? WHERE run_id=? AND program=?",
            (
                1 if counts_as_attempt else 0,
                cost,
                tokens_in,
                tokens_out,
                wall_s,
                _now(),
                run_id,
                program,
            ),
        )

    def attempts(self, run_id: str, program: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT program, attempt, role, status, steps, cost_usd, input_tokens, "
            "output_tokens, wall_s, summary, reason FROM attempts WHERE run_id=?"
        )
        params: tuple[Any, ...] = (run_id,)
        if program:
            sql += " AND program=?"
            params += (program,)
        cols = [
            "program",
            "attempt",
            "role",
            "status",
            "steps",
            "cost_usd",
            "input_tokens",
            "output_tokens",
            "wall_s",
            "summary",
            "reason",
        ]
        return [
            dict(zip(cols, r, strict=True))
            for r in self._exec(sql + " ORDER BY id", params).fetchall()
        ]

    # artifacts -------------------------------------------------------- #
    def put_artifact(self, run_id: str, program: str, kind: str, content: str) -> None:
        self._exec(
            "INSERT INTO artifacts VALUES (?,?,?,?,?)", (run_id, program, kind, content, _now())
        )

    def artifact(self, run_id: str, program: str, kind: str) -> str | None:
        """Latest artifact of ``kind`` for a module."""
        row = self._exec(
            "SELECT content FROM artifacts WHERE run_id=? AND program=? AND "
            "kind=? ORDER BY rowid DESC LIMIT 1",
            (run_id, program, kind),
        ).fetchone()
        return row[0] if row else None

    def artifacts(self, run_id: str, program: str, kind: str) -> list[str]:
        rows = self._exec(
            "SELECT content FROM artifacts WHERE run_id=? AND program=? AND kind=? ORDER BY rowid",
            (run_id, program, kind),
        ).fetchall()
        return [r[0] for r in rows]

    # decisions -------------------------------------------------------- #
    def decide(self, run_id: str, program: str, decision: str, detail: str = "") -> None:
        self._exec(
            "INSERT INTO decisions (run_id, program, decision, detail, ts) VALUES (?,?,?,?,?)",
            (run_id, program, decision, detail[:2000], _now()),
        )

    def decisions(self, run_id: str, program: str | None = None) -> list[tuple[str, str, str]]:
        sql = "SELECT program, decision, detail FROM decisions WHERE run_id=?"
        params: tuple[Any, ...] = (run_id,)
        if program:
            sql += " AND program=?"
            params += (program,)
        return [tuple(r) for r in self._exec(sql + " ORDER BY id", params).fetchall()]
