from __future__ import annotations

from cobalt.inventory import ProgramKind, Scope, build_inventory
from cobalt.inventory.report import to_json
from conftest import ROOT


def _synthetic():
    return build_inventory(["synthetic"], ROOT / "corpus", ROOT)


def test_synthetic_inventory_summary() -> None:
    inv = _synthetic()
    (corpus,) = inv.corpora
    assert corpus.name == "synthetic"
    assert corpus.files_by_kind["cobol"].files == 5
    assert corpus.files_by_kind["copybook"].files == 4
    assert corpus.files_by_kind["data"].files == 4
    assert corpus.programs_by_scope == {"now": 5}


def test_synthetic_programs_are_in_scope_batch() -> None:
    by_id = {p.program_id: p for p in _synthetic().programs}
    assert set(by_id) == {"CUSTRPT", "INTCALC", "PAYROLL", "RATECALC", "TXNPOST"}
    assert all(p.kind is ProgramKind.BATCH and p.scope is Scope.NOW for p in by_id.values())
    assert by_id["INTCALC"].call_count == 1
    assert by_id["TXNPOST"].file_organizations == {"line-sequential": 4}
    assert by_id["TXNPOST"].copy_count == 2
    assert by_id["RATECALC"].file_count == 0
    assert "size-error" in by_id["INTCALC"].features
    assert "rounded" in by_id["PAYROLL"].features


def test_inventory_is_deterministic() -> None:
    assert to_json(_synthetic()) == to_json(_synthetic())


def test_paths_are_repo_relative_posix() -> None:
    for p in _synthetic().programs:
        assert p.path.startswith("corpus/synthetic/src/")
        assert "\\" not in p.path
