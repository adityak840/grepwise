-- Schema step 3 (N3): background embedding.

-- While a process embeds this source's chunks it holds a lease, renewed every batch; another
-- process only takes over once the lease has expired (ISO time, compared as text).
ALTER TABLE sources ADD COLUMN embed_lease_until TEXT;
