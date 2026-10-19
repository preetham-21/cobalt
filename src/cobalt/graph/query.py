"""Questions the rest of cobalt asks the knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from cobalt.graph.build import copybook_id, program_id
from cobalt.graph.model import EdgeType, NodeType


def nodes_of(g: nx.MultiDiGraph, node_type: NodeType) -> list[str]:
    """Sorted node ids of one type."""
    return sorted(n for n, d in g.nodes(data=True) if d.get("type") == node_type.value)


def out_edges(g: nx.MultiDiGraph, node: str, edge: EdgeType) -> list[tuple[str, dict[str, Any]]]:
    """(target, attrs) for out-edges of one type, sorted by target."""
    return sorted(
        ((v, d) for _, v, d in g.out_edges(node, data=True) if d.get("type") == edge.value),
        key=lambda x: x[0],
    )


def in_edges(g: nx.MultiDiGraph, node: str, edge: EdgeType) -> list[tuple[str, dict[str, Any]]]:
    """(source, attrs) for in-edges of one type, sorted by source."""
    return sorted(
        ((u, d) for u, _, d in g.in_edges(node, data=True) if d.get("type") == edge.value),
        key=lambda x: x[0],
    )


def resolve(g: nx.MultiDiGraph, name: str) -> str | None:
    """Find a node by id or by bare name (programs, copybooks, files first)."""
    if name in g:
        return name
    upper = name.upper()
    for prefix in ("program", "copybook", "file", "external"):
        candidate = f"{prefix}:{upper}"
        if candidate in g:
            return candidate
    matches = [n for n, d in g.nodes(data=True) if d.get("name") == upper]
    return sorted(matches)[0] if matches else None


# --------------------------------------------------------------------------- #
# Migration order
# --------------------------------------------------------------------------- #
@dataclass
class OrderEntry:
    """One program in migration order."""

    program: str
    wave: int  # 0 = no in-corpus callees; wave n depends only on waves < n
    depends_on: list[str] = field(default_factory=list)
    cycle: list[str] = field(default_factory=list)  # other members of a CALL cycle


#: CALL kinds that create a build/migration dependency. CICS XCTL transfers
#: control and never returns, so it is navigation, not a dependency.
DEPENDENCY_CALL_KINDS = {"static", "dynamic", "cics-link"}


def call_graph(g: nx.MultiDiGraph) -> nx.DiGraph:
    """Program-to-program dependency graph (corpus programs only)."""
    cg = nx.DiGraph()
    for pid in nodes_of(g, NodeType.PROGRAM):
        cg.add_node(g.nodes[pid]["name"])
    for pid in nodes_of(g, NodeType.PROGRAM):
        for target, attrs in out_edges(g, pid, EdgeType.CALLS):
            if attrs.get("kind") not in DEPENDENCY_CALL_KINDS:
                continue
            if g.nodes[target].get("type") == NodeType.PROGRAM.value:
                cg.add_edge(g.nodes[pid]["name"], g.nodes[target]["name"])
    return cg


def migration_order(g: nx.MultiDiGraph, programs: list[str] | None = None) -> list[OrderEntry]:
    """Callees before callers; cycles collapse into one step; ties by name.

    If ``programs`` is given, the order is restricted to them (dependencies
    outside the set are still reported in ``depends_on``).
    """
    cg = call_graph(g)
    condensed = nx.condensation(cg)
    members = {c: sorted(condensed.nodes[c]["members"]) for c in condensed}
    # edges point caller -> callee; reverse so callees come first
    rev = condensed.reverse(copy=True)
    wave: dict[int, int] = {}
    for comp in nx.topological_sort(rev):
        preds = list(rev.predecessors(comp))
        wave[comp] = 1 + max((wave[p] for p in preds), default=-1)
    order = nx.lexicographical_topological_sort(rev, key=lambda c: (wave[c], members[c][0]))

    entries: list[OrderEntry] = []
    wanted = set(programs) if programs else None
    for comp in order:
        for name in members[comp]:
            if wanted is not None and name not in wanted:
                continue
            entries.append(
                OrderEntry(
                    program=name,
                    wave=wave[comp],
                    depends_on=sorted(cg.successors(name)),
                    cycle=[m for m in members[comp] if m != name],
                )
            )
    return entries


# --------------------------------------------------------------------------- #
# Context retrieval and impact analysis
# --------------------------------------------------------------------------- #
def copybooks_of(g: nx.MultiDiGraph, program: str) -> list[str]:
    """Copybooks a program includes (directly or nested)."""
    pid = program_id(program)
    seen: list[str] = []
    stack = [pid]
    while stack:
        node = stack.pop()
        for target, _ in out_edges(g, node, EdgeType.COPIES):
            name = g.nodes[target]["name"]
            if name not in seen:
                seen.append(name)
                stack.append(target)
    return sorted(seen)


def context_for(g: nx.MultiDiGraph, program: str) -> dict[str, Any]:
    """Exactly what an agent needs to migrate ``program``."""
    pid = program_id(program)
    if pid not in g:
        raise KeyError(program)
    node = g.nodes[pid]
    callees, externals = [], []
    for target, attrs in out_edges(g, pid, EdgeType.CALLS):
        t = g.nodes[target]
        entry = {"name": t["name"], "kind": attrs.get("kind"), "using": attrs.get("using", [])}
        if t["type"] == NodeType.PROGRAM.value:
            entry["path"] = t.get("path")
            entry["parameters"] = t.get("using", [])
            callees.append(entry)
        else:
            externals.append(entry)
    callers = sorted({g.nodes[u]["name"] for u, _ in in_edges(g, pid, EdgeType.CALLS)})
    files = []
    for edge in (EdgeType.READS, EdgeType.WRITES):
        for target, attrs in out_edges(g, pid, edge):
            files.append(
                {
                    "assign": g.nodes[target]["name"],
                    "direction": "input" if edge is EdgeType.READS else "output",
                    "fd": attrs.get("fd"),
                    "organization": attrs.get("organization"),
                    "record_key": attrs.get("record_key"),
                    "records": attrs.get("records", []),
                    "shared_with": sorted(
                        {
                            g.nodes[u]["name"]
                            for u, _, d in g.in_edges(target, data=True)
                            if u != pid and d.get("type") in ("READS", "WRITES")
                        }
                    ),
                }
            )
    cbs = copybooks_of(g, program)
    return {
        "program": program,
        "path": node.get("path"),
        "loc": node.get("loc"),
        "exec_kinds": node.get("exec_kinds", []),
        "parameters": node.get("using", []),
        "copybooks": [
            {
                "name": c,
                "resolved": g.nodes[copybook_id(c)].get("resolved", True),
                "used_by": sorted(
                    g.nodes[u]["name"]
                    for u, _ in in_edges(g, copybook_id(c), EdgeType.COPIES)
                    if g.nodes[u]["type"] == NodeType.PROGRAM.value
                ),
            }
            for c in cbs
        ],
        "callees": callees,
        "external_calls": externals,
        "callers": callers,
        "files": files,
    }


def impact(g: nx.MultiDiGraph, name: str) -> list[str]:
    """Programs that may need re-verification if ``name`` changes.

    A copybook impacts every program that copies it; a program impacts its
    (transitive) callers; a file impacts every program that reads it.
    """
    node = resolve(g, name)
    if node is None:
        raise KeyError(name)
    kind = g.nodes[node]["type"]
    affected: set[str] = set()
    if kind == NodeType.COPYBOOK.value:
        frontier = [node]
        while frontier:
            current = frontier.pop()
            for u, _ in in_edges(g, current, EdgeType.COPIES):
                if g.nodes[u]["type"] == NodeType.PROGRAM.value:
                    affected.add(g.nodes[u]["name"])
                elif u not in frontier:
                    frontier.append(u)
    elif kind == NodeType.FILE.value:
        affected = {g.nodes[u]["name"] for u, _ in in_edges(g, node, EdgeType.READS)}
    elif kind == NodeType.PROGRAM.value:
        cg = call_graph(g)
        affected = set(nx.ancestors(cg, g.nodes[node]["name"]))
    return sorted(affected)


def summary(g: nx.MultiDiGraph) -> dict[str, Any]:
    """Node and edge counts by type."""
    nodes: dict[str, int] = {}
    for _, d in g.nodes(data=True):
        nodes[d["type"]] = nodes.get(d["type"], 0) + 1
    edges: dict[str, int] = {}
    for _, _, d in g.edges(data=True):
        edges[d["type"]] = edges.get(d["type"], 0) + 1
    return {"nodes": dict(sorted(nodes.items())), "edges": dict(sorted(edges.items()))}
