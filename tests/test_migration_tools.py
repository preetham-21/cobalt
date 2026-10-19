"""Migration tools and end-to-end replay migrations (sandbox)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cobalt.agents.single import migrate_single
from cobalt.config import Settings
from cobalt.equivalence.harness import EquivalenceHarness
from cobalt.graph.store import get_graph
from cobalt.harness.loop import AgentStatus
from cobalt.harness.migration import build_tools, describe_module, for_program
from cobalt.harness.replay import replay_client
from cobalt.harness.trace import read_trace
from cobalt.sandbox import SandboxError, get_sandbox
from conftest import ROOT

BASELINES = ROOT / "baselines" / "synthetic"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, runs_root=tmp_path / "runs", workspace_root=tmp_path / "workspace"
    )


def context(settings: Settings, program: str, run_id: str = "r1", **kw):
    g = get_graph(settings, "synthetic")
    h = EquivalenceHarness(settings, "synthetic")
    return for_program(settings, "synthetic", run_id, program, h, g, **kw)


def test_describe_module(settings) -> None:
    info = describe_module(context(settings, "INTCALC"))
    assert info["main_class"] == "cobalt.migrated.intcalc.Intcalc"
    assert info["dependencies"][0]["program"] == "RATECALC"
    assert info["inputs"] == ["ACCTIN"] and "empty" in info["cases"]
    sub = describe_module(context(settings, "RATECALC"))
    assert sub["kind"] == "called subprogram" and sub["callers"] == ["INTCALC"]


def test_read_and_query_tools(settings) -> None:
    tools = build_tools(context(settings, "INTCALC"))
    src = tools.call("1", "read_cobol", {"name": "INTCALC", "line_numbers": True})
    assert not src.is_error and "PROGRAM-ID. INTCALC." in src.content
    cpy = tools.call("2", "read_cobol", {"name": "ACCTREC", "line_numbers": False})
    assert "ACCT-BALANCE" in cpy.content
    layout = tools.call("3", "query_graph", {"kind": "layout", "name": "INT-RECORD"})
    assert "INT-INTEREST" in layout.content and "offset 28, 12 bytes" in layout.content
    ctx = tools.call("4", "query_graph", {"kind": "context", "name": "INTCALC"})
    assert '"RATECALC"' in ctx.content
    runtime = tools.call(
        "5", "read_file", {"path": "runtime/src/main/java/cobalt/migrated/support/Pic.java"}
    )
    assert "public static String edit" in runtime.content
    golden = tools.call(
        "6", "show_golden", {"case": "default", "stream": "INTOUT", "start": 1, "count": 2}
    )
    assert "10000100S" in golden.content
    cases = tools.call("7", "list_cases", {})
    assert "empty (corpus)" in cases.content


def test_graph_ablation_disables_query(settings) -> None:
    tools = build_tools(context(settings, "CUSTRPT", use_graph=False))
    out = tools.call("1", "query_graph", {"kind": "context", "name": "CUSTRPT"})
    assert out.is_error and "disabled" in out.content


def test_write_file_is_confined(settings) -> None:
    ctx = context(settings, "INTCALC", write_cases=True)
    tools = build_tools(ctx)
    ok = tools.call(
        "1",
        "write_file",
        {"path": "src/main/java/cobalt/migrated/intcalc/Helper.java", "content": "class H {}"},
    )
    assert not ok.is_error and (ctx.workspace / ok.content.split()[1]).is_file()
    dep = tools.call(
        "2",
        "write_file",
        {"path": "src/main/java/cobalt/migrated/ratecalc/Fix.java", "content": "class F {}"},
    )
    assert not dep.is_error  # callees may be fixed by their caller's migration
    for bad in (
        "src/main/java/cobalt/migrated/custrpt/X.java",
        "src/main/java/cobalt/migrated/support/Pic.java",
        "../escape.java",
        "pom.xml",
        "cases/INTCALC/new-case/NOTANINPUT",
    ):
        out = tools.call("3", "write_file", {"path": bad, "content": "x"})
        assert out.is_error, bad
    case = tools.call("4", "write_file", {"path": "cases/INTCALC/new-case/ACCTIN", "content": ""})
    assert not case.is_error


# --------------------------------------------------------------------------- #
def _sandbox_or_skip(settings: Settings) -> None:
    try:
        get_sandbox(settings)
    except SandboxError:
        pytest.skip("no sandbox backend available")


@pytest.mark.sandbox
def test_replay_migration_end_to_end(settings) -> None:
    _sandbox_or_skip(settings)
    for program in ("RATECALC", "INTCALC"):
        ctx = context(settings, program, run_id="e2e")
        result = migrate_single(ctx, replay_client(program, BASELINES), model="scripted")
        assert result.status is AgentStatus.SUCCESS, result
    assert "PASS 2/2" in result.data["verdict"]
    trace = read_trace(settings.runs_dir / "traces" / "e2e" / "INTCALC-migrator-1.jsonl")
    tools = [e["name"] for e in trace if e["kind"] == "tool_call"]
    assert tools[-3:] == ["compile_java", "run_equivalence", "finish"]
    assert (settings.workspace_dir / "e2e" / "pom.xml").is_file()


@pytest.mark.sandbox
def test_replay_repairs_a_semantic_bug(settings) -> None:
    """The first write truncates salaried gross pay instead of ROUNDING it; the
    equivalence feedback reports the divergence and the corrected write passes."""
    _sandbox_or_skip(settings)
    ctx = context(settings, "PAYROLL", run_id="repair")
    client = replay_client(
        "PAYROLL",
        BASELINES,
        mutate={
            "Payroll.java": (
                "gross = Pic.rounded(Pic.divide(e.rate(), PERIODS), 2);",
                "gross = Pic.truncate(Pic.divide(e.rate(), PERIODS), 7, 2);",
            ),
        },
    )
    result = migrate_single(ctx, client, model="scripted")
    assert result.ok
    trace = read_trace(settings.runs_dir / "traces" / "repair" / "PAYROLL-migrator-1.jsonl")
    equiv = [e for e in trace if e["kind"] == "tool_call" and e["name"] == "run_equivalence"]
    assert "FAIL" in equiv[0]["result"] and "PASS" in equiv[1]["result"]


@pytest.mark.sandbox
def test_finish_rejects_unverified_success(settings) -> None:
    _sandbox_or_skip(settings)
    ctx = context(settings, "CUSTRPT", run_id="reject")
    tools = build_tools(ctx)
    tools.call(
        "1",
        "write_file",
        {
            "path": "src/main/java/cobalt/migrated/custrpt/Custrpt.java",
            "content": "package cobalt.migrated.custrpt;\npublic final class Custrpt {\n"
            "  public static void main(String[] a) { System.exit(0); }\n}\n",
        },
    )
    out = tools.call("2", "finish", {"success": True, "summary": "trust me"})
    assert out.is_error and "not passing" in out.content and not out.terminal
