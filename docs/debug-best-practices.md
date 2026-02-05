# Debug Best Practices

This document collects repeatable debug steps for common demo ingestion, timeline, and thumbnail issues. Keep it handy when data looks inconsistent across the API, DB, and UI.

**Safety**
1. Never paste real API keys or tokens into logs, docs, or chats. Rotate any key that was exposed.
2. Prefer read-only SQL first. Only run `UPDATE`/`DELETE` after you identify the root cause.
3. When auth is enabled, always include a valid bearer token in `curl` calls.

**Quick Health Checks**
1. API reachable:
```bash
curl -i "http://localhost:8000/health"
```
2. Auth check (expect `401` if missing token):
```bash
curl -i "http://localhost:8000/timeline/items?limit=1"
```
3. Services up:
```bash
docker compose ps
```

**Auth + Token Debug**
1. Create a key in the UI: Settings → API Keys.
2. Use it with curl:
```bash
TOKEN="omni_sk_...redacted..."
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/timeline/items?limit=10"
```
3. If `401`, the key is invalid/expired/revoked or auth settings are wrong.

**Timeline vs Items Debug**
1. Timeline (grouped by day):
```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/timeline?start_date=2026-02-02&end_date=2026-02-02&tz_offset_minutes=480&limit=600"
```
2. Items list for a day:
```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/timeline/items?start_date=2026-02-02&end_date=2026-02-02&tz_offset_minutes=480&limit=200"
```
3. If an item is missing from a day, check `event_time_utc` in DB.

**Event Time Debug**
1. Inspect a specific item:
```bash
docker exec -i lifelog-postgres psql -U lifelog -d lifelog \
  -c "select id, captured_at, event_time_utc, event_time_source from source_items where id='<ITEM_ID>';"
```
2. If `event_time_utc` is old (e.g., metadata), it won’t land on the intended day.
3. Demo fix (set `event_time_utc` to `captured_at`):
```bash
docker exec -i lifelog-api /app/.venv/bin/python -m app.scripts.fix_demo_event_times --provider demo --all
```

**Episode Preview Debug**
1. Episode previews are derived from the day’s items. If an item’s `event_time_utc` is wrong, the episode card will show a blank thumbnail.
2. Verify the item appears in the day list (same date and tz offset).
3. After fixing event times, re-run episode backfill only if previews are still blank:
```bash
docker exec -i lifelog-api /app/.venv/bin/python - <<'PY'
from app.tasks.episodes import backfill_episodes
backfill_episodes(user_id="YOUR_USER_ID", limit=500, offset=0, only_missing=False)
print("done")
PY
```

**Video Thumbnails / Posters**
1. Check keyframes in DB:
```bash
docker exec -i lifelog-postgres psql -U lifelog -d lifelog \
  -c "select si.id, da.payload->>'status' as keyframe_status, (da.payload->'poster'->>'storage_key') as poster_key \
      from source_items si \
      left join derived_artifacts da on da.source_item_id=si.id and da.artifact_type='keyframes' \
      where si.item_type='video' and si.provider='demo' \
      order by si.created_at desc;"
```
2. Confirm the API returns `poster_url`:
```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/timeline/items?limit=500&tz_offset_minutes=480" \
  | jq '.items[] | select(.item_type=="video") | {id, poster_url}'
```
3. Check the signed URL is reachable:
```bash
curl -I "http://localhost:9000/originals/users/.../poster/poster.jpg?AWSAccessKeyId=..."
```

**Provider Filter Caveat**
`/timeline` and `/timeline/items` filter `provider` via `data_connections.provider`. Demo uploads typically have no connection, so `provider=demo` can return zero items. Omit the filter unless you use data connections.

**Timezone Alignment**
1. Always pass `tz_offset_minutes` to timeline/search/dashboard calls.
2. Use the same offset for summaries and episode generation.
3. Verify user settings if items look off by a day:
```bash
docker exec -i lifelog-postgres psql -U lifelog -d lifelog \
  -c "select user_id, settings from user_settings where user_id='YOUR_USER_ID';"
```

**Reprocessing vs Backfill**
1. `backfill_pipeline` re-runs the media pipeline for existing items.
2. `backfill_episodes` re-groups items into episodes.
3. If you only fixed event times, prefer episode backfill + daily summary refresh.

**Daily Summary Refresh**
```bash
docker exec -i lifelog-api /app/.venv/bin/python - <<'PY'
from datetime import date, timedelta
from app.tasks.episodes import update_daily_summary

user_id = "YOUR_USER_ID"
tz_offset_minutes = 480
start = date(2026, 1, 20)
end = date(2026, 2, 4)
d = start
while d <= end:
    update_daily_summary(user_id, d.isoformat(), tz_offset_minutes)
    d += timedelta(days=1)
print("done")
PY
```

**Common Gotchas**
1. Using the wrong user id (OIDC vs default local).
2. Forgetting `tz_offset_minutes` in requests.
3. Relying on metadata timestamps that override demo capture dates.
4. Provider filter excludes demo uploads.
5. Signed URLs are valid, but the browser can’t reach the storage host.
