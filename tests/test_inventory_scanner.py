from __future__ import annotations

from pathlib import Path

import pytest

from cobalt.inventory.models import FileKind, ProgramKind, Scope
from cobalt.inventory.scanner import (
    analyze_program,
    classify_cobol_line,
    classify_file,
    code_text,
)


def fixed(code: str, indicator: str = " ", seq: str = "000100") -> str:
    """Build one fixed-format line."""
    return f"{seq}{indicator}{code}"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("", "blank"),
        ("000100", "blank"),
        (fixed("    "), "blank"),
        (fixed("COMMENT TEXT", "*"), "comment"),
        (fixed("PAGE EJECT", "/"), "comment"),
        (fixed(" *> FLOATING"), "comment"),
        (fixed("    MOVE A TO B."), "code"),
        (fixed("    'CONTINUED'", "-"), "code"),
        # text past column 72 is the identification area, not code
        ("       " + " " * 65 + "IDENT123", "blank"),
    ],
)
def test_classify_fixed_format_lines(line: str, expected: str) -> None:
    assert classify_cobol_line(line) == expected


def test_free_format_lines() -> None:
    assert classify_cobol_line("*> comment", free=True) == "comment"
    assert classify_cobol_line("   ", free=True) == "blank"
    assert classify_cobol_line("MOVE A TO B.", free=True) == "code"


def test_code_text_strips_comments_and_counts() -> None:
    src = "\n".join(
        [
            fixed("THIS IS A COMMENT", "*"),
            fixed(" IDENTIFICATION DIVISION."),
            "",
            fixed(" PROGRAM-ID. demo.").ljust(72) + "SEQ00001",
        ]
    )
    code, counts = code_text(src)
    assert "COMMENT" not in code
    assert "PROGRAM-ID. DEMO." in code
    assert "SEQ" not in code
    assert (counts.total, counts.code, counts.comment, counts.blank) == (4, 2, 1, 1)


@pytest.mark.parametrize(
    ("rel", "kind"),
    [
        ("app/cbl/CBACT01C.cbl", FileKind.COBOL),
        ("app/cbl/CBSTM03A.CBL", FileKind.COBOL),
        ("app/cpy/CVACT01Y.cpy", FileKind.COPYBOOK),
        ("app/jcl/ACCTFILE.jcl", FileKind.JCL),
        ("app/proc/REPROC.prc", FileKind.JCL),
        ("app/bms/COSGN00.bms", FileKind.BMS),
        ("app/ddl/TRNTYPE.ddl", FileKind.SQL),
        ("app/data/ASCII/acctdata.txt", FileKind.DATA),
        ("app/data/EBCDIC/AWS.M2.CARDDEMO.ACCTDATA.PS", FileKind.DATA),
        ("README.txt", FileKind.DOC),
        ("docs/README.md", FileKind.DOC),
        ("scripts/run.sh", FileKind.SCRIPT),
        ("weird.bin", FileKind.OTHER),
    ],
)
def test_classify_file(rel: str, kind: FileKind) -> None:
    assert classify_file(Path(rel)) is kind


def _program(tmp_path: Path, body: list[str]) -> Path:
    path = tmp_path / "PROG.cbl"
    path.write_text("\n".join(fixed(" " + line) for line in body) + "\n", encoding="ascii")
    return path


def test_analyze_batch_program(tmp_path: Path) -> None:
    path = _program(
        tmp_path,
        [
            "IDENTIFICATION DIVISION.",
            "PROGRAM-ID. MYPROG.",
            "ENVIRONMENT DIVISION.",
            "FILE-CONTROL.",
            "    SELECT IN-FILE ASSIGN TO INFILE.",
            "    SELECT OUT-FILE ASSIGN TO OUTFILE",
            "        ORGANIZATION IS LINE SEQUENTIAL.",
            "DATA DIVISION.",
            "WORKING-STORAGE SECTION.",
            "    COPY MYCOPY.",
            "01  WS-AMT PIC S9(7)V99 COMP-3.",
            "01  WS-ALT REDEFINES WS-AMT PIC X(5).",
            "PROCEDURE DIVISION.",
            "    CALL 'SUBPROG' USING WS-AMT",
            "    COMPUTE WS-AMT ROUNDED = WS-AMT / 3",
            "    STOP RUN.",
        ],
    )
    info = analyze_program(path, corpus="t", rel_path="PROG.cbl")
    assert info.program_id == "MYPROG"
    assert info.kind is ProgramKind.BATCH
    assert info.scope is Scope.NOW
    assert (info.copy_count, info.call_count, info.file_count) == (1, 1, 2)
    assert info.file_organizations == {"line-sequential": 1, "sequential": 1}
    assert {"comp-3", "redefines", "rounded"} <= set(info.features)
    assert "binary" not in info.features


def test_indexed_batch_program_is_later(tmp_path: Path) -> None:
    path = _program(
        tmp_path,
        [
            "PROGRAM-ID. VSAMPROG.",
            "    SELECT KSDS ASSIGN TO KSDS",
            "        ORGANIZATION IS INDEXED",
            "        RECORD KEY IS K.",
        ],
    )
    info = analyze_program(path, corpus="t", rel_path="PROG.cbl")
    assert info.scope is Scope.LATER
    assert info.file_organizations == {"indexed": 1}


@pytest.mark.parametrize(
    ("statement", "kind"),
    [
        ("EXEC CICS RETURN END-EXEC.", ProgramKind.CICS),
        ("EXEC SQL SELECT 1 INTO :X FROM T END-EXEC.", ProgramKind.DB2),
        ("CALL 'CBLTDLI' USING GU PCB.", ProgramKind.IMS),
        ("CALL 'MQOPEN' USING HCONN.", ProgramKind.MQ),
    ],
)
def test_online_programs_are_out_of_scope(
    tmp_path: Path, statement: str, kind: ProgramKind
) -> None:
    path = _program(tmp_path, ["PROGRAM-ID. ONLINE.", "PROCEDURE DIVISION.", "    " + statement])
    info = analyze_program(path, corpus="t", rel_path="PROG.cbl")
    assert info.kind is kind
    assert info.scope is Scope.OUT


def test_commented_out_cics_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "P.cbl"
    path.write_text(
        fixed(" PROGRAM-ID. P.") + "\n" + fixed("    EXEC CICS RETURN END-EXEC.", "*") + "\n",
        encoding="ascii",
    )
    assert analyze_program(path, corpus="t", rel_path="P.cbl").kind is ProgramKind.BATCH
