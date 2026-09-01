.PHONY: help install dev dev-backend dev-frontend test lint fmt typecheck check seed migrate eval clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	cd backend && uv sync --extra dev
	cd frontend && pnpm install

dev: ## Run backend and frontend together
	@$(MAKE) -j2 dev-backend dev-frontend

dev-backend: ## Run the FastAPI server with reload
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend: ## Run the Next.js dev server
	cd frontend && pnpm dev

test: ## Run the backend test suite
	cd backend && uv run pytest

lint: ## Lint backend and frontend
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && pnpm lint

fmt: ## Format backend and frontend
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd frontend && pnpm format

typecheck: ## Type-check backend and frontend
	cd backend && uv run mypy app
	cd frontend && pnpm typecheck

check: lint typecheck test ## Everything CI runs

seed: ## Create the demo workspace and roster
	cd backend && uv run python -m app.cli seed

migrate: ## Apply database migrations
	cd backend && uv run alembic upgrade head

eval: ## Regenerate the evaluation report
	cd backend && uv run python -m evals.runner --report ../docs/EVALUATION.md

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/.next
