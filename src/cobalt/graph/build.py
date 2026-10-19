"""Build the code knowledge graph for a corpus.

Node ids are namespaced strings so they stay readable in exports:

    program:CBTRN02C          copybook:CVACT01Y        file:ACCTFILE
    paragraph:CBTRN02C:1000-DALYTRAN-GET-NEXT
    data:CBTRN02C:WS-MISC-VARIABLES   data:CVACT01Y:ACCT-ID
    external:CEE3ABD          external:dynamic:CDEMO-TO-PROGRAM

Files are keyed by their ASSIGN name (the DD name), which is how separate
programs share a dataset.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import networkx as nx

from cobalt.graph.model import DataItem, EdgeType, NodeType, ParsedProgram
from cobalt.graph.parser import copybook_search_dirs, parse_program
from cobalt.inventory.models import FileKind
from cobalt.inventory.scanner import classify_file, iter_corpus_files
from cobalt.logging_setup import get_logger

GRAPH_VERSION = 1
log = get_logger("cobalt.graph")


def program_id(name: str) -> str:
    return f"program:{name}"


def copybook_id(name: str) -> str:
    return f"copybook:{name}"


def file_id(assign: str) -> str:
    return f"file:{assign}"


def external_id(name: str) -> str:
    return f"external:{name}"


def paragraph_id(program: str, para: str) -> str:
    return f"paragraph:{program}:{para}"


def data_id(owner: str, item: str) -> str:
    return f"data:{owner}:{item}"


def parse_corpus(corpus_root: Path, repo_root: Path) -> list[ParsedProgram]:
    """Parse every COBOL program under ``corpus_root``."""
    sources = [p for p in iter_corpus_files(corpus_root) if classify_file(p) is FileKind.COBOL]
    cache: dict[Path, list[Path]] = {}
    parsed: list[ParsedProgram] = []
    for path in sources:
        dirs = cache.get(path.parent)
        if dirs is None:
            dirs = cache[path.parent] = copybook_search_dirs(path, corpus_root)
        rel = path.relative_to(repo_root).as_posix()
        parsed.append(parse_program(path, dirs, rel_path=rel))
    return parsed


def build_graph(programs: list[ParsedProgram], *, corpus: str) -> nx.MultiDiGraph:
    """Turn parsed programs into a typed multigraph."""
    g = nx.MultiDiGraph(name=corpus, corpus=corpus, version=GRAPH_VERSION)
    known = {p.program_id for p in programs}

    for prog in programs:
        pid = program_id(prog.program_id)
        if pid in g:
            log.warning("graph.duplicate_program", program=prog.program_id, path=prog.path)
            continue
        g.add_node(
            pid,
            type=NodeType.PROGRAM.value,
            name=prog.program_id,
            path=prog.path,
            loc=prog.lines_of_code,
            exec_kinds=prog.exec_kinds,
            using=prog.using,
            paragraphs=len(prog.paragraphs),
        )

    for prog in programs:
        pid = program_id(prog.program_id)
        own_items = {i.name for root in prog.data for i in root.walk()}

        for name in prog.copybooks:
            cid = copybook_id(name)
            if cid not in g:
                g.add_node(
                    cid,
                    type=NodeType.COPYBOOK.value,
                    name=name,
                    resolved=name not in prog.unresolved_copybooks,
                )
            g.add_edge(pid, cid, key=EdgeType.COPIES.value, type=EdgeType.COPIES.value)

        for root in prog.data:
            _add_data(g, prog, pid, root)

        for para in prog.paragraphs:
            nid = paragraph_id(prog.program_id, para.name)
            g.add_node(
                nid,
                type=NodeType.PARAGRAPH.value,
                name=para.name,
                program=prog.program_id,
                section=para.section,
                is_section=para.is_section,
                line=para.line,
                statements=para.statements,
            )
            g.add_edge(pid, nid, key=EdgeType.DEFINES.value, type=EdgeType.DEFINES.value)
        for para in prog.paragraphs:
            src = paragraph_id(prog.program_id, para.name)
            for target in para.performs:
                g.add_edge(
                    src,
                    paragraph_id(prog.program_id, target),
                    type=EdgeType.PERFORMS.value,
                    via="perform",
                )
            for target in para.goes_to:
                g.add_edge(
                    src,
                    paragraph_id(prog.program_id, target),
                    type=EdgeType.PERFORMS.value,
                    via="goto",
                )

        for call in prog.calls:
            if call.target in known:
                target = program_id(call.target)
            elif call.target in own_items:
                target = external_id(f"dynamic:{call.target}")
                g.add_node(target, type=NodeType.EXTERNAL.value, name=call.target, dynamic=True)
            else:
                target = external_id(call.target)
                if target not in g:
                    g.add_node(target, type=NodeType.EXTERNAL.value, name=call.target)
            g.add_edge(
                pid,
                target,
                type=EdgeType.CALLS.value,
                kind=call.kind,
                paragraph=call.paragraph,
                using=call.using,
            )

        modes: dict[str, set[str]] = {}
        for op in prog.file_ops:
            m = modes.setdefault(op.file, set())
            if op.verb == "OPEN" and op.mode:
                m.add(op.mode)
            elif op.verb in ("READ", "START"):
                m.add("INPUT")
            elif op.verb in ("WRITE",):
                m.add("OUTPUT")
            elif op.verb in ("REWRITE", "DELETE"):
                m.add("I-O")
        for fdef in prog.files:
            if fdef.organization == "sort" or not fdef.assign:
                continue
            fid = file_id(fdef.assign)
            if fid not in g:
                g.add_node(fid, type=NodeType.FILE.value, name=fdef.assign)
            g.nodes[fid].setdefault("organizations", [])
            if fdef.organization not in g.nodes[fid]["organizations"]:
                g.nodes[fid]["organizations"].append(fdef.organization)
            used = modes.get(fdef.name, set())
            attrs: dict[str, Any] = {
                "fd": fdef.name,
                "organization": fdef.organization,
                "access": fdef.access,
                "record_key": fdef.record_key,
                "records": fdef.records,
            }
            if used & {"INPUT", "I-O"} or not used:
                g.add_edge(pid, fid, type=EdgeType.READS.value, **attrs)
            if used & {"OUTPUT", "EXTEND", "I-O"}:
                g.add_edge(pid, fid, type=EdgeType.WRITES.value, **attrs)
    return g


def _add_data(g: nx.MultiDiGraph, prog: ParsedProgram, pid: str, root: DataItem) -> None:
    for item in root.walk():
        if item.name == "FILLER":
            continue
        owner = item.copybook or prog.program_id
        nid = data_id(owner, item.name)
        if nid not in g:
            g.add_node(
                nid,
                type=NodeType.DATA_ITEM.value,
                name=item.name,
                owner=owner,
                level=item.level,
                section=item.section,
                picture=item.picture,
                usage=item.usage,
                offset=item.offset,
                size=item.size,
                occurs=item.occurs,
                redefines=item.redefines,
            )
            definer = copybook_id(item.copybook) if item.copybook else pid
            g.add_edge(definer, nid, type=EdgeType.DEFINES.value)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def save_graph(g: nx.MultiDiGraph, programs: list[ParsedProgram], out_dir: Path) -> Path:
    """Write ``<corpus>.graph.json`` (node-link) and ``<corpus>.parsed.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus = g.graph["corpus"]
    data = nx.node_link_data(g, edges="edges")
    graph_path = out_dir / f"{corpus}.graph.json"
    graph_path.write_text(json.dumps(data, indent=1, sort_keys=True, default=str), encoding="utf-8")
    parsed_path = out_dir / f"{corpus}.parsed.json"
    parsed_path.write_text(
        json.dumps([asdict(p) for p in programs], indent=1, sort_keys=True), encoding="utf-8"
    )
    return graph_path


def load_graph(path: Path) -> nx.MultiDiGraph:
    """Load a graph written by :func:`save_graph`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return nx.node_link_graph(data, directed=True, multigraph=True, edges="edges")


def build_corpus_graph(
    corpus: str, corpus_dir: Path, repo_root: Path
) -> tuple[nx.MultiDiGraph, list[ParsedProgram]]:
    """Parse and build in one step."""
    programs = parse_corpus(corpus_dir / corpus, repo_root)
    return build_graph(programs, corpus=corpus), programs
