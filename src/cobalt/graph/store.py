"""Cached graph access: rebuild only when corpus sources changed."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from cobalt.config import Settings
from cobalt.graph.build import build_corpus_graph, load_graph, save_graph
from cobalt.inventory.models import FileKind
from cobalt.inventory.scanner import classify_file, iter_corpus_files

_SOURCE_KINDS = {FileKind.COBOL, FileKind.COPYBOOK}


def graph_path(settings: Settings, corpus: str) -> Path:
    return settings.runs_dir / "graph" / f"{corpus}.graph.json"


def _latest_source_mtime(corpus_root: Path) -> float:
    return max(
        (
            p.stat().st_mtime
            for p in iter_corpus_files(corpus_root)
            if classify_file(p) in _SOURCE_KINDS
        ),
        default=0.0,
    )


def get_graph(settings: Settings, corpus: str, *, rebuild: bool = False) -> nx.MultiDiGraph:
    """Load the corpus graph, rebuilding it if missing or stale."""
    path = graph_path(settings, corpus)
    root = settings.corpus_dir / corpus
    if not root.is_dir():
        raise FileNotFoundError(f"corpus {corpus!r} not found under {settings.corpus_dir}")
    fresh = path.is_file() and path.stat().st_mtime >= _latest_source_mtime(root)
    if fresh and not rebuild:
        return load_graph(path)
    g, programs = build_corpus_graph(corpus, settings.corpus_dir, settings.root)
    save_graph(g, programs, path.parent)
    return g
