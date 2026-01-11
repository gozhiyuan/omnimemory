# ESP32 Camera Ingestion (Post-MVP)

This document describes a post‑MVP ingestion path for a low-power camera device (e.g., `Seeed XIAO ESP32S3 Sense`) that captures a JPEG every ~30 seconds, buffers to SD, and uploads to Lifelog when Wi‑Fi is available (e.g., phone hotspot).

For a detailed, day-by-day firmware plan (with streaming upload + NVS config baked in), see:
- `docs/esp32-ingestion/esp32_direct_webapp.md`
For audio + photo ingestion behavior and server VAD details, see:
- `docs/esp32-ingestion/esp32_audio_photo_ingestion.md`

## Goals

- Capture periodic snapshots reliably even when offline.
- Upload backlog when connected, without requiring Google OAuth on-device.
- Reuse the existing ingestion pipeline:
  - Upload bytes to Supabase Storage via presigned URL (`/storage/upload-url`)
  - Create a `source_items` record and enqueue processing (`/upload/ingest`)
- Enforce a simple device auth mechanism (`X-Device-Token`), revocable per device.
- Avoid two common real-world failure modes:
  - **OOM/heap fragmentation** from whole-file RAM buffering during upload
  - **Bricking on SD failure** if Wi‑Fi/device identity is stored on SD

## High-Level Architecture

```
ESP32 (capture -> SD queue + manifest) + NVS config
  ├─ POST /devices/upload-url (X-Device-Token)
  ├─ PUT <signed url>  ─────────────────────────▶ Supabase Storage
  └─ POST /devices/ingest (X-Device-Token) ─────▶ API -> /upload/ingest -> Celery -> processing/embeddings
```

Where:
- **NVS (flash)** stores: SSID/password, `device_token`, and a monotonic `seq` counter.
- **SD** stores: photos and a manifest queue for retries (no secrets, no device identity).

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

Response:
```json
{
  "object_key": "devices/<device_id>/<uuid>-2025-12-26T10-15-30Z.jpg",
  "upload_url": "https://<supabase-storage-signed-url>"
}
```

Implementation note: this can wrap `/storage/upload-url`, which may return `{key,url,headers}`; map `key → object_key` and `url → upload_url`.

### Ingest (enqueue processing)

`POST /devices/ingest`

Headers:
- `X-Device-Token: devtok_...`

Request:
```json
{
  "object_key": "devices/<device_id>/<uuid>-2025-12-26T10-15-30Z_000123.jpg",
  "captured_at": "2025-12-26T10:15:30Z",
  "content_type": "image/jpeg"
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
- `storage_key` (= `object_key`) and metadata passed through

Idempotency requirement:
- Include a **monotonic** `seq` in the request body and enforce `(device_id, seq)` uniqueness server-side so retries do not create duplicates.

## Devices API (Implementation Notes + Test Flow)

The ESP32-facing contract lives under `/devices/*` and uses a **device token** instead of user auth.

### Pair → Activate → Upload → Ingest (curl flow)

1) Pair (user-authenticated):
```bash
curl -X POST "$API_BASE/devices/pair" \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"esp32-proto"}'
```
Response:
```json
{ "device_id": "...", "pairing_code": "123456", "expires_at": "..." }
```

2) Activate (device uses pairing code to get token):
```bash
curl -X POST "$API_BASE/devices/activate" \
  -H "Content-Type: application/json" \
  -d '{"pairing_code":"123456"}'
```
Response:
```json
{ "device_id": "...", "device_token": "devtok_..." }
```

3) Get upload target (device-auth):
```bash
curl -X POST "$API_BASE/devices/upload-url" \
  -H "X-Device-Token: $DEVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filename":"2025-12-26T10-15-30Z_000123.jpg","content_type":"image/jpeg","seq":123}'
```
Response:
```json
{
  "upload_host": "storage.googleapis.com",
  "upload_port": 443,
  "upload_path": "/bucket/obj?X-Goog-Algorithm=...&X-Goog-Signature=...",
  "object_key": "devices/<device_id>/2025/12/26/123-2025-12-26T10-15-30Z_000123.jpg"
}
```

4) Upload bytes (HTTP PUT to host/port/path):
```bash
curl -X PUT "https://$UPLOAD_HOST$UPLOAD_PATH" \
  -H "Content-Type: image/jpeg" \
  --data-binary "@/path/to/file.jpg"
```

5) Notify ingest (device-auth, idempotent):
```bash
curl -X POST "$API_BASE/devices/ingest" \
  -H "X-Device-Token: $DEVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"object_key":"...","captured_at":"2025-12-26T10:15:30Z","seq":123,"ntp_synced":true}'
```
Response:
```json
{ "status": "queued", "item_id": "...", "task_id": "..." }
```
If duplicate:
```json
{ "status": "duplicate", "item_id": "..." }
```

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
- NVS config: `Preferences` (SSID/password, `device_token`, `seq` counter)
- Wi‑Fi provisioning: captive portal with `WebServer` + `DNSServer` (serve HTML from flash/PROGMEM)
- HTTPS: `WiFiClientSecure` + `HTTPClient`
- JSON: `ArduinoJson`
- Time: `configTime()` + NTP when online

### Storage layout (recommended)

- `/YYYYMMDD/HHMMSS_seq.jpg` (photos)
- `/manifests/<seq>.json` (upload queue; flat folder for simple scans)
- `/logs/YYYYMMDD.log` (optional; non-sensitive)

### Capture loop (offline-first)

1) Every 30s:
   - Capture JPEG from camera
   - Create a filename (ISO-ish, plus counter): `2025-12-26T10-15-30Z_000123.jpg`
   - Write to SD under `/queue/`
   - Write a manifest record under `/manifests/<seq>.json` marked `PENDING`

2) When SD is close to full:
   - Delete the oldest files in `/queue/` (ring buffer) to keep the device running unattended.

### Upload loop (when Wi‑Fi is available)

For each file in `/queue/` (oldest-first):

1) Request a presigned URL:
   - `POST /devices/upload-url` with `X-Device-Token`
2) Upload bytes to Supabase Storage:
   - `PUT <signed url>` **streaming from SD** (avoid loading the whole file into RAM)
3) Enqueue ingestion:
   - `POST /devices/ingest` with the returned `object_key` + `captured_at` (+ `seq` for idempotency)
4) Mark complete:
   - Move the file to `/uploaded/` or delete it

Streaming requirement (critical):
- Do not allocate `malloc(file.size())` and read the whole JPEG into memory; stream in chunks (e.g., 8KB) from SD to the network client.

### Retry & idempotency

- Use exponential backoff on failures (Wi‑Fi drop, 5xx).
- Make ingestion idempotent by:
  - a monotonic `seq` stored in NVS + `(device_id, seq)` uniqueness server-side
  - (optional) stable filenames or a content hash as additional protection

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
