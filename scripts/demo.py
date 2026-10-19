"""End-to-end demo: from a COBOL corpus to verified Java and an eval report.

    python scripts/demo.py               # offline: scripted replay provider
    python scripts/demo.py --live        # real model (needs ANTHROPIC_API_KEY)

Steps: inventory -> knowledge graph + migration order -> golden outputs
(original COBOL in the sandbox) -> equivalence of the reference Java ->
multi-agent migration of every synthetic program -> ablation eval + HTML
report. Everything that compiles or runs code goes through the sandbox.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from typer.testing import CliRunner

from cobalt.cli import app

STEP = "\n=== {} ==="


def cobalt(*args: str) -> int:
    print(f"$ cobalt {' '.join(args)}")
    result = CliRunner().invoke(app, list(args))
    print(result.output.rstrip())
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result.exit_code


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="cobalt end-to-end demo")
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the real model (costs money) instead of the replay provider",
    )
    args = parser.parse_args()
    provider = "anthropic" if args.live else "replay"
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    print(STEP.format("1. inventory: what is in the corpora?"))
    cobalt("inventory", "-c", "synthetic")
    print(STEP.format("2. knowledge graph: dependencies and migration order"))
    cobalt("graph", "build", "-c", "synthetic")
    cobalt("graph", "order")
    print(STEP.format("3. sandbox + golden outputs from the original COBOL"))
    if cobalt("sandbox") != 0:
        print("no sandbox available: see README (make build, or scripts/wsl_toolchain.sh)")
        return 2
    cobalt("golden")
    print(STEP.format("4. equivalence harness on the hand-written reference Java"))
    if cobalt("equiv") != 0:
        return 1
    print(STEP.format(f"5. multi-agent migration (provider: {provider})"))
    run_id = f"demo-{stamp}"
    cobalt("migrate", "--provider", provider, "--run-id", run_id)
    cobalt("status", run_id)
    print(STEP.format("6. ablation eval and HTML report"))
    eval_id = f"demo-eval-{stamp}"
    cobalt("eval", "--provider", provider, "--eval-id", eval_id)
    if not args.live:
        print(
            "\nNote: the replay provider re-enacts reference migrations without a model; "
            "run with --live for real agent results."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
