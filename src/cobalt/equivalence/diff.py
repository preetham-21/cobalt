"""Normalised comparison of golden vs. actual output.

Normalisation (and nothing more):

* bytes are decoded as ISO-8859-1 (lossless), ``\\r\\n`` becomes ``\\n``;
* trailing spaces are removed from every record - GnuCOBOL LINE SEQUENTIAL
  writes trim them, and a Java writer may or may not;
* trailing empty records at the end of a file are ignored.

Everything else must match exactly. The first divergent record is reported
with its column and - when the file's record layout is known - the name of
the COBOL field that differs.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from cobalt.graph.model import DataItem


def normalise(data: bytes | str | None) -> list[str] | None:
    """Split into records with the normalisation rules above."""
    if data is None:
        return None
    text = data.decode("latin-1") if isinstance(data, bytes) else data
    lines = [line.rstrip(" ") for line in text.replace("\r\n", "\n").split("\n")]
    while lines and lines[-1] == "":
        lines.pop()
    return lines


@dataclass
class Divergence:
    """Where two outputs first differ."""

    record: int  # 1-based record (line) number
    column: int  # 1-based column of the first differing character
    expected: str | None  # None = record absent
    actual: str | None
    field: str | None = None  # COBOL field covering the column, if known
    field_offset: int | None = None
    field_size: int | None = None

    def describe(self) -> str:
        where = f"record {self.record}, column {self.column}"
        if self.field:
            where += f" (field {self.field} @{self.field_offset}+{self.field_size})"
        if self.expected is None:
            return f"{where}: unexpected extra record {self.actual!r}"
        if self.actual is None:
            return f"{where}: missing record, expected {self.expected!r}"
        lo = max(0, self.column - 1 - 10)
        hi = self.column - 1 + 20
        return f"{where}: expected {self.expected[lo:hi]!r} got {self.actual[lo:hi]!r}"


@dataclass
class StreamDiff:
    """Comparison of one output stream (a file or stdout)."""

    name: str
    status: str  # match | differ | missing | unexpected
    expected_records: int = 0
    actual_records: int = 0
    differing_records: int = 0
    first: Divergence | None = None

    @property
    def ok(self) -> bool:
        return self.status == "match"


def field_at(layouts: list[DataItem], column: int, record: str | None) -> DataItem | None:
    """Deepest elementary item covering ``column`` (1-based).

    With several record layouts for one file (detail/trailer), prefer the
    one whose size equals the record length.
    """
    if not layouts:
        return None
    text = record or ""
    ordered = sorted(
        layouts, key=lambda r: (-_layout_fit(r, text), r.size != len(text), layouts.index(r))
    )
    offset = column - 1
    for root in ordered:
        # walk order lists an original definition before its REDEFINES, so the
        # first covering elementary item is the "primary" name for the bytes
        for item in root.walk():
            if item.children:
                continue
            if item.offset <= offset < item.offset + max(item.total_size, 1):
                return item
    return None


_DIGITS = re.compile(r"^\d+$")
_SIGNED = re.compile(r"^[+-]\d+$")


def _layout_fit(root: DataItem, record: str) -> int:
    """How plausibly ``record`` is an instance of ``root``.

    Counts DISPLAY numeric fields whose bytes are valid digits (+1) or not (-1)
    so that, e.g., a trailer is matched to the trailer layout.
    """
    score = 0
    padded = record.ljust(root.size)
    for item in root.walk():
        if item.children or item.category != "numeric" or (item.usage or "DISPLAY") != "DISPLAY":
            continue
        chunk = padded[item.offset : item.offset + item.size]
        pattern = _SIGNED if item.sign_separate else _DIGITS
        score += 1 if pattern.match(chunk) else -1
    return score


def compare_stream(
    name: str,
    expected: bytes | str | None,
    actual: bytes | str | None,
    layouts: list[DataItem] | None = None,
) -> StreamDiff:
    """Compare one stream; ``None`` means the stream was not produced."""
    exp, act = normalise(expected), normalise(actual)
    if exp is None and act is None:
        return StreamDiff(name, "match")
    if exp is None:
        return StreamDiff(name, "unexpected", 0, len(act or []))
    if act is None:
        return StreamDiff(name, "missing", len(exp), 0)
    diff = StreamDiff(name, "match", len(exp), len(act))
    for i in range(max(len(exp), len(act))):
        e = exp[i] if i < len(exp) else None
        a = act[i] if i < len(act) else None
        if e == a:
            continue
        diff.differing_records += 1
        if diff.first is None:
            col = 1
            if e is not None and a is not None:
                col = next(
                    (j + 1 for j, (x, y) in enumerate(zip(e, a, strict=False)) if x != y),
                    min(len(e), len(a)) + 1,
                )
            item = field_at(layouts or [], col, e if e is not None else a)
            diff.first = Divergence(
                i + 1,
                col,
                e,
                a,
                field=item.name if item else None,
                field_offset=item.offset if item else None,
                field_size=item.total_size if item else None,
            )
    if diff.differing_records:
        diff.status = "differ"
    return diff


@dataclass
class CaseResult:
    """Equivalence of one test case."""

    case: str
    passed: bool
    expected_rc: int
    actual_rc: int
    streams: list[StreamDiff] = field(default_factory=list)
    error: str | None = None  # e.g. timeout or crash text

    def first_failure(self) -> str | None:
        if self.error:
            return self.error
        if self.expected_rc != self.actual_rc:
            return f"exit code {self.actual_rc}, expected {self.expected_rc}"
        for s in self.streams:
            if not s.ok:
                if s.first:
                    return f"{s.name}: {s.first.describe()}"
                return f"{s.name}: {s.status}"
        return None

    def to_dict(self) -> dict:
        return asdict(self)
