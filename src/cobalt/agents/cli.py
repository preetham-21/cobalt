"""``cobalt migrate`` command."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer

from cobalt.agents.providers import PROVIDERS, make_client
from cobalt.agents.single import migrate_single
from cobalt.config import get_settings
from cobalt.equivalence.harness import EquivalenceHarness
from cobalt.graph.query import migration_order
from cobalt.graph.store import get_graph
from cobalt.harness.budget import Budget
from cobalt.harness.migration import for_program
from cobalt.sandbox import SandboxError


def new_run_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")


def migrate(
    programs: Annotated[list[str] | None, typer.Argument(help="Programs (default: all).")] = None,
    corpus: Annotated[str, typer.Option("--corpus", "-c")] = "synthetic",
    provider: Annotated[str, typer.Option(help=f"One of {', '.join(PROVIDERS)}.")] = "anthropic",
    mode: Annotated[
        str, typer.Option(help="single (one agent) or multi (Stage 5 team).")
    ] = "multi",
    run_id: Annotated[str | None, typer.Option(help="Resume or name a run.")] = None,
    model: Annotated[str | None, typer.Option(help="Override COBALT_MODEL.")] = None,
    max_cost_usd: Annotated[float, typer.Option(help="Budget per module attempt.")] = 5.0,
    max_steps: Annotated[int, typer.Option(help="Agent steps per attempt.")] = 40,
    graph: Annotated[
        bool, typer.Option("--graph/--no-graph", help="Knowledge graph on/off.")
    ] = True,
    folding: Annotated[
        bool, typer.Option("--folding/--no-folding", help="Context folding and summaries on/off.")
    ] = True,
) -> None:
    """Migrate programs to Java and verify them (callees first)."""
    settings = get_settings()
    run = run_id or new_run_id()
    g = get_graph(settings, corpus)
    harness = EquivalenceHarness(settings, corpus)
    order = [e.program for e in migration_order(g)]
    wanted = [p.upper() for p in programs] if programs else order
    names = [p for p in order if p in wanted]
    budget = Budget(max_steps=max_steps, max_cost_usd=max_cost_usd)
    chosen_model = model or settings.model
    typer.echo(
        f"run {run}  corpus={corpus}  provider={provider}  mode={mode}  model="
        f"{chosen_model if provider == 'anthropic' else 'scripted'}"
    )

    if mode == "multi":
        from cobalt.agents.supervisor import Supervisor, SupervisorConfig

        sup = Supervisor(
            settings,
            corpus,
            run,
            SupervisorConfig(
                provider=provider,
                model=chosen_model,
                budget=budget,
                use_graph=graph,
                use_folding=folding,
            ),
        )
        try:
            outcome = sup.run(names)
        except SandboxError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        for m in outcome.modules:
            typer.echo(
                f"  {m.program:<10} {m.status:<10} attempts={m.attempts} "
                f"cost=${m.cost_usd:.3f} tokens={m.tokens} {m.verdict}"
            )
        typer.echo(
            f"accepted {outcome.accepted}/{len(outcome.modules)}  "
            f"total cost ${outcome.cost_usd:.3f}"
        )
        raise typer.Exit(0 if outcome.accepted == len(outcome.modules) else 1)

    failures = 0
    for name in names:
        ctx = for_program(settings, corpus, run, name, harness, g, use_graph=graph)
        client = make_client(settings, provider, name, corpus)
        try:
            result = migrate_single(ctx, client, model=chosen_model, budget=budget)
        except SandboxError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from exc
        typer.echo(
            f"  {name:<10} {result.status.value:<16} steps={result.steps} "
            f"cost=${result.usage.cost_usd:.3f} {result.data.get('verdict', result.reason)}"
        )
        failures += 0 if result.ok else 1
    raise typer.Exit(0 if failures == 0 else 1)


def runs() -> None:
    """List migration runs recorded in runs/cobalt.db."""
    from cobalt.memory.state import MigrationState

    state = MigrationState(get_settings().runs_dir / "cobalt.db")
    rows = state.runs()
    if not rows:
        typer.echo("(no runs yet)")
    for r in rows:
        mods = state.modules(r["run_id"])
        accepted = sum(1 for m in mods if m.status == "accepted")
        cost = sum(m.cost_usd for m in mods)
        typer.echo(
            f"{r['run_id']:<28} {r['corpus']:<10} {r['status']:<9} "
            f"accepted {accepted}/{len(mods)}  ${cost:.3f}  "
            f"provider={r['config'].get('provider')}"
        )


def status(run_id: str) -> None:
    """Show every module of a run: status, attempts, spend and verdict."""
    from cobalt.memory.state import MigrationState

    state = MigrationState(get_settings().runs_dir / "cobalt.db")
    mods = state.modules(run_id)
    if not mods:
        typer.echo(f"no run {run_id}", err=True)
        raise typer.Exit(1)
    for m in mods:
        typer.echo(
            f"{m.program:<10} {m.status:<12} attempts={m.attempts} "
            f"tokens={m.tokens:<8} ${m.cost_usd:.3f}  {m.failure or ''} "
            f"{(m.verdict or '')[:70]}"
        )
