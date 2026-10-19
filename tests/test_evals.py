"""Stage 7: metrics, ablation runs, failure taxonomy and the HTML report."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobalt.agents.supervisor import Supervisor, SupervisorConfig
from cobalt.config import Settings
from cobalt.evals.metrics import run_metrics
from cobalt.evals.report import bar_chart, render_eval, render_runs, stacked_chart
from cobalt.evals.suite import VARIANTS, EvalConfig, load_eval, run_eval
from cobalt.harness.budget import Budget
from cobalt.harness.llm import ScriptedClient, scripted_tool_call
from cobalt.harness.trace import Tracer
from cobalt.memory.state import MigrationState


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, runs_root=tmp_path / "runs", workspace_root=tmp_path / "workspace"
    )


def _seed_run(settings: Settings, run_id: str) -> None:
    st = MigrationState(settings.runs_dir / "cobalt.db")
    st.start_run(
        run_id, "synthetic", {"provider": "anthropic", "use_graph": True, "use_folding": True}
    )
    for prog, status, failure in (
        ("A", "accepted", None),
        ("B", "failed", "semantic_diff"),
        ("C", "blocked", "blocked"),
    ):
        st.ensure_module(run_id, prog)
        st.set_status(run_id, prog, status, failure=failure, verdict=f"{prog} verdict")
    st.record_attempt(
        run_id,
        "A",
        1,
        "translator",
        "success",
        4,
        {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.5},
        10.0,
        "",
        "",
        counts_as_attempt=True,
    )
    st.record_attempt(
        run_id,
        "A",
        1,
        "reviewer",
        "success",
        1,
        {"cost_usd": 0.1},
        2.0,
        "",
        "",
        counts_as_attempt=False,
    )
    for i in (1, 2):
        st.record_attempt(
            run_id,
            "B",
            i,
            "translator",
            "failed",
            9,
            {"cost_usd": 1.0},
            20.0,
            "",
            "",
            counts_as_attempt=True,
        )
    tr = Tracer(settings.runs_dir, run_id, "A-translator-1", "translator")
    tr.event("llm_call", 1, usage={"input_tokens": 80, "cache_read_tokens": 1000})
    tr.event("tool_call", 1, name="compile_java", ok=False)
    tr.event("tool_call", 1, name="compile_java", ok=True)
    tr.close()


def test_run_metrics(settings) -> None:
    _seed_run(settings, "m1")
    m = run_metrics(settings.runs_dir / "cobalt.db", "m1")
    assert (m.accepted, len(m.modules)) == (1, 3)
    assert m.success_rate == pytest.approx(1 / 3)
    assert m.cost_usd == pytest.approx(2.6)
    assert m.cost_per_accepted == pytest.approx(2.6)
    assert m.mean_attempts_to_pass == 1
    assert m.failures() == {"semantic_diff": 1, "blocked": 1}
    a = next(x for x in m.modules if x.program == "A")
    assert (a.llm_calls, a.tool_calls, a.tool_errors, a.cache_read_tokens) == (1, 2, 1, 1000)
    assert a.roles == {"translator": 0.5, "reviewer": 0.1}
    assert m.role_costs()["translator"] == pytest.approx(2.5)
    with pytest.raises(KeyError):
        run_metrics(settings.runs_dir / "cobalt.db", "nope")


def test_report_rendering(settings) -> None:
    _seed_run(settings, "m2")
    run = run_metrics(settings.runs_dir / "cobalt.db", "m2").to_dict()
    result = {
        "eval_id": "e<1>",
        "started": "now",
        "config": {
            "corpus": "synthetic",
            "provider": "anthropic",
            "model": "claude-opus-5-5",
            "repeats": 1,
        },
        "runs": {"full": [run], "no-graph": [run]},
    }
    page = render_eval(result)
    assert page.startswith("<!doctype html>") and "e&lt;1&gt;" in page
    assert "semantic_diff" in page and "Scripted provider" not in page
    assert "prefers-color-scheme: dark" in page and "<script" not in page
    scripted = render_eval({**result, "config": {**result["config"], "provider": "replay"}})
    assert "Scripted provider - no language model ran." in scripted
    assert "A verdict" in render_runs([run])


def test_chart_helpers() -> None:
    assert "All values are zero" in bar_chart([("a", 0.0, "t")])
    svg = bar_chart([("full", 1.0, "tip"), ("x", 0.5, "tip2")], max_value=1.0)
    assert svg.count("<rect") == 2 and "<title>tip</title>" in svg
    stacked = stacked_chart([("full", {"loop": 2, "stopped": 1})], ["loop", "stopped"])
    assert stacked.count("<rect") == 2 and "legend" in stacked
    assert "No failures" in stacked_chart([("full", {})], [])


def _give_up(*_args) -> ScriptedClient:
    return ScriptedClient([scripted_tool_call("finish", {"success": False, "summary": "no"})])


def _cfg(**kw) -> SupervisorConfig:
    return SupervisorConfig(
        provider="replay",
        budget=Budget(max_steps=5, max_cost_usd=None),
        max_attempts=1,
        use_analyst=False,
        use_test_writer=False,
        use_reviewer=False,
        **kw,
    )


def test_graph_ablation_changes_order_and_blocking(settings) -> None:
    with_graph = Supervisor(
        settings, "synthetic", "g-on", _cfg(use_folding=False), client_factory=_give_up
    )
    outcome = {m.program: m.status for m in with_graph.run(["INTCALC", "RATECALC"]).modules}
    assert outcome == {"RATECALC": "failed", "INTCALC": "blocked"}
    starts = [p for p, d, _ in with_graph.state.decisions("g-on") if d in ("start", "block")]
    assert starts == ["RATECALC", "INTCALC"]

    no_graph = Supervisor(
        settings,
        "synthetic",
        "g-off",
        _cfg(use_graph=False, use_folding=False),
        client_factory=_give_up,
    )
    outcome = {m.program: m.status for m in no_graph.run(["INTCALC", "RATECALC"]).modules}
    assert outcome == {"INTCALC": "failed", "RATECALC": "failed"}  # attempted, not blocked
    starts = [p for p, d, _ in no_graph.state.decisions("g-off") if d == "start"]
    assert starts == ["INTCALC", "RATECALC"]  # alphabetical, callee second


def test_run_eval_writes_results(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "cobalt.agents.supervisor.Supervisor._default_client",
        lambda self, role, program, attempt: _give_up(),
    )
    cfg = EvalConfig(
        programs=["RATECALC"],
        variants=["full", "neither"],
        provider="replay",
        budget=Budget(max_steps=5, max_cost_usd=None),
    )
    result = run_eval(settings, cfg, "ev1")
    assert set(result.runs) == {"full", "neither"}
    saved = load_eval(settings, "ev1")
    assert saved["runs"]["full"][0]["run_id"] == "ev1-full-r1"
    assert saved["runs"]["neither"][0]["config"]["use_graph"] is False
    assert json.dumps(saved)  # serialisable
    with pytest.raises(ValueError):
        run_eval(settings, EvalConfig(variants=["bogus"]), "ev2")
    assert set(VARIANTS) == {"full", "no-graph", "no-folding", "neither"}
