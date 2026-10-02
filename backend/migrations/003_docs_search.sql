-- Full text of each document, so M3's read_file can return exact lines.
ALTER TABLE documents ADD COLUMN content text;

-- Docs sources index one folder of a repo.
ALTER TABLE sources ADD COLUMN subpath text;

-- Keyword search reads search_text (content + split identifiers), filled in by Python.
ALTER TABLE chunks DROP COLUMN tsv;
ALTER TABLE chunks ADD COLUMN search_text text;
ALTER TABLE chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  to_tsvector('english', coalesce(search_text, coalesce(symbol, '') || ' ' || content))
  || to_tsvector('simple', coalesce(search_text, coalesce(symbol, '') || ' ' || content))
) STORED;
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
