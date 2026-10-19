"""Toolchain smoke test: can this machine do cobalt's work end to end?

1. a sandbox backend is available (Docker, WSL or local-in-container) and
   has cobc, java and javac;
2. every synthetic program compiles with GnuCOBOL and its golden outputs
   (re)capture deterministically - they must equal the committed ones;
3. the hand-written Java baselines pass the equivalence harness;
4. ``cobalt inventory`` and ``cobalt graph order`` run.

Everything that compiles or executes code goes through the sandbox.
Run with ``make smoke`` or ``python scripts/smoke.py``.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from cobalt.cli import app
from cobalt.config import get_settings
from cobalt.equivalence.golden import GoldenStore
from cobalt.equivalence.harness import EquivalenceHarness
from cobalt.logging_setup import configure_logging
from cobalt.sandbox import SandboxError, get_sandbox, new_job_dir, remove_job_dir

ROOT = Path(__file__).resolve().parent.parent


class SmokeFailure(Exception):
    """A smoke check failed; the message says why."""


def check_sandbox() -> None:
    settings = get_settings()
    try:
        sb = get_sandbox(settings)
    except SandboxError as exc:
        raise SmokeFailure(str(exc)) from exc
    job = new_job_dir(settings.runs_dir / "jobs", "smoke")
    try:
        res = sb.run(
            [
                "bash",
                "-c",
                "cobc --version | head -1; java -version 2>&1 | head -1; javac -version 2>&1",
            ],
            job,
        )
    finally:
        remove_job_dir(job)
    if not res.ok:
        raise SmokeFailure(f"toolchain probe failed: {res.stderr}")
    print(f"    backend {sb.name} (network isolated: {sb.isolates_network})")
    for line in res.stdout.strip().splitlines():
        print(f"    {line}")


def check_golden() -> None:
    h = EquivalenceHarness(get_settings(), "synthetic")
    with tempfile.TemporaryDirectory(prefix="smoke-golden-") as tmp:
        store = GoldenStore(Path(tmp))
        for name in sorted(h.spec.programs):
            results = h.capture_golden(name, store=store, force=True)
            bad = [r for r in results if r.status != "captured"]
            if bad:
                raise SmokeFailure(f"{name}: {bad[0].status} {bad[0].detail}")
            for r in results:
                fresh = store.load(name, r.case)
                committed = h.golden.load(name, r.case)
                if committed is None:
                    raise SmokeFailure(f"{name}/{r.case}: no committed golden (run cobalt golden)")
                if (fresh.output.files, fresh.output.stdout, fresh.output.returncode) != (
                    committed.output.files,
                    committed.output.stdout,
                    committed.output.returncode,
                ):
                    raise SmokeFailure(f"{name}/{r.case}: golden output is not reproducible")
            print(f"    {name:<8} compiled, {len(results)} cases reproduced")


def check_equivalence() -> None:
    h = EquivalenceHarness(get_settings(), "synthetic")
    for name in sorted(h.spec.programs):
        deps = ["RATECALC"] if name == "INTCALC" else []
        report = h.check(name, ROOT / "baselines" / "synthetic", dependencies=deps)
        if not report.passed:
            raise SmokeFailure(report.summary())
        print(f"    {report.summary()}")


def check_cli() -> None:
    runner = CliRunner()
    for args in (["inventory", "-c", "synthetic", "--json"], ["graph", "order", "--json"]):
        result = runner.invoke(app, args)
        if result.exit_code != 0:
            raise SmokeFailure(f"cobalt {' '.join(args)} failed: {result.output}")
        print(f"    cobalt {' '.join(args[:2])} ok")


def main() -> int:
    configure_logging(get_settings(), console_level="ERROR")
    checks: list[tuple[str, Callable[[], None]]] = [
        ("sandbox", check_sandbox),
        ("golden (COBOL)", check_golden),
        ("equivalence (Java baselines)", check_equivalence),
        ("cli", check_cli),
    ]
    for title, check in checks:
        print(f"== {title}")
        try:
            check()
        except SmokeFailure as exc:
            print(f"FAIL: {exc}")
            return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
