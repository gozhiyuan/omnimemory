-- 002_ingestion_core.sql
-- Week 3 ingestion schema additions (artifacts + contexts + timestamps).

ALTER TABLE source_items
    ADD COLUMN IF NOT EXISTS provider TEXT,
    ADD COLUMN IF NOT EXISTS external_id TEXT,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS phash TEXT,
    ADD COLUMN IF NOT EXISTS event_time_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS event_time_source TEXT,
    ADD COLUMN IF NOT EXISTS event_time_confidence REAL;

CREATE INDEX IF NOT EXISTS source_items_user_event_time_idx
    ON source_items (user_id, event_time_utc DESC);
CREATE INDEX IF NOT EXISTS source_items_user_content_hash_idx
    ON source_items (user_id, content_hash);
CREATE INDEX IF NOT EXISTS source_items_provider_external_idx
    ON source_items (user_id, provider, external_id);

CREATE TABLE IF NOT EXISTS derived_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_item_id UUID NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    storage_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_item_id, artifact_type, producer, producer_version, input_fingerprint)
);

CREATE INDEX IF NOT EXISTS derived_artifacts_item_idx
    ON derived_artifacts (source_item_id);
CREATE INDEX IF NOT EXISTS derived_artifacts_user_type_idx
    ON derived_artifacts (user_id, artifact_type);

CREATE TABLE IF NOT EXISTS processed_contexts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    context_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    entities JSONB NOT NULL DEFAULT '[]'::jsonb,
    location JSONB NOT NULL DEFAULT '{}'::jsonb,
    event_time_utc TIMESTAMPTZ NOT NULL,
    start_time_utc TIMESTAMPTZ,
    end_time_utc TIMESTAMPTZ,
    is_episode BOOLEAN NOT NULL DEFAULT FALSE,
    source_item_ids UUID[] NOT NULL,
    merged_from_context_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
    vector_text TEXT NOT NULL,
    processor_versions JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS processed_contexts_user_time_idx
    ON processed_contexts (user_id, event_time_utc DESC);
CREATE INDEX IF NOT EXISTS processed_contexts_user_type_time_idx
    ON processed_contexts (user_id, context_type, event_time_utc DESC);
