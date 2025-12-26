# ESP32 Camera Ingestion (Post-MVP)

This document describes a post‑MVP ingestion path for a low-power camera device (e.g., `Seeed XIAO ESP32S3 Sense`) that captures a JPEG every ~30 seconds, buffers to SD, and uploads to Lifelog when Wi‑Fi is available (e.g., phone hotspot).

## Goals

- Capture periodic snapshots reliably even when offline.
- Upload backlog when connected, without requiring Google OAuth on-device.
- Reuse the existing ingestion pipeline:
  - Upload bytes to Supabase Storage via presigned URL (`/storage/upload-url`)
  - Create a `source_items` record and enqueue processing (`/upload/ingest`)
- Enforce a simple device auth mechanism (`X-Device-Token`), revocable per device.

## High-Level Architecture

```
ESP32 (capture -> SD queue)
  ├─ POST /devices/upload-url (X-Device-Token)
  ├─ PUT <signed url>  ─────────────────────────▶ Supabase Storage
  └─ POST /devices/ingest (X-Device-Token) ─────▶ API -> /upload/ingest -> Celery -> processing/embeddings
```

## Backend API (Proposed)

These endpoints are wrappers around the existing `/storage/*` and `/upload/ingest` endpoints so a device:
- never sends `user_id` directly
- only uploads under a safe prefix like `devices/{device_id}/...`

### Auth header

- `X-Device-Token: <token>`
- Token is generated server-side and stored hashed; it is never derived from user credentials.

### Device management

1) Pair (user-authenticated; called from web app)

`POST /devices/pair`

Response:
```json
{
  "device_id": "uuid",
  "pairing_code": "short-lived-code"
}
```

2) Activate (unauthenticated except for pairing code; called by device during setup)

`POST /devices/activate`

Request:
```json
{ "pairing_code": "..." }
```

Response:
```json
{
  "device_id": "uuid",
  "device_token": "devtok_..."
}
```

### Upload URL

`POST /devices/upload-url`

Headers:
- `X-Device-Token: devtok_...`

Request:
```json
{
  "filename": "2025-12-26T10-15-30Z.jpg",
  "content_type": "image/jpeg"
}
```

Response (same shape as `/storage/upload-url`):
```json
{
  "key": "devices/<device_id>/<uuid>-2025-12-26T10-15-30Z.jpg",
  "url": "https://<supabase-storage-signed-url>",
  "headers": {
    "content-type": "image/jpeg"
  }
}
```

### Ingest (enqueue processing)

`POST /devices/ingest`

Headers:
- `X-Device-Token: devtok_...`

Request:
```json
{
  "storage_key": "devices/<device_id>/<uuid>-2025-12-26T10-15-30Z.jpg",
  "captured_at": "2025-12-26T10:15:30Z",
  "content_type": "image/jpeg",
  "original_filename": "2025-12-26T10-15-30Z.jpg"
}
```

Response:
```json
{
  "item_id": "uuid",
  "task_id": "celery-task-id",
  "status": "queued"
}
```

Implementation note: this endpoint should call the existing `/upload/ingest` internals with:
- `user_id` derived from the device record
- `item_type="photo"`
- `storage_key` and metadata passed through

## Data Model (Proposed)

Table: `devices`

- `id` (UUID, pk)
- `user_id` (UUID, fk)
- `name` (text, optional)
- `device_token_hash` (text) – store only a hash (e.g., SHA256(token + server_secret))
- `created_at` (timestamp)
- `last_seen_at` (timestamp, nullable)
- `revoked_at` (timestamp, nullable)

Optional:
- `firmware_version` (text)
- `metadata` (jsonb)

## Firmware Behavior (Arduino)

### Key components

- Camera capture: `esp_camera.h` (board-specific camera pin config via Seeed examples)
- SD card: `SD_MMC` (Sense expansion board)
- Wi‑Fi provisioning: `WiFiManager` (captive portal to set SSID/password for home + hotspot)
- HTTPS: `WiFiClientSecure` + `HTTPClient`
- JSON: `ArduinoJson`
- Time: `configTime()` + NTP when online

### Capture loop (offline-first)

1) Every 30s:
   - Capture JPEG from camera
   - Create a filename (ISO-ish, plus counter): `2025-12-26T10-15-30Z_000123.jpg`
   - Write to SD under `/queue/`

2) When SD is close to full:
   - Delete the oldest files in `/queue/` (ring buffer) to keep the device running unattended.

### Upload loop (when Wi‑Fi is available)

For each file in `/queue/` (oldest-first):

1) Request a presigned URL:
   - `POST /devices/upload-url` with `X-Device-Token`
2) Upload bytes to Supabase Storage:
   - `PUT <signed url>` streaming from SD (avoid loading the whole file into RAM)
3) Enqueue ingestion:
   - `POST /devices/ingest` with the returned `key` + `captured_at`
4) Mark complete:
   - Move the file to `/uploaded/` or delete it

### Retry & idempotency

- Use exponential backoff on failures (Wi‑Fi drop, 5xx).
- Make ingestion idempotent by:
  - stable filenames (timestamp + counter)
  - server-side de-dupe (optional) using `(device_id, original_filename)` in metadata or a content hash when available

## Wi‑Fi Outside Home (Phone Hotspot)

- The ESP32 can only auto-upload on networks it can join.
- For “outside” uploads, the practical path is a phone hotspot:
  - hotspot SSID/password saved via the captive portal once
  - ensure hotspot provides a 2.4GHz network

## Security Notes

- Treat `device_token` like an API key:
  - rotateable (issue new token, revoke old)
  - revocable per device
  - rate-limited per device and per user
- Avoid putting user OAuth tokens (Google/Supabase keys) on the ESP32.
- Prefer TLS validation on the device in production; avoid `setInsecure()` outside early prototypes.

## Operational Notes

- Cost: 1 photo / 30s ≈ 2,880 photos/day/device. Plan retention and downscaling.
- Privacy: consider default “pause” and a physical indicator (LED) when capturing.

