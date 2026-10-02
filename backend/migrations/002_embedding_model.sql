-- Which embedder produced each vector (e.g. 'gemini:gemini-embedding-001:768').
-- Reuse only matches the same model, so switching models never keeps stale vectors.
-- Existing rows stay NULL and are simply re-embedded on their next index.
ALTER TABLE chunks ADD COLUMN embedding_model text;
