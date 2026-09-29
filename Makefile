# Shortcuts for Linux, macOS and WSL. On plain Windows, run the commands shown in README.md.
# Destructive targets (contract, faults, drill, reset) are for a LOCAL stack only.

SHELL := bash
PY ?= python3
GIT_SHA ?= $(shell git rev-parse HEAD 2>/dev/null || echo dev)
BASE ?= http://127.0.0.1:3000
SIM ?= http://127.0.0.1:8000
SMOKE_USER ?= operator
SMOKE_PASSWORD ?= demo-operator
export GIT_SHA SMOKE_USER SMOKE_PASSWORD

.PHONY: help up down logs ps test test-backend test-frontend test-ci lint lock smoke contract faults drill verify reset

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

up: ## Build and start the full stack, wait until healthy
	docker compose up -d --build --wait

down: ## Stop the stack (keeps the database volume)
	docker compose down

logs: ## Follow backend logs
	docker compose logs -f backend

ps: ## Show container health
	docker compose ps

test: test-backend test-frontend test-ci ## Run every offline test suite

test-backend: ## Ruff + pytest (needs: pip install -r backend/requirements-dev.lock)
	$(PY) -m ruff check backend
	PYTHONPATH=backend $(PY) -m pytest backend/tests -q

test-frontend: ## Lint, type-check, test and build the React app
	cd frontend && npm ci && npm run lint && npm run typecheck && npm run test:ci && npm run build

test-ci: ## Tests for the CI/CD automation scripts
	$(PY) -m unittest discover -s tests_ci

lint: ## Backend and frontend linters only
	$(PY) -m ruff check backend
	cd frontend && npm run lint

lock: ## Regenerate pinned Python 3.11 locks after editing backend/requirements*.txt
	docker run --rm -v "$(CURDIR)/backend:/w" -w /w \
	  $(if $(EXTRA_CA_FILE),-v "$(abspath $(EXTRA_CA_FILE)):/ca.pem:ro" -e PIP_CERT=/ca.pem) python:3.11-slim sh -c \
	  "pip install -q pip-tools && pip-compile -q --strip-extras --no-header --allow-unsafe -o requirements.lock requirements.txt \
	   && pip-compile -q --strip-extras --no-header --allow-unsafe -o requirements-dev.lock requirements-dev.txt"

smoke: ## Read-only health/auth/state check of the running stack
	$(PY) scripts/app_smoke.py --base $(BASE) --expected-sha $(GIT_SHA)

contract: ## DESTRUCTIVE: official simulator API contract (resets the simulator)
	$(PY) scripts/simulator_contract.py --simulator $(SIM) --allow-reset

faults: ## DESTRUCTIVE: stale/unavailable fault injection and recovery
	$(PY) scripts/fault_probe.py --simulator $(SIM) --base $(BASE) --expected-sha $(GIT_SHA) --isolated-ci

drill: ## DESTRUCTIVE: approvals, duplicates, delivery, disruption, demand spike
	$(PY) scripts/e2e_drill.py --base $(BASE) --simulator $(SIM) --allow-reset

verify: up contract smoke faults drill ## Full local verification with evidence in artifacts/

reset: ## DESTRUCTIVE: reset the simulator to tick 0, paused
	curl -fsS -X POST $(SIM)/admin/reset && curl -fsS -X POST $(SIM)/admin/pause && echo
