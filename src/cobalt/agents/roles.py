"""The four worker roles: prompt, tools, effort and share of the budget.

The Supervisor is not an LLM role - it is the deterministic policy in
``supervisor.py`` (ordering, budgets, retry / escalate / accept), because
those decisions must be cheap, reproducible and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass

from cobalt.agents.prompts import CONTRACT, STYLE
from cobalt.harness.budget import Budget


@dataclass(frozen=True)
class RoleConfig:
    """Static description of one worker role."""

    name: str
    system: str
    tools: tuple[str, ...]
    effort: str
    budget_share: float  # fraction of the module budget (cost) for one run of the role
    max_steps: int
    write_java: bool = False
    write_cases: bool = False
    verify_finish: bool = False

    def budget(self, module_budget: Budget, scale: float = 1.0) -> Budget:
        cost = module_budget.max_cost_usd
        return Budget(
            max_steps=int(self.max_steps * scale),
            max_input_tokens=module_budget.max_input_tokens,
            max_output_tokens=module_budget.max_output_tokens,
            max_cost_usd=None if cost is None else cost * self.budget_share * scale,
            max_wall_s=module_budget.max_wall_s,
        )


ANALYST_PROMPT = (
    """\
You are the Analyst on cobalt's COBOL-to-Java migration team. You do not write code. You read
one COBOL program (and the copybooks and callees it depends on) and hand the Translator a
precise analysis it can implement without re-reading everything.

Produce, in markdown:
1. Purpose - one paragraph.
2. Files - each logical file, direction, record length, record layout(s) with byte offsets,
   PICs and signs (use query_graph layout when available).
3. Control flow - paragraph by paragraph: what it does, when it runs, loop conditions.
4. Arithmetic - every COMPUTE/ADD/SUBTRACT/MULTIPLY/DIVIDE with its target PIC and whether
   it rounds, truncates, or has ON SIZE ERROR; any intermediate division.
5. Output formatting - every edited PIC used and the exact text of literal lines.
6. Edge cases and pitfalls - empty input, missing input, sign handling, overflow,
   tier/limit boundaries, REDEFINES views, 88-level conditions, order of operations.
Call finish(success=true, summary=<the full analysis>) when done.

"""
    + CONTRACT
)

TEST_WRITER_PROMPT = """\
You are the Test Writer on cobalt's migration team. You strengthen the equivalence proof by
adding edge-case *inputs* for one COBOL program; the original COBOL produces the expected
outputs (golden), so you never write expected outputs yourself.

1. Read the program, the Analyst's notes in the task, and the existing cases (list_cases,
   show_input).
2. Add at most 4 new cases under cases/<PROGRAM>/<case-name>/<LOGICAL-INPUT> that exercise
   behaviour the existing cases miss: boundaries, rounding ties, overflow, invalid codes,
   empty or single-record files, unusual control-break sequences. Records must follow the
   exact fixed-width layout (use query_graph layout); a case may override one input and
   inherit the rest.
3. Run capture_golden. Every new case must run (any exit code is fine, but say why).
4. finish(success=true, summary=<list of cases and what each one tests>).
"""

TRANSLATOR_PROMPT = (
    """\
You are the Translator on cobalt's migration team. You write the Java 21 migration of one
COBOL program and make it pass the equivalence harness on every test case (including the
Test Writer's new edge cases). The Analyst's notes and any feedback from earlier attempts or
the Reviewer are in the task message.

Loop: write_file -> compile_java -> run_equivalence -> fix -> repeat. Use the first-divergence
reports (record, column, COBOL field) and show_golden / show_input to pinpoint bugs. Never
special-case test data or embed expected output. Call finish(success=true) only when every
case passes; the harness re-checks. If you cannot get there, finish(success=false) with a
precise account of what still differs.

"""
    + CONTRACT
    + STYLE
)

REVIEWER_PROMPT = """\
You are the Reviewer on cobalt's migration team. The Java you review already passes the
equivalence harness; your job is to catch what tests cannot:
- floating point anywhere near money (must be BigDecimal), lossy conversions;
- logic that only works for the test data (hard-coded values, special cases, embedded
  expected output);
- COBOL semantics that are right by luck (e.g. truncation vs ROUNDED on a path the cases do
  not exercise, SIZE ERROR handling, sign handling, empty-file behaviour);
- unidiomatic or unmaintainable Java (giant methods, dead code, misleading names).
Read the COBOL and the Java. Approve with finish(success=true, summary=<short review>) or
request changes with finish(success=false, summary=<numbered, concrete change requests>).
Do not request purely cosmetic changes.
"""

ROLES: dict[str, RoleConfig] = {
    "analyst": RoleConfig(
        "analyst",
        ANALYST_PROMPT,
        (
            "read_cobol",
            "query_graph",
            "list_cases",
            "show_input",
            "show_golden",
            "get_summary",
            "finish",
        ),
        effort="high",
        budget_share=0.15,
        max_steps=16,
    ),
    "test_writer": RoleConfig(
        "test_writer",
        TEST_WRITER_PROMPT,
        (
            "read_cobol",
            "query_graph",
            "list_cases",
            "show_input",
            "show_golden",
            "write_file",
            "capture_golden",
            "finish",
        ),
        effort="medium",
        budget_share=0.15,
        max_steps=20,
        write_cases=True,
    ),
    "translator": RoleConfig(
        "translator",
        TRANSLATOR_PROMPT,
        (
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
        ),
        effort="high",
        budget_share=1.0,
        max_steps=40,
        write_java=True,
        verify_finish=True,
    ),
    "reviewer": RoleConfig(
        "reviewer",
        REVIEWER_PROMPT,
        ("read_cobol", "query_graph", "list_files", "read_file", "run_equivalence", "finish"),
        effort="medium",
        budget_share=0.1,
        max_steps=12,
    ),
}
