-- Rollback: Financial Tracking Table
-- Reverses: 012_financial_tracking_table.sql
-- WARNING: Destroys all financial entry data

BEGIN;

DROP INDEX IF EXISTS idx_financial_entries_category;
DROP INDEX IF EXISTS idx_financial_entries_created_at;
DROP INDEX IF EXISTS idx_financial_entries_date_range;
DROP TABLE IF EXISTS financial_entries;

COMMIT;
