"""Stage 5: supervisor policy (retry / escalate / review / block / resume)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cobalt.agents.checks import check_java, golden_records
from cobalt.agents.supervisor import Supervisor, SupervisorConfig
from cobalt.config import Settings
from cobalt.equivalence.golden import GoldenStore
from cobalt.harness.budget import Budget
from cobalt.harness.llm import ScriptedClient, scripted_tool_call
from cobalt.harness.replay import replay_script
from cobalt.memory.state import MigrationState
from cobalt.sandbox import SandboxError, get_sandbox
from conftest import ROOT, SYNTHETIC

BASELINES = ROOT / "baselines" / "synthetic"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, runs_root=tmp_path / "runs", workspace_root=tmp_path / "workspace"
    )


# --------------------------------------------------------------------------- #
# Static checks
# --------------------------------------------------------------------------- #
def _project(tmp_path: Path, program: str, code: str) -> Path:
    pkg = tmp_path / "src/main/java/cobalt/migrated" / program.lower()
    pkg.mkdir(parents=True)
    (pkg / f"{program.capitalize()}.java").write_text(code)
    return tmp_path


def test_checks_flag_floating_point_and_embedded_output(tmp_path: Path) -> None:
    golden = golden_records(GoldenStore(SYNTHETIC / "golden"), "CUSTRPT", ["default"])
    line = next(r for r in golden if "MAX BALANCE AT LIMIT" in r)
    code = (
        "package cobalt.migrated.custrpt;\n"
        "public final class Custrpt {\n"
        "  // a comment mentioning double is fine\n"
        "  static double rate = 0.5;\n"
        f'  static final String CHEAT = "{line}";\n'
        '  public static void main(String[] a) { System.out.println("double ok"); }\n}\n'
    )
    findings = check_java(_project(tmp_path, "CUSTRPT", code), "CUSTRPT", golden=golden)
    rules = sorted(f.rule for f in findings)
    assert rules == ["embedded-output", "floating-point"]


@pytest.mark.parametrize("program", ["CUSTRPT", "INTCALC", "PAYROLL", "TXNPOST", "RATECALC"])
def test_reference_migrations_pass_static_checks(program: str) -> None:
    store = GoldenStore(SYNTHETIC / "golden")
    cases = (
        [p.name for p in (SYNTHETIC / "golden" / program).iterdir()]
        if (SYNTHETIC / "golden" / program).is_dir()
        else []
    )
    cobol = (SYNTHETIC / "src" / f"{program}.cbl").read_text()
    findings = check_java(
        BASELINES, program, golden=golden_records(store, program, cases), cobol_source=cobol
    )
    assert [f for f in findings if f.severity == "error"] == []


# --------------------------------------------------------------------------- #
# Supervisor policy (real sandbox, scripted agents)
# --------------------------------------------------------------------------- #
def _sandbox_or_skip(settings: Settings) -> None:
    try:
        get_sandbox(settings)
    except SandboxError:
        pytest.skip("no sandbox backend available")


def give_up(program: str) -> list:
    return [
        scripted_tool_call("finish", {"success": False, "summary": f"could not migrate {program}"})
    ]


def approve() -> list:
    return [scripted_tool_call("finish", {"success": True, "summary": "LGTM"})]


class Factory:
    """(role, program, attempt) -> scripted client; records requests."""

    def __init__(self, scripts: dict[tuple[str, str, int], list]) -> None:
        self.scripts = scripts
        self.clients: dict[tuple[str, str, int], ScriptedClient] = {}

    def __call__(self, role: str, program: str, attempt: int) -> ScriptedClient:
        key = (role, program, attempt)
        script = self.scripts.get(key)
        if script is None:
            script = replay_script(program, BASELINES) if role == "translator" else approve()
        client = ScriptedClient(list(script))
        self.clients[key] = client
        return client


def supervisor(settings, run_id, factory, **cfg) -> Supervisor:
    config = SupervisorConfig(
        provider="replay",
        budget=Budget(max_steps=20, max_cost_usd=None),
        use_folding=False,
        use_analyst=False,
        use_test_writer=False,
        **cfg,
    )
    return Supervisor(settings, "synthetic", run_id, config, client_factory=factory)


@pytest.mark.sandbox
def test_retry_with_failure_feedback(settings) -> None:
    _sandbox_or_skip(settings)
    factory = Factory({("translator", "CUSTRPT", 1): give_up("CUSTRPT")})
    sup = supervisor(settings, "retry", factory, use_reviewer=False)
    outcome = sup.run(["CUSTRPT"])
    (m,) = outcome.modules
    assert (m.status, m.attempts) == ("accepted", 2)
    second_task = factory.clients[("translator", "CUSTRPT", 2)].requests[0].messages[0]["content"]
    assert "Earlier attempts" in second_task and "could not migrate" in second_task
    decisions = [d for _, d, _ in sup.state.decisions("retry", "CUSTRPT")]
    assert decisions == ["start", "retry", "accept"]


@pytest.mark.sandbox
def test_escalation_and_failure(settings) -> None:
    _sandbox_or_skip(settings)
    factory = Factory({("translator", "CUSTRPT", i): give_up("CUSTRPT") for i in (1, 2, 3)})
    sup = supervisor(
        settings, "esc", factory, use_reviewer=False, escalation_model="claude-fable-5-1"
    )
    (m,) = sup.run(["CUSTRPT"]).modules
    assert (m.status, m.attempts, m.failure) == ("failed", 3, "semantic_diff")
    decisions = [d for _, d, _ in sup.state.decisions("esc", "CUSTRPT")]
    assert decisions == ["start", "retry", "escalate", "fail"]
    last = factory.clients[("translator", "CUSTRPT", 3)].requests[0]
    assert last.effort == "xhigh"


@pytest.mark.sandbox
def test_reviewer_rejection_sends_back(settings) -> None:
    _sandbox_or_skip(settings)
    factory = Factory(
        {
            ("reviewer", "CUSTRPT", 1): [
                scripted_tool_call(
                    "finish", {"success": False, "summary": "1. split the 200-line method"}
                )
            ]
        }
    )
    sup = supervisor(settings, "review", factory)
    (m,) = sup.run(["CUSTRPT"]).modules
    assert (m.status, m.attempts) == ("accepted", 2)
    task = factory.clients[("translator", "CUSTRPT", 2)].requests[0].messages[0]["content"]
    assert "split the 200-line method" in task


@pytest.mark.sandbox
def test_failed_callee_blocks_caller(settings) -> None:
    _sandbox_or_skip(settings)
    factory = Factory({("translator", "RATECALC", i): give_up("RATECALC") for i in (1, 2, 3)})
    sup = supervisor(settings, "block", factory, use_reviewer=False, max_attempts=1)
    outcome = {m.program: m for m in sup.run(["RATECALC", "INTCALC"]).modules}
    assert outcome["RATECALC"].status == "failed"
    assert outcome["INTCALC"].status == "blocked" and outcome["INTCALC"].failure == "blocked"
    assert outcome["INTCALC"].attempts == 0


@pytest.mark.sandbox
def test_kill_and_resume_does_not_redo_work(settings) -> None:
    _sandbox_or_skip(settings)

    def killed(_req):
        raise KeyboardInterrupt

    script = replay_script("TXNPOST", BASELINES)
    interrupted = [*script[:3], killed]  # dies after writing Java, before compiling
    factory = Factory({("translator", "TXNPOST", 1): interrupted})
    sup = supervisor(settings, "resume", factory, use_reviewer=False)
    with pytest.raises(KeyboardInterrupt):
        sup.run(["CUSTRPT", "TXNPOST"])
    state = MigrationState(settings.runs_dir / "cobalt.db")
    assert state.module("resume", "CUSTRPT").status == "accepted"
    assert state.module("resume", "TXNPOST").status == "in_progress"

    # second process: same run id; TXNPOST's translator resumes from its checkpoint
    rest = script[3:]
    factory2 = Factory({("translator", "TXNPOST", 1): rest})
    sup2 = supervisor(settings, "resume", factory2, use_reviewer=False)
    outcome = {m.program: m for m in sup2.run(["CUSTRPT", "TXNPOST"]).modules}
    assert outcome["TXNPOST"].status == "accepted" and outcome["TXNPOST"].attempts == 1
    assert ("translator", "CUSTRPT", 1) not in factory2.clients  # not redone
    resumed = factory2.clients[("translator", "TXNPOST", 1)].requests[0]
    assert len(resumed.messages) > 1  # continued the saved conversation
    decisions = [(p, d) for p, d, _ in sup2.state.decisions("resume")]
    assert ("CUSTRPT", "skip") in decisions
