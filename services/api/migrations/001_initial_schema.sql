-- 001_initial_schema.sql
-- Initial database schema for lifelog service

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE,
    display_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO users (id, email, display_name)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'test-user@example.com',
    'Test User'
)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS data_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connection_id UUID REFERENCES data_connections(id) ON DELETE SET NULL,
    storage_key TEXT NOT NULL,
    item_type TEXT NOT NULL,
    content_type TEXT,
    original_filename TEXT,
    captured_at TIMESTAMPTZ,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processing_error TEXT,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE source_items
    ADD CONSTRAINT source_items_item_type_check
        CHECK (item_type IN ('photo', 'video', 'audio', 'document'));

ALTER TABLE source_items
    ADD CONSTRAINT source_items_processing_status_check
        CHECK (processing_status IN ('pending', 'processing', 'completed', 'failed'));

CREATE TABLE IF NOT EXISTS processed_content (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    item_id UUID NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    content_role TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE processed_content
    ADD CONSTRAINT processed_content_role_check
        CHECK (content_role IN ('metadata', 'caption', 'transcription', 'ocr'));

CREATE UNIQUE INDEX IF NOT EXISTS processed_content_item_role_idx
    ON processed_content (item_id, content_role);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    summary_date DATE NOT NULL,
    summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS daily_summaries_user_date_idx
    ON daily_summaries (user_id, summary_date);

CREATE INDEX IF NOT EXISTS source_items_user_id_idx ON source_items (user_id);
CREATE INDEX IF NOT EXISTS source_items_status_idx ON source_items (processing_status);
CREATE INDEX IF NOT EXISTS data_connections_user_idx ON data_connections (user_id);
CREATE INDEX IF NOT EXISTS processed_content_item_idx ON processed_content (item_id);

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_source_items_updated_at ON source_items;
CREATE TRIGGER set_source_items_updated_at
    BEFORE UPDATE ON source_items
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS set_data_connections_updated_at ON data_connections;
CREATE TRIGGER set_data_connections_updated_at
    BEFORE UPDATE ON data_connections
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

