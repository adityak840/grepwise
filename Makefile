# Run everything from the repo root. Backend commands run inside backend/.
unexport VIRTUAL_ENV

UV := cd backend && uv run
GRAMMARS := ["python", "javascript", "typescript", "tsx"]

.PHONY: setup db db-down migrate test lint fmt

setup:  ## Install dependencies and download tree-sitter grammars (needs network once)
	cd backend && uv sync
	$(UV) python -c 'import tree_sitter_language_pack as t; t.download($(GRAMMARS))'

db:  ## Start local Postgres + pgvector and wait until it is healthy
	docker compose up -d --wait

db-down:
	docker compose down

migrate: db
	$(UV) grepwise db migrate

test: db  ## Unit + integration tests (offline; integration uses Docker Postgres)
	$(UV) pytest

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

fmt:
	$(UV) ruff check --fix .
	$(UV) ruff format .
