-- Schema step 4 (N4): when a source was last searched or read, for `grepwise gc`.
ALTER TABLE sources ADD COLUMN last_used_at TEXT;
