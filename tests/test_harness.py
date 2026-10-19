from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cobalt.harness.budget import Budget, BudgetTracker
from cobalt.harness.checkpoint import CheckpointStore
from cobalt.harness.llm import (
    LLMError,
    LLMResponse,
    ScriptedClient,
    Usage,
    cost_usd,
    scripted_text,
    scripted_tool_call,
)
from cobalt.harness.loop import AgentLoop, AgentSpec, AgentStatus
from cobalt.harness.tools import Tool, ToolError, ToolInput, ToolRegistry, ToolReply, truncate
from cobalt.harness.trace import Tracer, read_trace


class EchoIn(ToolInput):
    text: str


class FinishIn(ToolInput):
    success: bool
    summary: str


class CountIn(ToolInput):
    n: int | None


def registry(calls: list[str] | None = None) -> ToolRegistry:
    calls = calls if calls is not None else []

    def echo(args: EchoIn) -> str:
        calls.append(args.text)
        return f"echo:{args.text}"

    def fail(_: EchoIn) -> str:
        raise ToolError("nope")

    def boom(_: EchoIn) -> str:
        raise RuntimeError("kaboom")

    def finish(args: FinishIn) -> ToolReply:
        return ToolReply("done", {"success": args.success, "summary": args.summary}, terminal=True)

    return ToolRegistry(
        [
            Tool("echo", "Echo text.", EchoIn, echo),
            Tool("fail", "Always fails.", EchoIn, fail),
            Tool("boom", "Crashes.", EchoIn, boom),
            Tool("finish", "Finish.", FinishIn, finish),
        ]
    )


def make_loop(
    tmp_path: Path,
    script,
    *,
    budget: Budget | None = None,
    task_id: str = "t1",
    tools: ToolRegistry | None = None,
    client: ScriptedClient | None = None,
    **spec_kw,
) -> tuple[AgentLoop, ScriptedClient]:
    client = client or ScriptedClient(script)
    spec = AgentSpec(
        "tester", "You test.", tools or registry(), "scripted", budget=budget or Budget(), **spec_kw
    )
    tracer = Tracer(tmp_path, "run1", task_id, "tester")
    loop = AgentLoop(
        spec,
        client,
        tracer,
        CheckpointStore(tmp_path),
        run_id="run1",
        task_id=task_id,
        retry_delays=(0.0, 0.0),
    )
    return loop, client


FINISH_OK = scripted_tool_call("finish", {"success": True, "summary": "all good"})


# --------------------------------------------------------------------------- #
def test_tool_schema_is_strict() -> None:
    schema = registry().schemas()[0]
    assert schema["name"] == "boom" and schema["strict"] is True
    s = schema["input_schema"]
    assert s["additionalProperties"] is False and s["required"] == ["text"]
    count = Tool("count", "c", CountIn, lambda a: "x").schema()["input_schema"]
    assert count["required"] == ["n"]  # optional -> nullable but still required


def test_registry_turns_failures_into_error_results() -> None:
    reg = registry()
    assert reg.call("1", "echo", {"text": "hi"}).content == "echo:hi"
    bad = reg.call("2", "echo", {"wrong": 1})
    assert bad.is_error and bad.error_kind == "invalid_input"
    assert reg.call("3", "nope", {}).error_kind == "unknown_tool"
    assert reg.call("4", "fail", {"text": "x"}).content == "Error: nope"
    crash = reg.call("5", "boom", {"text": "x"})
    assert crash.is_error and crash.error_kind == "crash" and "kaboom" in crash.content
    block = bad.to_block()
    assert block["is_error"] is True and block["tool_use_id"] == "2"


def test_truncate_keeps_head_and_tail() -> None:
    text = "A" * 10_000 + "B" * 10_000
    out = truncate(text, 1000)
    assert out.startswith("A") and out.endswith("B") and "omitted" in out


def test_cost_and_usage() -> None:
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert cost_usd("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.20)
    assert cost_usd("unknown-model", 1_000_000, 0) == pytest.approx(4.0)
    u = Usage(10, 5, 2, 3, 0.5)
    u.add(Usage(1, 1, 1, 1, 0.25))
    assert u.total_input == 18 and u.cost_usd == pytest.approx(0.75)
    assert Usage.from_dict(u.to_dict()).output_tokens == 6


def test_budget_tracker_limits() -> None:
    t = BudgetTracker(Budget(max_steps=2, max_cost_usd=None))
    assert t.exceeded() is None
    t.record(Usage())
    t.record(Usage())
    assert t.exceeded() == "max_steps (2)"
    t = BudgetTracker(Budget(max_steps=None, max_cost_usd=1.0))
    t.record(Usage(cost_usd=1.5))
    assert t.exceeded().startswith("max_cost_usd")
    assert Budget(max_steps=10, max_cost_usd=2.0).scaled(2).max_steps == 20


# --------------------------------------------------------------------------- #
def test_successful_run_is_traced(tmp_path: Path) -> None:
    loop, client = make_loop(
        tmp_path,
        [
            scripted_tool_call("echo", {"text": "a"}, text="thinking out loud"),
            FINISH_OK,
        ],
    )
    result = loop.run("do it")
    assert result.status is AgentStatus.SUCCESS and result.steps == 2
    assert result.data["summary"] == "all good"
    # second request carried the tool result for the first call
    second = client.requests[1].messages
    assert second[-1]["role"] == "user" and second[-1]["content"][0]["content"] == "echo:a"
    events = read_trace(tmp_path / "traces" / "run1" / "t1.jsonl")
    kinds = [e["kind"] for e in events]
    assert kinds == ["agent_start", "llm_call", "tool_call", "llm_call", "tool_call", "agent_end"]
    assert events[1]["usage"]["input_tokens"] > 0
    db = sqlite3.connect(tmp_path / "cobalt.db")
    rows = db.execute("SELECT kind, name FROM trace_events WHERE task_id='t1' ORDER BY id")
    assert [r[0] for r in rows] == kinds


def test_budget_exhaustion_stops(tmp_path: Path) -> None:
    script = [scripted_tool_call("echo", {"text": str(i)}) for i in range(10)]
    loop, _ = make_loop(tmp_path, script, budget=Budget(max_steps=3))
    result = loop.run("go")
    assert result.status is AgentStatus.BUDGET_EXHAUSTED and result.steps == 3
    assert "max_steps" in result.reason


def test_loop_detection(tmp_path: Path) -> None:
    script = [scripted_tool_call("echo", {"text": "same"}) for _ in range(10)]
    loop, _ = make_loop(tmp_path, script, loop_window=3)
    result = loop.run("go")
    assert result.status is AgentStatus.LOOP and result.steps == 3


def test_tool_misuse(tmp_path: Path) -> None:
    script = [scripted_tool_call("fail", {"text": str(i)}) for i in range(10)]
    loop, _ = make_loop(tmp_path, script, max_tool_errors=2)
    assert loop.run("go").status is AgentStatus.TOOL_MISUSE


def test_nudges_then_stopped(tmp_path: Path) -> None:
    loop, client = make_loop(tmp_path, [scripted_text("hmm")] * 5, max_nudges=2)
    result = loop.run("go")
    assert result.status is AgentStatus.STOPPED and result.steps == 3
    assert "without calling a tool" in client.requests[1].messages[-1]["content"]


def test_nudge_recovers(tmp_path: Path) -> None:
    loop, _ = make_loop(tmp_path, [scripted_text("let me think"), FINISH_OK])
    assert loop.run("go").status is AgentStatus.SUCCESS


def test_max_tokens_continues(tmp_path: Path) -> None:
    loop, client = make_loop(tmp_path, [scripted_text("partial", "max_tokens"), FINISH_OK])
    assert loop.run("go").ok
    assert "cut off" in client.requests[1].messages[-1]["content"]


def test_refusal(tmp_path: Path) -> None:
    refusal = LLMResponse(
        [], "refusal", "scripted", Usage(), extra={"stop_details": {"category": "cyber"}}
    )
    result = make_loop(tmp_path, [refusal])[0].run("go")
    assert result.status is AgentStatus.REFUSED and "cyber" in result.reason


def test_finish_with_failure(tmp_path: Path) -> None:
    script = [scripted_tool_call("finish", {"success": False, "summary": "stuck"})]
    result = make_loop(tmp_path, script)[0].run("go")
    assert result.status is AgentStatus.FAILED and result.summary == "stuck"


def test_retryable_errors_are_retried(tmp_path: Path) -> None:
    state = {"n": 0}

    def flaky(_req):
        state["n"] += 1
        if state["n"] < 3:
            raise LLMError("overloaded", retryable=True)
        return FINISH_OK

    loop, _ = make_loop(tmp_path, [flaky, flaky, flaky])
    assert loop.run("go").ok and state["n"] == 3


def test_fatal_provider_error(tmp_path: Path) -> None:
    def bad(_req):
        raise LLMError("invalid request", retryable=False)

    result = make_loop(tmp_path, [bad])[0].run("go")
    assert result.status is AgentStatus.ERROR


def test_resume_after_crash(tmp_path: Path) -> None:
    calls: list[str] = []

    def crash(_req):
        raise KeyboardInterrupt  # simulates the process being killed

    first, _ = make_loop(
        tmp_path,
        [
            scripted_tool_call("echo", {"text": "1"}),
            scripted_tool_call("echo", {"text": "2"}),
            crash,
        ],
        tools=registry(calls),
    )
    with pytest.raises(KeyboardInterrupt):
        first.run("go")
    assert calls == ["1", "2"]
    cp = CheckpointStore(tmp_path).load("run1", "t1")
    assert cp.step == 2 and cp.status == "running"

    resumed, client = make_loop(
        tmp_path, [scripted_tool_call("echo", {"text": "3"}), FINISH_OK], tools=registry(calls)
    )
    result = resumed.run("go")
    assert result.ok and result.steps == 4
    assert calls == ["1", "2", "3"]  # nothing redone
    assert len(client.requests[0].messages) == 5  # task + 2 x (assistant, results)
    events = read_trace(tmp_path / "traces" / "run1" / "t1.jsonl")
    assert any(e["kind"] == "agent_start" and e["resumed"] for e in events)

    # a finished task returns its stored result without calling the model
    again, client3 = make_loop(tmp_path, [], tools=registry(calls))
    assert again.run("go").ok and client3.requests == []


def test_new_episode_policy(tmp_path: Path) -> None:
    class Policy:
        def maybe_new_episode(self, messages, last_input_tokens, task):
            if len(messages) > 3:
                return [{"role": "user", "content": f"{task}\n(summary of progress)"}]
            return None

    loop, client = make_loop(
        tmp_path,
        [
            scripted_tool_call("echo", {"text": "1"}),
            scripted_tool_call("echo", {"text": "2"}),
            FINISH_OK,
        ],
    )
    loop.context = Policy()
    result = loop.run("task")
    assert result.ok and result.episodes == 2
    assert client.requests[2].messages[0]["content"].endswith("(summary of progress)")


def test_secrets_never_reach_traces(tmp_path: Path) -> None:
    secret = "sk-ant-super-secret-value"
    client = ScriptedClient([scripted_tool_call("echo", {"text": secret}), FINISH_OK])
    spec = AgentSpec("tester", "s", registry(), "scripted")
    tracer = Tracer(tmp_path, "run1", "sec", "tester", secrets=[secret])
    AgentLoop(spec, client, tracer, CheckpointStore(tmp_path), run_id="run1", task_id="sec").run(
        "go"
    )
    text = (tmp_path / "traces" / "run1" / "sec.jsonl").read_text()
    assert secret not in text and "REDACTED" in text
    db_text = json.dumps(
        sqlite3.connect(tmp_path / "cobalt.db").execute("SELECT data FROM trace_events").fetchall()
    )
    assert secret not in db_text
