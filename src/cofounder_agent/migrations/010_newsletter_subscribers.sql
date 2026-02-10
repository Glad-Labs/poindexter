-- Newsletter Subscribers Table
-- Stores email campaign signups from public site "Get Updates" button

CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    company VARCHAR(255),
    interest_categories VARCHAR(500), -- JSON array stored as string: ["AI", "Technology", "Automation"]
    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ip_address VARCHAR(45), -- IPv4 or IPv6
    user_agent TEXT,
    verified BOOLEAN DEFAULT FALSE,
    verification_token VARCHAR(255),
    verified_at TIMESTAMP WITH TIME ZONE,
    unsubscribed_at TIMESTAMP WITH TIME ZONE,
    unsubscribe_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    marketing_consent BOOLEAN DEFAULT FALSE
);

-- Create indexes for newsletter_subscribers
CREATE INDEX IF NOT EXISTS idx_newsletter_email ON newsletter_subscribers(email);
CREATE INDEX IF NOT EXISTS idx_newsletter_subscribed_at ON newsletter_subscribers(subscribed_at);
CREATE INDEX IF NOT EXISTS idx_newsletter_verified ON newsletter_subscribers(verified);

-- Campaign Email Logs Table
-- Tracks which emails were sent to which subscribers and engagement

CREATE TABLE IF NOT EXISTS campaign_email_logs (
    id SERIAL PRIMARY KEY,
    subscriber_id INTEGER NOT NULL REFERENCES newsletter_subscribers(id) ON DELETE CASCADE,
    campaign_name VARCHAR(255) NOT NULL,
    campaign_id INTEGER,
    email_subject VARCHAR(500),
    sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    delivery_status VARCHAR(50), -- 'pending', 'sent', 'failed', 'bounced'
    delivery_error TEXT,
    opened BOOLEAN DEFAULT FALSE,
    opened_at TIMESTAMP WITH TIME ZONE,
    clicked BOOLEAN DEFAULT FALSE,
    clicked_at TIMESTAMP WITH TIME ZONE,
    bounce_type VARCHAR(50), -- 'hard', 'soft', NULL if not bounced
    bounce_reason TEXT
);

-- Create indexes for campaign_email_logs
CREATE INDEX IF NOT EXISTS idx_campaign_subscriber_id ON campaign_email_logs(subscriber_id);
CREATE INDEX IF NOT EXISTS idx_campaign_name ON campaign_email_logs(campaign_name);
CREATE INDEX IF NOT EXISTS idx_campaign_sent_at ON campaign_email_logs(sent_at);
CREATE INDEX IF NOT EXISTS idx_campaign_delivery_status ON campaign_email_logs(delivery_status);

-- Add trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_newsletter_subscribers_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS newsletter_subscribers_update_timestamp ON newsletter_subscribers;
CREATE TRIGGER newsletter_subscribers_update_timestamp
BEFORE UPDATE ON newsletter_subscribers
FOR EACH ROW
EXECUTE FUNCTION update_newsletter_subscribers_timestamp();
