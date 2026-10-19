"""The equivalence harness: golden capture and COBOL-vs-Java verdicts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cobalt.config import Settings
from cobalt.equivalence.diff import CaseResult, compare_stream
from cobalt.equivalence.golden import GoldenStore, source_digest
from cobalt.equivalence.runtime import CobolRunner, CompileResult, JavaRunner
from cobalt.equivalence.spec import CorpusSpec, TestCase, discover_cases, load_corpus_spec
from cobalt.graph.model import DataItem
from cobalt.graph.parser import parse_program
from cobalt.logging_setup import get_logger
from cobalt.sandbox import Sandbox, get_sandbox

log = get_logger("cobalt.equivalence")


@dataclass
class GoldenResult:
    """Outcome of capturing golden output for one case."""

    program: str
    case: str
    status: str  # captured | fresh | compile-failed | rc-mismatch
    returncode: int | None = None
    detail: str = ""


@dataclass
class EquivalenceReport:
    """Verdict for one program across all its test cases."""

    program: str
    java_root: str
    compile_ok: bool
    compile_log: str = ""
    compile_errors: list[dict[str, str]] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)
    missing_golden: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.compile_ok
            and not self.missing_golden
            and bool(self.cases)
            and all(c.passed for c in self.cases)
        )

    @property
    def passed_cases(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    def summary(self) -> str:
        if not self.compile_ok:
            first = self.compile_errors[0] if self.compile_errors else None
            where = f" ({first['file']}:{first['line']}: {first['message']})" if first else ""
            return f"{self.program}: COMPILE ERROR{where}"
        status = "PASS" if self.passed else "FAIL"
        text = f"{self.program}: {status} {self.passed_cases}/{len(self.cases)} cases"
        if self.missing_golden:
            text += f"; no golden for {', '.join(self.missing_golden)}"
        for case in self.cases:
            if not case.passed:
                text += f"\n  [{case.case}] {case.first_failure()}"
        return text

    def to_dict(self) -> dict:
        data = asdict(self)
        data["passed"] = self.passed
        return data


class EquivalenceHarness:
    """Ties together specs, sandbox, runners and golden store for a corpus."""

    def __init__(
        self,
        settings: Settings,
        corpus: str,
        *,
        sandbox: Sandbox | None = None,
        golden_root: Path | None = None,
    ) -> None:
        self.settings = settings
        self.corpus = corpus
        self.spec: CorpusSpec = load_corpus_spec(settings.corpus_dir / corpus)
        self._sandbox = sandbox
        self.golden = GoldenStore(golden_root or self.spec.root / "golden")
        build = settings.runs_dir / "build"
        jobs = settings.runs_dir / "jobs"
        self._build, self._jobs = build, jobs

    @property
    def sandbox(self) -> Sandbox:
        if self._sandbox is None:
            self._sandbox = get_sandbox(self.settings)
        return self._sandbox

    def cobol(self) -> CobolRunner:
        return CobolRunner(self.spec, self.sandbox, self._build, self._jobs)

    def java(self) -> JavaRunner:
        return JavaRunner(self.sandbox, self._build, self._jobs)

    def cases(self, program: str, extra_roots: Iterable[Path] = ()) -> list[TestCase]:
        return discover_cases(self.spec, program, extra_roots)

    def _copybooks(self) -> list[Path]:
        return [p for d in self.spec.copybook_dirs if d.is_dir() for p in d.iterdir()]

    def toolchain(self) -> str:
        res = self.sandbox.run(["cobc", "--version"], self._jobs_dir())
        first = res.stdout.strip().splitlines()[:1]
        return first[0] if first else "unknown"

    def _jobs_dir(self) -> Path:
        self._jobs.mkdir(parents=True, exist_ok=True)
        return self._jobs

    # ------------------------------------------------------------------ #
    def capture_golden(
        self,
        program: str,
        *,
        extra_roots: Iterable[Path] = (),
        store: GoldenStore | None = None,
        force: bool = False,
    ) -> list[GoldenResult]:
        """Run the COBOL program on every case and store its outputs."""
        store = store or self.golden
        prog = self.spec.program(program)
        digest = source_digest(prog, self._copybooks())
        cases = self.cases(program, extra_roots)
        if not force and all(store.is_fresh(prog, c, digest) for c in cases):
            return [GoldenResult(prog.name, c.name, "fresh") for c in cases]
        runner = self.cobol()
        compiled = runner.compile(prog)
        if not compiled.ok or compiled.artifact is None:
            return [
                GoldenResult(prog.name, c.name, "compile-failed", detail=compiled.log)
                for c in cases
            ]
        toolchain = self.toolchain()
        results = []
        for case in cases:
            if not force and store.is_fresh(prog, case, digest):
                results.append(GoldenResult(prog.name, case.name, "fresh"))
                continue
            out = runner.run(prog, case, compiled.artifact)
            store.save(prog, case, out, source_sha=digest, toolchain=toolchain)
            status = "captured"
            if case.name == "default" and out.returncode != prog.expected_rc:
                status = "rc-mismatch"
            results.append(
                GoldenResult(prog.name, case.name, status, out.returncode, out.stderr.strip()[:500])
            )
            log.info("golden.captured", program=prog.name, case=case.name, rc=out.returncode)
        return results

    # ------------------------------------------------------------------ #
    def layouts(self, program: str) -> dict[str, list[DataItem]]:
        """Record layouts of each output file, keyed by logical name."""
        prog = self.spec.program(program)
        parsed = parse_program(prog.sources[0], list(self.spec.copybook_dirs))
        layouts: dict[str, list[DataItem]] = {}
        for f in parsed.files:
            records = [parsed.find_item(r) for r in f.records]
            layouts[f.assign] = [r for r in records if r is not None]
        return layouts

    def compile_java(
        self, program: str, java_root: Path, dependencies: Iterable[str] = ()
    ) -> CompileResult:
        return self.java().compile(java_root, [program, *dependencies])

    def check(
        self,
        program: str,
        java_root: Path,
        *,
        dependencies: Iterable[str] = (),
        extra_roots: Iterable[Path] = (),
        store: GoldenStore | None = None,
        cases: list[str] | None = None,
    ) -> EquivalenceReport:
        """Build the Java, run it on every case with golden output, and diff."""
        store = store or self.golden
        prog = self.spec.program(program)
        report = EquivalenceReport(prog.name, str(java_root), compile_ok=False)
        compiled = self.compile_java(prog.name, java_root, dependencies)
        report.compile_ok = compiled.ok
        report.compile_log = compiled.log[-4000:]
        report.compile_errors = compiled.errors[:20]
        if not compiled.ok or compiled.artifact is None:
            return report
        layouts = self.layouts(prog.name)
        runner = self.java()
        for case in self.cases(prog.name, extra_roots):
            if cases and case.name not in cases:
                continue
            golden = store.load(prog.name, case.name)
            if golden is None:
                report.missing_golden.append(case.name)
                continue
            out = runner.run(prog.name, prog.outputs, case, compiled.artifact)
            streams = [
                compare_stream(
                    name, golden.output.files.get(name), out.files.get(name), layouts.get(name)
                )
                for name in prog.outputs
            ]
            if prog.stdout:
                streams.append(compare_stream("SYSOUT", golden.output.stdout, out.stdout))
            error = None
            if out.timed_out:
                error = "timed out"
            elif out.returncode != golden.output.returncode and out.stderr.strip():
                error = f"exit {out.returncode}: {out.stderr.strip().splitlines()[-1][:300]}"
            passed = (
                error is None
                and out.returncode == golden.output.returncode
                and all(s.ok for s in streams)
            )
            report.cases.append(
                CaseResult(
                    case.name, passed, golden.output.returncode, out.returncode, streams, error
                )
            )
        log.info(
            "equivalence.checked", program=prog.name, passed=report.passed, cases=len(report.cases)
        )
        return report

    def save_report(self, report: EquivalenceReport) -> Path:
        out = self.settings.runs_dir / "equivalence" / f"{report.program}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return out
