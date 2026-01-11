#pragma once

// API base (dev) - must be reachable from the ESP32 (no localhost)
#define API_BASE_URL "http://192.168.86.221:8000"

// Wi-Fi (dev hardcode; replace with your network or NVS storage later)
#define WIFI_SSID "GGY"
#define WIFI_PASSWORD "2H2+O2=2h2o"

// Device auth (stored in NVS later)
// Device token from /devices/activate
#define DEVICE_TOKEN "T8rLQ_Y7yhjS5zmFT-vQONPhxYNeuE8ir09itQFLxwY"

// Devices API paths
#define DEVICES_PAIR_PATH "/devices/pair"
#define DEVICES_ACTIVATE_PATH "/devices/activate"
#define DEVICES_UPLOAD_URL_PATH "/devices/upload-url"
#define DEVICES_INGEST_PATH "/devices/ingest"

// ESP32 upload settings
#define UPLOAD_CHUNK_BYTES 8192

// Capture settings
#define CAPTURE_INTERVAL_MS 30000

// Upload retry settings
#define UPLOAD_MAX_ATTEMPTS 3
#define UPLOAD_BACKOFF_SEC_1 60
#define UPLOAD_BACKOFF_SEC_2 300
#define UPLOAD_BACKOFF_SEC_3 1800
#define UPLOAD_INTERVAL_MS 15000
#define UPLOAD_BATCH_SIZE 5

// Retention + telemetry
#define SD_MIN_FREE_PERCENT 15
#define SD_EMERGENCY_FREE_PERCENT 5
#define RETENTION_CHECK_INTERVAL_MS (60UL * 60UL * 1000UL)
#define TELEMETRY_INTERVAL_MS (60UL * 60UL * 1000UL)

#define FIRMWARE_VERSION "0.1.0"

// TLS dev flag (set to 0 for release)
#define ALLOW_INSECURE_TLS 1

// Audio capture (VAD + WAV)
#define AUDIO_ENABLED 1
#define AUDIO_USE_PDM 1
#define AUDIO_SAMPLE_RATE 16000
#define AUDIO_FRAME_MS 20
#define AUDIO_PREROLL_MS 1000
#define AUDIO_MIN_SEC 1
#define AUDIO_MAX_SEC 60
#define AUDIO_VAD_START_FRAMES 4
#define AUDIO_VAD_STOP_FRAMES 50
#define AUDIO_RMS_START_MULT 3.0f
#define AUDIO_RMS_STOP_MULT 1.8f
#define AUDIO_NOISE_EMA_ALPHA 0.01f
#define AUDIO_NOISE_UPDATE_MAX_MULT 1.5f
#define AUDIO_PHOTO_CLIP_ENABLED 1
#define AUDIO_PHOTO_CLIP_POST_MS 9000
#define AUDIO_HEARTBEAT_ENABLED 1
#define AUDIO_HEARTBEAT_INTERVAL_MS (5UL * 60UL * 1000UL)
#define AUDIO_HEARTBEAT_DURATION_MS 3000
