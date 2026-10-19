"""Stage 4: one agent migrates one module end to end."""

from __future__ import annotations

from cobalt.agents.prompts import SINGLE_AGENT, task_message
from cobalt.harness.budget import Budget
from cobalt.harness.checkpoint import CheckpointStore
from cobalt.harness.llm import LLMClient
from cobalt.harness.loop import AgentLoop, AgentResult, AgentSpec, ContextPolicy
from cobalt.harness.migration import MigrationContext, build_tools, describe_module
from cobalt.harness.trace import Tracer

SINGLE_AGENT_TOOLS = [
    "read_cobol",
    "query_graph",
    "list_files",
    "read_file",
    "write_file",
    "compile_java",
    "run_equivalence",
    "list_cases",
    "show_golden",
    "show_input",
    "get_summary",
    "finish",
]


def migrate_single(
    ctx: MigrationContext,
    client: LLMClient,
    *,
    model: str,
    budget: Budget | None = None,
    effort: str = "high",
    context: ContextPolicy | None = None,
    attempt: int = 1,
    resume: bool = True,
    extra_instructions: str = "",
) -> AgentResult:
    """Run the single migration agent on ``ctx.program``."""
    tools = build_tools(ctx).subset(
        [t for t in SINGLE_AGENT_TOOLS if ctx.use_graph or t != "query_graph"]
    )
    spec = AgentSpec(
        name="migrator",
        system=SINGLE_AGENT,
        tools=tools,
        model="scripted" if client.name == "scripted" else model,
        effort=effort,
        budget=budget or Budget(),
    )
    task_id = f"{ctx.program}-migrator-{attempt}"
    tracer = Tracer(
        ctx.settings.runs_dir, ctx.run_id, task_id, spec.name, secrets=ctx.settings.secret_values()
    )
    try:
        loop = AgentLoop(
            spec,
            client,
            tracer,
            CheckpointStore(ctx.settings.runs_dir),
            run_id=ctx.run_id,
            task_id=task_id,
            context=context,
        )
        return loop.run(task_message(describe_module(ctx), extra_instructions), resume=resume)
    finally:
        tracer.close()
