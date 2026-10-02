# Run everything from the repo root. Backend commands run inside backend/.
unexport VIRTUAL_ENV

UV := cd backend && uv run
GRAMMARS := ["python", "javascript", "typescript", "tsx"]

.PHONY: setup db db-down migrate test lint fmt

setup:  ## Install dependencies, tree-sitter grammars and the local embedding model (network once)
	cd backend && uv sync
	$(UV) python -c 'import tree_sitter_language_pack as t; t.download($(GRAMMARS))'
	$(UV) python -c 'from grepwise.config import get_settings as g; from grepwise.ingest.embed import LocalEmbedder as L; s = g(); L(s.local_embedding_model, cache_dir=s.model_cache_dir).embed_query("warm up")'

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
