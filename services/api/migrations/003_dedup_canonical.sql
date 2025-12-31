-- 003_dedup_canonical.sql
-- Canonical linkage for deduped source items.

ALTER TABLE source_items
    ADD COLUMN IF NOT EXISTS canonical_item_id UUID REFERENCES source_items(id) ON DELETE SET NULL;

UPDATE source_items
    SET canonical_item_id = id
    WHERE canonical_item_id IS NULL;

CREATE INDEX IF NOT EXISTS source_items_canonical_idx
    ON source_items (canonical_item_id);
