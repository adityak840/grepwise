-- One row per agent run (including failed and cancelled ones): input for M6's evals.
CREATE TABLE agent_runs (
  id             bigserial PRIMARY KEY,
  created_at     timestamptz NOT NULL DEFAULT now(),
  question       text NOT NULL,
  provider       text NOT NULL,
  model          text NOT NULL,
  prompt_version text NOT NULL,
  steps          jsonb NOT NULL,   -- [{tool, args, ok, summary}]
  answer         text,
  citations      jsonb NOT NULL,   -- [{citation, status}]
  grounded       boolean,
  input_tokens   int,
  output_tokens  int,
  seconds        real,
  error          text
);
