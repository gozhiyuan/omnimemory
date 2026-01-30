CREATE TABLE IF NOT EXISTS devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NULL,
    device_token_hash TEXT NULL,
    token_salt TEXT NOT NULL,
    pairing_code_hash TEXT NULL,
    pairing_code_expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    last_seen_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS devices_token_hash_idx ON devices(device_token_hash);
CREATE UNIQUE INDEX IF NOT EXISTS devices_pairing_code_hash_idx ON devices(pairing_code_hash);
CREATE INDEX IF NOT EXISTS devices_user_id_idx ON devices(user_id);

ALTER TABLE source_items ADD COLUMN IF NOT EXISTS device_id UUID REFERENCES devices(id) ON DELETE SET NULL;
ALTER TABLE source_items ADD COLUMN IF NOT EXISTS device_seq INTEGER NULL;

CREATE UNIQUE INDEX IF NOT EXISTS source_items_device_seq_idx ON source_items(device_id, device_seq);
CREATE INDEX IF NOT EXISTS source_items_device_id_idx ON source_items(device_id);
