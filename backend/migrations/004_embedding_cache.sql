-- Every embedding ever made, keyed by model and content fingerprint. The pipeline saves to it
-- as each group of embeddings comes back, so a run that fails halfway keeps its progress, and
-- any chunk seen before (in any source or version) is never embedded twice by the same model.
CREATE TABLE embedding_cache (
  model_id     text NOT NULL,
  content_hash text NOT NULL,
  embedding    halfvec(768) NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (model_id, content_hash)
);

-- Keep everything already paid for.
INSERT INTO embedding_cache (model_id, content_hash, embedding)
SELECT DISTINCT ON (embedding_model, content_hash) embedding_model, content_hash, embedding
FROM chunks
WHERE embedding IS NOT NULL AND embedding_model IS NOT NULL
ON CONFLICT DO NOTHING;
