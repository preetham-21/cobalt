# cobalt — Project Brief for Claude Code

> Read this file fully before doing any work in this repo. It describes the whole
> project. You will be given ONE stage at a time; implement only that stage.

---

## 1. What we are building

**cobalt** is an AI agent system that migrates legacy **COBOL batch programs** to
**Java 21**, and *proves* each migration is correct by running the original COBOL
and the generated Java on the same inputs and diffing the outputs.

It is a long-horizon, multi-agent system with a hand-built agent harness, a code
knowledge graph, context folding and persistent memory — built as a portfolio
project that should look and behave like a serious engineering product, not a demo.

### The real-world problem
Banks, insurers and governments still run core systems on decades-old COBOL. The
people who understand it are retiring, and migration projects routinely run years
late and over budget because:
- programs are deeply interdependent (copybooks, CALLs, shared files),
- COBOL semantics are subtle (packed decimal, ROUNDED, PIC editing, REDEFINES),
- nobody trusts a migration without proof of behavioural equivalence.

cobalt addresses all three: dependency-aware ordering via a knowledge graph,
semantic care via specialised agents, and trust via automated equivalence testing.

---

## 2. Scope

**In scope**
- COBOL batch programs using sequential (and later, if feasible, indexed) files
- Copybooks, CALLed subprograms, PERFORM logic, COMP-3, PIC editing, REDEFINES
- Target: idiomatic Java 21, plain Maven project (no Spring), BigDecimal for money
- Corpora: `corpus/synthetic/` (our own controlled programs) and
  `corpus/carddemo/` (AWS CardDemo, Apache 2.0, real-world reference codebase)

**Out of scope (for now)**
- CICS online transactions, BMS screens, DB2/EXEC SQL, JCL execution
  (CardDemo's CICS/DB programs are used for graph analysis and stats only)
- Any UI beyond a CLI and a static metrics report/dashboard

---

## 3. Architecture

```
                ┌──────────────────────────────────────────────┐
                │                 CLI (Typer)                  │
                │  inventory | graph | golden | migrate | eval │
                └──────────────────────┬───────────────────────┘
                                       │
                ┌──────────────────────▼───────────────────────┐
                │              Supervisor Agent                │
                │  picks next module in dependency order,      │
                │  assigns work, enforces budgets, decides     │
                │  retry / escalate / accept                   │
                └──┬─────────┬──────────┬───────────┬──────────┘
                   │         │          │           │
             ┌─────▼──┐ ┌────▼─────┐ ┌──▼───────┐ ┌─▼────────┐
             │Analyst │ │Translator│ │Test      │ │Reviewer  │
             │explains│ │COBOL →   │ │Writer    │ │checks    │
             │semantics│ │Java     │ │edge-case │ │idioms,   │
             │& risks │ │          │ │inputs    │ │diffs     │
             └────┬───┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
                  └──────────┴─────┬──────┴────────────┘
                                   │ tool calls
     ┌─────────────────────────────▼─────────────────────────────────┐
     │                       Agent Harness                           │
     │  agent loop · tool registry · sandboxed exec (Docker)         │
     │  step/token/cost budgets · retries · tracing · checkpoints    │
     │  context manager (folding/compaction) · memory store          │
     └───┬──────────────┬──────────────┬──────────────┬──────────────┘
         │              │              │              │
   ┌─────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼────────┐
   │Code       │ │Equivalence  │ │Migration   │ │Trace & Eval  │
   │Knowledge  │ │Harness      │ │State/Memory│ │Store         │
   │Graph      │ │cobc vs java │ │(SQLite)    │ │(JSONL+SQLite)│
   │(NetworkX) │ │golden diff  │ │summaries   │ │metrics       │
   └───────────┘ └─────────────┘ └────────────┘ └──────────────┘
```

### Components
1. **Code Knowledge Graph** — nodes: program, paragraph, copybook, data item,
   file, external call. Edges: CALLS, COPIES, READS, WRITES, PERFORMS, DEFINES.
   Used for topological migration order, impact analysis, and targeted context
   retrieval (give an agent exactly the copybooks/callees it needs).
2. **Equivalence Harness** — compiles/runs COBOL with GnuCOBOL to capture golden
   outputs; builds/runs the generated Java on identical inputs; normalised diff;
   reports pass/fail with the first divergent record/field.
3. **Agent Harness** (the heart of the project — hand-built, no agent framework):
   - explicit agent loop over the Anthropic Messages API with tool use
   - tool registry with typed schemas (read_file, query_graph, write_file,
     compile_java, run_equivalence, get_summary, etc.)
   - all code execution inside the Docker sandbox, never on the host
   - per-task budgets: max steps, max tokens, max cost; hard stop + report
   - structured traces of every step (prompt size, tool call, result, tokens, cost)
   - checkpointing so a run can resume after a crash
4. **Context Folding & Memory**
   - completed modules are folded into compact summaries (interface, data
     layouts, behaviour notes, pitfalls) stored in migration memory
   - agents receive summaries by default and expand to full source only on demand
   - context manager tracks window composition and compacts when near budget
   - persistent migration state: per-module status, attempts, failures, decisions
5. **Multi-agent roles** — Supervisor, Analyst, Translator, Test Writer, Reviewer.
   Each has its own system prompt, tool subset and budget.
6. **Evals & Metrics** — module-level success rate, attempts to pass, cost and
   tokens per module, wall time, failure taxonomy (compile error, semantic diff,
   budget exhausted, tool misuse, loop), and a static HTML report.

---

## 4. Tech stack

| Concern | Choice |
|---|---|
| Orchestrator language | Python 3.12 |
| LLM | Anthropic API via official `anthropic` SDK; model set by `COBALT_MODEL` |
| CLI | Typer |
| Config | pydantic-settings, `.env` |
| Logging | structlog (JSON) → `runs/logs/` |
| Graph | NetworkX (Neo4j optional later) |
| State / memory / traces | SQLite + JSONL |
| COBOL runtime | GnuCOBOL 3.x (`cobc`) |
| Java | OpenJDK 21 + Maven |
| Sandbox / dev env | Docker + docker compose (service `dev`) |
| Quality | pytest, ruff |

**No LangChain / LangGraph / CrewAI / AutoGen.** The harness is built by hand on purpose.

---

## 5. Repository layout

```
cobalt/
  CLAUDE.md
  src/cobalt/
    cli.py  config.py  logging_setup.py
    inventory/        # Stage 1
    graph/            # Stage 2
    equivalence/      # Stage 3
    harness/          # Stage 4: loop, tools, sandbox, budgets, tracing
    agents/           # Stage 5: role prompts + configs
    memory/           # Stage 6: summaries, folding, state store
    evals/            # Stage 7
  corpus/
    synthetic/        # our COBOL programs + data/
    carddemo/         # cloned, gitignored; scripts/fetch_corpus.sh
  workspace/          # generated Java projects (contents gitignored)
  runs/               # logs, traces, reports (contents gitignored)
  scripts/  tests/  docker/Dockerfile  docker-compose.yml
  pyproject.toml  Makefile  .env.example  README.md
```

---

## 6. Stage roadmap

Each stage ends with runnable commands and passing tests. Do not start a stage
until told to.

| # | Stage | Key deliverables |
|---|---|---|
| 1 | Foundation | repo skeleton, Docker toolchain, corpus clone, 4–5 synthetic COBOL programs, `make smoke`, `cobalt inventory` |
| 2 | Code Knowledge Graph | COBOL parser (programs, paragraphs, copybooks, CALL, file I/O, data items), graph build, `cobalt graph build/show/order`, topological migration order, graph export |
| 3 | Equivalence Harness | golden I/O capture from COBOL, Java build/run runner, normalised diff with first-divergence report, `cobalt golden` + hand-written Java baseline for one program proving the harness |
| 4 | Agent Harness Core | agent loop, tool registry, sandboxed exec, budgets, tracing, checkpoints; single-agent end-to-end migration of one synthetic program |
| 5 | Multi-agent | Supervisor + Analyst/Translator/Test Writer/Reviewer, role prompts, handoffs, retry/escalate policy; migrate all synthetic programs |
| 6 | Context Folding & Memory | module summaries, on-demand expansion, compaction, persistent state, resume after kill; run on larger CardDemo batch subset |
| 7 | Evals & Metrics | eval suite, failure taxonomy, ablations (with/without graph, with/without folding), HTML report |
| 8 | Packaging | README with architecture diagram and results, demo script, write-up |

---

## 7. Engineering rules (always follow)

1. **One stage at a time.** Implement only the stage you are asked for. Leave clean
   seams for later stages but do not build them early.
2. **Everything runs in Docker.** Commands are run via `docker compose run --rm dev ...`
   or Makefile targets. Agent-generated code must never execute on the host.
3. **Secrets:** never print, log or commit `ANTHROPIC_API_KEY`. `.env` is gitignored.
4. **Correctness over cleverness:** money is `BigDecimal` in Java; COBOL rounding and
   truncation semantics must be matched exactly; the equivalence harness is the judge.
5. **Observability first:** every LLM call and tool call is traced with tokens, cost
   and duration. If it isn't traced, it didn't happen.
6. **Determinism where possible:** fixed seeds, pinned versions, reproducible runs.
7. **Tests with every stage;** `make test` and `make lint` must pass before finishing.
8. **Small, typed, documented modules.** Type hints everywhere; docstrings on public APIs.
9. **When finishing a stage**, run the verification commands requested, show their
   full output, and list every deviation from the spec with the reason.
10. **If something in the spec is impossible or a bad idea, say so** and propose an
    alternative instead of silently changing course.

---

## 8. Success criteria for the finished project

- ≥ 90% of synthetic programs and a meaningful CardDemo batch subset migrated
  with passing equivalence tests
- Per-module cost, tokens and attempts reported
- Ablation results showing the measurable value of the knowledge graph and
  context folding
- A run can be killed at any point and resumed without redoing completed modules
- A newcomer can clone the repo, run `make build && make demo`, and see it work

---

## 9. Glossary

- **Golden output** — output produced by the original COBOL program; the ground truth.
- **Equivalence pass** — Java output matches golden output after normalisation.
- **Folding** — replacing a module's full source in context with a compact summary.
- **Compaction** — shrinking conversation history when near the context budget.
- **Module** — a COBOL program plus the copybooks it owns; the unit of migration.

---

## 10. Working notes (conventions established during the stages)

- Synthetic programs are fixed-format COBOL (cols 1–6 sequence, 7 indicator,
  8–72 code). `tests/test_synthetic_corpus.py` enforces the column rules.
- Synthetic data files are generated by `scripts/gen_synthetic_data.py` (fixed
  seed); never hand-edit `corpus/synthetic/data/*.dat` — change the generator.
- `corpus/synthetic/runs.toml` says how to compile and run each main program
  (sources to link, logical file name → input file, output logical names).
- Programs `ASSIGN TO "LOGICAL"`; runners execute in a scratch directory where
  inputs are copied under their logical names.
- Inside the dev container `COBALT_IN_DOCKER=1`; Makefile targets then run
  directly instead of re-entering `docker compose`.
