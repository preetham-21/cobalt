"""Stage 3: equivalence harness (golden capture, Java runner, normalised diff)."""

from cobalt.equivalence.diff import CaseResult, Divergence, StreamDiff, compare_stream
from cobalt.equivalence.harness import EquivalenceHarness, EquivalenceReport, GoldenResult
from cobalt.equivalence.runtime import main_class

__all__ = [
    "CaseResult",
    "Divergence",
    "EquivalenceHarness",
    "EquivalenceReport",
    "GoldenResult",
    "StreamDiff",
    "compare_stream",
    "main_class",
]
