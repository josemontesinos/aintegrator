.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install lint format format-check typecheck importcheck test check \
        contract-test integration-test service-test up down compose-check demo demo-agent clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "%-16s %s\n", $$1, $$2}'

install: ## Install the whole uv workspace with dev tools
	$(UV) sync --all-packages

lint: ## Ruff lint
	$(UV) run ruff check .

format: ## Format all Python files
	$(UV) run ruff format .

format-check: ## Verify formatting
	$(UV) run ruff format --check .

typecheck: ## mypy (strict for integration and agent) for all runtime packages
	$(UV) run mypy -p aintegrator_integration -p aintegrator_agent -p aintegrator_mcp_server

importcheck: ## Enforce the one-way dependency rules
	$(UV) run lint-imports

test: ## Unit tests (no running services required)
	$(UV) run pytest -q

contract-test: ## Adapter contract tests (requires `make up`)
	$(UV) run pytest -q -m contract

integration-test: ## Webhook/reconciler workflow tests (requires `make up`)
	$(UV) run pytest -q -m integration

service-test: contract-test integration-test ## All service-backed tests

check: lint format-check typecheck importcheck test ## All local checks (CI gate)

up: ## Start local services
	docker compose up -d --build --wait

down: ## Stop local services
	docker compose down

compose-check: ## Validate docker-compose.yml without starting services
	docker compose config -q

demo: ## Run the Phase 1 eligibility/order demo (requires `make up`)
	$(UV) run python -m aintegrator_integration.demo

demo-agent: ## Run the Phase 2 scripted agent demo (requires `make up`, no API key)
	$(UV) run python -m aintegrator_agent.demo

clean: ## Remove local caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
