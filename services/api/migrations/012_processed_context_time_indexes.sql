-- 012_processed_context_time_indexes.sql
-- Adds indexes to speed episode/timeline lookups with coalesced timestamps.

CREATE INDEX IF NOT EXISTS processed_contexts_user_episode_coalesce_time_idx
    ON processed_contexts (
        user_id,
        is_episode,
        (COALESCE(start_time_utc, event_time_utc)) DESC
    );

CREATE INDEX IF NOT EXISTS processed_contexts_user_episode_type_coalesce_time_idx
    ON processed_contexts (
        user_id,
        is_episode,
        context_type,
        (COALESCE(start_time_utc, event_time_utc)) DESC
    );

CREATE INDEX IF NOT EXISTS processed_contexts_user_type_created_idx
    ON processed_contexts (user_id, context_type, created_at DESC)
    WHERE is_episode IS TRUE;
