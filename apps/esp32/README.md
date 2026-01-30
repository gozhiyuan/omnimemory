# ESP32 Firmware (PlatformIO)

This folder is the ESP32 firmware workspace. Day 6 goal: **photo + audio -> SD -> upload + retention + telemetry**.

## Build/Flash

```bash
cd apps/esp32
pio run -t upload
pio device monitor
```

## Day 5 Validation

- Insert a FAT32 microSD.
- Flash the firmware.
- The device writes `/YYYYMMDD/HHMMSS_seq.jpg` (or `/unsynced/img_seq.jpg`) to the SD card.
- It also writes `/manifests/<seq>.json`.
- If Wi‑Fi + API are reachable, it uploads and marks the manifest as `UPLOADED`.
- Upload retries use backoff and mark `FAILED` after 3 attempts.
- Upload worker sends up to `UPLOAD_BATCH_SIZE` items per interval.
- Retention deletes oldest `UPLOADED` items when SD free space is low.
- Telemetry posts basic stats to `/devices/telemetry` hourly.
- Remove SD and open the JPEG on your computer.

If you see `SDMMCFS: some SD pins are not set`, verify the SD_MMC pin defines in
`apps/esp32/include/board_pins.h`.

## Wi‑Fi Duty Cycle (Power)

When `WIFI_DUTY_CYCLE_ENABLED=1`, Wi‑Fi turns on during upload windows:
- `WIFI_DUTY_CYCLE_INTERVAL_MS`: scheduled window cadence (default 1 hour).
- `WIFI_DUTY_CYCLE_WINDOW_MS`: scheduled window duration (default 2 minutes).
- `WIFI_DUTY_CYCLE_MAX_WINDOW_MS`: backlog window cap (default 30 minutes).
- `WIFI_DUTY_CYCLE_COOLDOWN_MS`: cooldown between backlog windows (default 10 minutes).

If backlog exists, the device can open a longer window (up to `MAX_WINDOW_MS`), then cool down
and retry later. Uploads, NTP sync, and telemetry only happen when Wi‑Fi is on.

## Audio MVP (VAD + WAV)

- Set mic pins in `apps/esp32/include/board_pins.h`.
  - PDM: set `MIC_DATA_PIN` + `MIC_CLK_PIN`, leave `MIC_BCLK_PIN`/`MIC_WS_PIN` as `-1`.
  - I2S: set `MIC_DATA_PIN` + `MIC_BCLK_PIN` + `MIC_WS_PIN`, leave `MIC_CLK_PIN` as `-1`.
- Set `AUDIO_USE_PDM` in `apps/esp32/include/config.h` to match your mic.
- Tune thresholds in `apps/esp32/include/config.h` if triggers are too aggressive.
- Audio clips are saved as `/audio/YYYYMMDD/HHMMSS_seq.wav` (or `/unsynced_audio/audio_seq.wav`).
- Manifests include `item_type=audio` and upload via `/devices/*`.
- Photo-adjacent clips: capture triggers a forced clip of `AUDIO_PREROLL_MS + AUDIO_PHOTO_CLIP_POST_MS`.
- Ambient heartbeat: records a short clip every `AUDIO_HEARTBEAT_INTERVAL_MS`.

## Endpoints Used

The device will later call these endpoints:
- `POST /devices/pair` (user auth)
- `POST /devices/activate` (pairing code -> device token)
- `POST /devices/upload-url` (device auth)
- `POST /devices/ingest` (device auth, idempotent by seq)

See `docs/esp32-ingestion/esp32_direct_webapp.md` for the full plan.
