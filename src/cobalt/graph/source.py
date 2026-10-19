"""Source normalisation and COPY expansion.

A COBOL source becomes a list of :class:`SourceLine` - the code area of each
non-comment line plus where it came from (file and line number) and whether
it starts in Area A. COPY statements are replaced, recursively, by the
copybook's lines with any ``REPLACING`` applied, so downstream parsing sees
the program exactly as the compiler would.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cobalt.inventory.scanner import is_free_format, read_source

MAX_COPY_DEPTH = 10
_WORD = r"[A-Z0-9][A-Z0-9-]*"
_COPY_START = re.compile(r"(?<![A-Z0-9-])COPY\s+")
_COPY_STMT = re.compile(
    rf"COPY\s+(['\"]?)({_WORD})\1(?:\s+(?:OF|IN)\s+{_WORD})?"
    r"(?:\s+SUPPRESS)?(?:\s+REPLACING\s+(?P<rep>.*?))?\s*\.(?=\s|$)",
    re.DOTALL,
)
_REPLACING_PAIR = re.compile(
    r"(?P<mode>LEADING\s+|TRAILING\s+)?(?:==(?P<old_pt>.*?)==|(?P<old_w>\S+))\s+BY\s+"
    r"(?:==(?P<new_pt>.*?)==|(?P<new_w>\S+))",
    re.DOTALL,
)


@dataclass(frozen=True)
class SourceLine:
    """One logical line of code (comments removed, upper-cased)."""

    text: str
    origin: str  # file name the line physically lives in
    line: int  # 1-based line number within ``origin``
    area_a: bool  # True if code starts in column 8 (fixed) / column 1 (free)
    copybook: str | None = None  # copybook name if the line came from one


@dataclass(frozen=True)
class CopyRef:
    """A COPY statement and how it resolved."""

    name: str
    origin: str
    line: int
    path: Path | None
    replacing: tuple[tuple[str, str, str], ...] = ()  # (mode, old, new)

    @property
    def resolved(self) -> bool:
        return self.path is not None


@dataclass
class ExpandedSource:
    """Program text after COPY expansion."""

    path: Path
    lines: list[SourceLine] = field(default_factory=list)
    copies: list[CopyRef] = field(default_factory=list)

    def text(self) -> str:
        """All code joined with single spaces (statement-level matching)."""
        return " ".join(line.text for line in self.lines)


def code_lines(text: str, origin: str, copybook: str | None = None) -> list[SourceLine]:
    """Strip sequence/indicator/identification areas and comments."""
    raw = text.splitlines()
    free = is_free_format(raw)
    out: list[SourceLine] = []
    for number, line in enumerate(raw, start=1):
        if free:
            if line.lstrip().startswith(("*>", ">>")):
                continue
            area = line.split("*>", 1)[0]
            area_a = bool(area) and not area[0].isspace()
        else:
            if len(line) > 6 and line[6] in "*/":
                continue
            if len(line) > 6 and line[6] == "-":
                # continuation: glue to the previous line (literal continues)
                cont = line[7:72].lstrip()
                if out and cont:
                    prev = out[-1]
                    joined = prev.text.rstrip()
                    if cont[0] in "'\"":
                        joined = joined.rstrip("'\"") if joined[-1:] in "'\"" else joined
                        cont = cont[1:]
                    out[-1] = SourceLine(
                        joined + cont.upper(), prev.origin, prev.line, prev.area_a, copybook
                    )
                continue
            area = line[7:72] if len(line) > 7 else ""
            area = area.split("*>", 1)[0]
            area_a = bool(area) and not area[0].isspace()
            if line.lstrip().startswith(">>"):
                continue
        if area.strip():
            out.append(SourceLine(area.rstrip().upper(), origin, number, area_a, copybook))
    return out


def find_copybook(name: str, search_dirs: Sequence[Path]) -> Path | None:
    """Locate ``name`` in the search path (case-insensitive, common suffixes)."""
    wanted = {name.upper(), *(f"{name.upper()}{ext}" for ext in (".CPY", ".CBL", ".COB"))}
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            if candidate.is_file() and candidate.name.upper() in wanted:
                return candidate
    return None


def parse_replacing(text: str | None) -> tuple[tuple[str, str, str], ...]:
    """Parse a REPLACING phrase into (mode, old, new) triples."""
    if not text:
        return ()
    pairs = []
    for m in _REPLACING_PAIR.finditer(text):
        mode = (m.group("mode") or "").strip()
        old = m.group("old_pt") if m.group("old_pt") is not None else m.group("old_w")
        new = m.group("new_pt") if m.group("new_pt") is not None else m.group("new_w")
        pairs.append((mode, " ".join(old.split()), " ".join(new.split())))
    return tuple(pairs)


def apply_replacing(text: str, replacing: Iterable[tuple[str, str, str]]) -> str:
    """Apply COPY ... REPLACING to one line of copybook text."""
    for mode, old, new in replacing:
        if not old:
            continue
        if mode == "LEADING":
            text = re.sub(rf"(?<![A-Z0-9-]){re.escape(old)}(?=[A-Z0-9-]*)", new, text)
        elif mode == "TRAILING":
            text = re.sub(rf"(?<=[A-Z0-9-]){re.escape(old)}(?![A-Z0-9-])", new, text)
        else:
            # word boundaries only where the pseudo-text itself starts/ends with
            # a word character (so ==:TAG:== also replaces inside :TAG:-ID)
            left = r"(?<![A-Z0-9-])" if old[0].isalnum() else ""
            right = r"(?![A-Z0-9-])" if old[-1].isalnum() or old[-1] == "-" else ""
            text = re.sub(left + re.escape(old) + right, new, text)
    return text


def expand(path: Path, search_dirs: Sequence[Path]) -> ExpandedSource:
    """Read ``path`` and expand every COPY statement recursively."""
    result = ExpandedSource(path=path)
    lines = code_lines(read_source(path), path.name)
    result.lines = _expand_lines(lines, search_dirs, result.copies, depth=0)
    return result


def _expand_lines(
    lines: list[SourceLine],
    search_dirs: Sequence[Path],
    copies: list[CopyRef],
    depth: int,
) -> list[SourceLine]:
    out: list[SourceLine] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        start = _COPY_START.search(line.text)
        if start is None or _inside_literal(line.text, start.start()):
            out.append(line)
            i += 1
            continue
        # gather the full statement (it may span lines) up to its period
        stmt = line.text[start.start() :]
        j = i
        match = _COPY_STMT.match(stmt)
        while match is None and j + 1 < len(lines) and j - i < 20:
            j += 1
            stmt += " " + lines[j].text
            match = _COPY_STMT.match(stmt)
        if match is None:
            out.append(line)
            i += 1
            continue
        before = line.text[: start.start()]
        if before.strip():
            out.append(SourceLine(before, line.origin, line.line, line.area_a, line.copybook))
        name = match.group(2)
        replacing = parse_replacing(match.group("rep"))
        found = find_copybook(name, search_dirs)
        copies.append(CopyRef(name, line.origin, line.line, found, replacing))
        if found is not None and depth < MAX_COPY_DEPTH:
            member = code_lines(read_source(found), found.name, copybook=name)
            member = [
                SourceLine(apply_replacing(m.text, replacing), m.origin, m.line, m.area_a, name)
                for m in member
            ]
            out.extend(_expand_lines(member, search_dirs, copies, depth + 1))
        rest = stmt[match.end() :].strip()
        if rest and j < len(lines):
            last = lines[j]
            out.append(SourceLine(rest, last.origin, last.line, False, last.copybook))
        i = j + 1
    return out


def _inside_literal(text: str, pos: int) -> bool:
    quotes = 0
    for ch in text[:pos]:
        if ch in "'\"":
            quotes += 1
    return quotes % 2 == 1
