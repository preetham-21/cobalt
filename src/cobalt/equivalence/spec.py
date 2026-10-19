"""Run specifications and test cases for a corpus.

A corpus that can be executed has a ``runs.toml`` (see
``corpus/synthetic/runs.toml``). Test cases come from three places, in order:

1. ``default`` - the inputs named in ``runs.toml``;
2. ``<corpus>/cases/<PROGRAM>/<case>/`` - committed extra cases;
3. any extra case roots a caller passes (e.g. agent-written cases).

A case directory holds input files named by their logical (ASSIGN) names.
Inputs a case does not provide fall back to the default inputs, so a case can
override just one file.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProgramSpec:
    """How to build and run one main program."""

    name: str
    sources: tuple[Path, ...]
    inputs: dict[str, Path]
    outputs: tuple[str, ...]
    stdout: bool
    expected_rc: int
    indexed: dict[str, str] = field(default_factory=dict)  # logical -> key spec


@dataclass(frozen=True)
class CorpusSpec:
    """Everything needed to execute a corpus's programs."""

    root: Path
    copybook_dirs: tuple[Path, ...]
    cobc_flags: tuple[str, ...]
    stubs: tuple[Path, ...]
    programs: dict[str, ProgramSpec]

    def program(self, name: str) -> ProgramSpec:
        try:
            return self.programs[name.upper()]
        except KeyError:
            known = ", ".join(sorted(self.programs))
            raise KeyError(f"no run spec for {name!r} (known: {known})") from None


@dataclass(frozen=True)
class TestCase:
    """One set of inputs for one program."""

    __test__ = False  # not a pytest class

    program: str
    name: str
    inputs: dict[str, Path]
    source: str  # "default" | "corpus" | "extra"


def load_corpus_spec(corpus_root: Path) -> CorpusSpec:
    """Read ``<corpus_root>/runs.toml``."""
    path = corpus_root / "runs.toml"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: corpus is not executable")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    programs: dict[str, ProgramSpec] = {}
    for name, prog in raw.get("programs", {}).items():
        programs[name.upper()] = ProgramSpec(
            name=name.upper(),
            sources=tuple(corpus_root / s for s in prog["sources"]),
            inputs={k: corpus_root / v for k, v in prog.get("inputs", {}).items()},
            outputs=tuple(prog.get("outputs", [])),
            stdout=bool(prog.get("stdout", False)),
            expected_rc=int(prog.get("expected_rc", 0)),
            indexed=dict(prog.get("indexed", {})),
        )
    return CorpusSpec(
        root=corpus_root,
        copybook_dirs=tuple(corpus_root / d for d in defaults.get("copybook_dirs", [])),
        cobc_flags=tuple(defaults.get("cobc_flags", ["-x"])),
        stubs=tuple(corpus_root / s for s in defaults.get("stubs", [])),
        programs=programs,
    )


def discover_cases(
    spec: CorpusSpec, program: str, extra_roots: Iterable[Path] = ()
) -> list[TestCase]:
    """All test cases for ``program``, default first, then by name."""
    prog = spec.program(program)
    cases = [TestCase(prog.name, "default", dict(prog.inputs), "default")]
    seen = {"default"}
    roots = [(spec.root / "cases", "corpus"), *((Path(r), "extra") for r in extra_roots)]
    for root, source in roots:
        base = root / prog.name
        if not base.is_dir():
            continue
        for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            if case_dir.name in seen:
                continue
            inputs = dict(prog.inputs)
            provided = {f.name: f for f in case_dir.iterdir() if f.is_file()}
            if not set(provided) & set(prog.inputs):
                continue
            inputs.update({k: v for k, v in provided.items() if k in prog.inputs})
            cases.append(TestCase(prog.name, case_dir.name, inputs, source))
            seen.add(case_dir.name)
    return cases
