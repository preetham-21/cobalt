"""Static checks on the synthetic corpus.

No COBOL compiler is needed here: these tests catch the mistakes that would
otherwise only show up in ``make smoke`` (column overruns, missing copybooks,
stale data files, mismatched record widths).
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from conftest import ROOT, SYNTHETIC

SOURCES = sorted((SYNTHETIC / "src").glob("*.cbl"))
COPYBOOKS = sorted((SYNTHETIC / "copybooks").glob("*.cpy"))
RUNS = tomllib.loads((SYNTHETIC / "runs.toml").read_text(encoding="utf-8"))

RECORD_WIDTHS = {
    "customers.dat": 80,
    "accounts.dat": 40,
    "transactions.dat": 40,
    "payroll.dat": 60,
}


def load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "gen_synthetic_data", ROOT / "scripts" / "gen_synthetic_data.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(module)
    return module


def code_of(path: Path) -> str:
    lines = path.read_text(encoding="ascii").splitlines()
    return " ".join(line[7:72] for line in lines if len(line) > 6 and line[6] not in "*/").upper()


def test_corpus_has_expected_programs() -> None:
    assert [p.stem for p in SOURCES] == ["CUSTRPT", "INTCALC", "PAYROLL", "RATECALC", "TXNPOST"]


@pytest.mark.parametrize("path", SOURCES + COPYBOOKS, ids=lambda p: p.name)
def test_fixed_format_columns(path: Path) -> None:
    raw = path.read_bytes()
    assert b"\r" not in raw, "CRLF line endings"
    assert b"\t" not in raw, "tabs are not allowed in fixed-format source"
    raw.decode("ascii")
    for number, line in enumerate(raw.decode("ascii").splitlines(), start=1):
        where = f"{path.name}:{number}"
        assert len(line.rstrip()) <= 72, f"{where} runs past column 72"
        if len(line) >= 7:
            assert line[:6].strip() == "" or line[:6].isdigit(), f"{where} sequence area"
            assert line[6] in " *-/D", f"{where} bad indicator {line[6]!r}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_copy_members_exist(path: Path) -> None:
    for member in re.findall(r"\bCOPY\s+([A-Z0-9-]+)", code_of(path)):
        assert (SYNTHETIC / "copybooks" / f"{member}.cpy").is_file(), member


def test_static_calls_resolve_to_corpus_programs() -> None:
    program_ids = {re.search(r"PROGRAM-ID\.\s*([A-Z0-9-]+)", code_of(p)).group(1) for p in SOURCES}
    for path in SOURCES:
        for callee in re.findall(r"\bCALL\s+'([A-Z0-9-]+)'", code_of(path)):
            assert callee in program_ids


def test_runs_toml_covers_every_source() -> None:
    used = {Path(s).name for prog in RUNS["programs"].values() for s in prog["sources"]}
    assert used == {p.name for p in SOURCES}


@pytest.mark.parametrize("name", sorted(RUNS["programs"]))
def test_runs_toml_entries_are_consistent(name: str) -> None:
    prog = RUNS["programs"][name]
    for rel in prog["sources"]:
        assert (SYNTHETIC / rel).is_file()
    assert Path(prog["sources"][0]).stem == name
    assigns = set(
        re.findall(r'ASSIGN\s+TO\s+"([A-Z0-9]+)"', code_of(SYNTHETIC / prog["sources"][0]))
    )
    for logical, rel in prog["inputs"].items():
        assert (SYNTHETIC / rel).is_file()
        assert logical in assigns
    for logical in prog["outputs"]:
        assert logical in assigns
    assert set(prog["inputs"]) | set(prog["outputs"]) == assigns


@pytest.mark.parametrize(("name", "width"), sorted(RECORD_WIDTHS.items()))
def test_data_record_widths(name: str, width: int) -> None:
    raw = (SYNTHETIC / "data" / name).read_bytes()
    assert b"\r" not in raw
    lines = raw.decode("ascii").split("\n")
    assert lines[-1] == "", "file must end with a newline"
    assert lines[:-1], "file is empty"
    assert all(len(line) == width for line in lines[:-1])


def test_data_files_match_generator(tmp_path: Path) -> None:
    """Committed data is exactly what the seeded generator produces."""
    written = load_generator().generate(tmp_path)
    assert set(written) == set(RECORD_WIDTHS)
    for name, path in written.items():
        assert path.read_bytes() == (SYNTHETIC / "data" / name).read_bytes(), name


def test_sorted_inputs_for_match_merge() -> None:
    def keys(name: str, width: int) -> list[str]:
        text = (SYNTHETIC / "data" / name).read_text(encoding="ascii")
        return [line[:width] for line in text.splitlines()]

    accounts = keys("accounts.dat", 8)
    assert accounts == sorted(accounts) and len(set(accounts)) == len(accounts)
    txns = keys("transactions.dat", 8)
    assert txns == sorted(txns)
    customers = [
        (line[31:33], line[:6])
        for line in (SYNTHETIC / "data" / "customers.dat").read_text(encoding="ascii").splitlines()
    ]
    assert customers == sorted(customers)


def test_payroll_trailer_matches_details() -> None:
    lines = (SYNTHETIC / "data" / "payroll.dat").read_text(encoding="ascii").splitlines()
    assert lines[0][0] == "H" and lines[-1][0] == "T"
    details = [line for line in lines if line[0] == "D"]
    assert int(lines[-1][1:7]) == len(details)
    assert int(lines[-1][7:19]) == sum(int(line[1:7]) for line in details)


def test_generator_formatters() -> None:
    gen = load_generator()
    from decimal import Decimal

    assert gen.signed_leading(Decimal("-0.01"), 7, 2) == "-000000001"
    assert gen.signed_leading(Decimal("0"), 3, 2) == "+00000"
    assert gen.unsigned(Decimal("18.375"), 7, 4) == "00000183750"
    with pytest.raises(ValueError):
        gen.unsigned(Decimal("1.005"), 3, 2)
    with pytest.raises(ValueError):
        gen.unsigned(Decimal("1000"), 3)
    with pytest.raises(ValueError):
        gen.alpha("TOO LONG", 3)
