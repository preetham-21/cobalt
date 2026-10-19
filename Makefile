# All targets run inside the `dev` container. Inside the container
# (COBALT_IN_DOCKER=1) they run directly, so `make test` works in both places.

COMPOSE ?= docker compose

ifeq ($(COBALT_IN_DOCKER),1)
RUN :=
else
RUN := $(COMPOSE) run --rm dev
endif

.DEFAULT_GOAL := help
.PHONY: help build shell smoke test lint fmt corpus data inventory clean

help: ## List targets
	@grep -E '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

build: ## Build the dev/toolchain image
	$(COMPOSE) build dev

shell: ## Open a shell in the dev container
	$(COMPOSE) run --rm dev bash

smoke: ## Check the toolchain, compile and run every synthetic program
	$(RUN) python scripts/smoke.py

test: ## Run the test suite
	$(RUN) pytest

lint: ## Lint and format-check
	$(RUN) ruff check src tests scripts
	$(RUN) ruff format --check src tests scripts

fmt: ## Auto-fix lint and format
	$(RUN) ruff check --fix src tests scripts
	$(RUN) ruff format src tests scripts

corpus: ## Fetch the AWS CardDemo corpus (pinned commit)
	$(RUN) bash scripts/fetch_corpus.sh

data: ## Regenerate the synthetic data files
	$(RUN) python scripts/gen_synthetic_data.py

inventory: ## Inventory all corpora
	$(RUN) cobalt inventory

clean: ## Remove generated run artefacts
	$(RUN) bash -c 'find runs workspace -mindepth 1 ! -name .gitkeep -exec rm -rf {} +'
