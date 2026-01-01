-- 005_memory_graph_chat.sql
-- Memory graph + chat persistence tables.

CREATE TABLE IF NOT EXISTS memory_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mention_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE (user_id, node_type, name)
);

CREATE INDEX IF NOT EXISTS memory_nodes_user_type_idx
    ON memory_nodes (user_id, node_type);

CREATE TABLE IF NOT EXISTS memory_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_node_id UUID NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
    target_node_id UUID NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    strength REAL NOT NULL DEFAULT 1.0,
    mention_count INTEGER NOT NULL DEFAULT 1,
    last_connected TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_item_id UUID REFERENCES source_items(id) ON DELETE SET NULL,
    source_context_id UUID REFERENCES processed_contexts(id) ON DELETE SET NULL,
    UNIQUE (user_id, source_node_id, target_node_id, relation_type)
);

CREATE INDEX IF NOT EXISTS memory_edges_user_source_idx
    ON memory_edges (user_id, source_node_id);
CREATE INDEX IF NOT EXISTS memory_edges_user_target_idx
    ON memory_edges (user_id, target_node_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_sessions_user_idx
    ON chat_sessions (user_id, last_message_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
    ON chat_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS chat_messages_user_idx
    ON chat_messages (user_id, created_at);

CREATE TABLE IF NOT EXISTS chat_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    message_id UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, message_id)
);
