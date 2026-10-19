"""cobalt command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from cobalt import __version__
from cobalt.agents import cli as agents_cli
from cobalt.config import get_settings
from cobalt.equivalence import cli as equivalence_cli
from cobalt.graph.cli import app as graph_app
from cobalt.inventory import available_corpora, build_inventory
from cobalt.inventory.report import render, to_json, write_json
from cobalt.logging_setup import configure_logging, get_logger

app = typer.Typer(
    name="cobalt",
    help="Agentic COBOL-to-Java migration with proven behavioural equivalence.",
    no_args_is_help=True,
    add_completion=False,
)
app.add_typer(graph_app, name="graph")
app.command("golden")(equivalence_cli.golden)
app.command("equiv")(equivalence_cli.equiv)
app.command("sandbox")(equivalence_cli.sandbox)
app.command("migrate")(agents_cli.migrate)
app.command("runs")(agents_cli.runs)
app.command("status")(agents_cli.status)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"cobalt {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show version."),
    ] = False,
) -> None:
    """Configure logging for every command."""
    configure_logging(get_settings())


@app.command()
def inventory(
    corpus: Annotated[
        list[str] | None,
        typer.Option("--corpus", "-c", help="Corpus to scan (repeatable). Default: all present."),
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print JSON to stdout instead of tables.")
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(help="Where to write the JSON report. Default: runs/inventory/."),
    ] = None,
) -> None:
    """Classify every corpus file and summarise each COBOL program."""
    settings = get_settings()
    log = get_logger("cobalt.inventory")
    present = available_corpora(settings.corpus_dir)
    names = corpus or present

    missing = [n for n in names if n not in present]
    if missing:
        hint = " (run `make corpus`)" if "carddemo" in missing else ""
        typer.echo(f"corpus not found or empty: {', '.join(missing)}{hint}", err=True)
        raise typer.Exit(code=1)
    if not corpus and "carddemo" not in present:
        typer.echo("note: carddemo corpus not fetched; run `make corpus`", err=True)

    result = build_inventory(names, settings.corpus_dir, settings.root)
    path = write_json(result, out or settings.runs_dir / "inventory" / "inventory.json")
    log.info(
        "inventory.done",
        corpora=names,
        programs=len(result.programs),
        report=str(path),
    )

    if json_output:
        typer.echo(to_json(result), nl=False)
        return
    console = Console(width=160)
    render(result, console)
    console.print(f"\nreport written to {path}")
