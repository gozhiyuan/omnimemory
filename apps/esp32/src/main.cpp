#include <Arduino.h>
#include <cstring>
#include "esp_camera.h"
#include "FS.h"
#include "SD_MMC.h"
#include <Preferences.h>
#include <time.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ArduinoJson.h>
#include <math.h>
#include "driver/i2s.h"

#include "board_pins.h"
#include "config.h"

static camera_config_t camera_config;
constexpr size_t kAudioFrameSamples = (AUDIO_SAMPLE_RATE * AUDIO_FRAME_MS) / 1000;
constexpr size_t kAudioPrerollSamples = (AUDIO_SAMPLE_RATE * AUDIO_PREROLL_MS) / 1000;

static void init_camera_config() {
    memset(&camera_config, 0, sizeof(camera_config));

    camera_config.pin_pwdn = CAMERA_PWDN_PIN;
    camera_config.pin_reset = CAMERA_RESET_PIN;
    camera_config.pin_xclk = CAMERA_XCLK_PIN;
    camera_config.pin_sccb_sda = CAMERA_SIOD_PIN;
    camera_config.pin_sccb_scl = CAMERA_SIOC_PIN;

    camera_config.pin_d7 = CAMERA_Y9_PIN;
    camera_config.pin_d6 = CAMERA_Y8_PIN;
    camera_config.pin_d5 = CAMERA_Y7_PIN;
    camera_config.pin_d4 = CAMERA_Y6_PIN;
    camera_config.pin_d3 = CAMERA_Y5_PIN;
    camera_config.pin_d2 = CAMERA_Y4_PIN;
    camera_config.pin_d1 = CAMERA_Y3_PIN;
    camera_config.pin_d0 = CAMERA_Y2_PIN;
    camera_config.pin_vsync = CAMERA_VSYNC_PIN;
    camera_config.pin_href = CAMERA_HREF_PIN;
    camera_config.pin_pclk = CAMERA_PCLK_PIN;

    camera_config.xclk_freq_hz = 20000000;
    camera_config.ledc_timer = LEDC_TIMER_0;
    camera_config.ledc_channel = LEDC_CHANNEL_0;

    camera_config.pixel_format = PIXFORMAT_JPEG;
    camera_config.frame_size = FRAMESIZE_SVGA;
    camera_config.jpeg_quality = 12;
    camera_config.fb_count = 2;
    camera_config.fb_location = CAMERA_FB_IN_PSRAM;
    camera_config.grab_mode = CAMERA_GRAB_LATEST;
}

static Preferences prefs;
static bool sd_ok = false;
static bool camera_ok = false;
static bool ntp_synced = false;
static bool wifi_ok = false;
static bool capture_paused = false;

static unsigned long last_capture = 0;
static unsigned long last_upload = 0;
static unsigned long last_wifi_attempt = 0;
static unsigned long last_ntp_attempt = 0;
static unsigned long last_retention_check = 0;
static unsigned long last_telemetry = 0;
static uint8_t upload_buf[UPLOAD_CHUNK_BYTES];
static bool audio_ok = false;
static bool audio_recording = false;
static File audio_file;
static String audio_filepath;
static time_t audio_start_epoch = 0;
static uint32_t audio_seq = 0;
static size_t audio_samples_written = 0;
static float noise_rms = 0.0f;
static int vad_over_count = 0;
static int vad_under_count = 0;
static bool audio_force_active = false;
static size_t audio_force_stop_samples = 0;
static bool audio_photo_clip_pending = false;
static time_t audio_photo_clip_epoch = 0;
static bool audio_heartbeat_pending = false;
static unsigned long last_audio_heartbeat = 0;
static int16_t* audio_preroll = nullptr;
static size_t preroll_index = 0;
static bool preroll_filled = false;
static int16_t audio_frame[kAudioFrameSamples];

static bool sync_time_best_effort(uint32_t timeout_ms = 8000) {
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    unsigned long start = millis();
    struct tm timeinfo;
    while (millis() - start < timeout_ms) {
        if (getLocalTime(&timeinfo, 200)) {
            return true;
        }
        delay(200);
    }
    return false;
}

static time_t now_epoch() {
    if (ntp_synced) {
        time_t now = 0;
        time(&now);
        return now;
    }
    return static_cast<time_t>(millis() / 1000);
}

static bool connect_wifi_best_effort(uint32_t timeout_ms = 10000) {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long start = millis();
    while (millis() - start < timeout_ms) {
        if (WiFi.status() == WL_CONNECTED) {
            return true;
        }
        delay(250);
    }
    return false;
}

static uint32_t get_next_seq() {
    uint32_t seq = prefs.getUInt("seq", 0);
    prefs.putUInt("seq", seq + 1);
    return seq;
}

static String build_date_folder() {
    if (!ntp_synced) {
        return "/unsynced";
    }
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    char folder[16];
    strftime(folder, sizeof(folder), "/%Y%m%d", &timeinfo);
    return String(folder);
}

static String build_filename(uint32_t seq) {
    if (!ntp_synced) {
        return String("/img_") + String(seq) + ".jpg";
    }
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    char filename[32];
    snprintf(filename, sizeof(filename), "/%02d%02d%02d_%06lu.jpg",
             timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec,
             static_cast<unsigned long>(seq));
    return String(filename);
}

static String build_audio_folder() {
    if (!ntp_synced) {
        return "/unsynced_audio";
    }
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    char folder[20];
    strftime(folder, sizeof(folder), "/audio/%Y%m%d", &timeinfo);
    return String(folder);
}

static String build_audio_filename(uint32_t seq) {
    if (!ntp_synced) {
        return String("/audio_") + String(seq) + ".wav";
    }
    time_t now;
    struct tm timeinfo;
    time(&now);
    localtime_r(&now, &timeinfo);

    char filename[32];
    snprintf(filename, sizeof(filename), "/%02d%02d%02d_%06lu.wav",
             timeinfo.tm_hour, timeinfo.tm_min, timeinfo.tm_sec,
             static_cast<unsigned long>(seq));
    return String(filename);
}

static bool ensure_audio_folder(const String& folder) {
    if (!sd_ok) return false;
    if (folder == "/unsynced_audio") {
        if (!SD_MMC.exists(folder.c_str())) {
            return SD_MMC.mkdir(folder.c_str());
        }
        return true;
    }
    if (folder.startsWith("/audio/")) {
        if (!SD_MMC.exists("/audio")) {
            if (!SD_MMC.mkdir("/audio")) {
                return false;
            }
        }
        if (!SD_MMC.exists(folder.c_str())) {
            return SD_MMC.mkdir(folder.c_str());
        }
        return true;
    }
    if (!SD_MMC.exists(folder.c_str())) {
        return SD_MMC.mkdir(folder.c_str());
    }
    return true;
}

static bool write_manifest_atomic(
    uint32_t seq,
    const String& filepath,
    time_t captured_epoch,
    const char* status,
    const char* item_type,
    const char* content_type,
    int upload_attempts,
    time_t last_attempt_epoch
) {
    if (!sd_ok) return false;
    if (!SD_MMC.exists("/manifests")) {
        SD_MMC.mkdir("/manifests");
    }

    String final_path = String("/manifests/") + String(seq) + ".json";
    String tmp_path = final_path + ".tmp";

    File f = SD_MMC.open(tmp_path.c_str(), FILE_WRITE);
    if (!f) return false;

    String payload = "{";
    payload += "\"filepath\":\"" + filepath + "\",";
    payload += "\"seq\":" + String(seq) + ",";
    payload += "\"captured_at_epoch\":" + String(static_cast<unsigned long>(captured_epoch)) + ",";
    payload += "\"status\":\"" + String(status) + "\",";
    payload += "\"item_type\":\"" + String(item_type) + "\",";
    payload += "\"content_type\":\"" + String(content_type) + "\",";
    payload += "\"upload_attempts\":" + String(upload_attempts) + ",";
    payload += "\"last_attempt_epoch\":" + String(static_cast<unsigned long>(last_attempt_epoch));
    payload += "}";

    size_t written = f.print(payload);
    f.flush();
    f.close();
    if (written == 0) {
        SD_MMC.remove(tmp_path.c_str());
        return false;
    }

    if (SD_MMC.exists(final_path.c_str())) {
        SD_MMC.remove(final_path.c_str());
    }
    if (!SD_MMC.rename(tmp_path.c_str(), final_path.c_str())) {
        SD_MMC.remove(tmp_path.c_str());
        return false;
    }
    return true;
}

struct PendingItem {
    String manifest_path;
    String filepath;
    String item_type;
    String content_type;
    uint32_t seq = 0;
    time_t captured_epoch = 0;
    int upload_attempts = 0;
    time_t last_attempt_epoch = 0;
};

static bool load_manifest(const String& manifest_path, PendingItem& out, String* status_out = nullptr) {
    File file = SD_MMC.open(manifest_path.c_str());
    if (!file) return false;

    StaticJsonDocument<384> doc;
    DeserializationError err = deserializeJson(doc, file);
    file.close();
    if (err != DeserializationError::Ok) return false;

    out.manifest_path = manifest_path;
    out.filepath = doc["filepath"] | "";
    out.item_type = doc["item_type"] | "";
    out.content_type = doc["content_type"] | "";
    out.seq = doc["seq"] | 0;
    out.captured_epoch = doc["captured_at_epoch"] | 0;
    out.upload_attempts = doc["upload_attempts"] | 0;
    out.last_attempt_epoch = doc["last_attempt_epoch"] | 0;

    if (out.item_type.isEmpty()) {
        if (out.filepath.endsWith(".wav")) {
            out.item_type = "audio";
        } else {
            out.item_type = "photo";
        }
    }
    if (out.content_type.isEmpty()) {
        if (out.item_type == "audio") {
            out.content_type = "audio/wav";
        } else {
            out.content_type = "image/jpeg";
        }
    }

    const char* status = doc["status"] | "";
    if (status_out) {
        *status_out = status;
    }
    return true;
}

static bool audio_pins_ready() {
#if AUDIO_USE_PDM
    return MIC_DATA_PIN >= 0 && MIC_CLK_PIN >= 0;
#else
    return MIC_DATA_PIN >= 0 && MIC_BCLK_PIN >= 0 && MIC_WS_PIN >= 0;
#endif
}

static void write_le16(uint8_t* out, uint16_t value) {
    out[0] = static_cast<uint8_t>(value & 0xFF);
    out[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

static void write_le32(uint8_t* out, uint32_t value) {
    out[0] = static_cast<uint8_t>(value & 0xFF);
    out[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
    out[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
    out[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

static void write_wav_header(File& file, uint32_t data_bytes) {
    uint8_t header[44];
    memset(header, 0, sizeof(header));
    memcpy(header, "RIFF", 4);
    write_le32(header + 4, 36 + data_bytes);
    memcpy(header + 8, "WAVE", 4);
    memcpy(header + 12, "fmt ", 4);
    write_le32(header + 16, 16);
    write_le16(header + 20, 1);
    write_le16(header + 22, 1);
    write_le32(header + 24, AUDIO_SAMPLE_RATE);
    uint32_t byte_rate = AUDIO_SAMPLE_RATE * 1 * 16 / 8;
    write_le32(header + 28, byte_rate);
    write_le16(header + 32, 2);
    write_le16(header + 34, 16);
    memcpy(header + 36, "data", 4);
    write_le32(header + 40, data_bytes);
    file.write(header, sizeof(header));
}

static void preroll_push(const int16_t* samples, size_t count) {
    if (!audio_preroll || kAudioPrerollSamples == 0) return;
    for (size_t i = 0; i < count; i++) {
        audio_preroll[preroll_index++] = samples[i];
        if (preroll_index >= kAudioPrerollSamples) {
            preroll_index = 0;
            preroll_filled = true;
        }
    }
}

static size_t preroll_write(File& file) {
    if (!audio_preroll || kAudioPrerollSamples == 0) return 0;
    size_t available = preroll_filled ? kAudioPrerollSamples : preroll_index;
    if (available == 0) return 0;

    size_t start = preroll_filled ? preroll_index : 0;
    size_t first_len = preroll_filled ? (kAudioPrerollSamples - start) : available;
    size_t written = 0;
    if (first_len > 0) {
        written += file.write(reinterpret_cast<const uint8_t*>(audio_preroll + start), first_len * sizeof(int16_t));
    }
    if (preroll_filled && start > 0) {
        written += file.write(reinterpret_cast<const uint8_t*>(audio_preroll), start * sizeof(int16_t));
    }
    return written / sizeof(int16_t);
}

static size_t ms_to_samples(uint32_t ms) {
    return static_cast<size_t>((static_cast<uint64_t>(AUDIO_SAMPLE_RATE) * ms) / 1000ULL);
}

static float compute_rms(const int16_t* samples, size_t count) {
    if (count == 0) return 0.0f;
    uint64_t sum = 0;
    for (size_t i = 0; i < count; i++) {
        int32_t s = samples[i];
        sum += static_cast<uint64_t>(s) * static_cast<uint64_t>(s);
    }
    float mean = static_cast<float>(sum) / static_cast<float>(count);
    return sqrtf(mean);
}

static time_t adjust_start_epoch(time_t epoch) {
    uint32_t preroll_sec = AUDIO_PREROLL_MS / 1000;
    if (preroll_sec == 0) return epoch;
    if (epoch > static_cast<time_t>(preroll_sec)) {
        return epoch - static_cast<time_t>(preroll_sec);
    }
    return epoch;
}

static bool init_audio() {
#if AUDIO_ENABLED
    if (!audio_pins_ready()) {
        Serial.println("Audio disabled: mic pins not set");
        return false;
    }

    if (kAudioPrerollSamples > 0) {
        size_t bytes = kAudioPrerollSamples * sizeof(int16_t);
        if (psramFound()) {
            audio_preroll = static_cast<int16_t*>(ps_malloc(bytes));
        } else {
            audio_preroll = static_cast<int16_t*>(malloc(bytes));
        }
        if (!audio_preroll) {
            Serial.println("Audio preroll alloc failed");
            return false;
        }
        memset(audio_preroll, 0, bytes);
    }

    i2s_config_t i2s_config = {};
    i2s_config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX
#if AUDIO_USE_PDM
                                              | I2S_MODE_PDM
#endif
    );
    i2s_config.sample_rate = AUDIO_SAMPLE_RATE;
    i2s_config.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    i2s_config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    i2s_config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    i2s_config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    i2s_config.dma_buf_count = 6;
    i2s_config.dma_buf_len = static_cast<int>(kAudioFrameSamples);
    i2s_config.use_apll = false;
    i2s_config.tx_desc_auto_clear = false;
    i2s_config.fixed_mclk = 0;

    if (i2s_driver_install(I2S_NUM_0, &i2s_config, 0, nullptr) != ESP_OK) {
        Serial.println("I2S install failed");
        return false;
    }

    i2s_pin_config_t pin_config = {};
#if AUDIO_USE_PDM
    pin_config.bck_io_num = I2S_PIN_NO_CHANGE;
    pin_config.ws_io_num = MIC_CLK_PIN;
    pin_config.data_out_num = I2S_PIN_NO_CHANGE;
    pin_config.data_in_num = MIC_DATA_PIN;
#else
    pin_config.bck_io_num = MIC_BCLK_PIN;
    pin_config.ws_io_num = MIC_WS_PIN;
    pin_config.data_out_num = I2S_PIN_NO_CHANGE;
    pin_config.data_in_num = MIC_DATA_PIN;
#endif

    if (i2s_set_pin(I2S_NUM_0, &pin_config) != ESP_OK) {
        Serial.println("I2S pin config failed");
        return false;
    }

    i2s_set_clk(I2S_NUM_0, AUDIO_SAMPLE_RATE, I2S_BITS_PER_SAMPLE_16BIT, I2S_CHANNEL_MONO);
    i2s_zero_dma_buffer(I2S_NUM_0);
    noise_rms = 0.0f;
    return true;
#else
    return false;
#endif
}

static bool write_audio_frame(const int16_t* samples, size_t count) {
    if (!audio_file) return false;
    size_t bytes = count * sizeof(int16_t);
    size_t written = audio_file.write(reinterpret_cast<const uint8_t*>(samples), bytes);
    if (written != bytes) {
        return false;
    }
    audio_samples_written += count;
    return true;
}

static void finish_audio_recording(bool keep) {
    if (!audio_recording) return;

    size_t min_samples = static_cast<size_t>(AUDIO_MIN_SEC) * AUDIO_SAMPLE_RATE;
    if (audio_samples_written < min_samples) {
        keep = false;
    }

    uint32_t data_bytes = static_cast<uint32_t>(audio_samples_written * sizeof(int16_t));
    if (audio_file) {
        audio_file.seek(0);
        write_wav_header(audio_file, data_bytes);
        audio_file.flush();
        audio_file.close();
    }

    if (!keep || audio_filepath.isEmpty()) {
        if (!audio_filepath.isEmpty() && SD_MMC.exists(audio_filepath.c_str())) {
            SD_MMC.remove(audio_filepath.c_str());
        }
    } else {
        write_manifest_atomic(audio_seq, audio_filepath, audio_start_epoch, "PENDING", "audio", "audio/wav", 0, 0);
        Serial.printf("Saved %s (%lu bytes)\n", audio_filepath.c_str(), static_cast<unsigned long>(data_bytes));
    }

    audio_recording = false;
    audio_force_active = false;
    audio_force_stop_samples = 0;
    audio_samples_written = 0;
    audio_filepath = "";
    vad_over_count = 0;
    vad_under_count = 0;
}

static bool start_audio_recording(const int16_t* samples, size_t count, time_t start_epoch, size_t force_stop_samples) {
    if (!sd_ok || capture_paused) return false;
    if (audio_recording) return false;

    audio_seq = get_next_seq();
    time_t epoch = start_epoch > 0 ? start_epoch : now_epoch();
    audio_start_epoch = adjust_start_epoch(epoch);
    audio_force_stop_samples = force_stop_samples;
    audio_force_active = force_stop_samples > 0;

    String folder = build_audio_folder();
    if (!ensure_audio_folder(folder)) {
        Serial.println("Failed to create audio folder");
        return false;
    }

    audio_filepath = folder + build_audio_filename(audio_seq);
    audio_file = SD_MMC.open(audio_filepath.c_str(), FILE_WRITE);
    if (!audio_file) {
        Serial.println("Failed to open audio file");
        audio_filepath = "";
        return false;
    }

    write_wav_header(audio_file, 0);
    audio_samples_written = 0;

    audio_samples_written += preroll_write(audio_file);
    audio_recording = true;
    vad_under_count = 0;

    if (!write_audio_frame(samples, count)) {
        finish_audio_recording(false);
        return false;
    }

    Serial.printf("Audio start seq %lu\n", static_cast<unsigned long>(audio_seq));
    return true;
}

static void audio_tick() {
#if AUDIO_ENABLED
    if (!audio_ok) return;

    size_t bytes_read = 0;
    if (i2s_read(I2S_NUM_0, audio_frame, sizeof(audio_frame), &bytes_read, portMAX_DELAY) != ESP_OK) {
        return;
    }

    size_t sample_count = bytes_read / sizeof(int16_t);
    if (sample_count == 0) return;

    float rms = compute_rms(audio_frame, sample_count);

    if (!audio_recording) {
        bool force_start = false;
        size_t force_samples = 0;
        time_t force_epoch = 0;

        if (audio_photo_clip_pending) {
            audio_photo_clip_pending = false;
            force_samples = kAudioPrerollSamples + ms_to_samples(AUDIO_PHOTO_CLIP_POST_MS);
            force_epoch = audio_photo_clip_epoch;
            force_start = true;
        } else if (audio_heartbeat_pending) {
            audio_heartbeat_pending = false;
            force_samples = kAudioPrerollSamples + ms_to_samples(AUDIO_HEARTBEAT_DURATION_MS);
            force_epoch = now_epoch();
            force_start = true;
        }

        if (force_start) {
            start_audio_recording(audio_frame, sample_count, force_epoch, force_samples);
            return;
        }

        preroll_push(audio_frame, sample_count);

        if (noise_rms <= 1.0f) {
            noise_rms = rms;
        } else if (rms < noise_rms * AUDIO_NOISE_UPDATE_MAX_MULT) {
            noise_rms = noise_rms * (1.0f - AUDIO_NOISE_EMA_ALPHA) + rms * AUDIO_NOISE_EMA_ALPHA;
        }

        if (rms > noise_rms * AUDIO_RMS_START_MULT) {
            vad_over_count++;
        } else {
            vad_over_count = 0;
        }

        if (vad_over_count >= AUDIO_VAD_START_FRAMES) {
            if (start_audio_recording(audio_frame, sample_count, now_epoch(), 0)) {
                vad_over_count = 0;
            }
        }
        return;
    }

    if (!write_audio_frame(audio_frame, sample_count)) {
        finish_audio_recording(false);
        return;
    }

    if (audio_force_active) {
        if (audio_force_stop_samples > 0 && audio_samples_written >= audio_force_stop_samples) {
            finish_audio_recording(true);
        }
        return;
    }

    if (rms < noise_rms * AUDIO_RMS_STOP_MULT) {
        vad_under_count++;
    } else {
        vad_under_count = 0;
    }

    float duration_sec = static_cast<float>(audio_samples_written) / static_cast<float>(AUDIO_SAMPLE_RATE);
    if (vad_under_count >= AUDIO_VAD_STOP_FRAMES || duration_sec >= AUDIO_MAX_SEC) {
        finish_audio_recording(true);
    }
#endif
}

static unsigned long backoff_seconds(int attempts) {
    if (attempts <= 0) return 0;
    if (attempts == 1) return UPLOAD_BACKOFF_SEC_1;
    if (attempts == 2) return UPLOAD_BACKOFF_SEC_2;
    return UPLOAD_BACKOFF_SEC_3;
}

static bool find_oldest_pending(PendingItem& out) {
    if (!sd_ok) return false;
    File root = SD_MMC.open("/manifests");
    if (!root) return false;

    bool found = false;
    time_t best_epoch = 0;
    uint32_t best_seq = 0;
    time_t now = now_epoch();

    while (File f = root.openNextFile()) {
        if (f.isDirectory()) { f.close(); continue; }
        String name = f.name();
        f.close();
        if (!name.endsWith(".json")) continue;

        PendingItem item;
        String status;
        if (!load_manifest(String("/manifests/") + name, item, &status)) continue;
        if (status != "PENDING") continue;

        if (item.upload_attempts >= UPLOAD_MAX_ATTEMPTS) {
            write_manifest_atomic(
                item.seq,
                item.filepath,
                item.captured_epoch,
                "FAILED",
                item.item_type.c_str(),
                item.content_type.c_str(),
                item.upload_attempts,
                item.last_attempt_epoch
            );
            continue;
        }

        unsigned long backoff = backoff_seconds(item.upload_attempts);
        if (backoff > 0 && (now - item.last_attempt_epoch) < (time_t)backoff) {
            continue;
        }

        bool better = false;
        if (!found) {
            better = true;
        } else if (item.captured_epoch > 0 && best_epoch > 0) {
            better = item.captured_epoch < best_epoch;
        } else if (item.captured_epoch > 0 && best_epoch == 0) {
            better = true;
        } else if (item.captured_epoch == 0 && best_epoch == 0) {
            better = item.seq < best_seq;
        }

        if (better) {
            out = item;
            found = true;
            best_epoch = item.captured_epoch;
            best_seq = item.seq;
        }
    }

    root.close();
    return found;
}

static bool find_oldest_uploaded(PendingItem& out) {
    if (!sd_ok) return false;
    File root = SD_MMC.open("/manifests");
    if (!root) return false;

    bool found = false;
    time_t best_epoch = 0;
    uint32_t best_seq = 0;

    while (File f = root.openNextFile()) {
        if (f.isDirectory()) { f.close(); continue; }
        String name = f.name();
        f.close();
        if (!name.endsWith(".json")) continue;

        PendingItem item;
        String status;
        if (!load_manifest(String("/manifests/") + name, item, &status)) continue;
        if (status != "UPLOADED") continue;

        bool better = false;
        if (!found) {
            better = true;
        } else if (item.captured_epoch > 0 && best_epoch > 0) {
            better = item.captured_epoch < best_epoch;
        } else if (item.captured_epoch > 0 && best_epoch == 0) {
            better = true;
        } else if (item.captured_epoch == 0 && best_epoch == 0) {
            better = item.seq < best_seq;
        }

        if (better) {
            out = item;
            found = true;
            best_epoch = item.captured_epoch;
            best_seq = item.seq;
        }
    }

    root.close();
    return found;
}

static uint8_t free_percent() {
    uint64_t total = SD_MMC.totalBytes();
    uint64_t used = SD_MMC.usedBytes();
    if (total == 0) return 0;
    uint64_t free_bytes = total - used;
    return static_cast<uint8_t>((free_bytes * 100) / total);
}

static void enforce_retention() {
    if (!sd_ok) return;

    uint8_t free_pct = free_percent();
    if (free_pct >= SD_MIN_FREE_PERCENT) {
        capture_paused = false;
        return;
    }

    Serial.printf("SD free %u%%, enforcing retention\n", free_pct);
    int deletions = 0;
    while (free_pct < SD_MIN_FREE_PERCENT) {
        PendingItem item;
        if (!find_oldest_uploaded(item)) {
            break;
        }

        if (SD_MMC.exists(item.filepath.c_str())) {
            SD_MMC.remove(item.filepath.c_str());
        }
        if (SD_MMC.exists(item.manifest_path.c_str())) {
            SD_MMC.remove(item.manifest_path.c_str());
        }
        deletions++;
        free_pct = free_percent();
    }

    Serial.printf("Retention removed %d items, free now %u%%\n", deletions, free_pct);
    capture_paused = (free_pct < SD_EMERGENCY_FREE_PERCENT);
    if (capture_paused) {
        Serial.println("EMERGENCY: capture paused (low SD free)");
    }
}

static int count_pending_manifests() {
    if (!sd_ok) return 0;
    File root = SD_MMC.open("/manifests");
    if (!root) return 0;

    int count = 0;
    while (File f = root.openNextFile()) {
        if (f.isDirectory()) { f.close(); continue; }
        String name = f.name();
        f.close();
        if (!name.endsWith(".json")) continue;

        PendingItem item;
        String status;
        if (!load_manifest(String("/manifests/") + name, item, &status)) continue;
        if (status == "PENDING") count++;
    }

    root.close();
    return count;
}

static void send_telemetry() {
    if (!wifi_ok || strlen(DEVICE_TOKEN) == 0) return;

    HTTPClient http;
    String url = String(API_BASE_URL) + "/devices/telemetry";
    http.begin(url);
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("Content-Type", "application/json");

    uint64_t used = SD_MMC.usedBytes();
    uint64_t total = SD_MMC.totalBytes();
    uint64_t free_bytes = total > used ? (total - used) : 0;

    StaticJsonDocument<256> payload;
    payload["uptime_seconds"] = millis() / 1000;
    payload["sd_used_mb"] = static_cast<int>(used / (1024 * 1024));
    payload["sd_free_mb"] = static_cast<int>(free_bytes / (1024 * 1024));
    payload["backlog_count"] = count_pending_manifests();
    payload["wifi_rssi"] = WiFi.RSSI();
    payload["firmware_version"] = FIRMWARE_VERSION;

    String body;
    serializeJson(payload, body);
    http.POST(body);
    http.end();
}

static int read_http_status_code(WiFiClient& client) {
    char line[96];
    size_t n = client.readBytesUntil('\n', line, sizeof(line) - 1);
    line[n] = 0;

    const char* p = strchr(line, ' ');
    if (!p) return -1;
    while (*p == ' ') p++;
    int code = atoi(p);
    return code;
}

static bool stream_upload(const PendingItem& item, const String& host, uint16_t port, const String& path) {
    File file = SD_MMC.open(item.filepath.c_str());
    if (!file) return false;

    WiFiClient* client = nullptr;
    WiFiClientSecure tls_client;
    WiFiClient plain_client;

    if (port == 443) {
#if ALLOW_INSECURE_TLS
        tls_client.setInsecure();
#endif
        tls_client.setTimeout(5000);
        client = &tls_client;
    } else {
        plain_client.setTimeout(5000);
        client = &plain_client;
    }

    if (!client->connect(host.c_str(), port)) {
        file.close();
        return false;
    }

    client->println("PUT " + path + " HTTP/1.1");
    client->println("Host: " + host);
    client->println("Content-Type: " + item.content_type);
    client->println("Content-Length: " + String(file.size()));
    client->println("Connection: close");
    client->println();

    while (file.available()) {
        size_t len = file.read(upload_buf, sizeof(upload_buf));
        if (client->write(upload_buf, len) != len) {
            file.close();
            client->stop();
            return false;
        }
    }

    file.close();
    int status_code = read_http_status_code(*client);
    client->stop();
    return status_code >= 200 && status_code < 300;
}

static bool request_upload_target(const PendingItem& item, String& host, uint16_t& port, String& path, String& object_key) {
    if (strlen(DEVICE_TOKEN) == 0) {
        Serial.println("DEVICE_TOKEN not set");
        return false;
    }

    HTTPClient http;
    String url = String(API_BASE_URL) + DEVICES_UPLOAD_URL_PATH;
    http.begin(url);
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<128> req;
    req["filename"] = item.filepath.substring(item.filepath.lastIndexOf('/') + 1);
    req["content_type"] = item.content_type;
    req["seq"] = item.seq;
    String body;
    serializeJson(req, body);

    int http_code = http.POST(body);
    if (http_code != 200) {
        Serial.printf("upload-url failed: %d\n", http_code);
        http.end();
        return false;
    }

    StaticJsonDocument<512> resp;
    DeserializationError err = deserializeJson(resp, http.getString());
    http.end();
    if (err != DeserializationError::Ok) {
        Serial.println("upload-url JSON parse failed");
        return false;
    }

    host = resp["upload_host"].as<String>();
    port = resp["upload_port"] | 443;
    path = resp["upload_path"].as<String>();
    object_key = resp["object_key"].as<String>();
    return !(host.isEmpty() || path.isEmpty() || object_key.isEmpty());
}

static bool notify_ingest(const PendingItem& item, const String& object_key) {
    HTTPClient http;
    String url = String(API_BASE_URL) + DEVICES_INGEST_PATH;
    http.begin(url);
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
    http.addHeader("Content-Type", "application/json");

    StaticJsonDocument<384> req;
    req["object_key"] = object_key;
    req["seq"] = item.seq;
    req["content_type"] = item.content_type;
    req["item_type"] = item.item_type;
    req["original_filename"] = item.filepath.substring(item.filepath.lastIndexOf('/') + 1);
    req["ntp_synced"] = ntp_synced;
    if (ntp_synced && item.captured_epoch > 0) {
        struct tm timeinfo;
        gmtime_r(&item.captured_epoch, &timeinfo);
        char iso[32];
        strftime(iso, sizeof(iso), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
        req["captured_at"] = iso;
    }

    String body;
    serializeJson(req, body);
    int http_code = http.POST(body);
    String response = http.getString();
    http.end();

    if (http_code == 200) {
        if (response.indexOf("\"duplicate\"") >= 0) {
            return true;
        }
        return true;
    }
    Serial.printf("ingest failed: %d\n", http_code);
    return false;
}

static void update_manifest_status(const PendingItem& item, const char* status, int attempts, time_t last_attempt_epoch) {
    write_manifest_atomic(
        item.seq,
        item.filepath,
        item.captured_epoch,
        status,
        item.item_type.c_str(),
        item.content_type.c_str(),
        attempts,
        last_attempt_epoch
    );
}

static bool upload_one_pending() {
    if (!sd_ok || !wifi_ok) return false;

    PendingItem item;
    if (!find_oldest_pending(item)) {
        return false;
    }

    time_t attempt_epoch = now_epoch();
    update_manifest_status(item, "PENDING", item.upload_attempts + 1, attempt_epoch);

    String host, path, object_key;
    uint16_t port = 443;
    if (!request_upload_target(item, host, port, path, object_key)) {
        if (item.upload_attempts + 1 >= UPLOAD_MAX_ATTEMPTS) {
            update_manifest_status(item, "FAILED", item.upload_attempts + 1, attempt_epoch);
        } else {
            update_manifest_status(item, "PENDING", item.upload_attempts + 1, attempt_epoch);
        }
        return false;
    }

    if (!stream_upload(item, host, port, path)) {
        if (item.upload_attempts + 1 >= UPLOAD_MAX_ATTEMPTS) {
            update_manifest_status(item, "FAILED", item.upload_attempts + 1, attempt_epoch);
        } else {
            update_manifest_status(item, "PENDING", item.upload_attempts + 1, attempt_epoch);
        }
        return false;
    }

    if (!notify_ingest(item, object_key)) {
        if (item.upload_attempts + 1 >= UPLOAD_MAX_ATTEMPTS) {
            update_manifest_status(item, "FAILED", item.upload_attempts + 1, attempt_epoch);
        } else {
            update_manifest_status(item, "PENDING", item.upload_attempts + 1, attempt_epoch);
        }
        return false;
    }

    update_manifest_status(item, "UPLOADED", item.upload_attempts + 1, attempt_epoch);
    Serial.printf("Uploaded seq %lu\n", static_cast<unsigned long>(item.seq));
    return true;
}

static void upload_batch() {
    for (int i = 0; i < UPLOAD_BATCH_SIZE; i++) {
        if (!upload_one_pending()) {
            break;
        }
        delay(10);
    }
}

static bool capture_and_save() {
    if (!sd_ok || !camera_ok) return false;
    if (capture_paused) return false;

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("Camera capture failed");
        return false;
    }

    uint32_t seq = get_next_seq();
    String folder = build_date_folder();
    if (!SD_MMC.exists(folder.c_str())) {
        SD_MMC.mkdir(folder.c_str());
    }

    String filepath = folder + build_filename(seq);
    File file = SD_MMC.open(filepath.c_str(), FILE_WRITE);
    if (!file) {
        Serial.println("Failed to open file on SD");
        esp_camera_fb_return(fb);
        return false;
    }

    size_t written = file.write(fb->buf, fb->len);
    file.close();
    esp_camera_fb_return(fb);

    if (written != fb->len) {
        Serial.println("Short write to SD");
        return false;
    }

    time_t captured_epoch = now_epoch();
    write_manifest_atomic(seq, filepath, captured_epoch, "PENDING", "photo", "image/jpeg", 0, 0);

    Serial.printf("Saved %s (%d bytes)\n", filepath.c_str(), (int)written);
#if AUDIO_ENABLED
    if (audio_ok && AUDIO_PHOTO_CLIP_ENABLED && !audio_recording) {
        audio_photo_clip_pending = true;
        audio_photo_clip_epoch = captured_epoch;
    }
#endif
    return true;
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n[ESP32] Day 6: photo + audio -> SD + upload");

    init_camera_config();

    if (!psramFound()) {
        Serial.println("PSRAM not found, lowering frame size.");
        camera_config.frame_size = FRAMESIZE_VGA;
        camera_config.fb_count = 1;
        camera_config.fb_location = CAMERA_FB_IN_DRAM;
    }

    prefs.begin("lifelog", false);

    esp_err_t cam_err = esp_camera_init(&camera_config);
    if (cam_err != ESP_OK) {
        Serial.printf("Camera init failed: 0x%x\n", cam_err);
        camera_ok = false;
    } else {
        camera_ok = true;
    }

    // SD_MMC in 1-bit mode for stability on small boards.
    SD_MMC.setPins(
        SD_MMC_CLK_PIN,
        SD_MMC_CMD_PIN,
        SD_MMC_D0_PIN,
        SD_MMC_D1_PIN,
        SD_MMC_D2_PIN,
        SD_MMC_D3_PIN
    );
    if (!SD_MMC.begin("/sdcard", true)) {
        Serial.println("SD_MMC mount failed");
        sd_ok = false;
    } else {
        sd_ok = true;
    }

#if AUDIO_ENABLED
    audio_ok = init_audio();
    if (audio_ok) {
        Serial.println("Audio init ok");
    } else {
        Serial.println("Audio init failed");
    }
#endif

    // Capture one immediately on boot.
    capture_and_save();

    wifi_ok = connect_wifi_best_effort();
    if (wifi_ok) {
        Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("WiFi connect failed");
    }

    ntp_synced = wifi_ok ? sync_time_best_effort() : false;
    Serial.printf("NTP sync: %s\n", ntp_synced ? "ok" : "failed");
}

void loop() {
    audio_tick();
    unsigned long now = millis();

    if (!wifi_ok && !audio_recording && now - last_wifi_attempt >= 10000) {
        wifi_ok = connect_wifi_best_effort(200);
        last_wifi_attempt = now;
        if (wifi_ok) {
            Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
        }
    }

    if (wifi_ok && !ntp_synced && !audio_recording && now - last_ntp_attempt >= 15000) {
        ntp_synced = sync_time_best_effort(500);
        last_ntp_attempt = now;
        Serial.printf("NTP sync: %s\n", ntp_synced ? "ok" : "failed");
    }

    if (now - last_capture >= CAPTURE_INTERVAL_MS) {
        capture_and_save();
        last_capture = now;
    }

#if AUDIO_ENABLED
    if (audio_ok && AUDIO_HEARTBEAT_ENABLED) {
        if (!audio_recording && !audio_heartbeat_pending &&
            now - last_audio_heartbeat >= AUDIO_HEARTBEAT_INTERVAL_MS) {
            audio_heartbeat_pending = true;
            last_audio_heartbeat = now;
        }
    }
#endif

    if (!audio_recording && now - last_upload >= UPLOAD_INTERVAL_MS) {
        upload_batch();
        last_upload = now;
    }

    if (!audio_recording && now - last_retention_check >= RETENTION_CHECK_INTERVAL_MS) {
        enforce_retention();
        last_retention_check = now;
    }

    if (!audio_recording && now - last_telemetry >= TELEMETRY_INTERVAL_MS) {
        send_telemetry();
        last_telemetry = now;
    }

    delay(5);
}
