-- Migration: Fix writing_samples.user_id type and add FK to users
-- Fixes issue #316 — writing_samples.user_id is VARCHAR(255) but users.id is UUID.
-- No referential integrity enforced; cross-type join prevents index use.
-- Version: 020
-- NOTE: Verify all user_id values are valid UUIDs before applying:
--   SELECT id, user_id FROM writing_samples
--   WHERE user_id !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
-- Clean up any invalid values before running this migration.

BEGIN;

-- Drop the existing index before changing the column type
DROP INDEX IF EXISTS idx_writing_samples_user_id;

-- Cast existing VARCHAR values to UUID
ALTER TABLE writing_samples
  ALTER COLUMN user_id TYPE UUID USING user_id::uuid;

-- Add the foreign key constraint with cascade delete
ALTER TABLE writing_samples
  ADD CONSTRAINT fk_writing_samples_user_id
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

-- Recreate the index on the new type
CREATE INDEX IF NOT EXISTS idx_writing_samples_user_id ON writing_samples(user_id);

COMMIT;
