"""Walk a corpus, classify its files and scan COBOL sources lexically.

This is deliberately a *lexical* scan (line classification plus regexes over
the code area). It answers "what is in this corpus and how much of it is in
scope?". Structural parsing (paragraphs, data items, call graph) belongs to
the knowledge-graph stage.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from cobalt.inventory.models import (
    CorpusSummary,
    FileKind,
    Inventory,
    KindTotals,
    LineCounts,
    ProgramInfo,
    ProgramKind,
    Scope,
)

_EXTENSION_KINDS: dict[str, FileKind] = {
    ".cbl": FileKind.COBOL,
    ".cob": FileKind.COBOL,
    ".cobol": FileKind.COBOL,
    ".cpy": FileKind.COPYBOOK,
    ".jcl": FileKind.JCL,
    ".prc": FileKind.JCL,
    ".proc": FileKind.JCL,
    ".bms": FileKind.BMS,
    ".sql": FileKind.SQL,
    ".ddl": FileKind.SQL,
    ".dcl": FileKind.SQL,
    ".psb": FileKind.IMS,
    ".dbd": FileKind.IMS,
    ".asm": FileKind.ASM,
    ".mac": FileKind.ASM,
    ".csd": FileKind.CSD,
    ".ctl": FileKind.CONTROL,
    ".ca7": FileKind.SCHEDULER,
    ".controlm": FileKind.SCHEDULER,
    ".sh": FileKind.SCRIPT,
    ".awk": FileKind.SCRIPT,
    ".py": FileKind.SCRIPT,
    ".toml": FileKind.CONFIG,
    ".yaml": FileKind.CONFIG,
    ".yml": FileKind.CONFIG,
    ".ps": FileKind.DATA,
    ".dat": FileKind.DATA,
    ".md": FileKind.DOC,
    ".png": FileKind.DOC,
    ".drawio": FileKind.DOC,
}
_LINE_COUNTED = {FileKind.COBOL, FileKind.COPYBOOK, FileKind.JCL, FileKind.BMS, FileKind.SQL}

_FREE_FORMAT = re.compile(r">>\s*SOURCE\s+(FORMAT\s+)?(IS\s+)?FREE", re.IGNORECASE)
_PROGRAM_ID = re.compile(r"\bPROGRAM-ID\s*\.\s*['\"]?([A-Z0-9][A-Z0-9-]*)")
_EXEC = re.compile(r"\bEXEC\s+(CICS|SQL|DLI)\b")
_COPY = re.compile(r"(?<![A-Z0-9-])COPY\s+['\"]?[A-Z0-9][A-Z0-9-]*")
_CALL = re.compile(r"(?<![A-Z0-9-])CALL\s+")
_MQ_CALL = re.compile(r"(?<![A-Z0-9-])CALL\s+['\"]MQ[A-Z]+['\"]")
_IMS_CALL = re.compile(r"(?<![A-Z0-9-])CALL\s+['\"](CBLTDLI|AIBTDLI)['\"]")
_SELECT = re.compile(r"(?<![A-Z0-9-])SELECT\s+(OPTIONAL\s+)?[A-Z0-9][A-Z0-9-]*\s+ASSIGN\b")
_ORGANIZATION = re.compile(
    r"\bORGANIZATION\s+(?:IS\s+)?(LINE\s+SEQUENTIAL|RECORD\s+SEQUENTIAL|SEQUENTIAL|INDEXED|RELATIVE)"
)
_FEATURES: dict[str, re.Pattern[str]] = {
    "comp-3": re.compile(r"\b(COMP-3|COMPUTATIONAL-3|PACKED-DECIMAL)\b"),
    "binary": re.compile(r"\b(COMP|COMP-4|COMP-5|BINARY|COMPUTATIONAL)\b(?!-)"),
    "redefines": re.compile(r"\bREDEFINES\b"),
    "occurs": re.compile(r"\bOCCURS\b"),
    "occurs-depending": re.compile(r"\bDEPENDING\s+ON\b"),
    "rounded": re.compile(r"\bROUNDED\b"),
    "size-error": re.compile(r"\bSIZE\s+ERROR\b"),
    "sort": re.compile(r"(?<![A-Z0-9-])SORT\s+[A-Z0-9-]+\s+(ON\s+)?(ASCENDING|DESCENDING)\b"),
    "goto": re.compile(r"\bGO\s+TO\b"),
    "string-unstring": re.compile(r"\b(UNSTRING|STRING)\b"),
    "inspect": re.compile(r"\bINSPECT\b"),
    "intrinsic-function": re.compile(r"\bFUNCTION\s+[A-Z]"),
}


# --------------------------------------------------------------------------- #
# File classification
# --------------------------------------------------------------------------- #
def classify_file(path: Path) -> FileKind:
    """Classify a corpus file by extension, treating anything under a
    ``data`` directory that is not source as data."""
    kind = _EXTENSION_KINDS.get(path.suffix.lower())
    in_data_dir = any(part.lower() == "data" for part in path.parts[:-1])
    if kind is None or kind is FileKind.DOC:
        if in_data_dir:
            return FileKind.DATA
        if path.suffix.lower() == ".txt":
            return FileKind.DOC
    return kind or FileKind.OTHER


def iter_corpus_files(root: Path) -> Iterator[Path]:
    """Yield every regular file under ``root`` in sorted order, skipping
    hidden files and directories such as ``.git``."""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.is_file():
            yield path


def read_source(path: Path) -> str:
    """Read a source file as text. Latin-1 never fails, and keeps column
    positions intact for fixed-format code."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# --------------------------------------------------------------------------- #
# COBOL line handling
# --------------------------------------------------------------------------- #
def is_free_format(lines: Iterable[str]) -> bool:
    """True if a ``>>SOURCE FORMAT FREE`` directive appears."""
    return any(_FREE_FORMAT.search(line) for line in lines)


def classify_cobol_line(line: str, *, free: bool = False) -> str:
    """Return ``"blank"``, ``"comment"`` or ``"code"`` for one physical line.

    Fixed format: columns 1-6 are the sequence area, column 7 the indicator
    (``*`` or ``/`` marks a comment), columns 8-72 the code. ``*>`` starts a
    floating comment in either format.
    """
    if free:
        stripped = line.strip()
        if not stripped:
            return "blank"
        return "comment" if stripped.startswith("*>") else "code"
    if len(line) > 6 and line[6] in "*/":
        return "comment"
    area = line[7:72] if len(line) > 6 else ""
    stripped = area.strip()
    if not stripped:
        # a line holding only a sequence number is still blank
        return "blank"
    return "comment" if stripped.startswith("*>") else "code"


def code_text(text: str) -> tuple[str, LineCounts]:
    """Return the upper-cased code area of a COBOL source (comments and
    sequence/identification areas removed) and its line counts."""
    lines = text.splitlines()
    free = is_free_format(lines)
    counts = LineCounts(total=len(lines))
    code: list[str] = []
    for line in lines:
        kind = classify_cobol_line(line, free=free)
        if kind == "blank":
            counts.blank += 1
        elif kind == "comment":
            counts.comment += 1
        else:
            counts.code += 1
            area = line if free else line[7:72]
            code.append(area.split("*>", 1)[0])
    return " ".join(code).upper(), counts


def count_lines(text: str) -> LineCounts:
    """Line counts for non-COBOL text files (no comment detection)."""
    lines = text.splitlines()
    blank = sum(1 for line in lines if not line.strip())
    return LineCounts(total=len(lines), code=len(lines) - blank, blank=blank)


# --------------------------------------------------------------------------- #
# Program analysis
# --------------------------------------------------------------------------- #
def analyze_program(path: Path, *, corpus: str, rel_path: str) -> ProgramInfo:
    """Scan one COBOL program and summarise its dependencies and features."""
    code, counts = code_text(read_source(path))
    # collapse whitespace so multi-line clauses match single-space patterns
    code = re.sub(r"\s+", " ", code)

    match = _PROGRAM_ID.search(code)
    exec_blocks = sorted(set(_EXEC.findall(code)))
    uses_mq = bool(_MQ_CALL.search(code))
    uses_ims = "DLI" in exec_blocks or bool(_IMS_CALL.search(code))

    if "CICS" in exec_blocks:
        kind = ProgramKind.CICS
    elif uses_ims:
        kind = ProgramKind.IMS
    elif "SQL" in exec_blocks:
        kind = ProgramKind.DB2
    elif uses_mq:
        kind = ProgramKind.MQ
    else:
        kind = ProgramKind.BATCH

    file_count = len(_SELECT.findall(code))
    orgs: Counter[str] = Counter()
    for org in _ORGANIZATION.findall(code):
        orgs[org.replace(" ", "-").lower()] += 1
    implicit = file_count - sum(orgs.values())
    if implicit > 0:
        orgs["sequential"] += implicit

    if kind is not ProgramKind.BATCH:
        scope = Scope.OUT
    elif orgs.get("indexed") or orgs.get("relative"):
        scope = Scope.LATER
    else:
        scope = Scope.NOW

    return ProgramInfo(
        path=rel_path,
        corpus=corpus,
        program_id=match.group(1) if match else None,
        lines=counts,
        kind=kind,
        scope=scope,
        exec_blocks=exec_blocks,
        copy_count=len(_COPY.findall(code)),
        call_count=len(_CALL.findall(code)),
        file_count=file_count,
        file_organizations=dict(sorted(orgs.items())),
        features=[name for name, pattern in _FEATURES.items() if pattern.search(code)],
    )


# --------------------------------------------------------------------------- #
# Corpus scan
# --------------------------------------------------------------------------- #
def scan_corpus(
    name: str, corpus_root: Path, repo_root: Path
) -> tuple[CorpusSummary, list[ProgramInfo]]:
    """Inventory one corpus directory."""
    files_by_kind: dict[str, KindTotals] = {k.value: KindTotals() for k in FileKind}
    programs: list[ProgramInfo] = []
    copybook_lines = 0

    for path in iter_corpus_files(corpus_root):
        kind = classify_file(path)
        totals = files_by_kind[kind.value]
        totals.files += 1
        rel = path.relative_to(repo_root).as_posix()
        if kind is FileKind.COBOL:
            info = analyze_program(path, corpus=name, rel_path=rel)
            programs.append(info)
            totals.lines += info.lines.total
        elif kind is FileKind.COPYBOOK:
            _, counts = code_text(read_source(path))
            totals.lines += counts.total
            copybook_lines += counts.code
        elif kind in _LINE_COUNTED:
            totals.lines += count_lines(read_source(path)).total

    summary = CorpusSummary(
        name=name,
        root=corpus_root.relative_to(repo_root).as_posix(),
        files_by_kind={k: v for k, v in files_by_kind.items() if v.files},
        programs_by_kind=dict(sorted(Counter(p.kind.value for p in programs).items())),
        programs_by_scope=dict(sorted(Counter(p.scope.value for p in programs).items())),
        cobol_code_lines=sum(p.lines.code for p in programs),
        copybook_lines=copybook_lines,
    )
    return summary, programs


def available_corpora(corpus_dir: Path) -> list[str]:
    """Names of corpus directories that contain at least one visible file."""
    names = []
    for child in sorted(corpus_dir.iterdir()):
        visible = child.is_dir() and not child.name.startswith(".")
        if visible and next(iter_corpus_files(child), None) is not None:
            names.append(child.name)
    return names


def build_inventory(names: Iterable[str], corpus_dir: Path, repo_root: Path) -> Inventory:
    """Scan the named corpora and combine the results."""
    corpora: list[CorpusSummary] = []
    programs: list[ProgramInfo] = []
    for name in names:
        summary, progs = scan_corpus(name, corpus_dir / name, repo_root)
        corpora.append(summary)
        programs.extend(progs)
    return Inventory(corpora=corpora, programs=programs)
