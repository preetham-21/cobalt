"""Compile and run COBOL and Java programs inside the sandbox.

Both languages follow the same execution contract so their outputs can be
compared byte for byte:

* the program runs with a fresh job directory as its current directory;
* every input file is present there under its logical (ASSIGN) name;
* output files are written there under their logical names;
* DISPLAY / System.out goes to stdout; the exit code is RETURN-CODE.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cobalt.equivalence.spec import CorpusSpec, ProgramSpec, TestCase
from cobalt.sandbox import ExecResult, Sandbox, new_job_dir, remove_job_dir

JAVA_ROOT_PACKAGE = "cobalt.migrated"
_JAVAC_ERROR = re.compile(r"^(?P<file>[^\n]+?\.java):(?P<line>\d+): error: (?P<msg>.*)$", re.M)


@dataclass
class RunOutput:
    """Result of running one program on one test case."""

    returncode: int
    stdout: str
    stderr: str
    files: dict[str, bytes | None]  # logical name -> content (None if not written)
    duration_s: float
    timed_out: bool = False


@dataclass
class CompileResult:
    """Outcome of a compilation."""

    ok: bool
    artifact: Path | None
    log: str
    errors: list[dict[str, str]] = field(default_factory=list)
    cached: bool = False
    duration_s: float = 0.0


def main_class(program: str) -> str:
    """Fully-qualified Java main class for a migrated program.

    ``CUSTRPT`` -> ``cobalt.migrated.custrpt.Custrpt``
    """
    pkg = program.lower().replace("-", "")
    cls = "".join(part.capitalize() for part in program.lower().split("-"))
    return f"{JAVA_ROOT_PACKAGE}.{pkg}.{cls}"


def java_package_dir(program: str) -> Path:
    """Source directory (relative to ``src/main/java``) of a program's package."""
    return Path(*main_class(program).split(".")[:-1])


def _digest(paths: list[Path], *extra: str) -> str:
    h = hashlib.sha256()
    for p in sorted(paths, key=lambda x: x.as_posix()):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    for e in extra:
        h.update(e.encode())
    return h.hexdigest()[:16]


def _copy_inputs(case: TestCase, job: Path) -> None:
    for logical, src in case.inputs.items():
        shutil.copyfile(src, job / logical)


def _collect(job: Path, outputs: tuple[str, ...], result: ExecResult) -> RunOutput:
    files: dict[str, bytes | None] = {}
    for logical in outputs:
        path = job / logical
        files[logical] = path.read_bytes() if path.is_file() else None
    return RunOutput(
        result.returncode, result.stdout, result.stderr, files, result.duration_s, result.timed_out
    )


# --------------------------------------------------------------------------- #
# COBOL
# --------------------------------------------------------------------------- #
class CobolRunner:
    """Compile (cached) and run COBOL programs with GnuCOBOL."""

    def __init__(
        self, spec: CorpusSpec, sandbox: Sandbox, build_root: Path, jobs_root: Path
    ) -> None:
        self.spec = spec
        self.sandbox = sandbox
        self.build_root = build_root
        self.jobs_root = jobs_root

    def _copybooks(self) -> list[Path]:
        return [
            p
            for d in self.spec.copybook_dirs
            if d.is_dir()
            for p in sorted(d.iterdir())
            if p.is_file()
        ]

    def compile(self, prog: ProgramSpec) -> CompileResult:
        sources = [*prog.sources, *self.spec.stubs]
        key = _digest([*sources, *self._copybooks()], *self.spec.cobc_flags)
        out_dir = self.build_root / "cobol" / prog.name / key
        exe = out_dir / prog.name
        if exe.is_file():
            return CompileResult(True, exe, "", cached=True)
        job = new_job_dir(self.jobs_root, f"cobc-{prog.name}")
        try:
            (job / "src").mkdir()
            (job / "copy").mkdir()
            for cb in self._copybooks():
                shutil.copyfile(cb, job / "copy" / cb.name)
            names = []
            for src in sources:
                shutil.copyfile(src, job / "src" / src.name)
                names.append(f"src/{src.name}")
            argv = ["cobc", *self.spec.cobc_flags, "-Icopy", "-o", prog.name, *names]
            res = self.sandbox.run(argv, job)
            log = (res.stdout + res.stderr).strip()
            if not res.ok or not (job / prog.name).is_file():
                return CompileResult(False, None, log, duration_s=res.duration_s)
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(job / prog.name, exe)
            return CompileResult(True, exe, log, duration_s=res.duration_s)
        finally:
            remove_job_dir(job)

    def run(self, prog: ProgramSpec, case: TestCase, exe: Path) -> RunOutput:
        job = new_job_dir(self.jobs_root, f"cob-{prog.name}-{case.name}")
        try:
            shutil.copy2(exe, job / exe.name)
            _copy_inputs(case, job)
            res = self.sandbox.run(["bash", "-c", f"chmod +x ./{exe.name} && ./{exe.name}"], job)
            return _collect(job, prog.outputs, res)
        finally:
            remove_job_dir(job)


# --------------------------------------------------------------------------- #
# Java
# --------------------------------------------------------------------------- #
class JavaRunner:
    """Compile and run migrated Java (plain ``javac``; no network needed).

    ``project`` is a Maven-layout directory (``src/main/java/...``). Only the
    packages of the target program and its dependencies are compiled, so a
    half-finished migration of an unrelated program cannot break the build.
    """

    def __init__(
        self, sandbox: Sandbox, build_root: Path, jobs_root: Path, heap_mb: int = 512
    ) -> None:
        self.sandbox = sandbox
        self.build_root = build_root
        self.jobs_root = jobs_root
        self.heap_mb = heap_mb

    @staticmethod
    def sources(project: Path, programs: list[str]) -> list[Path]:
        base = project / "src" / "main" / "java"
        out: list[Path] = []
        for prog in programs:
            pkg = base / java_package_dir(prog)
            if pkg.is_dir():
                out.extend(sorted(pkg.rglob("*.java")))
        support = base / Path(*JAVA_ROOT_PACKAGE.split(".")) / "support"
        if support.is_dir():
            out.extend(sorted(support.rglob("*.java")))
        return out

    def compile(self, project: Path, programs: list[str]) -> CompileResult:
        sources = self.sources(project, programs)
        if not sources:
            return CompileResult(False, None, f"no Java sources for {programs} under {project}")
        key = _digest(sources, "javac-21")
        out_dir = self.build_root / "java" / key / "classes"
        if out_dir.is_dir():
            return CompileResult(True, out_dir, "", cached=True)
        base = project / "src" / "main" / "java"
        job = new_job_dir(self.jobs_root, f"javac-{programs[0]}")
        try:
            rels = []
            for src in sources:
                rel = src.relative_to(base)
                dest = job / "src" / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
                rels.append(("src" / rel).as_posix())
            (job / "sources.txt").write_text("\n".join(rels) + "\n", encoding="utf-8")
            argv = [
                "javac",
                "--release",
                "21",
                "-encoding",
                "UTF-8",
                "-Xlint:none",
                "-d",
                "classes",
                "@sources.txt",
            ]
            res = self.sandbox.run(argv, job)
            log = (res.stdout + res.stderr).strip()
            if not res.ok:
                errors = [
                    {
                        "file": m.group("file").removeprefix("src/"),
                        "line": m.group("line"),
                        "message": m.group("msg"),
                    }
                    for m in _JAVAC_ERROR.finditer(log)
                ]
                return CompileResult(False, None, log, errors, duration_s=res.duration_s)
            out_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(job / "classes", out_dir, dirs_exist_ok=True)
            return CompileResult(True, out_dir, log, duration_s=res.duration_s)
        finally:
            remove_job_dir(job)

    def run(
        self, program: str, outputs: tuple[str, ...], case: TestCase, classes: Path
    ) -> RunOutput:
        job = new_job_dir(self.jobs_root, f"java-{program}-{case.name}")
        try:
            shutil.copytree(classes, job / "classes")
            _copy_inputs(case, job)
            argv = [
                "java",
                f"-Xmx{self.heap_mb}m",
                "-Xss8m",
                "-XX:+UseSerialGC",
                "-XX:TieredStopAtLevel=1",
                "-Dfile.encoding=ISO-8859-1",
                "-Dstdout.encoding=ISO-8859-1",
                "-cp",
                "classes",
                main_class(program),
            ]
            res = self.sandbox.run(argv, job)
            return _collect(job, outputs, res)
        finally:
            remove_job_dir(job)
