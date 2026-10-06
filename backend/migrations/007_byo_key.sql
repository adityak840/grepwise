-- Whether the visitor brought their own Gemini key (X-Gemini-Key). The key itself is never
-- stored anywhere; only this flag is.
ALTER TABLE agent_runs ADD COLUMN byo_key boolean NOT NULL DEFAULT false;
