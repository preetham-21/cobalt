from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cobalt.config import get_settings
from cobalt.equivalence.diff import compare_stream, field_at, normalise
from cobalt.equivalence.golden import GoldenStore
from cobalt.equivalence.harness import EquivalenceHarness, EquivalenceReport
from cobalt.equivalence.runtime import RunOutput, java_package_dir, main_class
from cobalt.equivalence.spec import discover_cases, load_corpus_spec
from cobalt.graph.parser import parse_program
from cobalt.sandbox import SandboxError, get_sandbox, to_wsl_path
from conftest import ROOT, SYNTHETIC

BASELINES = ROOT / "baselines" / "synthetic"


# --------------------------------------------------------------------------- #
# Pure unit tests
# --------------------------------------------------------------------------- #
def test_normalise_rules() -> None:
    assert normalise(b"A  \r\nB\n\n\n") == ["A", "B"]
    assert normalise("") == []
    assert normalise(None) is None


def test_compare_stream_match_and_missing() -> None:
    assert compare_stream("F", b"X \n", b"X\n").ok
    assert compare_stream("F", None, None).ok
    assert compare_stream("F", b"X\n", None).status == "missing"
    assert compare_stream("F", None, b"X\n").status == "unexpected"


def test_first_divergence_names_the_field() -> None:
    parsed = parse_program(SYNTHETIC / "src" / "INTCALC.cbl", [SYNTHETIC / "copybooks"])
    layouts = [parsed.find_item("INT-RECORD"), parsed.find_item("INT-TRAILER")]
    good = "10000100S+000000999990005000+00000000042+0000010004100"
    bad = "10000100S+000000999990005000+00000000041+0000010004100"
    diff = compare_stream("INTOUT", f"{good}\n{good}\n", f"{good}\n{bad}\n", layouts)
    assert diff.status == "differ" and diff.differing_records == 1
    first = diff.first
    assert (first.record, first.column) == (2, 40)
    assert (first.field, first.field_offset, first.field_size) == ("INT-INTEREST", 28, 12)
    assert "INT-INTEREST" in first.describe()
    trailer = field_at(layouts, 12, "TRAILER 000032+0000000710333000002")
    assert trailer is not None and trailer.name == "INT-TRL-COUNT"


def test_extra_and_missing_records() -> None:
    diff = compare_stream("F", b"A\nB\n", b"A\n")
    assert diff.first.record == 2 and diff.first.actual is None
    assert "missing record" in diff.first.describe()
    diff = compare_stream("F", b"A\n", b"A\nZ\n")
    assert "extra record" in diff.first.describe()


def test_main_class_convention() -> None:
    assert main_class("CUSTRPT") == "cobalt.migrated.custrpt.Custrpt"
    assert main_class("PAY-CALC") == "cobalt.migrated.paycalc.PayCalc"
    assert java_package_dir("INTCALC") == Path("cobalt/migrated/intcalc")


def test_to_wsl_path() -> None:
    assert to_wsl_path(Path(r"C:\Users\x\job dir")) == "/mnt/c/Users/x/job dir"


def test_spec_and_case_discovery(tmp_path: Path) -> None:
    spec = load_corpus_spec(SYNTHETIC)
    assert set(spec.programs) == {"CUSTRPT", "INTCALC", "PAYROLL", "TXNPOST"}
    assert spec.program("intcalc").sources[1].name == "RATECALC.cbl"
    cases = discover_cases(spec, "TXNPOST")
    names = [c.name for c in cases]
    assert names[0] == "default" and "no-transactions" in names
    no_txn = next(c for c in cases if c.name == "no-transactions")
    assert no_txn.inputs["ACCTIN"] == spec.program("TXNPOST").inputs["ACCTIN"]  # fallback
    assert no_txn.inputs["TXNIN"].parent.name == "no-transactions"
    extra = tmp_path / "TXNPOST" / "agent-case"
    extra.mkdir(parents=True)
    (extra / "TXNIN").write_text("")
    (tmp_path / "TXNPOST" / "ignored").mkdir()  # no matching inputs -> skipped
    more = discover_cases(spec, "TXNPOST", [tmp_path])
    assert [c.name for c in more][-1] == "agent-case" and more[-1].source == "extra"
    with pytest.raises(KeyError):
        spec.program("NOPE")


def test_golden_store_roundtrip(tmp_path: Path) -> None:
    spec = load_corpus_spec(SYNTHETIC)
    prog = spec.program("TXNPOST")
    case = discover_cases(spec, "TXNPOST")[0]
    store = GoldenStore(tmp_path)
    out = RunOutput(0, "TOTALS\n", "", {"ACCTNEW": b"X\n", "TXNERR": None}, 0.1)
    store.save(prog, case, out, source_sha="abc", toolchain="cobc 3.2")
    loaded = store.load("TXNPOST", "default")
    assert loaded.output.files == {"ACCTNEW": b"X\n", "TXNERR": None}
    assert loaded.meta["missing"] == ["TXNERR"]
    assert store.is_fresh(prog, case, "abc") and not store.is_fresh(prog, case, "xyz")
    assert store.load("TXNPOST", "nope") is None


def test_committed_golden_is_fresh() -> None:
    """Golden outputs in the repo match the current sources and inputs."""
    from cobalt.equivalence.golden import source_digest

    spec = load_corpus_spec(SYNTHETIC)
    store = GoldenStore(SYNTHETIC / "golden")
    copybooks = list((SYNTHETIC / "copybooks").iterdir())
    for name, prog in spec.programs.items():
        digest = source_digest(prog, copybooks)
        for case in discover_cases(spec, name):
            assert store.is_fresh(prog, case, digest), f"stale golden {name}/{case.name}"


def test_report_summary_lines() -> None:
    r = EquivalenceReport(
        "X",
        "root",
        compile_ok=False,
        compile_errors=[{"file": "A.java", "line": "3", "message": "boom"}],
    )
    assert r.summary() == "X: COMPILE ERROR (A.java:3: boom)" and not r.passed


def test_unknown_sandbox_backend() -> None:
    with pytest.raises(SandboxError):
        get_sandbox(get_settings(), "nonsense")


# --------------------------------------------------------------------------- #
# Integration: real COBOL vs real Java in the sandbox
# --------------------------------------------------------------------------- #
def _sandbox_or_skip():
    try:
        return get_sandbox(get_settings())
    except SandboxError:
        pytest.skip("no sandbox backend available")


@pytest.mark.sandbox
def test_baselines_are_equivalent() -> None:
    _sandbox_or_skip()
    h = EquivalenceHarness(get_settings(), "synthetic")
    for name in sorted(h.spec.programs):
        deps = ["RATECALC"] if name == "INTCALC" else []
        report = h.check(name, BASELINES, dependencies=deps)
        assert report.passed, report.summary()
        assert report.passed_cases == len(h.cases(name))


@pytest.mark.sandbox
def test_semantic_bug_is_caught_with_field(tmp_path: Path) -> None:
    """Truncating instead of ROUNDED in RATECALC must fail on INT-INTEREST."""
    _sandbox_or_skip()
    project = tmp_path / "proj"
    shutil.copytree(BASELINES, project)
    rate = project / "src/main/java/cobalt/migrated/ratecalc/Ratecalc.java"
    text = rate.read_text()
    rate.write_text(
        text.replace("Pic.rounded(Pic.divide(", "Pic.truncate(Pic.divide(").replace(
            "TWELVE), 2);", "TWELVE), 9, 2);"
        )
    )
    h = EquivalenceHarness(get_settings(), "synthetic")
    report = h.check("INTCALC", project, dependencies=["RATECALC"], cases=["default"])
    assert report.compile_ok and not report.passed
    first = report.cases[0].streams[0].first
    assert first.field == "INT-INTEREST"
    assert "INT-INTEREST" in report.summary()


@pytest.mark.sandbox
def test_compile_errors_are_reported(tmp_path: Path) -> None:
    _sandbox_or_skip()
    project = tmp_path / "proj"
    shutil.copytree(BASELINES, project)
    src = project / "src/main/java/cobalt/migrated/custrpt/Custrpt.java"
    src.write_text(src.read_text().replace("private long readCount;", "private lng readCount;"))
    report = EquivalenceHarness(get_settings(), "synthetic").check("CUSTRPT", project)
    assert not report.compile_ok
    assert report.compile_errors[0]["file"].endswith("Custrpt.java")
    assert "COMPILE ERROR" in report.summary()


@pytest.mark.sandbox
def test_golden_capture_into_temp_store(tmp_path: Path) -> None:
    _sandbox_or_skip()
    h = EquivalenceHarness(get_settings(), "synthetic")
    results = h.capture_golden("CUSTRPT", store=GoldenStore(tmp_path))
    assert {r.status for r in results} == {"captured"}
    ours = GoldenStore(tmp_path).load("CUSTRPT", "default").output.files["RPTOUT"]
    committed = h.golden.load("CUSTRPT", "default").output.files["RPTOUT"]
    assert ours == committed  # deterministic
