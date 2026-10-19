"""Parsed-program data model (the parser's output, the graph's input)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeType(StrEnum):
    """Knowledge-graph node types."""

    PROGRAM = "program"
    PARAGRAPH = "paragraph"
    COPYBOOK = "copybook"
    DATA_ITEM = "data_item"
    FILE = "file"
    EXTERNAL = "external"


class EdgeType(StrEnum):
    """Knowledge-graph edge types."""

    CALLS = "CALLS"
    COPIES = "COPIES"
    READS = "READS"
    WRITES = "WRITES"
    PERFORMS = "PERFORMS"
    DEFINES = "DEFINES"


@dataclass
class DataItem:
    """One data description entry, with its byte layout inside its record."""

    level: int
    name: str
    section: str  # FILE | WORKING-STORAGE | LOCAL-STORAGE | LINKAGE
    line: int
    origin: str
    copybook: str | None = None
    picture: str | None = None
    usage: str | None = None
    redefines: str | None = None
    occurs: int | None = None
    occurs_max: int | None = None
    depending_on: str | None = None
    value: str | None = None
    sign_separate: bool = False
    sign_leading: bool = False
    category: str = "group"
    digits: int = 0
    scale: int = 0
    signed: bool = False
    offset: int = 0
    size: int = 0  # bytes for one occurrence
    parent: str | None = None
    children: list[DataItem] = field(default_factory=list)
    conditions: list[tuple[str, str]] = field(default_factory=list)  # 88-levels

    @property
    def is_group(self) -> bool:
        return bool(self.children) or (self.picture is None and self.usage not in _FIXED)

    @property
    def total_size(self) -> int:
        return self.size * (self.occurs_max or self.occurs or 1)

    def walk(self) -> list[DataItem]:
        """This item and all descendants, depth first."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


_FIXED = {"COMP-1", "COMP-2", "INDEX", "POINTER", "COMPUTATIONAL-1", "COMPUTATIONAL-2"}


@dataclass
class FileDef:
    """A SELECT entry joined with its FD/SD."""

    name: str
    assign: str
    organization: str = "sequential"
    access: str = "sequential"
    record_key: str | None = None
    alternate_keys: list[tuple[str, bool]] = field(default_factory=list)  # (name, duplicates)
    status_field: str | None = None
    records: list[str] = field(default_factory=list)  # 01-level record names
    line: int = 0


@dataclass
class Paragraph:
    """A paragraph or section in the PROCEDURE DIVISION."""

    name: str
    section: str | None
    line: int
    origin: str
    is_section: bool = False
    performs: list[str] = field(default_factory=list)
    goes_to: list[str] = field(default_factory=list)
    statements: int = 0


@dataclass
class CallRef:
    """A CALL (or CICS LINK/XCTL) to another program."""

    target: str
    kind: str  # static | dynamic | cics-link | cics-xctl
    line: int
    paragraph: str | None
    using: list[str] = field(default_factory=list)


@dataclass
class FileOp:
    """An I/O verb against a file."""

    verb: str  # OPEN | READ | WRITE | REWRITE | DELETE | START
    mode: str | None  # INPUT | OUTPUT | I-O | EXTEND for OPEN
    file: str
    paragraph: str | None


@dataclass
class ParsedProgram:
    """Everything the parser extracts from one program."""

    program_id: str
    path: str
    copybooks: list[str]
    unresolved_copybooks: list[str]
    files: list[FileDef]
    data: list[DataItem]  # 01/77 level roots
    paragraphs: list[Paragraph]
    calls: list[CallRef]
    file_ops: list[FileOp]
    using: list[str]  # PROCEDURE DIVISION USING parameters
    exec_kinds: list[str]
    lines_of_code: int

    def find_item(self, name: str) -> DataItem | None:
        """First data item called ``name`` (any level)."""
        for root in self.data:
            for item in root.walk():
                if item.name == name:
                    return item
        return None

    def record_file(self, record: str) -> FileDef | None:
        """The file whose FD declares the 01 record ``record``."""
        for f in self.files:
            if record in f.records:
                return f
        return None
