"""Toolchain smoke test: is the dev container able to do cobalt's work?

Checks, in order:
  1. cobc, java, javac and mvn are on PATH (prints their versions);
  2. every synthetic main program in corpus/synthetic/runs.toml compiles with
     GnuCOBOL, runs on its committed input data, exits with the expected code
     and writes every declared output file;
  3. javac/java can compile and run a BigDecimal rounding check;
  4. ``cobalt inventory`` runs on the synthetic corpus.

This compiles and executes code, so it refuses to run outside the dev
container unless ``--host`` is given. Run it with ``make smoke``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ROOT / "corpus" / "synthetic"
BUILD_DIR = ROOT / "runs" / "smoke"
TIMEOUT_S = 60

JAVA_CHECK = """\
import java.math.BigDecimal;
import java.math.RoundingMode;

public class Smoke {
    public static void main(String[] args) {
        // COBOL ROUNDED (default mode) rounds half away from zero.
        BigDecimal up = new BigDecimal("1.005").setScale(2, RoundingMode.HALF_UP);
        BigDecimal down = new BigDecimal("-1.005").setScale(2, RoundingMode.HALF_UP);
        System.out.println(up + " " + down);
    }
}
"""


class SmokeFailure(Exception):
    """A smoke check failed; the message says why."""


def run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command with a timeout, capturing text output."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )


def check_tools() -> None:
    """Fail if a required tool is missing; print one version line for each."""
    probes = {
        "cobc": ["cobc", "--version"],
        "java": ["java", "-version"],
        "javac": ["javac", "-version"],
        "mvn": ["mvn", "-v"],
    }
    missing = [name for name in probes if shutil.which(name) is None]
    if missing:
        raise SmokeFailure(f"missing tools: {', '.join(missing)}")
    for name, cmd in probes.items():
        result = run(cmd)
        first = (result.stdout or result.stderr).strip().splitlines()[0]
        print(f"    {name:<6} {first}")


def check_cobol_programs() -> None:
    """Compile and run every main program declared in runs.toml."""
    spec = tomllib.loads((SYNTHETIC / "runs.toml").read_text(encoding="utf-8"))
    defaults = spec["defaults"]
    includes = [f"-I{SYNTHETIC / d}" for d in defaults["copybook_dirs"]]
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    for name, prog in spec["programs"].items():
        exe = BUILD_DIR / name
        sources = [str(SYNTHETIC / s) for s in prog["sources"]]
        compiled = run(["cobc", *defaults["cobc_flags"], *includes, "-o", str(exe), *sources])
        if compiled.returncode != 0:
            raise SmokeFailure(f"{name}: cobc failed\n{compiled.stderr}")

        with tempfile.TemporaryDirectory(prefix=f"smoke-{name}-") as scratch:
            work = Path(scratch)
            for logical, rel in prog["inputs"].items():
                shutil.copyfile(SYNTHETIC / rel, work / logical)
            result = run([str(exe)], cwd=work)
            if result.returncode != prog["expected_rc"]:
                raise SmokeFailure(
                    f"{name}: exit code {result.returncode}, expected {prog['expected_rc']}\n"
                    f"{result.stdout}{result.stderr}"
                )
            sizes = []
            for logical in prog["outputs"]:
                out = work / logical
                if not out.is_file() or out.stat().st_size == 0:
                    raise SmokeFailure(f"{name}: output {logical} missing or empty")
                lines = len(out.read_text(encoding="latin-1").splitlines())
                sizes.append(f"{logical}={lines} lines")
            if prog["stdout"] and not result.stdout.strip():
                raise SmokeFailure(f"{name}: expected DISPLAY output on stdout")
        print(f"    {name:<8} ok  ({', '.join(sizes)})")


def check_java() -> None:
    """Compile and run a tiny BigDecimal program."""
    with tempfile.TemporaryDirectory(prefix="smoke-java-") as scratch:
        work = Path(scratch)
        (work / "Smoke.java").write_text(JAVA_CHECK, encoding="utf-8")
        compiled = run(["javac", "Smoke.java"], cwd=work)
        if compiled.returncode != 0:
            raise SmokeFailure(f"javac failed\n{compiled.stderr}")
        result = run(["java", "-cp", ".", "Smoke"], cwd=work)
        if result.stdout.strip() != "1.01 -1.01":
            raise SmokeFailure(f"unexpected java output: {result.stdout!r}")
    print("    BigDecimal HALF_UP check ok")


def check_inventory() -> None:
    """Run the inventory CLI on the synthetic corpus."""
    out = BUILD_DIR / "inventory.json"
    result = run(
        [sys.executable, "-m", "cobalt", "inventory", "--corpus", "synthetic", "--out", str(out)]
    )
    if result.returncode != 0:
        raise SmokeFailure(f"cobalt inventory failed\n{result.stdout}{result.stderr}")
    print(f"    cobalt inventory ok  ({out.relative_to(ROOT)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="cobalt toolchain smoke test")
    parser.add_argument(
        "--host", action="store_true", help="allow running outside the dev container"
    )
    args = parser.parse_args()
    if os.environ.get("COBALT_IN_DOCKER") != "1" and not args.host:
        print("smoke compiles and runs code; run it in the dev container: make smoke")
        return 2

    checks: list[tuple[str, Callable[[], None]]] = [
        ("toolchain", check_tools),
        ("synthetic COBOL", check_cobol_programs),
        ("java", check_java),
        ("inventory", check_inventory),
    ]
    for title, check in checks:
        print(f"== {title}")
        try:
            check()
        except (SmokeFailure, subprocess.TimeoutExpired) as exc:
            print(f"FAIL: {exc}")
            return 1
    print("SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
