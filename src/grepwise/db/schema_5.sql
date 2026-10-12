-- Schema step 5 (N6a): ecosystems (spec 16, section 7).
-- needs: foreign_keys off
--
-- The same package name can exist in two ecosystems (requests is on PyPI and npm), so the
-- uniqueness rule gains the ecosystem: '' for sources that aren't packages (repo URLs, local
-- folders, API specs), so it can be a plain column in the rule. SQLite can't change a
-- constraint in place, so `sources` is rebuilt (create, copy, drop, rename). Foreign keys are
-- off meanwhile, so dropping the old table doesn't cascade to documents and chunks.

CREATE TABLE sources_new (
  id                INTEGER PRIMARY KEY,
  type              TEXT NOT NULL CHECK (type IN ('repo', 'docs', 'openapi')),
  name              TEXT NOT NULL,
  url               TEXT NOT NULL,
  version           TEXT NOT NULL,
  subpath           TEXT,
  commit_sha        TEXT,
  status            TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'indexing', 'ready', 'failed')),
  error             TEXT,
  embedding_model   TEXT,
  generation        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  ref               TEXT,
  package           TEXT,
  embed_lease_until TEXT,
  last_used_at      TEXT,
  ecosystem         TEXT NOT NULL DEFAULT '',  -- 'pypi', 'npm'...; '' if not a package
  UNIQUE (type, ecosystem, name, version)
);

INSERT INTO sources_new (
  id, type, name, url, version, subpath, commit_sha, status, error, embedding_model,
  generation, created_at, updated_at, ref, package, embed_lease_until, last_used_at, ecosystem
)
SELECT
  id, type, name, url, version, subpath, commit_sha, status, error, embedding_model,
  generation, created_at, updated_at, ref, package, embed_lease_until, last_used_at,
  CASE WHEN package IS NOT NULL THEN 'pypi' ELSE '' END
FROM sources;

DROP TABLE sources;
ALTER TABLE sources_new RENAME TO sources;
CREATE INDEX sources_package ON sources (ecosystem, package, version);

-- A package in a repo holding several packages lives in a subfolder (npm's
-- repository.directory); remembered with the resolution.
ALTER TABLE resolutions ADD COLUMN subpath TEXT;
