.DEFAULT_GOAL := help
UV ?= uv
EVAL_REPORT ?= evals/reports/latest.json
COST_REPORT ?= docs/cost-report.md

.PHONY: help install lint format format-check typecheck importcheck test check \
        contract-test integration-test service-test eval eval-report eval-baseline \
        cost-report cost-report-check rag-index rag-eval rag-eval-baseline \
        rag-ingest-postgres up down compose-check terraform-check terraform-validate \
        lock-check demo demo-agent clean

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

typecheck: ## mypy (strict for integration/agent/observability, defaults elsewhere)
	$(UV) run mypy -p aintegrator_integration -p aintegrator_agent -p aintegrator_mcp_server -p aintegrator_rag -p aintegrator_observability

importcheck: ## Enforce the one-way dependency rules
	$(UV) run lint-imports

test: ## Unit tests (no running services required)
	$(UV) run pytest -q

contract-test: ## Adapter contract tests (requires `make up`)
	$(UV) run pytest -q -m contract

integration-test: ## Webhook/reconciler workflow tests (requires `make up`)
	$(UV) run pytest -q -m integration

service-test: contract-test integration-test ## All service-backed tests

eval: ## Deterministic eval suite; writes the report and fails on baseline drift
	$(UV) run python -m aintegrator_evals --json $(EVAL_REPORT)

eval-report: ## Regenerate the deterministic JSON report used by the cost report
	$(UV) run python -m aintegrator_evals --json $(EVAL_REPORT)

eval-baseline: ## Regenerate the baseline after intentional eval changes
	$(UV) run python -m aintegrator_evals --update-baseline --json $(EVAL_REPORT)

cost-report: ## Render the deterministic cost report from the eval report
	$(UV) run aintegrator-cost-report --report $(EVAL_REPORT) --output $(COST_REPORT)

cost-report-check: ## Verify the checked-in cost report has no drift
	$(UV) run aintegrator-cost-report --report $(EVAL_REPORT) --output $(COST_REPORT) --check

rag-index: ## Build the offline RAG index from the fictional corpus
	$(UV) run python -m aintegrator_rag ingest

rag-eval: rag-index ## Rebuild the index, then compare retrieval against the baseline
	$(UV) run python -m aintegrator_rag eval

rag-eval-baseline: rag-index ## Regenerate the retrieval baseline after a corpus change
	$(UV) run python -m aintegrator_rag eval --update-baseline

rag-ingest-postgres: ## Ingest the corpus into pgvector (requires `make up`; set RAG_DSN)
	$(UV) run python -m aintegrator_rag ingest --dsn "$${RAG_DSN:-postgresql://aintegrator:aintegrator@localhost:5432/aintegrator}"

check: lint format-check typecheck importcheck test cost-report-check ## All local checks (CI gate)

up: ## Start local services
	docker compose up -d --build --wait

down: ## Stop local services
	docker compose down

compose-check: ## Validate docker-compose.yml without starting services
	docker compose config -q

terraform-check: ## Static AWS/Terraform checks (no Terraform CLI needed)
	$(UV) run pytest -q infra/tests

terraform-validate: ## Terraform fmt/validate (requires the Terraform CLI)
	terraform fmt -check -recursive infra/terraform
	terraform -chdir=infra/terraform init -backend=false -input=false
	terraform -chdir=infra/terraform validate

lock-check: ## Verify uv.lock is current
	$(UV) lock --check

demo: ## Run the Phase 1 eligibility/order demo (requires `make up`)
	$(UV) run python -m aintegrator_integration.demo

demo-agent: ## Run the Phase 2 scripted agent demo (requires `make up`, no API key)
	$(UV) run python -m aintegrator_agent.demo

clean: ## Remove local caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
