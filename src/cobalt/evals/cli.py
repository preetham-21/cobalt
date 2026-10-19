"""``cobalt eval`` and ``cobalt report`` commands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from cobalt.config import get_settings
from cobalt.evals.metrics import RunMetrics, run_metrics
from cobalt.evals.report import render_eval, render_runs
from cobalt.evals.suite import VARIANTS, EvalConfig, eval_dir, load_eval, run_eval
from cobalt.harness.budget import Budget
from cobalt.sandbox import SandboxError


def eval_cmd(
    corpus: Annotated[str, typer.Option("--corpus", "-c")] = "synthetic",
    programs: Annotated[list[str] | None, typer.Option("--program", "-p")] = None,
    variants: Annotated[str, typer.Option(help=f"Comma list of {', '.join(VARIANTS)}.")] = ",".join(
        VARIANTS
    ),
    provider: Annotated[str, typer.Option(help="anthropic | replay")] = "anthropic",
    model: Annotated[str | None, typer.Option()] = None,
    repeats: Annotated[int, typer.Option()] = 1,
    max_cost_usd: Annotated[float, typer.Option(help="Per translator attempt.")] = 5.0,
    eval_id: Annotated[str | None, typer.Option(help="Name or resume an eval.")] = None,
) -> None:
    """Run the ablation suite and write runs/evals/<id>/{results.json,report.html}."""
    settings = get_settings()
    eid = eval_id or datetime.now(UTC).strftime("eval-%Y%m%d-%H%M%S")
    cfg = EvalConfig(
        corpus=corpus,
        programs=[p.upper() for p in programs] if programs else None,
        variants=[v.strip() for v in variants.split(",") if v.strip()],
        provider=provider,
        model=model or settings.model,
        budget=Budget(max_cost_usd=max_cost_usd),
        repeats=repeats,
    )

    def progress(variant: str, m: RunMetrics) -> None:
        typer.echo(
            f"  {variant:<11} accepted {m.accepted}/{len(m.modules)}  "
            f"cost ${m.cost_usd:.3f}  failures {m.failures() or '-'}"
        )

    typer.echo(f"eval {eid}: corpus={corpus} provider={provider} variants={cfg.variants}")
    try:
        result = run_eval(settings, cfg, eid, on_run=progress)
    except SandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    out = eval_dir(settings, eid) / "report.html"
    out.write_text(render_eval(result.to_dict()), encoding="utf-8")
    typer.echo(f"report: {out}")


def report_cmd(
    targets: Annotated[list[str], typer.Argument(help="Eval id, or one or more run ids.")],
    out: Annotated[Path | None, typer.Option(help="Output HTML path.")] = None,
) -> None:
    """Render an HTML report for an eval or for runs."""
    settings = get_settings()
    if len(targets) == 1 and (eval_dir(settings, targets[0]) / "results.json").is_file():
        page = render_eval(load_eval(settings, targets[0]))
        path = out or eval_dir(settings, targets[0]) / "report.html"
    else:
        try:
            runs = [run_metrics(settings.runs_dir / "cobalt.db", t).to_dict() for t in targets]
        except KeyError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        page = render_runs(runs)
        path = out or settings.runs_dir / "reports" / f"{'+'.join(targets)}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    typer.echo(str(path))
