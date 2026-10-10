# Run everything from the repo root. Backend commands run inside backend/.
unexport VIRTUAL_ENV

UV := cd backend && uv run
GW := $(UV) grepwise
GRAMMARS := ["python", "javascript", "typescript", "tsx"]

.PHONY: setup test lint fmt demo-sources eval-retrieval

setup:  ## Install dependencies, tree-sitter grammars and the local embedding model (network once)
	cd backend && uv sync
	$(UV) python -c 'import tree_sitter_language_pack as t; t.download($(GRAMMARS))'
	$(UV) python -c 'from grepwise.config import get_settings as g; from grepwise.ingest.embed import LocalEmbedder as L; s = g(); L(s.local_embedding_model, cache_dir=s.model_cache_dir).embed_query("warm up")'

test:  ## Unit + integration tests (offline; each test gets its own temporary database)
	$(UV) pytest

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

fmt:
	$(UV) ruff check --fix .
	$(UV) ruff format .

demo-sources:  ## Index the sources the eval questions use (about 10 minutes the first time)
	$(GW) add https://github.com/pallets/itsdangerous --ref 2.2.0
	$(GW) add https://github.com/fastapi/typer --ref 0.27.2
	$(GW) add https://github.com/pallets/click --ref 8.5.0
	$(GW) index-openapi https://petstore3.swagger.io/api/v3/openapi.json --name petstore --version 1.0.27

eval-retrieval:  ## Hit@5 and MRR for hybrid, keyword-only and vector-only search (offline)
	$(GW) eval retrieval --mode hybrid
	$(GW) eval retrieval --mode keyword
	$(GW) eval retrieval --mode vector
