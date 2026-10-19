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

from cobalt.equivalence.files import (
    FileLayout,
    file_layouts,
    fixed_to_text,
    indexed_utility,
    parm_driver,
    parm_text,
    run_script,
    text_to_fixed,
)
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
        self._layouts: dict[str, dict[str, FileLayout]] = {}

    def _copybooks(self) -> list[Path]:
        return [
            p
            for d in self.spec.copybook_dirs
            if d.is_dir()
            for p in sorted(d.iterdir())
            if p.is_file()
        ]

    def layouts(self, prog: ProgramSpec) -> dict[str, FileLayout]:
        """File layouts of a program (cached per program)."""
        cached = self._layouts.get(prog.name)
        if cached is None:
            cached = file_layouts(prog.sources[0], list(self.spec.copybook_dirs))
            self._layouts[prog.name] = cached
        return cached

    def _compile_sources(
        self, name: str, out_name: str, sources: list[tuple[str, bytes]], key_extra: str = ""
    ) -> CompileResult:
        """Compile (sources given as (file name, bytes)) into a cached executable."""
        h = hashlib.sha256()
        for fname, data in sources:
            h.update(fname.encode())
            h.update(data)
        for cb in self._copybooks():
            h.update(cb.name.encode())
            h.update(cb.read_bytes())
        h.update(" ".join(self.spec.cobc_flags).encode())
        h.update(key_extra.encode())
        out_dir = self.build_root / "cobol" / name / h.hexdigest()[:16]
        exe = out_dir / out_name
        if exe.is_file():
            return CompileResult(True, exe, "", cached=True)
        job = new_job_dir(self.jobs_root, f"cobc-{name}")
        try:
            (job / "src").mkdir()
            (job / "copy").mkdir()
            for cb in self._copybooks():
                shutil.copyfile(cb, job / "copy" / cb.name)
            names = []
            for fname, data in sources:
                (job / "src" / fname).write_bytes(data)
                names.append(f"src/{fname}")
            argv = ["cobc", *self.spec.cobc_flags, "-Icopy", "-o", out_name, *names]
            res = self.sandbox.run(argv, job)
            log = (res.stdout + res.stderr).strip()
            if not res.ok or not (job / out_name).is_file():
                return CompileResult(False, None, log, duration_s=res.duration_s)
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(job / out_name, exe)
            return CompileResult(True, exe, log, duration_s=res.duration_s)
        finally:
            remove_job_dir(job)

    def compile(self, prog: ProgramSpec) -> CompileResult:
        sources = [(p.name, p.read_bytes()) for p in [*prog.sources, *self.spec.stubs]]
        if prog.parm:
            sources.insert(0, ("COBDRIVR.cbl", parm_driver(prog.name).encode()))
        return self._compile_sources(prog.name, prog.name, sources)

    def _utility(self, kind: str, layout: FileLayout) -> Path:
        name = f"{kind}_{layout.utility_suffix}"
        result = self._compile_sources(
            "_adapters", name, [(f"{kind}.cbl", indexed_utility(kind, layout).encode())]
        )
        if not result.ok or result.artifact is None:
            raise RuntimeError(f"cannot build file adapter {name}: {result.log}")
        return result.artifact

    def run(self, prog: ProgramSpec, case: TestCase, exe: Path) -> RunOutput:
        layouts = self.layouts(prog)
        job = new_job_dir(self.jobs_root, f"cob-{prog.name}-{case.name}")
        try:
            shutil.copy2(exe, job / exe.name)
            loaded: list[str] = []
            for logical, src in case.inputs.items():
                lay = layouts.get(logical)
                if lay is None or lay.organization == "line-sequential":
                    shutil.copyfile(src, job / logical)
                elif lay.organization == "indexed":
                    shutil.copyfile(src, job / f"{logical}.seq")
                    util = self._utility("IDXLOAD", lay)
                    shutil.copy2(util, job / util.name)
                    loaded.append(logical)
                else:
                    (job / logical).write_bytes(text_to_fixed(src.read_bytes(), lay.record_length))
            dumped = [
                o
                for o in prog.outputs
                if (lay := layouts.get(o)) is not None and lay.organization == "indexed"
            ]
            for logical in dumped:
                util = self._utility("IDXDUMP", layouts[logical])
                shutil.copy2(util, job / util.name)
            (job / "run.sh").write_text(
                run_script(exe.name, layouts, loaded, dumped), encoding="utf-8", newline="\n"
            )
            res = self.sandbox.run(["bash", "run.sh"], job, env=self.spec.clock_env("cobol"))
            out = _collect(job, prog.outputs, res)
            for logical, data in out.files.items():
                lay = layouts.get(logical)
                if (
                    data is not None
                    and lay is not None
                    and lay.organization in ("sequential", "record-sequential")
                ):
                    out.files[logical] = fixed_to_text(data, lay.record_length)
            return out
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
        self,
        sandbox: Sandbox,
        build_root: Path,
        jobs_root: Path,
        heap_mb: int = 512,
        runtime_root: Path | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.build_root = build_root
        self.jobs_root = jobs_root
        self.heap_mb = heap_mb
        #: Maven-layout project holding the shared support runtime
        #: (``cobalt.migrated.support``), compiled together with every program.
        self.runtime_root = runtime_root

    def sources(self, project: Path, programs: list[str]) -> list[tuple[Path, Path]]:
        """(source root, file) pairs to compile: program packages + runtime."""
        base = project / "src" / "main" / "java"
        out: list[tuple[Path, Path]] = []
        for prog in programs:
            pkg = base / java_package_dir(prog)
            if pkg.is_dir():
                out.extend((base, f) for f in sorted(pkg.rglob("*.java")))
        if self.runtime_root is not None:
            rbase = self.runtime_root / "src" / "main" / "java"
            support = rbase / Path(*JAVA_ROOT_PACKAGE.split(".")) / "support"
            out.extend((rbase, f) for f in sorted(support.rglob("*.java")))
        return out

    def compile(self, project: Path, programs: list[str]) -> CompileResult:
        pairs = self.sources(project, programs)
        if not any(base == project / "src" / "main" / "java" for base, _ in pairs):
            return CompileResult(False, None, f"no Java sources for {programs} under {project}")
        key = _digest(
            [f for _, f in pairs], "javac-21", *(f.relative_to(b).as_posix() for b, f in pairs)
        )
        out_dir = self.build_root / "java" / key / "classes"
        if out_dir.is_dir():
            return CompileResult(True, out_dir, "", cached=True)
        job = new_job_dir(self.jobs_root, f"javac-{programs[0]}")
        try:
            rels = []
            for base, src in pairs:
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
        self,
        program: str,
        outputs: tuple[str, ...],
        case: TestCase,
        classes: Path,
        *,
        parm: bool = False,
        env: dict[str, str] | None = None,
    ) -> RunOutput:
        """Run the main class; a JCL PARM (file ``PARM``) becomes ``args[0]``."""
        job = new_job_dir(self.jobs_root, f"java-{program}-{case.name}")
        try:
            shutil.copytree(classes, job / "classes")
            _copy_inputs(case, job)
            args = [parm_text(case.inputs.get("PARM"))] if parm else []
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
                *args,
            ]
            res = self.sandbox.run(argv, job, env=env)
            return _collect(job, outputs, res)
        finally:
            remove_job_dir(job)
