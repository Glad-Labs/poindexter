-- Rollback: Newsletter Subscribers Tables
-- Reverses: 010_newsletter_subscribers.sql
-- WARNING: Destroys all newsletter subscriber and campaign log data

BEGIN;

DROP INDEX IF EXISTS idx_newsletter_email;
DROP INDEX IF EXISTS idx_newsletter_subscribed_at;
DROP INDEX IF EXISTS idx_newsletter_verified;
DROP INDEX IF EXISTS idx_campaign_logs_campaign_id;
DROP INDEX IF EXISTS idx_campaign_logs_subscriber_id;
DROP INDEX IF EXISTS idx_campaign_logs_sent_at;

DROP TABLE IF EXISTS campaign_email_logs;
DROP TABLE IF EXISTS newsletter_subscribers;

COMMIT;
