"""Database migration: create GDPR request and audit tables."""

MIGRATION_UP = """
CREATE TABLE IF NOT EXISTS gdpr_requests (
    id UUID PRIMARY KEY,
    user_id UUID,
    request_type VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL,
    email VARCHAR(320) NOT NULL,
    name VARCHAR(255),
    details TEXT,
    data_categories JSONB DEFAULT '[]'::jsonb,
    verification_token VARCHAR(255) UNIQUE,
    verification_sent_at TIMESTAMP WITH TIME ZONE,
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    deadline_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS gdpr_audit_log (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID REFERENCES gdpr_requests(id) ON DELETE CASCADE,
    operation VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_gdpr_requests_email ON gdpr_requests(email);
CREATE INDEX IF NOT EXISTS idx_gdpr_requests_status ON gdpr_requests(status);
CREATE INDEX IF NOT EXISTS idx_gdpr_requests_created_at ON gdpr_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gdpr_requests_deadline_at ON gdpr_requests(deadline_at);
CREATE INDEX IF NOT EXISTS idx_gdpr_audit_request_id ON gdpr_audit_log(request_id);
CREATE INDEX IF NOT EXISTS idx_gdpr_audit_created_at ON gdpr_audit_log(created_at DESC);
"""

MIGRATION_DOWN = """
DROP INDEX IF EXISTS idx_gdpr_audit_created_at;
DROP INDEX IF EXISTS idx_gdpr_audit_request_id;
DROP INDEX IF EXISTS idx_gdpr_requests_deadline_at;
DROP INDEX IF EXISTS idx_gdpr_requests_created_at;
DROP INDEX IF EXISTS idx_gdpr_requests_status;
DROP INDEX IF EXISTS idx_gdpr_requests_email;

DROP TABLE IF EXISTS gdpr_audit_log;
DROP TABLE IF EXISTS gdpr_requests;
"""


if __name__ == "__main__":
    print("Migration: create GDPR request and audit tables")
    print("=" * 70)
    print("\nUP Migration:")
    print(MIGRATION_UP)
    print("\n" + "=" * 70)
    print("\nDOWN Migration:")
    print(MIGRATION_DOWN)
