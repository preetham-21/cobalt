"""``cobalt graph`` subcommands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from cobalt.config import get_settings
from cobalt.graph.export import FORMATS, export
from cobalt.graph.query import context_for, impact, migration_order, resolve, summary
from cobalt.graph.store import get_graph, graph_path

app = typer.Typer(help="Code knowledge graph: build, inspect, order, export.", no_args_is_help=True)
CorpusOpt = Annotated[str, typer.Option("--corpus", "-c", help="Corpus name.")]
console = Console(width=140)


def _graph(corpus: str, rebuild: bool = False):  # noqa: ANN202
    try:
        return get_graph(get_settings(), corpus, rebuild=rebuild)
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command("build")
def build_cmd(
    corpus: Annotated[list[str] | None, typer.Option("--corpus", "-c")] = None,
) -> None:
    """Parse the corpus and (re)build its graph under runs/graph/."""
    settings = get_settings()
    for name in corpus or ["synthetic"]:
        g = _graph(name, rebuild=True)
        counts = summary(g)
        typer.echo(f"{name}: {graph_path(settings, name)}")
        typer.echo(f"  nodes {json.dumps(counts['nodes'])}")
        typer.echo(f"  edges {json.dumps(counts['edges'])}")


@app.command("show")
def show_cmd(name: str, corpus: CorpusOpt = "synthetic") -> None:
    """Show a node's attributes and its edges."""
    g = _graph(corpus)
    node = resolve(g, name)
    if node is None:
        typer.echo(f"no node named {name!r} in {corpus}", err=True)
        raise typer.Exit(1)
    attrs = g.nodes[node]
    console.print(f"[bold]{node}[/bold]")
    for key, value in sorted(attrs.items()):
        console.print(f"  {key}: {value}")
    table = Table("direction", "edge", "node", "details")
    for _, v, d in sorted(g.out_edges(node, data=True), key=lambda e: (e[2]["type"], e[1])):
        if d["type"] == "DEFINES" and g.nodes[v]["type"] == "data_item":
            continue
        table.add_row("out", d["type"], v, _details(d))
    for u, _, d in sorted(g.in_edges(node, data=True), key=lambda e: (e[2]["type"], e[0])):
        table.add_row("in", d["type"], u, _details(d))
    defined = sum(
        1
        for _, v, d in g.out_edges(node, data=True)
        if d["type"] == "DEFINES" and g.nodes[v]["type"] == "data_item"
    )
    console.print(table)
    if defined:
        console.print(f"  (+{defined} data items defined)")


def _details(d: dict) -> str:
    keep = {k: v for k, v in d.items() if k not in ("type",) and v not in (None, [], "")}
    return ", ".join(f"{k}={v}" for k, v in sorted(keep.items()))


@app.command("order")
def order_cmd(
    corpus: CorpusOpt = "synthetic",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Topological migration order (callees first) with parallel waves."""
    entries = migration_order(_graph(corpus))
    if json_output:
        typer.echo(json.dumps([e.__dict__ for e in entries], indent=2))
        return
    table = Table("#", "wave", "program", "depends on", "cycle")
    for i, e in enumerate(entries, start=1):
        table.add_row(str(i), str(e.wave), e.program, ", ".join(e.depends_on), ", ".join(e.cycle))
    console.print(table)


@app.command("context")
def context_cmd(program: str, corpus: CorpusOpt = "synthetic") -> None:
    """The targeted context an agent receives for one program (JSON)."""
    try:
        typer.echo(json.dumps(context_for(_graph(corpus), program.upper()), indent=2))
    except KeyError as exc:
        typer.echo(f"unknown program {program}", err=True)
        raise typer.Exit(1) from exc


@app.command("impact")
def impact_cmd(name: str, corpus: CorpusOpt = "synthetic") -> None:
    """Programs to re-verify if a copybook, program or file changes."""
    try:
        affected = impact(_graph(corpus), name)
    except KeyError as exc:
        typer.echo(f"no node named {name!r}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("\n".join(affected) if affected else "(nothing else affected)")


@app.command("export")
def export_cmd(
    fmt: Annotated[str, typer.Option("--format", "-f", help=f"One of {', '.join(FORMATS)}.")],
    corpus: CorpusOpt = "synthetic",
    out: Annotated[Path | None, typer.Option(help="Output path.")] = None,
    full: Annotated[bool, typer.Option(help="Include paragraphs and data items.")] = False,
) -> None:
    """Export the graph (DOT and Mermaid always use the program-level overview)."""
    ext = {"json": "json", "graphml": "graphml", "dot": "dot", "mermaid": "mmd"}
    if fmt not in ext:
        typer.echo(f"unknown format {fmt}", err=True)
        raise typer.Exit(1)
    path = out or get_settings().runs_dir / "graph" / f"{corpus}.{ext[fmt]}"
    typer.echo(str(export(_graph(corpus), fmt, path, full=full)))
