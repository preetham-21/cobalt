"""Per-task budgets: steps, tokens, dollars and wall time - hard stops."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from cobalt.harness.llm import Usage


@dataclass(frozen=True)
class Budget:
    """Limits for one agent task. ``None`` means unlimited."""

    max_steps: int | None = 40
    max_input_tokens: int | None = 2_000_000
    max_output_tokens: int | None = 200_000
    max_cost_usd: float | None = 5.0
    max_wall_s: float | None = 3600.0

    def scaled(self, factor: float) -> Budget:
        """A proportionally larger (or smaller) budget, e.g. for escalation."""

        def mul(v: float | int | None) -> float | int | None:
            if v is None:
                return None
            return type(v)(v * factor)

        return Budget(
            max_steps=mul(self.max_steps),  # type: ignore[arg-type]
            max_input_tokens=mul(self.max_input_tokens),  # type: ignore[arg-type]
            max_output_tokens=mul(self.max_output_tokens),  # type: ignore[arg-type]
            max_cost_usd=mul(self.max_cost_usd),  # type: ignore[arg-type]
            max_wall_s=mul(self.max_wall_s),  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BudgetTracker:
    """Accumulates spend against a :class:`Budget`."""

    budget: Budget
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    elapsed_before_s: float = 0.0  # wall time spent before a resume
    started: float = field(default_factory=time.monotonic)

    @property
    def elapsed_s(self) -> float:
        return self.elapsed_before_s + (time.monotonic() - self.started)

    def record(self, usage: Usage) -> None:
        self.steps += 1
        self.usage.add(usage)

    def exceeded(self) -> str | None:
        """Name of the first exhausted limit, or ``None``."""
        b = self.budget
        if b.max_steps is not None and self.steps >= b.max_steps:
            return f"max_steps ({b.max_steps})"
        if b.max_input_tokens is not None and self.usage.total_input >= b.max_input_tokens:
            return f"max_input_tokens ({b.max_input_tokens})"
        if b.max_output_tokens is not None and self.usage.output_tokens >= b.max_output_tokens:
            return f"max_output_tokens ({b.max_output_tokens})"
        if b.max_cost_usd is not None and self.usage.cost_usd >= b.max_cost_usd:
            return f"max_cost_usd ({b.max_cost_usd})"
        if b.max_wall_s is not None and self.elapsed_s >= b.max_wall_s:
            return f"max_wall_s ({b.max_wall_s})"
        return None

    def remaining_summary(self) -> str:
        b = self.budget
        parts = []
        if b.max_steps is not None:
            parts.append(f"steps {self.steps}/{b.max_steps}")
        if b.max_cost_usd is not None:
            parts.append(f"cost ${self.usage.cost_usd:.2f}/${b.max_cost_usd:.2f}")
        return ", ".join(parts)
