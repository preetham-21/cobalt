"""Static checks the Supervisor runs on migrated Java before accepting it.

They catch what equivalence cannot: floating point near money, and a
migration that "passes" by embedding the expected output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cobalt.equivalence.golden import GoldenStore
from cobalt.equivalence.runtime import java_package_dir

_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"|"""(.*?)"""', re.DOTALL)
_FLOAT = re.compile(r"\b(double|float|Double|Float)\b|\b\d+\.\d*[dDfF]\b")
MIN_EMBED = 24  # a golden record at least this long found inside a literal is suspicious


@dataclass(frozen=True)
class Finding:
    """One problem found in the Java."""

    severity: str  # error | warning
    rule: str
    file: str
    message: str


def _java_files(project: Path, program: str) -> list[Path]:
    pkg = project / "src" / "main" / "java" / java_package_dir(program)
    return sorted(pkg.rglob("*.java")) if pkg.is_dir() else []


def golden_records(store: GoldenStore, program: str, cases: list[str]) -> set[str]:
    """Distinctive output records of every case (for the embedding check)."""
    records: set[str] = set()
    for case in cases:
        g = store.load(program, case)
        if g is None:
            continue
        streams = [d.decode("latin-1") for d in g.output.files.values() if d] + [g.output.stdout]
        for text in streams:
            for line in text.splitlines():
                line = line.rstrip()
                if len(line) >= MIN_EMBED and len(set(line)) > 3:
                    records.add(line)
    return records


def check_java(
    project: Path, program: str, *, golden: set[str] | None = None, cobol_source: str = ""
) -> list[Finding]:
    """Run every static rule on the program's Java package."""
    findings: list[Finding] = []
    cobol_literals = set(re.findall(r"'([^']*)'", cobol_source))
    files = _java_files(project, program)
    if not files:
        return [Finding("error", "missing", "", f"no Java sources for {program}")]
    for path in files:
        rel = path.relative_to(project).as_posix()
        code = _COMMENT.sub(" ", path.read_text(encoding="utf-8", errors="replace"))
        literals = [m.group(1) or m.group(2) or "" for m in _STRING.finditer(code)]
        no_strings = _STRING.sub('""', code)
        for m in _FLOAT.finditer(no_strings):
            findings.append(
                Finding(
                    "error",
                    "floating-point",
                    rel,
                    f"uses {m.group(0)!r}: COBOL numerics must be BigDecimal",
                )
            )
        if golden:
            for lit in literals:
                if len(lit) < MIN_EMBED or lit in cobol_literals:
                    continue
                if any(lit in record for record in golden):
                    findings.append(
                        Finding(
                            "error",
                            "embedded-output",
                            rel,
                            f"string literal {lit[:40]!r}... reproduces golden output",
                        )
                    )
                    break
    main = [p for p in files if "static void main" in p.read_text(encoding="utf-8")]
    if not main and golden is not None:
        findings.append(Finding("warning", "no-main", "", "no main method in the package"))
    return findings
