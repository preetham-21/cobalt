"""Eval suites: the same modules migrated under several configurations.

Each *variant* is a full migration run (its own run id, workspace and
state), so variants never share work. The standard ablations switch off the
knowledge graph (dependency order, callee facts, ``query_graph``) and/or
context folding (callee summaries, episode compaction, ``get_summary``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cobalt.agents.supervisor import Supervisor, SupervisorConfig
from cobalt.config import Settings
from cobalt.evals.metrics import RunMetrics, run_metrics
from cobalt.harness.budget import Budget

VARIANTS: dict[str, dict[str, bool]] = {
    "full": {"use_graph": True, "use_folding": True},
    "no-graph": {"use_graph": False, "use_folding": True},
    "no-folding": {"use_graph": True, "use_folding": False},
    "neither": {"use_graph": False, "use_folding": False},
}


@dataclass
class EvalConfig:
    """What to run."""

    corpus: str = "synthetic"
    programs: list[str] | None = None
    variants: list[str] = field(default_factory=lambda: list(VARIANTS))
    provider: str = "anthropic"
    model: str = "claude-opus-5-5"
    budget: Budget = field(default_factory=Budget)
    repeats: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["budget"] = self.budget.to_dict()
        return d


@dataclass
class EvalResult:
    """All runs of one eval, keyed by variant."""

    eval_id: str
    config: dict[str, Any]
    started: str
    runs: dict[str, list[RunMetrics]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "eval_id": self.eval_id,
            "config": self.config,
            "started": self.started,
            "runs": {v: [r.to_dict() for r in rs] for v, rs in self.runs.items()},
        }


def eval_dir(settings: Settings, eval_id: str) -> Path:
    return settings.runs_dir / "evals" / eval_id


def run_eval(
    settings: Settings, config: EvalConfig, eval_id: str, *, on_run: Any = None
) -> EvalResult:
    """Run every variant (resumable: finished runs are not redone)."""
    unknown = [v for v in config.variants if v not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variants {unknown}; known: {', '.join(VARIANTS)}")
    started = datetime.now(UTC).isoformat(timespec="seconds")
    runs: dict[str, list[RunMetrics]] = {}
    for variant in config.variants:
        for rep in range(1, config.repeats + 1):
            run_id = f"{eval_id}-{variant}-r{rep}"
            sup = Supervisor(
                settings,
                config.corpus,
                run_id,
                SupervisorConfig(
                    provider=config.provider,
                    model=config.model,
                    budget=config.budget,
                    **VARIANTS[variant],
                ),
            )
            sup.run(config.programs)
            metrics = run_metrics(settings.runs_dir / "cobalt.db", run_id)
            runs.setdefault(variant, []).append(metrics)
            if on_run is not None:
                on_run(variant, metrics)
    result = EvalResult(eval_id, config.to_dict(), started, runs)
    out = eval_dir(settings, eval_id)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


def load_eval(settings: Settings, eval_id: str) -> dict[str, Any]:
    """Results JSON of a finished eval."""
    return json.loads((eval_dir(settings, eval_id) / "results.json").read_text(encoding="utf-8"))
