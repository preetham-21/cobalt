"""File adapters: one text representation for every COBOL file organisation.

The equivalence contract shows every file to Java - and compares every file -
as LINE SEQUENTIAL text (one record per line). COBOL, however, needs its
files in their native organisation, so around each COBOL run the harness:

* **record SEQUENTIAL** - turns text lines into fixed-length records (padded
  with spaces) before the run, and splits written files back into lines;
* **INDEXED** - loads text records into a BDB indexed file with a generated
  loader program, and after the run dumps any indexed file the program wrote
  or updated back to text, in ascending key order (the contract for Java).

Programs that take a JCL PARM get a generated driver that reads the PARM text
from the input file ``PARM`` and CALLs the program with the mainframe
halfword-length + text convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cobalt.graph.parser import parse_program

UTIL_KEYWORDS = ("IDXLOAD", "IDXDUMP")


@dataclass(frozen=True)
class FileLayout:
    """What the harness needs to know about one SELECT."""

    logical: str
    organization: str  # line-sequential | sequential | indexed | relative | sort
    record_length: int
    key_offset: int = 0
    key_length: int = 0
    written: bool = False  # opened OUTPUT / I-O / EXTEND
    alternate_keys: tuple[tuple[int, int], ...] = ()  # (offset, length); loaded WITH DUPLICATES

    @property
    def utility_suffix(self) -> str:
        alts = "".join(f"_a{o}x{n}" for o, n in self.alternate_keys)
        return f"{self.record_length}_{self.key_offset}_{self.key_length}{alts}"


def file_layouts(source: Path, copybook_dirs: list[Path]) -> dict[str, FileLayout]:
    """Layouts of every file the program declares, keyed by ASSIGN name."""
    parsed = parse_program(source, copybook_dirs)
    written_files = {
        op.file
        for op in parsed.file_ops
        if (op.verb == "OPEN" and op.mode in ("OUTPUT", "I-O", "EXTEND"))
        or op.verb in ("WRITE", "REWRITE", "DELETE")
    }
    out: dict[str, FileLayout] = {}
    for f in parsed.files:
        if not f.assign or f.organization == "sort":
            continue
        sizes = [item.size for r in f.records if (item := parsed.find_item(r)) is not None]
        length = max(sizes, default=0)
        key_off = key_len = 0
        alts: list[tuple[int, int]] = []
        if f.organization == "indexed" and f.record_key:
            key = parsed.find_item(f.record_key)
            if key is not None:
                key_off, key_len = key.offset, key.total_size
            for name, _dups in f.alternate_keys:
                alt = parsed.find_item(name)
                if alt is not None:
                    alts.append((alt.offset, alt.total_size))
        out[f.assign] = FileLayout(
            f.assign, f.organization, length, key_off, key_len, f.name in written_files, tuple(alts)
        )
    return out


def parm_text(path: Path | None) -> str:
    """The PARM string held in a case's ``PARM`` file (first line)."""
    if path is None or not path.is_file():
        return ""
    lines = path.read_text(encoding="latin-1").splitlines()
    return lines[0].rstrip() if lines else ""


# --------------------------------------------------------------------------- #
# Record sequential <-> text
# --------------------------------------------------------------------------- #
def text_to_fixed(data: bytes, length: int) -> bytes:
    """Text lines -> concatenated fixed-length records."""
    text = data.decode("latin-1").replace("\r\n", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return b"".join(line[:length].ljust(length).encode("latin-1") for line in lines)


def fixed_to_text(data: bytes, length: int) -> bytes:
    """Concatenated fixed-length records -> text lines (trailing spaces trimmed)."""
    if length <= 0:
        return data
    records = [data[i : i + length] for i in range(0, len(data), length)]
    return b"".join(r.rstrip(b" ") + b"\n" for r in records)


# --------------------------------------------------------------------------- #
# Generated COBOL utilities
# --------------------------------------------------------------------------- #
def _key_record(record: str, key: str, offset: int, length: int, total: int) -> list[str]:
    rest = total - offset - length
    lines = [f"       01  {record}."]
    if offset:
        lines.append(f"           05  FILLER        PIC X({offset}).")
    lines.append(f"           05  {key}    PIC X({length}).")
    if rest > 0:
        lines.append(f"           05  FILLER        PIC X({rest}).")
    return lines


def _record_def(name: str, layout: FileLayout) -> list[str]:
    """Primary record plus one implicit-redefinition record per alternate key."""
    lines = _key_record(
        f"{name}-REC", f"{name}-KEY", layout.key_offset, layout.key_length, layout.record_length
    )
    for i, (off, length) in enumerate(layout.alternate_keys, start=1):
        lines += _key_record(
            f"{name}-ALT{i}-REC", f"{name}-ALT{i}", off, length, layout.record_length
        )
    return lines


def indexed_utility(kind: str, layout: FileLayout) -> str:
    """Source of IDXLOAD (text -> indexed) or IDXDUMP (indexed -> text).

    Both work on the fixed names SEQFILE and IDXFILE in the job directory.
    """
    assert kind in UTIL_KEYWORDS
    load = kind == "IDXLOAD"
    head = [
        "       IDENTIFICATION DIVISION.",
        f"       PROGRAM-ID. {kind}.",
        "      * GENERATED BY COBALT: INDEXED FILE ADAPTER.",
        "       ENVIRONMENT DIVISION.",
        "       INPUT-OUTPUT SECTION.",
        "       FILE-CONTROL.",
        '           SELECT SEQ-FILE ASSIGN TO "SEQFILE"',
        "               ORGANIZATION IS LINE SEQUENTIAL.",
        '           SELECT IDX-FILE ASSIGN TO "IDXFILE"',
        "               ORGANIZATION IS INDEXED",
        f"               ACCESS MODE IS {'RANDOM' if load else 'SEQUENTIAL'}",
        "               RECORD KEY IS IDX-KEY",
        *[
            f"               ALTERNATE RECORD KEY IS IDX-ALT{i} WITH DUPLICATES"
            for i in range(1, len(layout.alternate_keys) + 1)
        ],
        "               FILE STATUS IS WS-STATUS.",
        "       DATA DIVISION.",
        "       FILE SECTION.",
        "       FD  SEQ-FILE.",
        f"       01  SEQ-REC           PIC X({layout.record_length}).",
        "       FD  IDX-FILE.",
        *_record_def("IDX", layout),
        "       WORKING-STORAGE SECTION.",
        "       01  WS-STATUS         PIC XX.",
        "       01  WS-EOF            PIC X VALUE 'N'.",
        "       01  WS-DUPS           PIC 9(6) VALUE 0.",
        "       PROCEDURE DIVISION.",
    ]
    if load:
        body = [
            "           OPEN INPUT SEQ-FILE",
            "           OPEN OUTPUT IDX-FILE",
            "           PERFORM UNTIL WS-EOF = 'Y'",
            "               READ SEQ-FILE",
            "                   AT END MOVE 'Y' TO WS-EOF",
            "                   NOT AT END",
            "                       MOVE SEQ-REC TO IDX-REC",
            "                       WRITE IDX-REC",
            "                           INVALID KEY ADD 1 TO WS-DUPS",
            "                       END-WRITE",
            "               END-READ",
            "           END-PERFORM",
            "           CLOSE SEQ-FILE IDX-FILE",
            "           IF WS-DUPS > 0",
            "               DISPLAY 'IDXLOAD DUPLICATE KEYS ' WS-DUPS",
            "           END-IF",
            "           STOP RUN.",
        ]
    else:
        body = [
            "           OPEN INPUT IDX-FILE",
            "           OPEN OUTPUT SEQ-FILE",
            "           PERFORM UNTIL WS-EOF = 'Y'",
            "               READ IDX-FILE NEXT RECORD",
            "                   AT END MOVE 'Y' TO WS-EOF",
            "                   NOT AT END WRITE SEQ-REC FROM IDX-REC",
            "               END-READ",
            "           END-PERFORM",
            "           CLOSE IDX-FILE SEQ-FILE",
            "           STOP RUN.",
        ]
    return "\n".join([*head, *body]) + "\n"


def parm_driver(program: str, parm_length: int = 100) -> str:
    """A main program that passes the PARM file's text to ``program``."""
    lines = [
        "       IDENTIFICATION DIVISION.",
        "       PROGRAM-ID. COBDRIVR.",
        "      * GENERATED BY COBALT: JCL PARM DRIVER.",
        "       ENVIRONMENT DIVISION.",
        "       INPUT-OUTPUT SECTION.",
        "       FILE-CONTROL.",
        '           SELECT PARM-FILE ASSIGN TO "PARM"',
        "               ORGANIZATION IS LINE SEQUENTIAL",
        "               FILE STATUS IS WS-STATUS.",
        "       DATA DIVISION.",
        "       FILE SECTION.",
        "       FD  PARM-FILE.",
        f"       01  PARM-REC          PIC X({parm_length}).",
        "       WORKING-STORAGE SECTION.",
        "       01  WS-STATUS         PIC XX.",
        "       01  DRV-PARMS.",
        "           05  DRV-LENGTH    PIC S9(4) COMP VALUE 0.",
        f"           05  DRV-TEXT      PIC X({parm_length}) VALUE SPACES.",
        "       PROCEDURE DIVISION.",
        "           OPEN INPUT PARM-FILE",
        "           IF WS-STATUS = '00'",
        "               READ PARM-FILE",
        "                   NOT AT END",
        "                       MOVE PARM-REC TO DRV-TEXT",
        "                       COMPUTE DRV-LENGTH = FUNCTION LENGTH(",
        "                           FUNCTION TRIM(PARM-REC TRAILING))",
        "               END-READ",
        "               CLOSE PARM-FILE",
        "           END-IF",
        f"           CALL '{program}' USING DRV-PARMS",
        "           STOP RUN.",
    ]
    return "\n".join(lines) + "\n"


def run_script(
    program_exe: str, layouts: dict[str, FileLayout], inputs: list[str], outputs: list[str]
) -> str:
    """Bash that loads indexed inputs, runs the program, dumps indexed outputs.

    An indexed file with alternate keys is a *family* of files under BDB
    (``NAME``, ``NAME.1``, ...); renames move the whole family.
    """
    lines = [
        "#!/bin/bash",
        "chmod +x ./* 2>/dev/null",
        # rename_family FROM TO: move FROM and FROM.<n> to TO and TO.<n>
        'rename_family() { for f in "$1" "$1".[0-9]*; do [ -e "$f" ] && '
        'mv "$f" "$2${f#"$1"}"; done; return 0; }',
    ]
    for logical in inputs:
        lay = layouts.get(logical)
        if lay and lay.organization == "indexed":
            util = f"IDXLOAD_{lay.utility_suffix}"
            lines.append(
                f"cp '{logical}.seq' SEQFILE && ./{util} >>adapter.log 2>&1; "
                f"rename_family IDXFILE '{logical}'; rm -f SEQFILE '{logical}.seq'"
            )
    lines.append(f"./{program_exe}")
    lines.append("rc=$?")
    for logical in outputs:
        lay = layouts.get(logical)
        if lay and lay.organization == "indexed":
            util = f"IDXDUMP_{lay.utility_suffix}"
            lines.append(
                f"if [ -f '{logical}' ]; then rename_family '{logical}' IDXFILE && "
                f"./{util} >>adapter.log 2>&1; rm -f IDXFILE IDXFILE.[0-9]*; "
                f"mv SEQFILE '{logical}'; fi"
            )
    lines.append("exit $rc")
    return "\n".join(lines) + "\n"
