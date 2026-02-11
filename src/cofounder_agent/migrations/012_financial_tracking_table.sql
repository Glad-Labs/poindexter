-- Migration: Create financial_entries table for financial tracking
-- Version: 012
-- Purpose: Track financial transactions and cost entries for budget management
-- This table supports the admin_db.add_financial_entry() and get_financial_summary() methods

CREATE TABLE IF NOT EXISTS financial_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category VARCHAR(100) NOT NULL,      -- expense, revenue, refund, transfer, etc.
    amount DECIMAL(15, 2) NOT NULL,      -- Amount in USD
    description TEXT,
    tags JSONB,                          -- Optional tags array for categorization
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_financial_entries_category ON financial_entries(category);
CREATE INDEX IF NOT EXISTS idx_financial_entries_created_at ON financial_entries(created_at);

-- Composite index for date range queries
CREATE INDEX IF NOT EXISTS idx_financial_entries_date_range ON financial_entries(created_at, category);

-- Comment on table
COMMENT ON TABLE financial_entries IS 'Financial transaction log for budget and cost tracking';
COMMENT ON COLUMN financial_entries.category IS 'Transaction type: expense, revenue, refund, transfer, etc.';
COMMENT ON COLUMN financial_entries.tags IS 'JSON array of tags for filtering and categorization';
