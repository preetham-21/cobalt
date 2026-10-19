# cobalt - design write-up

## The bet

Legacy-migration projects fail on trust more than on translation. A model can produce
plausible Java from COBOL; what nobody can do cheaply is *show* that the Java behaves the
same on the inputs that matter. cobalt is built around that proof: the original program is
the oracle, every migration is judged by running both and diffing, and the agents are
organised around making that judgement pass for the right reasons.

## Pipeline

1. **Inventory** - classify every file, scan every program lexically, and decide scope:
   batch with sequential files (`now`), batch with VSAM (`later`), online / DB2 / IMS / MQ
   (`out`, analysed only).
2. **Knowledge graph** - a division-aware parser expands COPY members (with REPLACING),
   computes record layouts down to byte offsets (PIC sizes, COMP-3, binary, SIGN SEPARATE,
   REDEFINES, OCCURS), finds paragraphs, PERFORM/GO TO flow, CALLs (static, dynamic with
   resolved VALUE, CICS LINK/XCTL) and file I/O modes. The graph gives migration *waves*
   (call graph condensed by SCC, callees first; XCTL is navigation, not a dependency),
   per-program context bundles and impact analysis.
3. **Golden outputs** - each main program is compiled with GnuCOBOL and run on every test
   case in the sandbox. Golden outputs are stored with digests of the sources and inputs,
   so staleness is detectable.
4. **Migration** - the Supervisor walks the waves. For each module: Analyst - Test Writer -
   Translator (retry, then escalate) - independent gate - Reviewer - accept and fold.
5. **Evaluation** - metrics come from the state store and traces; ablations switch off the
   graph and folding; a static HTML report summarises success, cost, attempts and the
   failure taxonomy.

## Decisions worth explaining

**A deterministic Supervisor.** The brief describes a Supervisor *agent*. Ordering, budgets
and retry / escalate / accept decisions are cheap, safe and auditable as code, and they
benefit from being reproducible. The model's judgement is spent where it is needed - the
four worker roles. Every supervisor decision is recorded and traced.

**The harness, not the agent, accepts.** `finish(success=true)` re-runs equivalence and
refuses unverified success; the Supervisor then runs equivalence *again* on all cases
(including the Test Writer's), plus static checks: no floating point and no string literal
that reproduces a golden record. A migration that passes by echoing expected output is
rejected.

**One text contract for every file organisation.** Java never sees BDB files or
fixed-length binary records. Around each COBOL run, generated COBOL utilities load text
records into indexed files (including alternate-key indexes, which BDB stores as a file
family) and dump written indexed files back to text in key order; record-sequential files
are converted in Python. The diff therefore compares text everywhere, and an agent writing
Java can implement keyed access however it likes.

**Pinned clocks and volatile masks.** CardDemo stamps records with `FUNCTION CURRENT-DATE`.
GnuCOBOL can pin the starting instant (`COB_CURRENT_DATE`; the compact form also pins
hundredths) and Java reads the same instant through `CobolClock`. Because GnuCOBOL lets the
clock run during the program, seconds and fractions still vary, so they are masked by an
explicit per-corpus regex: date, hour and minute are still compared.

**Append-only context management.** Current Claude models (Opus 5.5 included) bind thinking
blocks to the exact conversation and reject edited history, so cobalt never rewrites turns.
When the prompt passes a budget, the loop starts a new episode: the original task plus a
folded digest (files written, last compile and equivalence results, last note). The
workspace is the durable memory.

**Folding.** Accepted modules are summarised deterministically (Java interface, COBOL
parameters and layouts, behaviour notes, pitfalls from failed attempts). A caller's
Translator receives its callees' summaries inline and can expand to full sources with one
tool call.

**Rootless sandbox on Windows.** Without Docker, cobalt runs GnuCOBOL and the JDK inside
WSL from official Ubuntu packages unpacked into a user prefix (no root), and isolates each
command with an unprivileged network namespace, `timeout` and `prlimit`. Two toolchain
traps were handled on the way: GCC 15 defaults to C23, which breaks the K&R prototypes
cobc 3.2 emits for static CALLs (forced `-std=gnu17`), and the unpacked JDK's configuration
symlinks point into `/etc` (relinked into the prefix).

## Failure taxonomy

`compile_error`, `semantic_diff`, `static_check`, `review`, `budget_exhausted`,
`tool_misuse`, `loop`, `stopped`, `refused`, `provider_error`, `blocked` - recorded per
module and per attempt, and broken down by variant in the report.

## Limitations and next steps

- **Live results are pending an API key.** The pipeline has been exercised end to end with
  a scripted provider that re-enacts reference migrations; the success rate, cost and
  ablation effect of real agents are the next measurement (`cobalt eval --provider
  anthropic`).
- The parser is a careful scanner, not a full COBOL grammar; it covers the synthetic corpus
  and all 44 CardDemo programs, and unresolved constructs degrade gracefully.
- Variable-length records, ESDS/RRDS, SORT verbs inside programs, and CICS/DB2 execution
  are out of scope; CardDemo's CBACT01C and statement programs are excluded for that
  reason.
- The equivalence proof is only as strong as the test cases. The Test Writer and the edge
  cases help; coverage-guided case generation (paragraph coverage from GnuCOBOL tracing) is
  the natural next step.
