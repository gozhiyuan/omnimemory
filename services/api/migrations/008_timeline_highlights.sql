CREATE TABLE IF NOT EXISTS timeline_day_highlights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    highlight_date DATE NOT NULL,
    source_item_id UUID NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, highlight_date)
);

CREATE INDEX IF NOT EXISTS timeline_day_highlights_user_date_idx
    ON timeline_day_highlights (user_id, highlight_date);
