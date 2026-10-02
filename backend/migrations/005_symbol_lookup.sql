-- find_symbol matches the full symbol or its last part, through indexes (never a scan).
-- The last part depends on the kind: code splits on '.' (Signer.sign -> sign), docs sections
-- on ' > ' (Tutorial > Options -> Options; headings may contain dots), endpoints stay whole.
ALTER TABLE chunks ADD COLUMN symbol_short text GENERATED ALWAYS AS (
  CASE
    WHEN symbol IS NULL THEN NULL
    WHEN kind = 'section' THEN regexp_replace(symbol, '^.* > ', '')
    WHEN kind = 'endpoint' THEN symbol
    ELSE regexp_replace(symbol, '^.*\.', '')
  END
) STORED;

CREATE INDEX chunks_symbol_short       ON chunks (symbol_short);
CREATE INDEX chunks_symbol_lower       ON chunks (lower(symbol));
CREATE INDEX chunks_symbol_short_lower ON chunks (lower(symbol_short));
