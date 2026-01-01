-- 006_chat_attachments.sql
-- Persist chat attachments (images, later other files).

CREATE TABLE IF NOT EXISTS chat_attachments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    message_id UUID REFERENCES chat_messages(id) ON DELETE CASCADE,
    storage_key TEXT NOT NULL,
    content_type TEXT,
    original_filename TEXT,
    size_bytes INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_attachments_session_idx
    ON chat_attachments (session_id, created_at);
CREATE INDEX IF NOT EXISTS chat_attachments_message_idx
    ON chat_attachments (message_id, created_at);
