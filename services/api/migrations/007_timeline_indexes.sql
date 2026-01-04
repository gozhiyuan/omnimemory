-- 007_timeline_indexes.sql
-- Adds composite index to speed timeline ordering by event_time_utc + created_at.

CREATE INDEX IF NOT EXISTS source_items_user_event_time_created_idx
    ON source_items (user_id, event_time_utc DESC, created_at DESC);
