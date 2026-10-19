# cobalt

AI agents that migrate legacy **COBOL batch programs** to **Java 21**, and
*prove* each migration by running the original COBOL and the generated Java on
the same inputs and diffing the outputs.

> Status: **Stage 1 — Foundation.** Toolchain image, corpora, synthetic
> programs, smoke test and `cobalt inventory`. See [CLAUDE.md](CLAUDE.md) for
> the full brief and stage roadmap.

## Quick start

Requires Docker (Docker Desktop on Windows/macOS) and `make`.

```bash
cp .env.example .env      # add ANTHROPIC_API_KEY (needed from Stage 4)
make build                # dev image: Python 3.12, GnuCOBOL 3, JDK 21, Maven
make corpus               # fetch AWS CardDemo at a pinned commit
make smoke                # compile + run every synthetic program, check Java
make inventory            # classify both corpora
make test lint
```

Every target runs inside the `dev` container (`docker compose run --rm dev …`).
Inside the container (`make shell`) the same targets run directly.

## `cobalt inventory`

Walks each corpus, classifies every file (COBOL, copybook, JCL, BMS, SQL, IMS,
data, …) and does a lexical scan of each COBOL program: line counts, EXEC
blocks, COPY/CALL counts, file organisations and notable features (COMP-3,
REDEFINES, OCCURS DEPENDING, ROUNDED, SIZE ERROR, GO TO, …). Each program is
given a migration scope:

| scope | meaning |
|---|---|
| `now` | batch, sequential files only — in scope today |
| `later` | batch, but uses INDEXED/RELATIVE (VSAM) files |
| `out` | CICS / DB2 / IMS / MQ — used for graph analysis only |

```bash
cobalt inventory                      # tables for every corpus present
cobalt inventory -c synthetic --json  # JSON to stdout
```

The JSON report is always written to `runs/inventory/inventory.json` and is
deterministic (sorted, no timestamps).

## Corpora

- `corpus/synthetic/` — five controlled programs (four main + one CALLed
  subprogram) with generated edge-case data. See its
  [README](corpus/synthetic/README.md).
- `corpus/carddemo/` — [AWS CardDemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo)
  (Apache-2.0), fetched by `scripts/fetch_corpus.sh` at a pinned commit;
  gitignored. Treated as data only, never executed.

## Layout

```
src/cobalt/       cli, config, logging_setup, inventory/
corpus/           synthetic/ (committed), carddemo/ (fetched)
scripts/          fetch_corpus.sh, gen_synthetic_data.py, smoke.py
docker/           Dockerfile          docker-compose.yml   Makefile
tests/            pytest suite
runs/ workspace/  generated output (gitignored)
```

## Conventions

- Secrets: `ANTHROPIC_API_KEY` lives only in `.env` (gitignored), is held as a
  `SecretStr`, and every log event passes through a redactor.
- Logs: structlog JSON lines in `runs/logs/cobalt.jsonl`.
- Determinism: pinned dependency versions, seeded data generation, fixed
  processing dates inside the COBOL programs.
- Line endings: `.gitattributes` forces LF — COBOL is column-sensitive.
