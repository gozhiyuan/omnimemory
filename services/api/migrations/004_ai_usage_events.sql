CREATE TABLE IF NOT EXISTS ai_usage_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_id UUID REFERENCES source_items(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    step_name TEXT NOT NULL,
    prompt_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ai_usage_events_user_created_idx
    ON ai_usage_events (user_id, created_at);

CREATE INDEX IF NOT EXISTS ai_usage_events_item_created_idx
    ON ai_usage_events (item_id, created_at);

CREATE INDEX IF NOT EXISTS ai_usage_events_model_idx
    ON ai_usage_events (model);
