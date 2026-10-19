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
from dataclasses import dataclass
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
    #: the program takes a JCL PARM (PROCEDURE DIVISION USING); the PARM text
    #: is the input file named ``PARM`` (COBOL gets it via a generated driver,
    #: Java as ``args[0]``)
    parm: bool = False
    #: regexes for fields that legitimately vary between runs (masked in diffs)
    volatile: tuple[str, ...] = ()


@dataclass(frozen=True)
class CorpusSpec:
    """Everything needed to execute a corpus's programs."""

    root: Path
    copybook_dirs: tuple[Path, ...]
    cobc_flags: tuple[str, ...]
    stubs: tuple[Path, ...]
    programs: dict[str, ProgramSpec]
    spec_dir: Path = Path()
    cases_dir: Path = Path()
    golden_dir: Path = Path()
    #: pinned "now" for both languages ("YYYY-MM-DD HH:MM:SS"), or None
    current_date: str | None = None

    def clock_env(self, language: str) -> dict[str, str]:
        """Environment that pins the clock for a COBOL or Java run."""
        if not self.current_date:
            return {}
        if language == "cobol":
            # compact form: GnuCOBOL then also pins hundredths to 00 (the
            # "YYYY/MM/DD HH:MM:SS" form takes them from the real clock)
            digits = "".join(c for c in self.current_date if c.isdigit())
            return {"COB_CURRENT_DATE": digits}
        return {"COBALT_CURRENT_DATE": self.current_date.replace(" ", "T")}

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


def spec_path(corpus_root: Path) -> Path:
    """Where a corpus's run spec lives.

    ``<corpus>/runs.toml`` for corpora we own; for an external corpus that is
    fetched and gitignored (CardDemo), a committed sibling directory
    ``<corpus>-support/runs.toml`` holding the spec, stubs, cases and golden.
    """
    inside = corpus_root / "runs.toml"
    if inside.is_file():
        return inside
    return corpus_root.parent / f"{corpus_root.name}-support" / "runs.toml"


def load_corpus_spec(corpus_root: Path) -> CorpusSpec:
    """Read a corpus's run spec (see :func:`spec_path`).

    ``[defaults] source_root`` (relative to the spec file, default ".") is the
    base of program sources, inputs and copybook dirs; ``stubs``, ``cases_dir``
    and ``golden_dir`` are relative to the spec file.
    """
    path = spec_path(corpus_root)
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found: corpus is not executable")
    spec_dir = path.parent
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    src = (spec_dir / defaults.get("source_root", ".")).resolve()
    programs: dict[str, ProgramSpec] = {}
    for name, prog in raw.get("programs", {}).items():
        inputs = {k: src / v for k, v in prog.get("inputs", {}).items()}
        for k, v in prog.get("support_inputs", {}).items():
            inputs[k] = spec_dir / v
        programs[name.upper()] = ProgramSpec(
            name=name.upper(),
            sources=tuple(src / s for s in prog["sources"]),
            inputs=inputs,
            outputs=tuple(prog.get("outputs", [])),
            stdout=bool(prog.get("stdout", False)),
            expected_rc=int(prog.get("expected_rc", 0)),
            parm=bool(prog.get("parm", False)),
            volatile=tuple(prog.get("volatile", defaults.get("volatile", []))),
        )
    return CorpusSpec(
        root=corpus_root,
        copybook_dirs=tuple(src / d for d in defaults.get("copybook_dirs", [])),
        cobc_flags=tuple(defaults.get("cobc_flags", ["-x"])),
        stubs=tuple(spec_dir / s for s in defaults.get("stubs", [])),
        programs=programs,
        spec_dir=spec_dir,
        cases_dir=spec_dir / defaults.get("cases_dir", "cases"),
        golden_dir=spec_dir / defaults.get("golden_dir", "golden"),
        current_date=defaults.get("current_date"),
    )


def discover_cases(
    spec: CorpusSpec, program: str, extra_roots: Iterable[Path] = ()
) -> list[TestCase]:
    """All test cases for ``program``, default first, then by name."""
    prog = spec.program(program)
    cases = [TestCase(prog.name, "default", dict(prog.inputs), "default")]
    seen = {"default"}
    roots = [(spec.cases_dir, "corpus"), *((Path(r), "extra") for r in extra_roots)]
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
