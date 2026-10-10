-- Grepwise's SQLite schema, version 1. Applied by db/connection.py when `PRAGMA user_version`
-- is 0. A later change becomes step 2 in connection.py's SCHEMA_STEPS, never an edit here.

CREATE TABLE sources (
  id              INTEGER PRIMARY KEY,
  type            TEXT NOT NULL CHECK (type IN ('repo', 'docs', 'openapi')),
  name            TEXT NOT NULL,            -- e.g. 'pallets/itsdangerous'
  url             TEXT NOT NULL,
  version         TEXT NOT NULL,            -- tag or version we indexed
  subpath         TEXT,                     -- docs folder, for docs sources
  commit_sha      TEXT,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'indexing', 'ready', 'failed')),
  error           TEXT,
  embedding_model TEXT,                     -- model of the last successful index
  -- Changes on every successful index, so in-memory vector caches know to reload.
  generation      INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  UNIQUE (type, name, version)
);

CREATE TABLE documents (
  id         INTEGER PRIMARY KEY,
  source_id  INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
  path       TEXT NOT NULL,                 -- relative to the repo root, or 'GET /pet/{petId}'
  language   TEXT,
  line_count INTEGER NOT NULL,
  content    TEXT NOT NULL,                 -- full text, for read_file
  UNIQUE (source_id, path)
);

CREATE TABLE chunks (
  id           INTEGER PRIMARY KEY,         -- also the rowid of its chunks_fts row
  document_id  INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
  source_id    INTEGER NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
  kind         TEXT NOT NULL,               -- function | method | class | module | window | section | endpoint
  symbol       TEXT,                        -- e.g. 'Signer.sign'
  symbol_short TEXT,                        -- e.g. 'sign' (computed in Python, see store.py)
  start_line   INTEGER NOT NULL,            -- 1-based, inclusive
  end_line     INTEGER NOT NULL,            -- 1-based, inclusive
  header       TEXT NOT NULL,               -- context header, embedded with the content
  content      TEXT NOT NULL,
  content_hash TEXT NOT NULL,               -- sha256 of header + content: the key to `embeddings`
  search_text  TEXT NOT NULL                -- what keyword search indexes (see keywords.py)
);
CREATE INDEX chunks_source            ON chunks (source_id);
CREATE INDEX chunks_document          ON chunks (document_id);
CREATE INDEX chunks_symbol            ON chunks (symbol);
CREATE INDEX chunks_symbol_short      ON chunks (symbol_short);
CREATE INDEX chunks_symbol_lower      ON chunks (lower(symbol));
CREATE INDEX chunks_symbol_short_lower ON chunks (lower(symbol_short));

-- One vector per (model, chunk text). Chunks find theirs by content_hash, so identical chunks
-- share a vector, re-indexing reuses everything, and a chunk with no vector yet is simply
-- missing from vector search (it is still found by keyword search).
CREATE TABLE embeddings (
  model_id     TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  vector       BLOB NOT NULL,               -- float16, little-endian, `embedding_dim` values
  PRIMARY KEY (model_id, content_hash)
) WITHOUT ROWID;

-- Keyword search. rowid = chunks.id; kept in sync by store.py (no triggers).
-- unicode61 splits on anything that isn't a letter or digit, so a path like
-- 'docs/tutorial/options/required.md' is indexed as its words; porter matches word forms.
CREATE VIRTUAL TABLE chunks_fts USING fts5 (path, symbol, body, tokenize = 'porter unicode61');
