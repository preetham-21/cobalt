"""The agent loop: model turn -> tool calls -> results -> repeat, under budget.

Hand-built on the Messages API on purpose. One :class:`AgentLoop` runs one
task for one agent role and always terminates with an :class:`AgentResult`
whose status is one of the failure-taxonomy values below:

``success``          the agent called ``finish`` and the harness accepted it
``failed``           the agent called ``finish`` admitting failure
``budget_exhausted`` a step/token/cost/time limit was hit
``loop``             the same tool call was repeated without progress
``tool_misuse``      too many invalid or failing tool calls
``refused``          the model declined (``stop_reason == "refusal"``)
``stopped``          the model stopped talking without calling ``finish``
``error``            the provider failed and retries were exhausted

The conversation is append-only (earlier turns are never edited), which
preserved thinking on current models requires; context management is done
by starting a fresh *episode* (see :class:`ContextPolicy`), never by
rewriting history in place.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from cobalt.harness.budget import Budget, BudgetTracker
from cobalt.harness.checkpoint import Checkpoint, CheckpointStore
from cobalt.harness.llm import LLMClient, LLMError, LLMRequest, Usage
from cobalt.harness.tools import ToolRegistry
from cobalt.harness.trace import Tracer


class AgentStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOOP = "loop"
    TOOL_MISUSE = "tool_misuse"
    REFUSED = "refused"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class AgentSpec:
    """Static configuration of one agent role."""

    name: str
    system: str
    tools: ToolRegistry
    model: str
    effort: str = "high"
    max_tokens: int = 32000
    budget: Budget = field(default_factory=Budget)
    max_nudges: int = 2
    loop_window: int = 4  # identical consecutive calls that count as a loop
    max_tool_errors: int = 12


@dataclass
class AgentResult:
    """How a task ended."""

    status: AgentStatus
    summary: str
    steps: int
    usage: Usage
    reason: str = ""
    data: dict[str, Any] = field(default_factory=dict)  # finish payload
    episodes: int = 1

    @property
    def ok(self) -> bool:
        return self.status is AgentStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "steps": self.steps,
            "usage": self.usage.to_dict(),
            "reason": self.reason,
            "data": self.data,
            "episodes": self.episodes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentResult:
        return cls(
            AgentStatus(d["status"]),
            d["summary"],
            d["steps"],
            Usage.from_dict(d["usage"]),
            d.get("reason", ""),
            d.get("data", {}),
            d.get("episodes", 1),
        )


class ContextPolicy(Protocol):
    """Decides when the conversation is too large and builds a fresh episode."""

    def maybe_new_episode(
        self, messages: list[dict[str, Any]], last_input_tokens: int, task: str
    ) -> list[dict[str, Any]] | None:
        """Return replacement messages to start a new episode, or ``None``."""
        ...


class NoContextPolicy:
    """Never compacts (Stage 4 default)."""

    def maybe_new_episode(
        self, messages: list[dict[str, Any]], last_input_tokens: int, task: str
    ) -> list[dict[str, Any]] | None:
        return None


NUDGE = (
    "You ended your turn without calling a tool. Keep working with the tools, and call "
    "`finish` when the task is complete (or when you cannot complete it)."
)
CONTINUE = "Your previous response hit the output limit and was cut off. Continue from there."
RETRY_DELAYS_S = (2.0, 8.0, 30.0)


def _signature(name: str, tool_input: Any) -> str:
    raw = json.dumps([name, tool_input], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AgentLoop:
    """Runs one task for one agent; traced, budgeted, checkpointed."""

    def __init__(
        self,
        spec: AgentSpec,
        client: LLMClient,
        tracer: Tracer,
        checkpoints: CheckpointStore,
        *,
        run_id: str,
        task_id: str,
        context: ContextPolicy | None = None,
        retry_delays: tuple[float, ...] = RETRY_DELAYS_S,
    ) -> None:
        self.spec = spec
        self.client = client
        self.tracer = tracer
        self.checkpoints = checkpoints
        self.run_id = run_id
        self.task_id = task_id
        self.context = context or NoContextPolicy()
        self.retry_delays = retry_delays

    # ------------------------------------------------------------------ #
    def run(self, task: str, *, resume: bool = True) -> AgentResult:
        cp = self.checkpoints.load(self.run_id, self.task_id) if resume else None
        if cp is not None and cp.status == "finished" and "result" in cp.state:
            return AgentResult.from_dict(cp.state["result"])

        if cp is not None:
            messages = cp.messages
            tracker = BudgetTracker(
                self.spec.budget,
                steps=cp.step,
                usage=Usage.from_dict(cp.usage),
                elapsed_before_s=cp.elapsed_s,
            )
            state = cp.state
        else:
            messages = [{"role": "user", "content": task}]
            tracker = BudgetTracker(self.spec.budget)
            state = {"nudges": 0, "tool_errors": 0, "recent": [], "episodes": 1}

        self.tracer.event(
            "agent_start",
            tracker.steps,
            name=self.spec.name,
            model=self.spec.model,
            provider=self.client.name,
            resumed=cp is not None,
            budget=self.spec.budget.to_dict(),
            tools=self.spec.tools.names,
        )
        tool_schemas = self.spec.tools.schemas()
        last_input = 0

        while True:
            why = tracker.exceeded()
            if why:
                return self._finish(
                    AgentStatus.BUDGET_EXHAUSTED,
                    f"budget exhausted: {why}",
                    tracker,
                    messages,
                    state,
                    reason=why,
                )

            replacement = self.context.maybe_new_episode(messages, last_input, task)
            if replacement is not None:
                state["episodes"] = state.get("episodes", 1) + 1
                self.tracer.event(
                    "new_episode",
                    tracker.steps,
                    episode=state["episodes"],
                    old_messages=len(messages),
                    old_input_tokens=last_input,
                )
                messages = replacement
                state["recent"] = []

            request = LLMRequest(
                model=self.spec.model,
                system=self.spec.system,
                messages=list(messages),  # snapshot: the list keeps growing
                tools=tool_schemas,
                max_tokens=self.spec.max_tokens,
                effort=self.spec.effort,
            )
            response = None
            for attempt in range(len(self.retry_delays) + 1):
                try:
                    response = self.client.complete(request)
                    break
                except LLMError as exc:
                    self.tracer.event(
                        "llm_error",
                        tracker.steps,
                        ok=False,
                        error=str(exc),
                        retryable=exc.retryable,
                        attempt=attempt,
                    )
                    if not exc.retryable or attempt == len(self.retry_delays):
                        return self._finish(
                            AgentStatus.ERROR,
                            f"provider error: {exc}",
                            tracker,
                            messages,
                            state,
                            reason=str(exc),
                        )
                    time.sleep(self.retry_delays[attempt])
            assert response is not None

            tracker.record(response.usage)
            last_input = response.usage.total_input
            self.tracer.event(
                "llm_call",
                tracker.steps,
                name=response.model,
                duration_s=response.duration_s,
                usage=response.usage.to_dict(),
                stop_reason=response.stop_reason,
                prompt_messages=len(messages),
                prompt_chars=sum(len(json.dumps(m, default=str)) for m in messages),
                tool_calls=[t["name"] for t in response.tool_uses],
                text=response.text[:2000],
                response_id=response.response_id,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                details = response.extra.get("stop_details") or {}
                return self._finish(
                    AgentStatus.REFUSED,
                    "model declined the request",
                    tracker,
                    messages,
                    state,
                    reason=json.dumps(details),
                )

            tool_uses = response.tool_uses
            if not tool_uses:
                if response.stop_reason == "max_tokens":
                    messages.append({"role": "user", "content": CONTINUE})
                elif state["nudges"] >= self.spec.max_nudges:
                    return self._finish(
                        AgentStatus.STOPPED,
                        response.text[:500] or "stopped without finishing",
                        tracker,
                        messages,
                        state,
                        reason="no tool call",
                    )
                else:
                    state["nudges"] += 1
                    messages.append({"role": "user", "content": NUDGE})
                self._checkpoint(tracker, messages, state)
                continue

            results = []
            terminal = None
            for tu in tool_uses:
                outcome = self.spec.tools.call(tu["id"], tu["name"], tu.get("input"))
                self.tracer.event(
                    "tool_call",
                    tracker.steps,
                    name=tu["name"],
                    duration_s=outcome.duration_s,
                    ok=not outcome.is_error,
                    input=_summarise_input(tu.get("input")),
                    result_chars=len(outcome.content),
                    error_kind=outcome.error_kind,
                    result=outcome.content[:1500],
                )
                results.append(outcome.to_block())
                if outcome.is_error:
                    state["tool_errors"] += 1
                if outcome.terminal:
                    terminal = outcome
                state["recent"] = [*state["recent"], _signature(tu["name"], tu.get("input"))][
                    -self.spec.loop_window :
                ]
            messages.append({"role": "user", "content": results})
            self._checkpoint(tracker, messages, state)

            if terminal is not None:
                data = terminal.data or {}
                status = AgentStatus.SUCCESS if data.get("success") else AgentStatus.FAILED
                return self._finish(
                    status,
                    data.get("summary", ""),
                    tracker,
                    messages,
                    state,
                    data=data,
                    reason=data.get("reason", ""),
                )
            recent = state["recent"]
            if len(recent) == self.spec.loop_window and len(set(recent)) == 1:
                return self._finish(
                    AgentStatus.LOOP,
                    "repeated the same tool call",
                    tracker,
                    messages,
                    state,
                    reason=f"{tool_uses[-1]['name']} x{len(recent)}",
                )
            if state["tool_errors"] > self.spec.max_tool_errors:
                return self._finish(
                    AgentStatus.TOOL_MISUSE,
                    "too many failed tool calls",
                    tracker,
                    messages,
                    state,
                    reason=f"{state['tool_errors']} tool errors",
                )

    # ------------------------------------------------------------------ #
    def _checkpoint(
        self,
        tracker: BudgetTracker,
        messages: list[dict[str, Any]],
        state: dict[str, Any],
        status: str = "running",
    ) -> None:
        self.checkpoints.save(
            Checkpoint(
                self.run_id,
                self.task_id,
                self.spec.name,
                tracker.steps,
                messages,
                tracker.usage.to_dict(),
                tracker.elapsed_s,
                status,
                state,
            )
        )

    def _finish(
        self,
        status: AgentStatus,
        summary: str,
        tracker: BudgetTracker,
        messages: list[dict[str, Any]],
        state: dict[str, Any],
        *,
        reason: str = "",
        data: dict[str, Any] | None = None,
    ) -> AgentResult:
        result = AgentResult(
            status,
            summary,
            tracker.steps,
            tracker.usage,
            reason,
            data or {},
            state.get("episodes", 1),
        )
        state = {**state, "result": result.to_dict()}
        self._checkpoint(tracker, messages, state, status="finished")
        self.tracer.event(
            "agent_end",
            tracker.steps,
            name=self.spec.name,
            ok=result.ok,
            status=status.value,
            summary=summary[:1000],
            reason=reason,
            usage=tracker.usage.to_dict(),
            wall_s=round(tracker.elapsed_s, 2),
            episodes=result.episodes,
        )
        return result


def _summarise_input(value: Any, limit: int = 300) -> Any:
    """Shorten long string fields (file contents) for traces."""
    if isinstance(value, dict):
        return {k: _summarise_input(v, limit) for k, v in value.items()}
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value)} chars]"
    return value
