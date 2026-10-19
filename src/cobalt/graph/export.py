"""Graph exports: node-link JSON, GraphML, Graphviz DOT and Mermaid."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from cobalt.graph.model import EdgeType, NodeType

FORMATS = ("json", "graphml", "dot", "mermaid")
_OVERVIEW_NODES = {
    NodeType.PROGRAM.value,
    NodeType.COPYBOOK.value,
    NodeType.FILE.value,
    NodeType.EXTERNAL.value,
}
_OVERVIEW_EDGES = {
    EdgeType.CALLS.value,
    EdgeType.COPIES.value,
    EdgeType.READS.value,
    EdgeType.WRITES.value,
}


def overview(g: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Program-level view: programs, copybooks, files, externals."""
    keep = [n for n, d in g.nodes(data=True) if d["type"] in _OVERVIEW_NODES]
    sub = nx.MultiDiGraph(g.subgraph(keep))
    drop = [
        (u, v, k)
        for u, v, k, d in sub.edges(keys=True, data=True)
        if d["type"] not in _OVERVIEW_EDGES
    ]
    sub.remove_edges_from(drop)
    return sub


def _scalar_attrs(g: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """GraphML only supports scalar attributes."""
    h = nx.MultiDiGraph()
    for n, d in g.nodes(data=True):
        h.add_node(n, **{k: _scalar(v) for k, v in d.items() if v is not None})
    for u, v, k, d in g.edges(keys=True, data=True):
        h.add_edge(u, v, key=str(k), **{a: _scalar(b) for a, b in d.items() if b is not None})
    return h


def _scalar(value: object) -> str | int | float | bool:
    if isinstance(value, str | int | float | bool):
        return value
    return json.dumps(value)


def _safe(node: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in node)


def to_dot(g: nx.MultiDiGraph) -> str:
    """Graphviz DOT of the program-level overview."""
    g = overview(g)
    shapes = {"program": "box", "copybook": "note", "file": "cylinder", "external": "ellipse"}
    lines = [f'digraph "{g.graph.get("corpus", "cobalt")}" {{', "  rankdir=LR;"]
    for n, d in sorted(g.nodes(data=True)):
        lines.append(f'  {_safe(n)} [label="{d["name"]}", shape={shapes.get(d["type"], "box")}];')
    for u, v, d in sorted(g.edges(data=True), key=lambda e: (e[0], e[1], e[2]["type"])):
        lines.append(f'  {_safe(u)} -> {_safe(v)} [label="{d["type"]}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def to_mermaid(g: nx.MultiDiGraph) -> str:
    """Mermaid flowchart of the program-level overview."""
    g = overview(g)
    shape = {
        "program": ('["', '"]'),
        "copybook": ('[/"', '"/]'),
        "file": ('[("', '")]'),
        "external": ('(("', '"))'),
    }
    lines = ["flowchart LR"]
    for n, d in sorted(g.nodes(data=True)):
        left, right = shape.get(d["type"], ('["', '"]'))
        lines.append(f"  {_safe(n)}{left}{d['name']}{right}")
    seen = set()
    for u, v, d in sorted(g.edges(data=True), key=lambda e: (e[0], e[1], e[2]["type"])):
        key = (u, v, d["type"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {_safe(u)} -->|{d['type']}| {_safe(v)}")
    return "\n".join(lines) + "\n"


def export(g: nx.MultiDiGraph, fmt: str, path: Path, *, full: bool = False) -> Path:
    """Write ``g`` in ``fmt``. DOT/Mermaid always use the overview."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    view = g if full else overview(g)
    if fmt == "json":
        path.write_text(
            json.dumps(
                nx.node_link_data(view, edges="edges"), indent=1, sort_keys=True, default=str
            ),
            encoding="utf-8",
        )
    elif fmt == "graphml":
        nx.write_graphml(_scalar_attrs(view), path)
    elif fmt == "dot":
        path.write_text(to_dot(g), encoding="utf-8")
    else:
        path.write_text(to_mermaid(g), encoding="utf-8")
    return path
