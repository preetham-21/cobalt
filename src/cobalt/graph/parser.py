"""Structural COBOL parser.

Not a full COBOL grammar: a careful, division-aware scanner that recovers the
facts the knowledge graph needs - files, record layouts with byte offsets,
paragraphs, PERFORM/GO TO flow, CALLs (including CICS LINK/XCTL), file I/O
and copybook usage. It works on the COPY-expanded text, so data items
declared in copybooks are attributed to the copybook they came from.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cobalt.graph.model import CallRef, DataItem, FileDef, FileOp, Paragraph, ParsedProgram
from cobalt.graph.pic import FIXED_SIZE_USAGES, analyze_pic, storage_size
from cobalt.graph.source import SourceLine, expand

_W = r"[A-Z0-9][A-Z0-9-]*"
_DIVISION = re.compile(r"\b(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b")
_SECTION_HDR = re.compile(
    r"^(FILE|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE|COMMUNICATION|REPORT|SCREEN)\s+SECTION$"
)
_LITERAL = re.compile(r"'[^']*'|\"[^\"]*\"")
_TERMINATOR = re.compile(r"\.(?=\s|$)")

USAGE_WORDS = {
    "COMP",
    "COMP-1",
    "COMP-2",
    "COMP-3",
    "COMP-4",
    "COMP-5",
    "BINARY",
    "PACKED-DECIMAL",
    "DISPLAY",
    "INDEX",
    "POINTER",
    "COMPUTATIONAL",
    "COMPUTATIONAL-1",
    "COMPUTATIONAL-2",
    "COMPUTATIONAL-3",
    "COMPUTATIONAL-4",
    "COMPUTATIONAL-5",
    "FUNCTION-POINTER",
    "PROCEDURE-POINTER",
}
_VERBS = re.compile(
    r"(?<![A-Z0-9-])(ACCEPT|ADD|CALL|CLOSE|COMPUTE|CONTINUE|DELETE|DISPLAY|DIVIDE|EVALUATE|"
    r"EXIT|GO|GOBACK|IF|INITIALIZE|INSPECT|MOVE|MULTIPLY|OPEN|PERFORM|READ|RETURN|REWRITE|"
    r"SEARCH|SET|SORT|START|STOP|STRING|SUBTRACT|UNSTRING|WRITE|EXEC)(?![A-Z0-9-])"
)


@dataclass
class _Sentence:
    text: str
    line: SourceLine


def copybook_search_dirs(program: Path, corpus_root: Path) -> list[Path]:
    """Directories holding copybooks, nearest to ``program`` first."""
    dirs = {p.parent for p in corpus_root.rglob("*") if p.suffix.lower() == ".cpy"}
    dirs.add(program.parent)
    prog_parts = program.parent.parts

    def closeness(d: Path) -> tuple[int, str]:
        common = 0
        for a, b in zip(d.parts, prog_parts, strict=False):
            if a != b:
                break
            common += 1
        return (-common, d.as_posix())

    return sorted(dirs, key=closeness)


def parse_program(
    path: Path, search_dirs: Sequence[Path], *, rel_path: str | None = None
) -> ParsedProgram:
    """Parse one COBOL program file."""
    src = expand(path, search_dirs)
    divisions = _split_divisions(src.lines)
    ident = " ".join(line.text for line in divisions.get("IDENTIFICATION", []))
    m = re.search(rf"PROGRAM-ID\s*\.?\s*['\"]?({_W})", ident)
    program_id = m.group(1) if m else path.stem.upper()

    files = _parse_file_control(divisions.get("ENVIRONMENT", []))
    data = _parse_data_division(divisions.get("DATA", []), files)
    paragraphs, calls, ops, using, exec_kinds = _parse_procedure(
        divisions.get("PROCEDURE", []), files, data
    )
    own_lines = sum(1 for line in src.lines if line.copybook is None)
    copy_names = list(dict.fromkeys(c.name for c in src.copies))
    unresolved = list(dict.fromkeys(c.name for c in src.copies if not c.resolved))
    return ParsedProgram(
        program_id=program_id,
        path=rel_path or path.as_posix(),
        copybooks=copy_names,
        unresolved_copybooks=unresolved,
        files=files,
        data=data,
        paragraphs=paragraphs,
        calls=calls,
        file_ops=ops,
        using=using,
        exec_kinds=exec_kinds,
        lines_of_code=own_lines,
    )


# --------------------------------------------------------------------------- #
def _split_divisions(lines: list[SourceLine]) -> dict[str, list[SourceLine]]:
    out: dict[str, list[SourceLine]] = {}
    current = "IDENTIFICATION"
    for line in lines:
        m = _DIVISION.search(line.text)
        if m and not _LITERAL.search(line.text[: m.start()] + "x"):
            current = "IDENTIFICATION" if m.group(1) == "ID" else m.group(1)
            rest = line.text[m.end() :].strip()
            if current == "PROCEDURE":
                # keep the header so USING can be parsed
                out.setdefault(current, []).append(
                    SourceLine(line.text[m.start() :], line.origin, line.line, False, line.copybook)
                )
            elif rest.strip(". "):
                out.setdefault(current, []).append(
                    SourceLine(rest, line.origin, line.line, False, line.copybook)
                )
            continue
        out.setdefault(current, []).append(line)
    return out


def _sentences(lines: list[SourceLine]) -> list[_Sentence]:
    """Split lines into period-terminated sentences, ignoring periods in literals."""
    out: list[_Sentence] = []
    buf: list[str] = []
    first: SourceLine | None = None
    for line in lines:
        text = line.text
        pos = 0
        masked = _LITERAL.sub(lambda m: "x" * len(m.group(0)), text)
        for term in _TERMINATOR.finditer(masked):
            if first is None:
                first = line
            buf.append(text[pos : term.start()])
            sentence = " ".join(" ".join(buf).split())
            if sentence:
                out.append(_Sentence(sentence, first))
            buf, first, pos = [], None, term.end()
        tail = text[pos:]
        if tail.strip():
            if first is None:
                first = line
            buf.append(tail)
    if buf and first is not None:
        sentence = " ".join(" ".join(buf).split())
        if sentence:
            out.append(_Sentence(sentence, first))
    return out


def _unquote(word: str) -> str:
    return word.strip("'\"")


# --------------------------------------------------------------------------- #
# ENVIRONMENT DIVISION
# --------------------------------------------------------------------------- #
def _parse_file_control(lines: list[SourceLine]) -> list[FileDef]:
    files: list[FileDef] = []
    for s in _sentences(lines):
        m = re.search(
            rf"\bSELECT\s+(?:OPTIONAL\s+)?({_W})\s+ASSIGN\s+(?:TO\s+)?(?:EXTERNAL\s+|DYNAMIC\s+)?"
            r"('[^']*'|\"[^\"]*\"|\S+)",
            s.text,
        )
        if not m:
            continue
        f = FileDef(name=m.group(1), assign=_unquote(m.group(2)), line=s.line.line)
        org = re.search(
            r"\bORGANIZATION\s+(?:IS\s+)?(LINE\s+SEQUENTIAL|RECORD\s+SEQUENTIAL|SEQUENTIAL|"
            r"INDEXED|RELATIVE)",
            s.text,
        )
        if org:
            f.organization = "-".join(org.group(1).lower().split())
        elif re.search(r"\bRECORD\s+KEY\b", s.text):
            f.organization = "indexed"
        acc = re.search(r"\bACCESS\s+(?:MODE\s+)?(?:IS\s+)?(SEQUENTIAL|RANDOM|DYNAMIC)", s.text)
        if acc:
            f.access = acc.group(1).lower()
        key = re.search(rf"\bRECORD\s+KEY\s+(?:IS\s+)?({_W})", s.text)
        if key:
            f.record_key = key.group(1)
        status = re.search(rf"\bSTATUS\s+(?:IS\s+)?({_W})", s.text)
        if status:
            f.status_field = status.group(1)
        files.append(f)
    return files


# --------------------------------------------------------------------------- #
# DATA DIVISION
# --------------------------------------------------------------------------- #
def _parse_data_division(lines: list[SourceLine], files: list[FileDef]) -> list[DataItem]:
    by_name = {f.name: f for f in files}
    roots: list[DataItem] = []
    stack: list[DataItem] = []
    section = "WORKING-STORAGE"
    current_fd: FileDef | None = None
    last: DataItem | None = None

    for s in _sentences(lines):
        text = s.text
        hdr = _SECTION_HDR.match(text)
        if hdr:
            section = hdr.group(1)
            current_fd, stack = None, []
            continue
        fd = re.match(rf"^(FD|SD|RD)\s+({_W})", text)
        if fd:
            current_fd = by_name.get(fd.group(2))
            if current_fd is None and fd.group(1) == "SD":
                current_fd = FileDef(name=fd.group(2), assign="", organization="sort")
                files.append(current_fd)
                by_name[current_fd.name] = current_fd
            stack = []
            continue
        entry = re.match(r"^(\d{1,2})(?:\s+(.*))?$", text)
        if not entry:
            continue
        level = int(entry.group(1))
        rest = entry.group(2) or ""
        if level == 88:
            if last is not None:
                nm = re.match(rf"^({_W})\s+(?:VALUES?\s+(?:ARE\s+|IS\s+)?)?(.*)$", rest)
                if nm:
                    last.conditions.append((nm.group(1), nm.group(2).strip()))
            continue
        if level == 66:
            continue
        item = _parse_entry(level, rest, section, s.line)
        last = item
        if level in (1, 77):
            stack = [item]
            roots.append(item)
            if current_fd is not None and section == "FILE" and level == 1:
                current_fd.records.append(item.name)
            continue
        while stack and stack[-1].level >= level:
            stack.pop()
        if not stack:  # malformed nesting: treat as a root
            roots.append(item)
            stack = [item]
            continue
        parent = stack[-1]
        item.parent = parent.name
        if item.usage is None and parent.usage is not None:
            item.usage = parent.usage
            _apply_pic(item)
        parent.children.append(item)
        stack.append(item)

    for root in roots:
        _layout(root, 0)
    return roots


def _parse_entry(level: int, rest: str, section: str, line: SourceLine) -> DataItem:
    tokens = rest.split()
    name = "FILLER"
    if tokens and tokens[0] not in ("PIC", "PICTURE", "REDEFINES", "VALUE", "OCCURS", "USAGE"):
        name = tokens[0]
        rest = rest[len(tokens[0]) :]
    item = DataItem(
        level=level,
        name=name,
        section=section,
        line=line.line,
        origin=line.origin,
        copybook=line.copybook,
    )
    pic = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", rest)
    if pic:
        item.picture = pic.group(1).rstrip(",")
    red = re.search(rf"\bREDEFINES\s+({_W})", rest)
    if red:
        item.redefines = red.group(1)
    occ = re.search(r"\bOCCURS\s+(\d+)(?:\s+TO\s+(\d+))?", rest)
    if occ:
        item.occurs = int(occ.group(1))
        item.occurs_max = int(occ.group(2)) if occ.group(2) else None
        dep = re.search(rf"\bDEPENDING\s+(?:ON\s+)?({_W})", rest)
        if dep:
            item.depending_on = dep.group(1)
    for word in re.findall(rf"(?<![A-Z0-9-])({_W})", rest):
        if word in USAGE_WORDS:
            item.usage = word
            break
    sign = re.search(r"\bSIGN\s+(?:IS\s+)?(LEADING|TRAILING)(\s+SEPARATE)?", rest)
    if sign:
        item.sign_leading = sign.group(1) == "LEADING"
        item.sign_separate = bool(sign.group(2))
    elif re.search(r"\b(LEADING|TRAILING)\s+SEPARATE\b", rest):
        item.sign_separate = True
        item.sign_leading = "LEADING" in rest
    val = re.search(r"\bVALUES?\s+(?:IS\s+|ARE\s+)?('(?:[^']|'')*'|\"[^\"]*\"|\S+)", rest)
    if val:
        item.value = val.group(1)
    _apply_pic(item)
    return item


def _apply_pic(item: DataItem) -> None:
    usage = (item.usage or "DISPLAY").upper()
    if item.picture:
        info = analyze_pic(item.picture, sign_separate=item.sign_separate)
        item.category = info.category
        item.digits, item.scale, item.signed = info.digits, info.scale, info.signed
        item.size = storage_size(info, usage, sign_separate=item.sign_separate)
    elif usage in FIXED_SIZE_USAGES:
        item.category = "numeric" if usage.startswith("COMP") else usage.lower()
        item.size = FIXED_SIZE_USAGES[usage]


def _layout(item: DataItem, offset: int) -> int:
    """Assign offsets/sizes; returns the item's size (one occurrence)."""
    item.offset = offset
    if not item.children:
        return item.size
    cursor = offset
    starts: dict[str, int] = {}
    end = offset
    for child in item.children:
        start = starts[child.redefines] if child.redefines and child.redefines in starts else cursor
        starts[child.name] = start
        _layout(child, start)
        child_end = start + child.total_size
        if not child.redefines:
            cursor = child_end
        end = max(end, child_end)
    item.size = end - offset
    return item.size


# --------------------------------------------------------------------------- #
# PROCEDURE DIVISION
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"'[^']*'|\"[^\"]*\"|[A-Z0-9][A-Z0-9-]*|[^\sA-Z0-9]")
_IDENT = re.compile(r"^[A-Z0-9][A-Z0-9-]*$")
_CALL_SKIP = {"BY", "REFERENCE", "CONTENT", "VALUE", "ADDRESS", "OF", "OMITTED", ","}
_CALL_STOP = {
    "ON",
    "NOT",
    "RETURNING",
    "GIVING",
    "ELSE",
    "WHEN",
    "IF",
    "MOVE",
    "PERFORM",
    "CALL",
    "DISPLAY",
    "EVALUATE",
    "SET",
    "ADD",
    "SUBTRACT",
    "COMPUTE",
    "GO",
    "GOBACK",
    "STOP",
    "EXIT",
    "READ",
    "WRITE",
    "OPEN",
    "CLOSE",
    "INITIALIZE",
    "STRING",
    "UNSTRING",
    "CONTINUE",
    "EXCEPTION",
    "OVERFLOW",
}
_PARA_HDR = re.compile(rf"^({_W})(\s+SECTION)?\s*\.(.*)$")
_NOT_PARAGRAPHS = {"DECLARATIVES", "END", "EXIT", "GOBACK", "CONTINUE", "STOP"}


def _parse_procedure(
    lines: list[SourceLine], files: list[FileDef], data: list[DataItem]
) -> tuple[list[Paragraph], list[CallRef], list[FileOp], list[str], list[str]]:
    if not lines:
        return [], [], [], [], []
    header_text = []
    body_start = 0
    for idx, line in enumerate(lines):
        header_text.append(line.text)
        if _TERMINATOR.search(_LITERAL.sub("x", line.text)):
            body_start = idx + 1
            break
    header = " ".join(header_text)
    using: list[str] = []
    um = re.search(r"\bUSING\s+(.*?)(?:\bRETURNING\b|\.\s*$|$)", header)
    if um:
        using = [
            w
            for w in re.findall(_W, um.group(1))
            if w not in ("BY", "REFERENCE", "VALUE", "CONTENT")
        ]

    paragraphs: list[Paragraph] = []
    bodies: list[list[str]] = []
    section: str | None = None
    current: Paragraph | None = None
    preamble: list[str] = []
    for line in lines[body_start:]:
        m = _PARA_HDR.match(line.text.strip()) if line.area_a else None
        if m and m.group(1) not in _NOT_PARAGRAPHS and not m.group(1).startswith("END-"):
            is_section = bool(m.group(2))
            if is_section:
                section = m.group(1)
            current = Paragraph(
                name=m.group(1),
                section=None if is_section else section,
                line=line.line,
                origin=line.origin,
                is_section=is_section,
            )
            paragraphs.append(current)
            bodies.append([m.group(3)] if m.group(3).strip() else [])
            continue
        if current is None:
            preamble.append(line.text)
        else:
            bodies[-1].append(line.text)

    if preamble:  # statements before the first paragraph name
        paragraphs.insert(0, Paragraph("(MAIN)", None, lines[body_start].line, lines[0].origin))
        bodies.insert(0, preamble)

    names = {p.name for p in paragraphs}
    order = [p.name for p in paragraphs]
    file_names = {f.name for f in files}
    record_to_file = {r: f.name for f in files for r in f.records}
    values = {i.name: i.value for root in data for i in root.walk() if i.value}

    calls: list[CallRef] = []
    ops: list[FileOp] = []
    exec_kinds: set[str] = set()
    for para, body in zip(paragraphs, bodies, strict=True):
        raw = " ".join(body)
        para.statements = len(_VERBS.findall(_LITERAL.sub("''", raw)))

        # EXEC blocks first (they may contain literals and look-alike words)
        for ex in re.finditer(r"\bEXEC\s+(CICS|SQL|DLI)\b(.*?)\bEND-EXEC\b", raw, re.DOTALL):
            exec_kinds.add(ex.group(1))
            if ex.group(1) == "CICS":
                link = re.search(
                    r"\b(LINK|XCTL)\b.*?\bPROGRAM\s*\(\s*('[^']*'|\"[^\"]*\"|[A-Z0-9-]+)\s*\)",
                    ex.group(2),
                )
                if link:
                    target = _unquote(link.group(2))
                    if not link.group(2).startswith(("'", '"')):
                        target = _unquote(values.get(target, target))
                    calls.append(
                        CallRef(target, f"cics-{link.group(1).lower()}", para.line, para.name)
                    )
        raw = re.sub(r"\bEXEC\s+(CICS|SQL|DLI)\b.*?\bEND-EXEC\b", " ", raw, flags=re.DOTALL)

        tokens = _TOKEN.findall(raw)
        for i, tok in enumerate(tokens):
            if tok != "CALL" or i + 1 >= len(tokens):
                continue
            target = tokens[i + 1]
            args: list[str] = []
            if i + 2 < len(tokens) and tokens[i + 2] == "USING":
                for arg in tokens[i + 3 :]:
                    if arg in _CALL_STOP or arg.startswith("END-") or not _IDENT.match(arg):
                        if arg in _CALL_SKIP:
                            continue
                        break
                    args.append(arg)
            if target.startswith(("'", '"')):
                calls.append(CallRef(_unquote(target), "static", para.line, para.name, args))
            elif _IDENT.match(target):
                resolved = values.get(target)
                calls.append(
                    CallRef(
                        _unquote(resolved) if resolved else target,
                        "dynamic",
                        para.line,
                        para.name,
                        args,
                    )
                )

        code = _LITERAL.sub("''", raw)
        for pm in re.finditer(
            rf"(?<![A-Z0-9-])PERFORM\s+({_W})(?:\s+(?:THRU|THROUGH)\s+({_W}))?", code
        ):
            start, end = pm.group(1), pm.group(2)
            if start not in names:
                continue
            targets = [start]
            if end and end in names:
                i, j = order.index(start), order.index(end)
                targets = order[i : j + 1] if i <= j else [start, end]
            for t in targets:
                if t not in para.performs:
                    para.performs.append(t)
        for gm in re.finditer(rf"(?<![A-Z0-9-])GO\s+TO\s+((?:{_W}\s*)+)", code):
            for t in gm.group(1).split():
                if t in names and t not in para.goes_to:
                    para.goes_to.append(t)

        ctoks = _TOKEN.findall(code)
        for i, tok in enumerate(ctoks):
            if tok != "OPEN":
                continue
            mode = None
            for word in ctoks[i + 1 :]:
                if word in ("INPUT", "OUTPUT", "I-O", "EXTEND"):
                    mode = word
                elif word in file_names and mode:
                    ops.append(FileOp("OPEN", mode, word, para.name))
                elif word not in (",", "WITH", "NO", "REWIND", "LOCK"):
                    break
        for vm in re.finditer(rf"(?<![A-Z0-9-])(READ|DELETE|START|WRITE|REWRITE)\s+({_W})", code):
            verb, target = vm.group(1), vm.group(2)
            if verb in ("WRITE", "REWRITE"):
                target = record_to_file.get(target, target if target in file_names else "")
            if target in file_names:
                ops.append(FileOp(verb, None, target, para.name))

    return paragraphs, calls, ops, using, sorted(exec_kinds)
