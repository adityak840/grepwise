-- Schema step 2 (N2): sources added by package name, and cached package resolutions.

-- The git ref the files came from (a tag or commit); with `url`, enough to link to GitHub.
ALTER TABLE sources ADD COLUMN ref TEXT;
-- The PyPI name, for sources added by package (they are named after it: 'typer@0.27.2').
ALTER TABLE sources ADD COLUMN package TEXT;
CREATE INDEX sources_package ON sources (package, version);

-- package@version -> GitHub repo and tag, so the next lookup needs no network.
CREATE TABLE resolutions (
  ecosystem   TEXT NOT NULL,                -- 'pypi'
  package     TEXT NOT NULL,                -- normalized name
  version     TEXT NOT NULL,
  repo_url    TEXT NOT NULL,
  tag         TEXT NOT NULL,
  resolved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (ecosystem, package, version)
);
