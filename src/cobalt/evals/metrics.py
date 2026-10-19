"""Metrics for migration runs, computed from the state store and the traces.

Module level: status, attempts to pass, dollars, tokens (input / cache read /
cache write / output), wall time, LLM calls, tool calls and tool errors.
Run level: success rate, attempts per accepted module, cost per accepted
module, the failure taxonomy and spend per role.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from cobalt.memory.state import MigrationState

#: Failure taxonomy (module.failure values), in report order.
TAXONOMY = (
    "compile_error",
    "semantic_diff",
    "static_check",
    "review",
    "budget_exhausted",
    "tool_misuse",
    "loop",
    "stopped",
    "refused",
    "provider_error",
    "blocked",
)


@dataclass
class ModuleMetrics:
    """Everything measured about one module in one run."""

    program: str
    status: str
    failure: str | None
    attempts: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    wall_s: float
    llm_calls: int
    tool_calls: int
    tool_errors: int
    verdict: str
    roles: dict[str, float] = field(default_factory=dict)  # role -> cost

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass
class RunMetrics:
    """Aggregates for one run."""

    run_id: str
    corpus: str
    config: dict[str, Any]
    modules: list[ModuleMetrics]

    @property
    def accepted(self) -> int:
        return sum(1 for m in self.modules if m.accepted)

    @property
    def success_rate(self) -> float:
        return self.accepted / len(self.modules) if self.modules else 0.0

    @property
    def cost_usd(self) -> float:
        return sum(m.cost_usd for m in self.modules)

    @property
    def tokens(self) -> int:
        return sum(m.input_tokens + m.output_tokens for m in self.modules)

    @property
    def wall_s(self) -> float:
        return sum(m.wall_s for m in self.modules)

    @property
    def mean_attempts_to_pass(self) -> float | None:
        passed = [m.attempts for m in self.modules if m.accepted]
        return sum(passed) / len(passed) if passed else None

    @property
    def cost_per_accepted(self) -> float | None:
        return self.cost_usd / self.accepted if self.accepted else None

    def failures(self) -> dict[str, int]:
        counts = Counter(m.failure for m in self.modules if not m.accepted and m.failure)
        return {k: counts[k] for k in TAXONOMY if counts.get(k)}

    def role_costs(self) -> dict[str, float]:
        out: Counter[str] = Counter()
        for m in self.modules:
            out.update(m.roles)
        return dict(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "corpus": self.corpus,
            "config": self.config,
            "accepted": self.accepted,
            "modules_total": len(self.modules),
            "success_rate": self.success_rate,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "wall_s": self.wall_s,
            "mean_attempts_to_pass": self.mean_attempts_to_pass,
            "cost_per_accepted": self.cost_per_accepted,
            "failures": self.failures(),
            "role_costs": self.role_costs(),
            "modules": [asdict(m) for m in self.modules],
        }


def run_metrics(db_path: Path, run_id: str) -> RunMetrics:
    """Collect metrics for ``run_id`` from ``runs/cobalt.db``."""
    state = MigrationState(db_path)
    info = next((r for r in state.runs() if r["run_id"] == run_id), None)
    if info is None:
        raise KeyError(f"no run {run_id}")
    db = sqlite3.connect(db_path)
    trace_rows = db.execute(
        "SELECT task_id, kind, ok, input_tokens, cache_read, cache_write FROM trace_events "
        "WHERE run_id=?",
        (run_id,),
    ).fetchall()
    per_task: dict[str, Counter[str]] = {}
    for task_id, kind, ok, tin, cread, cwrite in trace_rows:
        c = per_task.setdefault(task_id, Counter())
        if kind == "llm_call":
            c["llm_calls"] += 1
            c["input_tokens"] += tin or 0
            c["cache_read"] += cread or 0
            c["cache_write"] += cwrite or 0
        elif kind == "tool_call":
            c["tool_calls"] += 1
            if ok == 0:
                c["tool_errors"] += 1
    modules = []
    for row in state.modules(run_id):
        counters: Counter[str] = Counter()
        for task_id, c in per_task.items():
            if task_id.startswith(f"{row.program}-"):
                counters.update(c)
        roles: Counter[str] = Counter()
        for a in state.attempts(run_id, row.program):
            roles[a["role"]] += a["cost_usd"]
        modules.append(
            ModuleMetrics(
                program=row.program,
                status=row.status,
                failure=row.failure,
                attempts=row.attempts,
                cost_usd=row.cost_usd,
                input_tokens=counters["input_tokens"],
                output_tokens=row.output_tokens,
                cache_read_tokens=counters["cache_read"],
                cache_write_tokens=counters["cache_write"],
                wall_s=row.wall_s,
                llm_calls=counters["llm_calls"],
                tool_calls=counters["tool_calls"],
                tool_errors=counters["tool_errors"],
                verdict=row.verdict or "",
                roles=dict(roles),
            )
        )
    db.close()
    return RunMetrics(run_id, info["corpus"], info["config"], modules)
