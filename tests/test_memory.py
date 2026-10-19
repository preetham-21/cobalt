"""Stage 6: state store, folding, context episodes, CardDemo file adapters."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cobalt.agents.supervisor import Supervisor, SupervisorConfig
from cobalt.config import Settings
from cobalt.equivalence.diff import compare_stream, mask
from cobalt.equivalence.files import (
    FileLayout,
    fixed_to_text,
    indexed_utility,
    parm_driver,
    run_script,
    text_to_fixed,
)
from cobalt.equivalence.golden import GoldenStore, source_digest
from cobalt.equivalence.harness import EquivalenceHarness
from cobalt.equivalence.spec import discover_cases, load_corpus_spec
from cobalt.graph.store import get_graph
from cobalt.harness.migration import for_program
from cobalt.memory.context import EpisodeCompactor, composition, digest
from cobalt.memory.state import MigrationState
from cobalt.memory.summaries import SummaryMemory, java_interface
from cobalt.sandbox import SandboxError, get_sandbox
from conftest import ROOT

BASELINES = ROOT / "baselines" / "synthetic"
CARDDEMO = ROOT / "corpus" / "carddemo"
needs_carddemo = pytest.mark.skipif(not (CARDDEMO / "app").is_dir(), reason="no carddemo")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, runs_root=tmp_path / "runs", workspace_root=tmp_path / "workspace"
    )


# --------------------------------------------------------------------------- #
# State store
# --------------------------------------------------------------------------- #
def test_state_store_roundtrip(tmp_path: Path) -> None:
    st = MigrationState(tmp_path / "db.sqlite")
    assert st.start_run("r", "synthetic", {"a": 1}) is False
    assert st.start_run("r", "synthetic", {"a": 1}) is True  # resume
    st.ensure_module("r", "X")
    st.record_attempt(
        "r",
        "X",
        1,
        "translator",
        "failed",
        5,
        {"input_tokens": 10, "cache_read_tokens": 5, "output_tokens": 3, "cost_usd": 0.5},
        2.0,
        "s",
        "why",
        counts_as_attempt=True,
    )
    st.record_attempt(
        "r",
        "X",
        1,
        "reviewer",
        "success",
        1,
        {"cost_usd": 0.1},
        1.0,
        "",
        "",
        counts_as_attempt=False,
    )
    st.set_status("r", "X", "accepted", verdict="PASS")
    row = st.module("r", "X")
    assert (row.status, row.attempts, row.tokens) == ("accepted", 1, 18)
    assert row.cost_usd == pytest.approx(0.6)
    st.put_artifact("r", "X", "failure", "one")
    st.put_artifact("r", "X", "failure", "two")
    assert st.artifact("r", "X", "failure") == "two"
    assert st.artifacts("r", "X", "failure") == ["one", "two"]
    st.decide("r", "X", "accept", "ok")
    assert st.decisions("r") == [("X", "accept", "ok")]
    assert st.run_config("r") == {"a": 1}


# --------------------------------------------------------------------------- #
# Context episodes
# --------------------------------------------------------------------------- #
def _conversation() -> list[dict]:
    return [
        {"role": "user", "content": "Migrate X."},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Writing it."},
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "write_file",
                    "input": {
                        "path": "src/main/java/cobalt/migrated/x/X.java",
                        "content": "x" * 500,
                    },
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "a", "content": "wrote it"}],
        },
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "b", "name": "run_equivalence", "input": {"case": None}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "b",
                    "content": "X: FAIL 0/1 cases\n  [default] RPTOUT ...",
                }
            ],
        },
    ]


def test_composition_and_digest() -> None:
    msgs = _conversation()
    comp = composition(msgs)
    assert comp["task"] == len("Migrate X.")
    assert comp["result:run_equivalence"] > 0 and comp["tool_inputs"] > 500
    d = digest(msgs)
    assert "write_file x1" in d and "src/main/java/cobalt/migrated/x/X.java" in d
    assert "X: FAIL 0/1" in d and "Writing it." in d


def test_episode_compactor_threshold() -> None:
    comp = EpisodeCompactor(threshold_tokens=1000)
    assert comp.maybe_new_episode(_conversation(), 999, "Migrate X.") is None
    fresh = comp.maybe_new_episode(_conversation(), 1000, "Migrate X.")
    assert len(fresh) == 1 and fresh[0]["content"].startswith("Migrate X.")
    assert "Progress from your previous context" in fresh[0]["content"]
    assert comp.last_composition["task"] == len("Migrate X.")


# --------------------------------------------------------------------------- #
# Folding
# --------------------------------------------------------------------------- #
def _seed_workspace(settings: Settings, run: str, programs: list[str]) -> None:
    for p in programs:
        src = BASELINES / "src/main/java/cobalt/migrated" / p.lower()
        dst = settings.workspace_dir / run / "src/main/java/cobalt/migrated" / p.lower()
        shutil.copytree(src, dst)


def test_summary_folds_interface_and_layouts(settings) -> None:
    _seed_workspace(settings, "f", ["RATECALC"])
    assert any(
        "public static Result calculate" in d
        for d in java_interface(settings.workspace_dir / "f", "RATECALC")
    )
    state = MigrationState(settings.runs_dir / "cobalt.db")
    state.start_run("f", "synthetic", {})
    state.ensure_module("f", "RATECALC")
    state.set_status("f", "RATECALC", "accepted", verdict="RATECALC: compiles")
    state.put_artifact("f", "RATECALC", "failure", "Attempt 1 (semantic_diff): truncated")
    mem = SummaryMemory(state, "f")
    g = get_graph(settings, "synthetic")
    h = EquivalenceHarness(settings, "synthetic")
    ctx = for_program(settings, "synthetic", "f", "RATECALC", h, g)
    text = mem.fold("RATECALC", ctx, analysis="Tiered savings rates.")
    assert "LK-BALANCE PIC S9(9)V99 COMP-3 @0+6" in text
    assert "Tiered savings rates." in text and "semantic_diff" in text
    assert mem.available() == ["RATECALC"] and mem.get("RATECALC") == text


def test_translator_brief_folding_vs_full(settings) -> None:
    _seed_workspace(settings, "brief", ["RATECALC"])
    cfg = SupervisorConfig(provider="replay", use_folding=True)
    sup = Supervisor(settings, "synthetic", "brief", cfg)
    sup.state.start_run("brief", "synthetic", {})
    sup.state.ensure_module("brief", "RATECALC")
    sup.state.set_status("brief", "RATECALC", "accepted", verdict="ok")
    ctx = for_program(settings, "synthetic", "brief", "RATECALC", sup.harness, sup.graph)
    sup.memory.fold("RATECALC", ctx)
    folded = sup._translator_brief("INTCALC", None, None)
    assert "RATECALC (folded summary" in folded and "public static Result calculate" in folded

    cfg_off = SupervisorConfig(provider="replay", use_folding=False)
    sup_off = Supervisor(settings, "synthetic", "brief", cfg_off)
    full = sup_off._translator_brief("INTCALC", None, None)
    assert "folded summary" not in full and "read_cobol" in full and "RATECALC" in full


# --------------------------------------------------------------------------- #
# File adapters (CardDemo)
# --------------------------------------------------------------------------- #
def test_record_sequential_conversion() -> None:
    fixed = text_to_fixed(b"AB\nCDEFG\n", 4)
    assert fixed == b"AB  CDEF"
    assert fixed_to_text(b"AB  CD  ", 4) == b"AB\nCD\n"


def test_generated_utilities_fit_fixed_format() -> None:
    lay = FileLayout("XREFFILE", "indexed", 50, 0, 16, False, ((25, 11),))
    for src in (
        indexed_utility("IDXLOAD", lay),
        indexed_utility("IDXDUMP", lay),
        parm_driver("CBACT04C"),
    ):
        for line in src.splitlines():
            assert len(line) <= 72, line
    assert "ALTERNATE RECORD KEY IS IDX-ALT1 WITH DUPLICATES" in indexed_utility("IDXLOAD", lay)
    script = run_script("PROG", {"XREFFILE": lay}, ["XREFFILE"], [])
    assert "rename_family IDXFILE 'XREFFILE'" in script and script.endswith("exit $rc\n")


def test_volatile_masking() -> None:
    pattern = (r"(?<=\d{4}-\d\d-\d\d-\d\d\.\d\d\.)\d\d\.\d{6}",)
    a = "X2022-07-06-10.00.01.310000Y"
    b = "X2022-07-06-10.00.03.420000Y"
    assert mask([a], pattern) == mask([b], pattern)
    assert compare_stream("F", a, b, None, pattern).ok
    assert not compare_stream("F", a, b.replace("10.00", "11.00"), None, pattern).ok


@needs_carddemo
def test_carddemo_spec_and_committed_golden() -> None:
    spec = load_corpus_spec(CARDDEMO)
    assert set(spec.programs) == {
        "CBACT02C",
        "CBACT03C",
        "CBACT04C",
        "CBCUS01C",
        "CBTRN01C",
        "CBTRN02C",
        "CBTRN03C",
    }
    assert spec.golden_dir.name == "golden" and spec.spec_dir.name == "carddemo-support"
    assert spec.program("CBACT04C").parm and spec.program("CBTRN02C").expected_rc == 4
    assert spec.programs["CBTRN03C"].inputs["TRANFILE"].parent.name == "data"
    store = GoldenStore(spec.golden_dir)
    copybooks = [p for d in spec.copybook_dirs for p in d.iterdir()]
    for name, prog in spec.programs.items():
        digest_ = source_digest(prog, copybooks)
        for case in discover_cases(spec, name):
            g = store.load(name, case.name)
            assert g is not None and store.is_fresh(prog, case, digest_), name
            assert g.output.returncode == prog.expected_rc, name


@needs_carddemo
@pytest.mark.sandbox
def test_carddemo_golden_reproduces(settings) -> None:
    try:
        get_sandbox(settings)
    except SandboxError:
        pytest.skip("no sandbox")
    h = EquivalenceHarness(settings, "carddemo")
    store = GoldenStore(settings.runs_dir / "g")
    for name in ("CBACT03C", "CBTRN02C"):  # indexed read; indexed I-O + volatile stamps
        assert {r.status for r in h.capture_golden(name, store=store)} == {"captured"}
        prog = h.spec.program(name)
        fresh = store.load(name, "default").output
        committed = h.golden.load(name, "default").output
        for out in prog.outputs:
            assert compare_stream(
                out, committed.files[out], fresh.files[out], None, prog.volatile
            ).ok, out
        assert compare_stream("SYSOUT", committed.stdout, fresh.stdout, None, prog.volatile).ok
