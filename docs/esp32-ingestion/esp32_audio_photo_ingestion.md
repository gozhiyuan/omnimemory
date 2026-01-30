# ESP32 Photo + Audio Ingestion (Details)

This document describes how the ESP32 firmware captures **photos** and **audio** and how those assets flow through the `/devices/*` API into the server pipeline (VAD + ASR + indexing).

## Scope

- Photos: periodic JPEG capture -> SD -> upload -> ingest.
- Audio: on-device RMS VAD -> WAV clips -> SD -> upload -> ingest.
- Extra audio clips:
  - Photo-adjacent clips (pre-roll + post window).
  - Ambient heartbeat clips (short clips at a fixed interval).

## Firmware Flow

### Photo capture

1) Capture JPEG to SD (`/YYYYMMDD/HHMMSS_seq.jpg` or `/unsynced/img_seq.jpg`).
2) Write manifest entry (`/manifests/<seq>.json`).
3) Upload worker retries until success (or failure after max attempts).

### Audio capture (VAD)

1) Read 20ms PCM frames (16 kHz, 16-bit mono).
2) Maintain a pre-roll ring buffer (default 1000ms).
3) Start recording when RMS exceeds threshold for `AUDIO_VAD_START_FRAMES`.
4) Stop when RMS falls below threshold for `AUDIO_VAD_STOP_FRAMES` or `AUDIO_MAX_SEC`.
5) Save WAV to SD:
   - `/audio/YYYYMMDD/HHMMSS_seq.wav`
   - or `/unsynced_audio/audio_seq.wav` when NTP is not synced.
6) Write manifest entry with `item_type=audio` + `content_type=audio/wav`.

### Photo-adjacent clips

When a photo is captured and audio is idle:
1) Trigger a forced audio clip with duration:
   - `AUDIO_PREROLL_MS + AUDIO_PHOTO_CLIP_POST_MS`
2) The clip uses the pre-roll buffer (pre-photo audio) plus post window.

### Ambient heartbeat clips

When audio is idle, every `AUDIO_HEARTBEAT_INTERVAL_MS`:
1) Trigger a forced audio clip of `AUDIO_HEARTBEAT_DURATION_MS`.

## Manifest Format (SD)

Each capture writes `/manifests/<seq>.json`:

```json
{
  "filepath": "/audio/20260110/135901_000123.wav",
  "seq": 123,
  "captured_at_epoch": 1768043941,
  "status": "PENDING",
  "item_type": "audio",
  "content_type": "audio/wav",
  "upload_attempts": 0,
  "last_attempt_epoch": 0
}
```

`status` is one of: `PENDING`, `UPLOADED`, `FAILED`.

## Device Endpoints Used

- `POST /devices/upload-url`
  - Request: `filename`, `content_type`, `seq`
  - Response: `upload_host`, `upload_port`, `upload_path`, `object_key`
- `PUT upload_host/upload_path` with `Content-Type`
- `POST /devices/ingest`
  - Request: `object_key`, `seq`, `content_type`, `item_type`, `captured_at`, `ntp_synced`

All device endpoints use `X-Device-Token`.

## Server Pipeline (Audio)

1) Ingest creates `source_items` with `item_type=audio`.
2) Pipeline probes metadata and enforces duration limits (`AUDIO_MAX_DURATION_SEC`).
3) Server-side VAD runs with `ffmpeg` `silencedetect` to find speech segments.
4) Speech segments are chunked and sent to the audio understanding provider (ASR + context).
5) Transcripts + contexts are stored and indexed.

### VAD settings

Set in environment (see `.env.dev.example`):

```
AUDIO_VAD_ENABLED=true
AUDIO_VAD_SILENCE_DB=-35
AUDIO_VAD_MIN_SILENCE_SEC=0.4
AUDIO_VAD_PADDING_SEC=0.15
AUDIO_VAD_MIN_SEGMENT_SEC=0.8
```

VAD runs for **all** audio items under `AUDIO_MAX_DURATION_SEC`. Audio longer than that is rejected before VAD.

## Firmware Knobs

Config lives in `apps/esp32/include/config.h`:

- `AUDIO_SAMPLE_RATE` (16 kHz recommended).
- `AUDIO_PREROLL_MS` (default 1000ms).
- `AUDIO_MIN_SEC` / `AUDIO_MAX_SEC`.
- `AUDIO_RMS_START_MULT` / `AUDIO_RMS_STOP_MULT`.
- `AUDIO_PHOTO_CLIP_POST_MS`.
- `AUDIO_HEARTBEAT_INTERVAL_MS` / `AUDIO_HEARTBEAT_DURATION_MS`.

Pins are defined in `apps/esp32/include/board_pins.h`:

```
#define MIC_CLK_PIN 42
#define MIC_DATA_PIN 41
```

## Testing Checklist

1) Flash firmware and open serial monitor.
2) Confirm audio init:
   - `Audio init ok`
3) Speak near the mic:
   - Expect a WAV file under `/audio/...`.
4) Capture a photo:
   - Expect a short photo-adjacent WAV clip.
5) Wait for heartbeat interval:
   - Expect a short ambient clip.
6) Verify uploads and ingestion in the web app.

## Troubleshooting

- No audio files:
  - Check mic pins (`MIC_CLK_PIN=42`, `MIC_DATA_PIN=41`).
  - Ensure `AUDIO_USE_PDM=1`.
  - Lower `AUDIO_RMS_START_MULT`.
- Too many clips:
  - Increase `AUDIO_RMS_START_MULT`.
  - Increase `AUDIO_VAD_MIN_SEGMENT_SEC`.
- Server VAD not running:
  - Verify `ffmpeg`/`ffprobe` installed.
  - Ensure `AUDIO_VAD_ENABLED=true`.
