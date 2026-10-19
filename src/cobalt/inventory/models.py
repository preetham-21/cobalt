"""Data model for a corpus inventory."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FileKind(StrEnum):
    """What a file in a corpus is, judged from its extension and location."""

    COBOL = "cobol"
    COPYBOOK = "copybook"
    JCL = "jcl"
    BMS = "bms"
    SQL = "sql"
    IMS = "ims"
    ASM = "asm"
    CSD = "csd"
    CONTROL = "control"
    SCHEDULER = "scheduler"
    SCRIPT = "script"
    CONFIG = "config"
    DATA = "data"
    DOC = "doc"
    OTHER = "other"


class ProgramKind(StrEnum):
    """Runtime environment a COBOL program depends on."""

    BATCH = "batch"
    CICS = "cics"
    DB2 = "db2"
    IMS = "ims"
    MQ = "mq"


class Scope(StrEnum):
    """Whether cobalt can migrate a program, per the project scope."""

    NOW = "now"  # batch, sequential files only
    LATER = "later"  # batch, but uses indexed/relative files
    OUT = "out"  # online / database / messaging: analysis only


class LineCounts(BaseModel):
    """Physical line counts for a source file."""

    total: int = 0
    code: int = 0
    comment: int = 0
    blank: int = 0


class ProgramInfo(BaseModel):
    """Inventory facts about one COBOL program (lightweight lexical scan).

    Counts and features come from the program's own text; COPY members are
    not expanded, so a REDEFINES that lives in a copybook is not reported
    here. Copybook resolution is the knowledge graph's job.
    """

    path: str
    corpus: str
    program_id: str | None
    lines: LineCounts
    kind: ProgramKind
    scope: Scope
    exec_blocks: list[str] = Field(description="EXEC kinds present, e.g. CICS, SQL")
    copy_count: int
    call_count: int
    file_count: int
    file_organizations: dict[str, int]
    features: list[str] = Field(description="notable COBOL features present")


class KindTotals(BaseModel):
    """File and line totals for one file kind."""

    files: int = 0
    lines: int = 0


class CorpusSummary(BaseModel):
    """Totals for one corpus."""

    name: str
    root: str
    files_by_kind: dict[str, KindTotals]
    programs_by_kind: dict[str, int]
    programs_by_scope: dict[str, int]
    cobol_code_lines: int
    copybook_lines: int


class Inventory(BaseModel):
    """Full inventory across the scanned corpora. Deterministic: no timestamps."""

    corpora: list[CorpusSummary]
    programs: list[ProgramInfo]
