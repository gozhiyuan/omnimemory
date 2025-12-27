-- 002_google_photos_tokens.sql
-- Add OAuth token storage for data connections.

ALTER TABLE data_connections
    ADD COLUMN IF NOT EXISTS oauth_token JSONB;

ALTER TABLE data_connections
    ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;
