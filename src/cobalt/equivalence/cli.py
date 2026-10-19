"""``cobalt golden``, ``cobalt equiv`` and ``cobalt sandbox`` commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from cobalt.config import get_settings
from cobalt.equivalence.harness import EquivalenceHarness
from cobalt.graph.query import migration_order
from cobalt.graph.store import get_graph
from cobalt.sandbox import SandboxError, get_sandbox, new_job_dir, remove_job_dir

CorpusOpt = Annotated[str, typer.Option("--corpus", "-c", help="Corpus name.")]


def _harness(corpus: str) -> EquivalenceHarness:
    try:
        return EquivalenceHarness(get_settings(), corpus)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def golden(
    programs: Annotated[list[str] | None, typer.Argument(help="Programs (default: all).")] = None,
    corpus: CorpusOpt = "synthetic",
    force: Annotated[bool, typer.Option(help="Re-capture even if up to date.")] = False,
) -> None:
    """Run the original COBOL on every test case and store the golden outputs."""
    h = _harness(corpus)
    names = [p.upper() for p in programs] if programs else sorted(h.spec.programs)
    failed = False
    try:
        for name in names:
            for r in h.capture_golden(name, force=force):
                rc = "" if r.returncode is None else f" rc={r.returncode}"
                typer.echo(f"{r.program:<10} {r.case:<16} {r.status}{rc}")
                if r.status in ("compile-failed", "rc-mismatch"):
                    failed = True
                    if r.detail:
                        typer.echo("  " + r.detail.replace("\n", "\n  "))
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"golden root: {h.golden.root}")
    if failed:
        raise typer.Exit(1)


def equiv(
    programs: Annotated[list[str] | None, typer.Argument(help="Programs (default: all).")] = None,
    corpus: CorpusOpt = "synthetic",
    java_root: Annotated[
        Path | None,
        typer.Option(help="Maven project holding the Java (default: baselines/<corpus>)."),
    ] = None,
) -> None:
    """Build the migrated Java, run it on every case and diff against golden."""
    settings = get_settings()
    h = _harness(corpus)
    root = java_root or settings.root / "baselines" / corpus
    g = get_graph(settings, corpus)
    deps = {e.program: e.depends_on for e in migration_order(g)}
    names = [p.upper() for p in programs] if programs else sorted(h.spec.programs)
    all_ok = True
    try:
        for name in names:
            report = h.check(name, root, dependencies=deps.get(name, []))
            h.save_report(report)
            typer.echo(report.summary())
            all_ok &= report.passed
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if not all_ok:
        raise typer.Exit(1)


def sandbox(
    backend: Annotated[str | None, typer.Option(help="auto | docker | wsl | local")] = None,
) -> None:
    """Show which sandbox backend is used and the toolchain inside it."""
    settings = get_settings()
    try:
        sb = get_sandbox(settings, backend)
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"backend: {sb.name}  (network isolated: {sb.isolates_network})")
    job = new_job_dir(settings.runs_dir / "jobs", "probe")
    try:
        res = sb.run(
            [
                "bash",
                "-c",
                "cobc --version | head -1; java -version 2>&1 | head -1; javac -version 2>&1",
            ],
            job,
        )
        for line in res.stdout.strip().splitlines():
            typer.echo(f"  {line}")
    finally:
        remove_job_dir(job)
