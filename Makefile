.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install lint format format-check typecheck importcheck test check \
        up down compose-check demo clean

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

typecheck: ## mypy strict for integration and agent
	$(UV) run mypy -p aintegrator_integration -p aintegrator_agent

importcheck: ## Enforce the one-way dependency rules
	$(UV) run lint-imports

test: ## Unit tests (no running services required)
	$(UV) run pytest -q

check: lint format-check typecheck importcheck test ## All local checks (CI gate)

up: ## Start local services
	docker compose up -d

down: ## Stop local services
	docker compose down

compose-check: ## Validate docker-compose.yml without starting services
	docker compose config -q

demo: ## Placeholder; the real demo arrives in phase 1
	@echo "Demo arrives in phase 1 (providers + integration)."

clean: ## Remove local caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
