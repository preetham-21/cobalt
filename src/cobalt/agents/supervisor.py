"""Stage 5: the Supervisor and its team.

For every module, in knowledge-graph order (callees first):

1. **Analyst** writes an analysis of the COBOL (once per module).
2. **Test Writer** adds edge-case inputs; the COBOL produces their golden
   outputs (main programs only, once per module).
3. **Translator** writes the Java until equivalence passes - up to
   ``max_attempts`` attempts. Each retry receives the previous failure; the
   last attempt is *escalated* (bigger budget, higher effort, optionally a
   stronger model).
4. The Supervisor runs **static checks** (no floating point, no embedded
   expected output) and its **own** equivalence check - the agent's word is
   never taken for it.
5. **Reviewer** approves or requests changes; requested changes send the
   module back to the Translator while attempts remain.

A module is *accepted* only if the independent equivalence check and the
static checks pass. Modules whose callees were not accepted are *blocked*.
All state lives in SQLite, so a killed run resumes where it stopped.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cobalt.agents.checks import check_java, golden_records
from cobalt.agents.prompts import task_message
from cobalt.agents.roles import ROLES, RoleConfig
from cobalt.config import Settings
from cobalt.equivalence.harness import EquivalenceHarness, EquivalenceReport
from cobalt.graph.query import migration_order
from cobalt.graph.store import get_graph
from cobalt.harness.budget import Budget
from cobalt.harness.checkpoint import CheckpointStore
from cobalt.harness.llm import AnthropicClient, LLMClient, ScriptedClient
from cobalt.harness.loop import AgentLoop, AgentResult, AgentSpec, AgentStatus, ContextPolicy
from cobalt.harness.migration import (
    MigrationContext,
    build_tools,
    describe_module,
    for_program,
    format_report,
)
from cobalt.harness.replay import analyst_script, replay_script, reviewer_script, test_writer_script
from cobalt.harness.trace import Tracer
from cobalt.memory.state import MigrationState

# failure taxonomy (module level)
COMPILE_ERROR = "compile_error"
SEMANTIC_DIFF = "semantic_diff"
STATIC_CHECK = "static_check"
BLOCKED = "blocked"
_STATUS_CATEGORY = {
    AgentStatus.BUDGET_EXHAUSTED: "budget_exhausted",
    AgentStatus.LOOP: "loop",
    AgentStatus.TOOL_MISUSE: "tool_misuse",
    AgentStatus.REFUSED: "refused",
    AgentStatus.STOPPED: "stopped",
    AgentStatus.ERROR: "provider_error",
}


@dataclass
class SupervisorConfig:
    """Knobs for a migration run (also the ablation switches)."""

    provider: str = "anthropic"  # anthropic | replay
    model: str = "claude-opus-5-5"
    escalation_model: str | None = None  # e.g. "claude-fable-5-1" for the last attempt
    budget: Budget = field(default_factory=Budget)  # per translator attempt
    max_attempts: int = 3
    escalation_scale: float = 2.0
    use_graph: bool = True
    use_folding: bool = True
    use_analyst: bool = True
    use_test_writer: bool = True
    use_reviewer: bool = True
    #: prompt size (tokens) at which an agent's context is folded into a new episode
    context_threshold_tokens: int = 150_000

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["budget"] = self.budget.to_dict()
        return d


@dataclass
class ModuleOutcome:
    """Final state of one module, for the CLI and reports."""

    program: str
    status: str
    attempts: int
    cost_usd: float
    tokens: int
    verdict: str
    failure: str | None = None


@dataclass
class RunOutcome:
    """Result of a whole run."""

    run_id: str
    modules: list[ModuleOutcome]

    @property
    def accepted(self) -> int:
        return sum(1 for m in self.modules if m.status == "accepted")

    @property
    def cost_usd(self) -> float:
        return sum(m.cost_usd for m in self.modules)


ClientFactory = Callable[[str, str, int], LLMClient]  # (role, program, attempt) -> client


class Supervisor:
    """Deterministic orchestration of the four agent roles."""

    def __init__(
        self,
        settings: Settings,
        corpus: str,
        run_id: str,
        config: SupervisorConfig,
        *,
        client_factory: ClientFactory | None = None,
        context_factory: Callable[[str], ContextPolicy | None] | None = None,
        memory: Any = None,
    ) -> None:
        self.settings = settings
        self.corpus = corpus
        self.run_id = run_id
        self.config = config
        self.graph = get_graph(settings, corpus)
        self.harness = EquivalenceHarness(settings, corpus)
        self.state = MigrationState(settings.runs_dir / "cobalt.db")
        self.checkpoints = CheckpointStore(settings.runs_dir)
        self._client_factory = client_factory or self._default_client
        self._context_factory = context_factory
        self.memory = memory
        if config.use_folding:
            from cobalt.memory.context import EpisodeCompactor
            from cobalt.memory.summaries import SummaryMemory

            if memory is None:
                self.memory = SummaryMemory(self.state, run_id)
            if context_factory is None:
                threshold = config.context_threshold_tokens
                self._context_factory = lambda _program: EpisodeCompactor(threshold)
        self._deps = {e.program: e.depends_on for e in migration_order(self.graph)}
        self._anthropic: AnthropicClient | None = None

    # ------------------------------------------------------------------ #
    def _default_client(self, role: str, program: str, attempt: int) -> LLMClient:
        if self.config.provider == "anthropic":
            if self._anthropic is None:
                key = self.settings.anthropic_api_key
                self._anthropic = AnthropicClient(api_key=key.get_secret_value() if key else None)
            return self._anthropic
        if self.config.provider == "replay":
            baselines = self.settings.root / "baselines" / self.corpus
            if role == "analyst":
                return ScriptedClient(analyst_script(program))
            if role == "test_writer":
                inputs = self.harness.spec.program(program).inputs
                return ScriptedClient(test_writer_script(program, inputs))
            if role == "reviewer":
                return ScriptedClient(reviewer_script(program))
            return ScriptedClient(replay_script(program, baselines))
        raise ValueError(f"unknown provider {self.config.provider}")

    def _ctx(self, program: str, role: RoleConfig) -> MigrationContext:
        return for_program(
            self.settings,
            self.corpus,
            self.run_id,
            program,
            self.harness,
            self.graph,
            use_graph=self.config.use_graph,
            write_java=role.write_java,
            write_cases=role.write_cases,
            verify_finish=role.verify_finish,
            summaries=self.memory if self.config.use_folding else None,
        )

    def _run_role(
        self,
        program: str,
        role: RoleConfig,
        task: str,
        attempt: int,
        *,
        scale: float = 1.0,
        effort: str | None = None,
        model: str | None = None,
        counts_as_attempt: bool = False,
    ) -> tuple[AgentResult, MigrationContext]:
        ctx = self._ctx(program, role)
        names = [
            t
            for t in role.tools
            if (self.config.use_graph or t != "query_graph")
            and (self.config.use_folding or t != "get_summary")
        ]
        client = self._client_factory(role.name, program, attempt)
        spec = AgentSpec(
            name=role.name,
            system=role.system,
            tools=build_tools(ctx).subset(names),
            model="scripted" if client.name == "scripted" else (model or self.config.model),
            effort=effort or role.effort,
            budget=role.budget(self.config.budget, scale),
        )
        task_id = f"{program}-{role.name}-{attempt}"
        tracer = Tracer(
            self.settings.runs_dir,
            self.run_id,
            task_id,
            role.name,
            secrets=self.settings.secret_values(),
        )
        context = self._context_factory(program) if self._context_factory else None
        start = time.monotonic()
        try:
            loop = AgentLoop(
                spec,
                client,
                tracer,
                self.checkpoints,
                run_id=self.run_id,
                task_id=task_id,
                context=context,
            )
            result = loop.run(task)
        finally:
            tracer.close()
        self.state.record_attempt(
            self.run_id,
            program,
            attempt,
            role.name,
            result.status.value,
            result.steps,
            result.usage.to_dict(),
            time.monotonic() - start,
            result.summary,
            result.reason,
            counts_as_attempt=counts_as_attempt,
        )
        return result, ctx

    def _decide(self, tracer: Tracer, program: str, decision: str, detail: str = "") -> None:
        self.state.decide(self.run_id, program, decision, detail)
        tracer.event("decision", 0, name=decision, program=program, detail=detail[:1000])

    # ------------------------------------------------------------------ #
    def run(self, programs: list[str] | None = None) -> RunOutcome:
        order = migration_order(self.graph)
        deps = {e.program: e.depends_on for e in order}
        names = [e.program for e in order if programs is None or e.program in programs]
        if not self.config.use_graph:
            # ablation: no dependency knowledge - plain name order, no blocking
            names = sorted(names)
            deps = {}
        resumed = self.state.start_run(self.run_id, self.corpus, self.config.to_dict())
        tracer = Tracer(
            self.settings.runs_dir,
            self.run_id,
            "supervisor",
            "supervisor",
            secrets=self.settings.secret_values(),
        )
        tracer.event(
            "run_start",
            0,
            name=self.run_id,
            corpus=self.corpus,
            resumed=resumed,
            modules=names,
            config=self.config.to_dict(),
        )
        try:
            for name in names:
                self.state.ensure_module(self.run_id, name)
                row = self.state.module(self.run_id, name)
                if row and row.status in ("accepted", "failed"):
                    self._decide(tracer, name, "skip", f"already {row.status} (resume)")
                    continue
                missing = [
                    d
                    for d in deps.get(name, [])
                    if (r := self.state.module(self.run_id, d)) is None or r.status != "accepted"
                ]
                if missing:
                    self.state.set_status(
                        self.run_id,
                        name,
                        "blocked",
                        failure=BLOCKED,
                        verdict=f"callees not migrated: {', '.join(missing)}",
                    )
                    self._decide(tracer, name, "block", ", ".join(missing))
                    continue
                self._migrate_module(name, tracer)
            self.state.finish_run(self.run_id, "finished")
        finally:
            tracer.event("run_end", 0, name=self.run_id)
            tracer.close()
        return self.outcome(names)

    def outcome(self, names: list[str] | None = None) -> RunOutcome:
        rows = self.state.modules(self.run_id)
        if names is not None:
            rows = [r for r in rows if r.program in names]
        return RunOutcome(
            self.run_id,
            [
                ModuleOutcome(
                    r.program,
                    r.status,
                    r.attempts,
                    r.cost_usd,
                    r.tokens,
                    r.verdict or "",
                    r.failure,
                )
                for r in rows
            ],
        )

    # ------------------------------------------------------------------ #
    def _migrate_module(self, program: str, tracer: Tracer) -> None:
        cfg = self.config
        self.state.set_status(self.run_id, program, "in_progress")
        self._decide(tracer, program, "start")
        probe = self._ctx(program, ROLES["translator"])
        module = self._describe(probe)
        main_program = probe.has_run_spec

        analysis = self.state.artifact(self.run_id, program, "analysis")
        if cfg.use_analyst and analysis is None:
            res, _ = self._run_role(
                program, ROLES["analyst"], task_message(module, "Write the analysis."), 1
            )
            analysis = res.summary if res.summary else None
            if analysis:
                self.state.put_artifact(self.run_id, program, "analysis", analysis)

        tests = self.state.artifact(self.run_id, program, "tests")
        if cfg.use_test_writer and main_program and tests is None:
            extra = f"## Analyst's notes\n{analysis}" if analysis else ""
            res, _ = self._run_role(program, ROLES["test_writer"], task_message(module, extra), 1)
            tests = res.summary or "(no new cases)"
            self.state.put_artifact(self.run_id, program, "tests", tests)
            module = self._describe(probe)  # refresh the case list

        done = sum(
            1 for a in self.state.attempts(self.run_id, program) if a["role"] == "translator"
        )
        last_failure: str | None = None
        for attempt in range(done + 1, cfg.max_attempts + 1):
            escalate = attempt == cfg.max_attempts and attempt > 1
            if escalate:
                self._decide(
                    tracer,
                    program,
                    "escalate",
                    f"attempt {attempt}: budget x{cfg.escalation_scale}, effort xhigh"
                    + (f", model {cfg.escalation_model}" if cfg.escalation_model else ""),
                )
            elif attempt > 1:
                self._decide(tracer, program, "retry", f"attempt {attempt}")
            task = task_message(module, self._translator_brief(program, analysis, tests))
            result, ctx = self._run_role(
                program,
                ROLES["translator"],
                task,
                attempt,
                scale=cfg.escalation_scale if escalate else 1.0,
                effort="xhigh" if escalate else None,
                model=cfg.escalation_model if escalate else None,
                counts_as_attempt=True,
            )
            category, detail = self._gate(program, ctx, result, main_program)
            if category is None and cfg.use_reviewer and attempt < cfg.max_attempts:
                review = self._review(program, module, result)
                if review is not None:
                    category, detail = "review", review
            if category is None:
                verdict = detail
                self.state.set_status(self.run_id, program, "accepted", verdict=verdict)
                self._decide(tracer, program, "accept", verdict)
                if self.memory is not None:
                    self.memory.fold(program, ctx, analysis=analysis)
                return
            last_failure = category
            self.state.put_artifact(
                self.run_id, program, "failure", f"Attempt {attempt} ({category}): {detail}"
            )
        self.state.set_status(
            self.run_id,
            program,
            "failed",
            failure=last_failure,
            verdict=f"not accepted after {cfg.max_attempts} attempts",
        )
        self._decide(tracer, program, "fail", last_failure or "")

    def _describe(self, ctx: MigrationContext) -> dict[str, Any]:
        """Module facts for the task; without the graph, callee facts are withheld."""
        module = describe_module(ctx)
        if not self.config.use_graph:
            module.pop("dependencies", None)
            module.pop("callers", None)
            module["note"] = (
                "Dependency information is unavailable: read the source to find "
                "any CALLed programs."
            )
        return module

    def _translator_brief(self, program: str, analysis: str | None, tests: str | None) -> str:
        parts = []
        if analysis:
            parts.append(f"## Analyst's notes\n{analysis}")
        if tests:
            parts.append(f"## Test Writer's cases\n{tests}")
        failures = self.state.artifacts(self.run_id, program, "failure")
        if failures:
            parts.append(
                "## Earlier attempts (do not repeat these mistakes)\n" + "\n\n".join(failures[-3:])
            )
        deps = self._deps.get(program, []) if self.config.use_graph else []
        if self.memory is not None and self.config.use_folding:
            # folded context: callee summaries inline, full sources on demand
            for dep in deps:
                summary = self.memory.get(dep)
                if summary:
                    parts.append(summary)
            others = [d for d in self.memory.available() if d != program and d not in deps]
            if others:
                parts.append(
                    "## Other migrated modules (get_summary for details)\n" + ", ".join(others)
                )
        elif deps:
            parts.append(
                "## Callees\nRead each callee's COBOL (read_cobol) and its migrated Java "
                "(read_file) before calling it: " + ", ".join(deps)
            )
        return "\n\n".join(parts)

    def _gate(
        self, program: str, ctx: MigrationContext, result: AgentResult, main_program: bool
    ) -> tuple[str | None, str]:
        """Independent acceptance checks. Returns (failure category or None, detail)."""
        if not result.ok:
            report = ctx.last_report
            if result.status is AgentStatus.FAILED or result.status in _STATUS_CATEGORY:
                category = _STATUS_CATEGORY.get(result.status)
                if category is None:
                    category = COMPILE_ERROR if report and not report.compile_ok else SEMANTIC_DIFF
                detail = result.summary or result.reason
                if report is not None:
                    detail += "\n" + format_report(report, ctx.golden)
                return category, detail
        cobol = ctx.program_path(program)
        records: set[str] = set()
        report: EquivalenceReport | None = None
        if main_program:
            report = self.harness.check(
                program,
                ctx.workspace,
                dependencies=ctx.dependencies,
                extra_roots=[ctx.cases_root],
                store=ctx.golden,
            )
            if not report.passed:
                category = COMPILE_ERROR if not report.compile_ok else SEMANTIC_DIFF
                return category, format_report(report, ctx.golden)
            records = golden_records(ctx.golden, program, [c.case for c in report.cases])
        else:
            compiled = self.harness.compile_java(program, ctx.workspace, ctx.dependencies)
            if not compiled.ok:
                return COMPILE_ERROR, compiled.log[-2000:]
        findings = check_java(
            ctx.workspace,
            program,
            golden=records,
            cobol_source=cobol.read_text(encoding="latin-1") if cobol else "",
        )
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            return STATIC_CHECK, "\n".join(f"{f.file}: {f.rule}: {f.message}" for f in errors)
        if report is not None:
            return None, report.summary()
        return None, f"{program}: compiles; verified through callers"

    def _review(self, program: str, module: dict[str, Any], result: AgentResult) -> str | None:
        """Run the Reviewer; returns change requests, or None if approved."""
        reviews = self.state.artifacts(self.run_id, program, "review")
        task = task_message(
            module,
            f"## Translator's summary\n{result.summary}\n\n"
            "The Java passes the equivalence harness. Review it.",
        )
        res, _ = self._run_role(program, ROLES["reviewer"], task, len(reviews) + 1)
        self.state.put_artifact(
            self.run_id, program, "review", f"{res.status.value}: {res.summary}"
        )
        if res.status is AgentStatus.FAILED:
            return f"Reviewer requested changes:\n{res.summary}"
        return None  # approved, or the reviewer itself failed (non-blocking)
