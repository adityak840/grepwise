CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE schema_migrations (
  version    text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sources (
  id          bigserial PRIMARY KEY,
  type        text NOT NULL CHECK (type IN ('repo','docs','openapi')),
  name        text NOT NULL,              -- e.g. 'pallets/itsdangerous'
  url         text NOT NULL,
  version     text NOT NULL,              -- tag or commit we indexed
  commit_sha  text,
  status      text NOT NULL DEFAULT 'pending'
              CHECK (status IN ('pending','indexing','ready','failed')),
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (type, name, version)
);

CREATE TABLE documents (
  id         bigserial PRIMARY KEY,
  source_id  bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  path       text NOT NULL,               -- relative to repo root
  language   text,
  line_count int NOT NULL,
  UNIQUE (source_id, path)
);

CREATE TABLE chunks (
  id           bigserial PRIMARY KEY,
  document_id  bigint NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  source_id    bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  kind         text NOT NULL,             -- function | method | class | module | window
  symbol       text,                      -- e.g. 'Signer.sign'
  start_line   int NOT NULL,              -- 1-based, inclusive
  end_line     int NOT NULL,              -- 1-based, inclusive
  header       text NOT NULL,             -- context header (see 6.4)
  content      text NOT NULL,
  content_hash text NOT NULL,             -- sha256 of header + content
  embedding    halfvec(768),
  tsv          tsvector GENERATED ALWAYS AS
               (to_tsvector('simple', coalesce(symbol,'') || ' ' || content)) STORED
);

CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX chunks_tsv_gin       ON chunks USING gin (tsv);
CREATE INDEX chunks_symbol_btree  ON chunks (symbol);
CREATE INDEX chunks_source        ON chunks (source_id);
CREATE INDEX chunks_hash          ON chunks (source_id, content_hash);

CREATE TABLE jobs (
  id          bigserial PRIMARY KEY,
  source_id   bigint NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  status      text NOT NULL DEFAULT 'queued'
              CHECK (status IN ('queued','running','done','failed')),
  progress    int NOT NULL DEFAULT 0,     -- 0..100
  error       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
