-- Migration 025: Add `is_active` to app_settings for soft-delete + fallback testing.
--
-- Context: 2026-04-12. Matt asked for this after the admin_db migration on
-- 2026-04-12 (#193) ended up using HARD delete because the `app_settings`
-- schema had no active flag. Soft-delete makes it possible to:
--
--   1. Disable a setting without losing the value (quick revert)
--   2. A/B test by toggling between settings without rewriting them
--   3. Fallback-test by temporarily deactivating a production value
--
-- The column defaults to `true` so every existing row stays active without
-- a backfill step. The CLI exposes `poindexter settings enable|disable` and
-- `poindexter settings list --include-inactive` on top of this.

ALTER TABLE app_settings
    ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

-- Partial index so active-only queries stay fast even as inactive rows
-- accumulate. Existing `app_settings_key_key` unique constraint still
-- holds across ALL rows — one row per key regardless of active state.
CREATE INDEX IF NOT EXISTS idx_app_settings_is_active
    ON app_settings (is_active)
    WHERE is_active = true;
