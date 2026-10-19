"""Context folding: compact summaries of completed modules.

When a module is accepted it is *folded*: instead of its full COBOL and Java,
later agents receive a summary with exactly what a caller needs - the Java
interface, the COBOL parameters and record layouts, behaviour notes and the
pitfalls earlier attempts ran into. Full sources stay one tool call away
(``read_cobol`` / ``read_file``), so folding never loses information; it only
changes what is in the context window by default.

Summaries are built deterministically from the workspace, the knowledge graph
and the agents' own artifacts (no extra model call), so they are cheap and
reproducible.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from cobalt.equivalence.runtime import java_package_dir, main_class
from cobalt.graph.parser import parse_program
from cobalt.memory.state import MigrationState

if TYPE_CHECKING:
    from cobalt.harness.migration import MigrationContext

_PUBLIC = re.compile(
    r"^\s*public\s+(?:static\s+)?(?:final\s+)?(?:record|class|enum|interface|[\w<>\[\],.? ]+?)"
    r"\s+\w+\s*(?:\([^)]*\))?",
    re.MULTILINE,
)
MAX_BEHAVIOUR_CHARS = 1500


def java_interface(workspace: Path, program: str) -> list[str]:
    """Public declarations of the program's Java package."""
    pkg = workspace / "src" / "main" / "java" / java_package_dir(program)
    out: list[str] = []
    for path in sorted(pkg.rglob("*.java")) if pkg.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _PUBLIC.finditer(text):
            decl = " ".join(m.group(0).split())
            if decl.startswith("public static void main"):
                decl = "public static void main(String[] args)"
            out.append(f"{path.name}: {decl}")
    return out


def _layout_lines(item: object, depth: int = 0, limit: int = 40) -> list[str]:
    lines: list[str] = []

    def walk(it: object, d: int) -> None:
        if len(lines) >= limit:
            return
        pic = f" PIC {it.picture}" if it.picture else ""  # type: ignore[attr-defined]
        usage = f" {it.usage}" if it.usage else ""  # type: ignore[attr-defined]
        lines.append(f"{'  ' * d}{it.name}{pic}{usage} @{it.offset}+{it.total_size}")  # type: ignore[attr-defined]
        for child in it.children:  # type: ignore[attr-defined]
            walk(child, d + 1)

    walk(item, depth)
    if len(lines) >= limit:
        lines.append("  ... (use query_graph layout for the rest)")
    return lines


def build_summary(
    ctx: MigrationContext,
    *,
    analysis: str | None,
    failures: list[str],
    reviews: list[str],
    verdict: str | None,
) -> str:
    """The folded summary text of an accepted module."""
    program = ctx.program
    parts = [f"# {program} (folded summary - accepted migration)"]
    parts.append("## Java interface")
    parts.append(f"main class / package: {main_class(program)}")
    parts.extend(f"- {d}" for d in java_interface(ctx.workspace, program) or ["(none found)"])

    source = ctx.program_path(program)
    if source is not None:
        parsed = parse_program(source, list(ctx.harness.spec.copybook_dirs) or [source.parent])
        if parsed.using:
            parts.append("## COBOL parameters (PROCEDURE DIVISION USING)")
            for name in parsed.using:
                item = parsed.find_item(name)
                parts.extend(_layout_lines(item) if item else [name])
        if parsed.files:
            parts.append("## Files")
            for f in parsed.files:
                parts.append(
                    f"- {f.assign}: {f.organization}, records {', '.join(f.records)}"
                    + (f", key {f.record_key}" if f.record_key else "")
                )
    if analysis:
        text = analysis.strip()
        if len(text) > MAX_BEHAVIOUR_CHARS:
            text = (
                text[:MAX_BEHAVIOUR_CHARS] + " ... (truncated; ask for the full analysis by "
                "reading the COBOL)"
            )
        parts.append("## Behaviour (from the Analyst)")
        parts.append(text)
    pitfalls = [f.splitlines()[0][:300] for f in failures] + [
        r.splitlines()[0][:300] for r in reviews if r.startswith("failed")
    ]
    if pitfalls:
        parts.append("## Pitfalls hit during migration")
        parts.extend(f"- {p}" for p in pitfalls)
    if verdict:
        parts.append(f"## Verification\n{verdict}")
    parts.append("Full sources: read_cobol for the COBOL, read_file for the Java.")
    return "\n".join(parts)


class SummaryMemory:
    """Folded summaries for one run, persisted as ``summary`` artifacts."""

    def __init__(self, state: MigrationState, run_id: str) -> None:
        self.state = state
        self.run_id = run_id

    def get(self, program: str) -> str | None:
        return self.state.artifact(self.run_id, program.upper(), "summary")

    def available(self) -> list[str]:
        return [
            m.program
            for m in self.state.modules(self.run_id)
            if m.status == "accepted" and self.get(m.program)
        ]

    def fold(self, program: str, ctx: MigrationContext, *, analysis: str | None = None) -> str:
        row = self.state.module(self.run_id, program)
        text = build_summary(
            ctx,
            analysis=analysis,
            failures=self.state.artifacts(self.run_id, program, "failure"),
            reviews=self.state.artifacts(self.run_id, program, "review"),
            verdict=row.verdict if row else None,
        )
        self.state.put_artifact(self.run_id, program, "summary", text)
        return text
