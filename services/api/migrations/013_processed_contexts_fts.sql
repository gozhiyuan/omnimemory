CREATE INDEX IF NOT EXISTS processed_contexts_fts_idx
ON processed_contexts
USING GIN (
  to_tsvector(
    'english',
    coalesce(title, '') || ' ' || coalesce(summary, '') || ' ' || coalesce(keywords::text, '')
  )
);
