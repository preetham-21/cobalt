"""Render an :class:`Inventory` as console tables or JSON."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from cobalt.inventory.models import Inventory


def to_json(inventory: Inventory) -> str:
    """Stable, pretty-printed JSON (sorted keys, trailing newline)."""
    return json.dumps(inventory.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def write_json(inventory: Inventory, path: Path) -> Path:
    """Write the inventory JSON to ``path``, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(to_json(inventory), encoding="utf-8", newline="\n")
    return path


def render(inventory: Inventory, console: Console) -> None:
    """Print summary and per-program tables."""
    for corpus in inventory.corpora:
        files = Table(title=f"corpus: {corpus.name}  ({corpus.root})", title_justify="left")
        files.add_column("file kind")
        files.add_column("files", justify="right")
        files.add_column("lines", justify="right")
        for kind, totals in sorted(corpus.files_by_kind.items()):
            files.add_row(kind, str(totals.files), str(totals.lines) if totals.lines else "")
        console.print(files)
        console.print(
            f"  COBOL code lines: {corpus.cobol_code_lines}   "
            f"copybook code lines: {corpus.copybook_lines}"
        )
        console.print(f"  programs by kind : {_fmt(corpus.programs_by_kind)}")
        console.print(f"  programs by scope: {_fmt(corpus.programs_by_scope)}")
        console.print()

    programs = Table(title="programs", title_justify="left")
    for col, justify in (
        ("corpus", "left"),
        ("program", "left"),
        ("kind", "left"),
        ("scope", "left"),
        ("code", "right"),
        ("copy", "right"),
        ("call", "right"),
        ("files", "left"),
        ("features", "left"),
    ):
        programs.add_column(col, justify=justify)  # type: ignore[arg-type]
    for p in sorted(inventory.programs, key=lambda p: (p.corpus, p.scope, p.path)):
        programs.add_row(
            p.corpus,
            p.program_id or Path(p.path).stem,
            p.kind.value,
            p.scope.value,
            str(p.lines.code),
            str(p.copy_count),
            str(p.call_count),
            _fmt(p.file_organizations) or "-",
            ", ".join(p.features),
        )
    console.print(programs)


def _fmt(counts: dict[str, int]) -> str:
    return "  ".join(f"{k}={v}" for k, v in counts.items())
