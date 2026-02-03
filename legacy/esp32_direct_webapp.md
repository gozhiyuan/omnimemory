# Lifelog ESP32 - Direct Webapp Integration (2-Week Plan - Detailed, Risk-Reduced)

> **Assumption:** Backend `/devices/*` endpoints + manual upload are already working  
> **Goal:** ESP32 captures photos → buffers safely → auto-uploads → appears in timeline  
> **Key fixes baked in:** Streaming upload (no OOM), config in NVS (no SD dependency), schedule reordered (prove pipeline before captive portal).

---

## Critical Design Decisions

### What we’re doing right
1. **Presigned URL uploads** (simple `PUT`, not multipart form)
2. **Offline-first buffering** on SD with a **manifest queue** for retries
3. **Physical button** to enter setup mode (no “always captive portal”)
4. **Server-driven config** (tune without reflashing)

### Critical fixes (must do)
1. **Streaming upload (no whole-file RAM buffer).** Never do `malloc(file.size())` for uploads.
2. **Config stored in NVS (flash), not SD.** SD is only for media/manifests/logs.
3. **De-risk schedule.** Get **Camera → Cloud** working before captive portal UI.

---

## Hardware + Materials

### You have
- XIAO ESP32S3 Sense (camera + microSD slot)
- USB‑C cable for flashing

### Order immediately (prototype)
| Item | Spec | Why |
|------|------|-----|
| microSD | 32GB, Class 10, FAT32 | Media buffer + manifests (not config) |
| LiPo (runtime prototype) | 3.7V 3000mAh, JST‑PH 2.0 | Power profiling / “all-day” test |
| LiPo (wearable) | 3.7V 500–1000mAh, JST‑PH 2.0 | Form-factor realism (weight/comfort) |
| Inline switch | JST‑PH male/female | Hard power off |
| Momentary button | 6x6mm | Setup mode trigger |
| LEDs + resistors | 220Ω (LED), 10kΩ (pullup) | Status indicator |
| Enclosure | small box | Protection |

### Hardware reality checks
- **Battery size vs wearability:** 3000mAh packs are phone-sized/heavy; great for profiling, not great for a pendant/clip.
- **Thermals:** camera + Wi‑Fi transmit can heat the ESP32‑S3; avoid a fully sealed case (add vents or allow the RF shield to conduct heat).

---

## Backend API Requirements (Device-Friendly Wrapper)

These are the minimum endpoints the firmware needs. The important properties are:
- **`X-Device-Token` auth** (device API key style)
- **idempotent ingest** (retries never create duplicate timeline items)
- **presigned PUT** uploads

```text
# 1) Webapp generates pairing code (user-authenticated)
POST /devices/pair
Headers: Authorization: Bearer <user_token>
Returns: { device_id, pairing_code, expires_at }

# 2) Device activates (idempotent)
POST /devices/activate
Body: { pairing_code }
Returns: { device_id, device_token }

# 3) Get presigned upload URL (device-auth)
POST /devices/upload-url
Headers: X-Device-Token: <device_token>
Body: { filename, seq? }
Returns: { upload_host, upload_port, upload_path, object_key }

# 4) Notify upload complete (device-auth, MUST be idempotent)
POST /devices/ingest
Headers: X-Device-Token: <device_token>
Body: { object_key, captured_at, seq, ntp_synced }
Idempotency key: (device_id, seq) unique constraint

# 5) Telemetry (optional but recommended)
POST /devices/telemetry

# 6) Remote config (optional but recommended)
GET /devices/config
```

Suggested success codes:
- Presigned `PUT`: accept **any 2xx** (providers vary).

High-ROI backend requirement (avoids embedded URL parsing bugs):
- `/devices/upload-url` should return **pre-split upload target fields**:
  - `upload_host` (no scheme, no path)
  - `upload_port` (usually 443)
  - `upload_path` (starts with `/` and includes the full `?query=...` string, already URL-encoded; firmware must send as-is)
  - `object_key`

Example:
```json
{
  "upload_host": "storage.googleapis.com",
  "upload_port": 443,
  "upload_path": "/bucket/obj?X-Goog-Algorithm=...&X-Goog-Signature=...",
  "object_key": "devices/dev_abc123/20251202/..."
}
```

Idempotency improvement (optional but recommended):
- Allow passing `seq` into `/devices/upload-url` and make it **idempotent-ish** by returning a stable `object_key` for the same `(device_id, seq)` (you can re-issue a fresh presigned URL on each call).

Stable errors (device behavior):
- If `/devices/ingest` reports the item is a duplicate (e.g., `409` or `{ "status": "duplicate" }`), treat it as **success** on the device and mark the manifest `UPLOADED`.

---

## ESP32 Firmware Project Structure (PlatformIO Recommended)

```
lifelog-esp32/
├── platformio.ini
├── include/
│   ├── board_pins.h          # single source of truth for pins
│   └── config.h              # API URLs, constants
├── lib/
│   ├── LifelogNVS/           # NVS config + seq counter (flash)
│   ├── LifelogCamera/        # capture
│   ├── LifelogStorage/       # SD photos + manifests + logs
│   ├── LifelogUpload/        # presigned URL + streaming PUT + ingest notify
│   ├── LifelogWiFi/          # connect + NTP
│   └── LifelogSetup/         # captive portal (HTML served from PROGMEM)
├── src/
│   └── main.cpp
└── README.md
```

---

## Code Patterns (Corrected)

### 1) NVS config (source of truth in flash)

```cpp
#include <Preferences.h>

class LifelogNVS {
public:
  bool begin() { return prefs.begin("lifelog", false); }

  bool saveWiFi(const char* ssid, const char* password) {
    prefs.putString("ssid", ssid);
    prefs.putString("password", password);
    return true;
  }

  bool loadWiFi(String& ssid, String& password) {
    ssid = prefs.getString("ssid", "");
    password = prefs.getString("password", "");
    return !ssid.isEmpty();
  }

  bool saveDeviceToken(const char* token) { prefs.putString("device_token", token); return true; }
  bool loadDeviceToken(String& token) { token = prefs.getString("device_token", ""); return !token.isEmpty(); }

  uint32_t getNextSeq() {
    uint32_t seq = prefs.getUInt("seq", 0);
    prefs.putUInt("seq", seq + 1);
    return seq;
  }

private:
  Preferences prefs;
};
```

Notes:
- Store **SSID/password/token** in NVS; never rely on SD for identity.
- SD can be removed/corrupted; the device should still boot and re-enter setup mode.

### 2) Streaming upload (no heap fragmentation, no OOM)

```cpp
// Hard rule: never allocate the entire file in RAM for upload.
// Also: never send an "Expect: 100-continue" header (some servers behave poorly with embedded clients).
// If you switch to a higher-level HTTP library, verify it does not implicitly add an Expect header.
bool uploadPhotoStreaming(const char* filepath, const String& uploadHost, uint16_t uploadPort, const String& uploadPathWithQuery) {
  File file = SD.open(filepath);
  if (!file) return false;

  WiFiClientSecure client;
#if ALLOW_INSECURE_TLS
  client.setInsecure(); // dev-only; validate certs for production
#else
  // configure client.setCACert(...) or certificate bundle
#endif
  client.setTimeout(5000);

  if (!client.connect(uploadHost.c_str(), uploadPort)) {
    file.close();
    return false;
  }

  client.println("PUT " + uploadPathWithQuery + " HTTP/1.1");
  client.println("Host: " + uploadHost);
  client.println("Content-Type: image/jpeg");
  client.println("Content-Length: " + String(file.size()));
  client.println("Connection: close");
  client.println();

  uint8_t buf[8192];
  while (file.available()) {
    size_t len = file.read(buf, sizeof(buf));
    if (client.write(buf, len) != len) {
      file.close();
      client.stop();
      return false;
    }
  }

  file.close();
  int statusCode = readHttpStatusCode(client);
  client.stop();

  return statusCode >= 200 && statusCode < 300;
}

int readHttpStatusCode(WiFiClient& client) {
  // Avoid readStringUntil('\n') (can block/hang on fragmented responses).
  // Read only the status line with a small fixed buffer + timeout.
  char line[96];
  size_t n = client.readBytesUntil('\n', line, sizeof(line) - 1);
  line[n] = 0;

  // Expected: "HTTP/1.1 200 OK"
  const char* p = strchr(line, ' ');
  if (!p) return -1;
  while (*p == ' ') p++;
  int code = atoi(p);
  return code;
}

// Fallback only (prefer backend to return host/port/path_with_query).
// Handles:
// - https://host/path?query
// - https://host:443/path?query
// - https://host (path becomes "/")
bool parseUrlRobust(const String& url, String& hostOut, uint16_t& portOut, String& pathOut) {
  int schemeSep = url.indexOf("://");
  int hostStart = schemeSep >= 0 ? schemeSep + 3 : 0;
  if (hostStart >= (int)url.length()) return false;

  // First separator after host can be '/' (normal) or '?' (rare, but possible).
  int slash = url.indexOf('/', hostStart);
  int qmark = url.indexOf('?', hostStart);
  int cut = -1;
  if (slash >= 0 && qmark >= 0) cut = (slash < qmark) ? slash : qmark;
  else if (slash >= 0) cut = slash;
  else if (qmark >= 0) cut = qmark;

  String hostPort = (cut >= 0) ? url.substring(hostStart, cut) : url.substring(hostStart);
  if (cut < 0) pathOut = "/";
  else if (url.charAt(cut) == '/') pathOut = url.substring(cut);
  else pathOut = String("/") + url.substring(cut); // "/?query=..."
  if (pathOut.isEmpty()) pathOut = "/";

  int colon = hostPort.indexOf(':');
  if (colon >= 0) {
    hostOut = hostPort.substring(0, colon);
    portOut = (uint16_t)hostPort.substring(colon + 1).toInt();
    if (portOut == 0) portOut = 443;
  } else {
    hostOut = hostPort;
    portOut = 443;
  }

  hostOut.trim();
  return !hostOut.isEmpty();
}
```

Anti-pattern (don’t do this):
```cpp
// uint8_t* buffer = (uint8_t*)malloc(file.size()); // OOM/fragmentation risk
```

---

## 2-Week Development Plan (Detailed)

### Prerequisites (do before Day 1)
- [ ] Backend endpoints exist and are testable with curl/Postman.
- [ ] Verify idempotency: `POST /devices/ingest` twice with same `(device_id, seq)` yields one item.
- [ ] Format microSD as FAT32.
- [ ] Create PlatformIO project + confirm a basic “blink” builds/flash.
- [ ] Confirm **verified** XIAO ESP32S3 Sense camera/SD pin mappings from Seeed examples (make `include/board_pins.h` your single source of truth).

---

## Week 1: The Core Loop (Camera → SD → Cloud)

### Day 1: Camera + SD (local storage only)
**Goal:** Take one photo and save to SD; no Wi‑Fi yet.

Tasks:
- [ ] Add `include/board_pins.h` with verified camera + SD pins.
- [ ] Initialize camera (`esp_camera_init`).
- [ ] Capture frame buffer (`esp_camera_fb_get`), write JPEG to SD, return frame buffer.
- [ ] Confirm the JPEG opens on your laptop via an SD reader.

Deliverable:
- One valid JPEG on SD (`/test.jpg` or `/YYYYMMDD/HHMMSS_000.jpg`).

Example `include/board_pins.h` (verify against Seeed examples/schematic; treat as a starting point only):
```cpp
#pragma once

// Verified from Seeed XIAO ESP32S3 Sense examples (double-check before soldering)
#define CAMERA_PWDN_PIN     -1
#define CAMERA_RESET_PIN    -1
#define CAMERA_XCLK_PIN     10
#define CAMERA_SIOD_PIN     40
#define CAMERA_SIOC_PIN     39
#define CAMERA_Y9_PIN       48
#define CAMERA_Y8_PIN       11
#define CAMERA_Y7_PIN       12
#define CAMERA_Y6_PIN       14
#define CAMERA_Y5_PIN       16
#define CAMERA_Y4_PIN       18
#define CAMERA_Y3_PIN       17
#define CAMERA_Y2_PIN       15
#define CAMERA_VSYNC_PIN    38
#define CAMERA_HREF_PIN     47
#define CAMERA_PCLK_PIN     13

// SD card (verify from board schematic!)
#define SD_CS_PIN           21

// UI GPIOs (choose available ones)
#define SETUP_BUTTON_PIN    1
#define LED_RED_PIN         2
#define LED_GREEN_PIN       3
```

---

### Day 2: Foldering + NVS config (no SD config)
**Goal:** Capture on an interval; store identity/config safely in NVS.

Tasks:
- [ ] Implement `LifelogNVS` (SSID/password/device token + monotonic `seq` counter).
- [ ] Add NTP time sync (best-effort; don’t block boot forever).
- [ ] Create date folders `/YYYYMMDD/` and consistent filenames.
- [ ] Save a manifest record per capture (even before upload exists).

Suggested file naming:
- Folder: `/YYYYMMDD/`
- File: `HHMMSS_seq.jpg`
- Manifest: `/manifests/seq.json` (flat structure)

Deliverable:
- 20+ photos written with predictable names; `seq` survives reboot.

Best-effort NTP rule (prevents “boot hang”):
- Try NTP for **5–10 seconds max**. If it fails, proceed with:
  - `captured_at` derived from uptime (or omitted) and
  - `ntp_synced=false` in `/devices/ingest`
  - ordering still guaranteed by `seq`.

Best-effort NTP snippet (sketch):
```cpp
bool syncTimeBestEffort(uint32_t timeoutMs = 8000) {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  unsigned long start = millis();
  struct tm timeinfo;
  while (millis() - start < timeoutMs) {
    if (getLocalTime(&timeinfo, 200)) return true;
    delay(200);
  }
  return false;
}
```

---

### Day 3: Hardcoded Wi‑Fi + streaming upload (end-to-end proof)
**Goal:** First photo appears in the timeline (prove the pipeline before UI).

Tasks:
- [ ] Hardcode SSID/password temporarily (or pre-seed NVS for dev).
- [ ] Implement upload flow:
  - `POST /devices/upload-url` → `upload_host` + `upload_port` + `upload_path` (+ stable `object_key`)
  - `PUT` to storage via **streaming upload**
  - `POST /devices/ingest` with `(seq, captured_at)`; treat “duplicate” as success
- [ ] Verify a photo shows up in the webapp timeline.

Deliverable:
- End-to-end: “press reset, take photo, see it in timeline”.

Non-blocking rule (make it explicit):
- **Capture must stay on schedule** even if Wi‑Fi is down or uploading is slow.
- Upload runs as a **background worker** that can be paused without affecting capture.

Simple main loop state machine (sketch):
```cpp
bool captureDue(unsigned long nowMs) { return nowMs - lastCaptureMs >= captureIntervalMs; }
bool uploadDue(unsigned long nowMs)  { return nowMs - lastUploadMs >= uploadIntervalMs; }

void loop() {
  unsigned long now = millis();

  if (sdOk && captureDue(now)) {
    captureOneToSdAndManifest(); // must return quickly
    lastCaptureMs = now;
  }

  if (sdOk && wifiOk && uploadDue(now)) {
    uploadOnePendingIfAny();     // attempt 1 item (or small batch), then return
    lastUploadMs = now;
  }

  delay(10);
}
```

---

### Day 4: Manifest queue + retries
**Goal:** Survive spotty Wi‑Fi without losing data or duplicating timeline items.

Manifest schema (example):
```json
{
  "filepath": "/20251202/143022_001.jpg",
  "seq": 42,
  "captured_at_epoch": 1701527422,
  "status": "PENDING",
  "upload_attempts": 0,
  "last_attempt_epoch": 0
}
```

Tasks:
- [ ] On capture: write photo + create `/manifests/<seq>.json` as `PENDING`.
- [ ] Upload worker:
  - scan `/manifests/` (flat) for `PENDING`
  - pick oldest by `captured_at_epoch`
  - enforce backoff: 1m → 5m → 30m, max 3 attempts
  - on success: mark manifest `UPLOADED` (or delete after retention policy)

Power-loss safety (high ROI): manifest writes must be “atomic-ish”
- Write to `/manifests/<seq>.json.tmp`
- `flush()` + `close()`
- `rename()` to `/manifests/<seq>.json`
- On boot: delete any stray `.tmp` files

Example atomic-ish manifest write:
```cpp
bool writeManifestAtomic(uint32_t seq, const JsonDocument& doc) {
  if (!SD.exists("/manifests")) SD.mkdir("/manifests");

  String finalPath = String("/manifests/") + String(seq) + ".json";
  String tmpPath = finalPath + ".tmp";

  File f = SD.open(tmpPath.c_str(), FILE_WRITE);
  if (!f) return false;
  if (serializeJson(doc, f) == 0) { f.close(); SD.remove(tmpPath.c_str()); return false; }
  f.flush();
  f.close();

  // SD.rename usually fails if destination exists.
  if (SD.exists(finalPath.c_str())) SD.remove(finalPath.c_str());
  if (!SD.rename(tmpPath.c_str(), finalPath.c_str())) {
    SD.remove(tmpPath.c_str());
    return false;
  }
  return true;
}
```

Example backoff logic + “oldest pending” scan (flat manifest folder):
```cpp
unsigned long backoffSecondsForAttempts(int attempts) {
  if (attempts <= 0) return 60;
  if (attempts == 1) return 5 * 60;
  return 30 * 60;
}

// NOTE: this is a sketch; keep allocations minimal to avoid fragmentation.
bool findOldestPending(String& manifestPathOut, String& filePathOut, uint32_t& seqOut) {
  File manifests = SD.open("/manifests");
  if (!manifests) return false;

  bool found = false;
  time_t bestEpoch = 0x7FFFFFFF;

  while (File f = manifests.openNextFile()) {
    if (f.isDirectory()) { f.close(); continue; }
    String name = f.name();
    if (!name.endsWith(".json")) { f.close(); continue; }

    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, f) != DeserializationError::Ok) { f.close(); continue; }
    f.close();

    if (String(doc["status"] | "") != "PENDING") continue;

    time_t captured = doc["captured_at_epoch"] | 0;
    int attempts = doc["upload_attempts"] | 0;
    time_t lastAttempt = doc["last_attempt_epoch"] | 0;

    time_t now = time(nullptr);
    unsigned long backoff = backoffSecondsForAttempts(attempts);
    if (now - lastAttempt < (time_t)backoff) continue;

    if (!found || captured < bestEpoch) {
      found = true;
      bestEpoch = captured;
      manifestPathOut = String("/manifests/") + name;
      filePathOut = doc["filepath"].as<String>();
      seqOut = doc["seq"] | 0;
    }
  }

  manifests.close();
  return found;
}
```

Deliverable:
- Turn hotspot on/off; backlog drains; no duplicate timeline items.

---

### Day 5: SD retention + telemetry
**Goal:** Run indefinitely without SD filling or silent failures.

Tasks:
- [ ] Free space monitoring:
  - if free < 15%: delete oldest `UPLOADED` photos/manifests
  - if free < 5%: pause capture (fail safe)
- [ ] Telemetry heartbeat (`/devices/telemetry`) including backlog count, RSSI, SD usage, battery voltage if available.
- [ ] Add a fast SD health check + fallback behavior.

SD health check + fallback (high ROI):
- If SD fails to mount at boot or becomes unavailable:
  - set LED to **solid red**
  - **stop capture** (don’t pretend you’re recording)
  - keep setup mode usable (config is in NVS)

Telemetry payload (example fields):
```json
{
  "uptime_seconds": 3600,
  "sd_used_mb": 450,
  "sd_free_mb": 1200,
  "backlog_count": 3,
  "battery_mv": 3850,
  "wifi_rssi": -65,
  "firmware_version": "1.0.0"
}
```

Deliverable:
- Device stays alive when SD is near full and reports useful health metrics.

---

### Weekend: Field test (measure, don’t guess)
Test scenarios:
- [ ] Capture-only (Wi‑Fi off) for 12h
- [ ] Normal mode (upload batch interval) for 8h
- [ ] Walking test using phone hotspot (dropouts)
- [ ] SD stress: fill SD to ~90% and validate retention + no crashes
- [ ] Optional: capture the current profile to explain “why not 16h?”

Deliverable:
- Real current draw + stability notes + crash logs (if any).

Battery measurement (optional but high signal):
- Early: a cheap USB power meter is “good enough” for dev.
- Later: an inline current sensor like **INA219/INA226** makes bursty ESP32 draw much easier to reason about.

---

## Week 2: Usability + Polish

### Day 8: Captive portal setup mode (now that core works)
**Goal:** Configure SSID/password + pairing code without reflashing.

Hard requirements:
- Serve the setup page from **flash** (PROGMEM), not SD.
- Save SSID/password/pairing code and device token in **NVS**.

Tasks:
- [ ] Setup mode entry (hold button 3s at boot).
- [ ] SoftAP SSID: `Lifelog-Setup-XXXX` (MAC suffix).
- [ ] Captive portal DNS redirect + web server.
- [ ] Form submit:
  - save Wi‑Fi credentials + pairing code → NVS
  - call `POST /devices/activate` using pairing code
  - save `device_token` → NVS
  - reboot into normal mode

Deliverable:
- Fresh board can be paired and starts uploading with no code changes.

Minimal captive portal sketch (PROGMEM HTML + NVS save):
```cpp
#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>

static const char SETUP_HTML[] PROGMEM = R"rawliteral(
<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lifelog Setup</title></head>
<body style="font-family:Arial;max-width:420px;margin:30px auto;padding:0 16px">
  <h2>Lifelog Device Setup</h2>
  <form action="/save" method="POST">
    <label>Pairing Code</label><br/>
    <input name="pairing_code" pattern="[0-9]{6}" required style="width:100%;padding:10px"><br/><br/>
    <label>Wi‑Fi SSID</label><br/>
    <input name="ssid" required style="width:100%;padding:10px"><br/><br/>
    <label>Wi‑Fi Password</label><br/>
    <input name="password" type="password" required style="width:100%;padding:10px"><br/><br/>
    <button type="submit" style="width:100%;padding:12px">Save</button>
  </form>
</body></html>
)rawliteral";

WebServer server(80);
DNSServer dnsServer;

void startCaptivePortal() {
  WiFi.mode(WIFI_AP);
  WiFi.softAP("Lifelog-Setup");
  dnsServer.start(53, "*", WiFi.softAPIP());

  server.on("/", HTTP_GET, []() { server.send_P(200, "text/html", SETUP_HTML); });
  server.on("/save", HTTP_POST, []() {
    String pairing = server.arg("pairing_code");
    String ssid = server.arg("ssid");
    String password = server.arg("password");

    // Save to NVS here (ssid/password/pairing); activate; store device_token in NVS.
    server.send(200, "text/html", "<h3>Saved. Rebooting…</h3>");
    delay(1000);
    ESP.restart();
  });

  server.begin();
}

void loopSetupPortal() {
  dnsServer.processNextRequest();
  server.handleClient();
}
```

---

### Day 9: Remote config + burst mode
**Goal:** Tune capture/upload behavior from the webapp.

Tasks:
- [ ] Implement `GET /devices/config` polling when online.
- [ ] Add `capture_mode`:
  - `interval`: 1 photo every N seconds
  - `burst`: N photos spaced 1s apart every M seconds

Deliverable:
- Change config server-side and watch device behavior update.

---

### Day 10–11: Stability (watchdog, LEDs, recovery)
**Goal:** Self-healing device with clear user feedback.

Tasks:
- [ ] Task watchdog (e.g., 30s) with periodic reset in main loop.
- [ ] LED patterns:
  - solid green: capturing
  - blink green: uploading
  - blink red: recoverable error
  - solid red: critical (camera/SD failure)
- [ ] Recovery:
  - camera init retry (3x)
  - SD mount retry (3x)
  - Wi‑Fi connect timeout, continue offline
- [ ] Heap/PSRAM monitoring logs (avoid chatty logs).
- [ ] Security knobs (MVP-friendly):
  - compile flag to allow insecure TLS only in dev
  - never print `device_token` in serial logs

Deliverable:
- Device can run overnight without wedging; status is obvious without serial.

Minimal compile-time security knob (sketch):
```cpp
// config.h
#define ALLOW_INSECURE_TLS 1  // dev=1, release=0

// upload code
#if ALLOW_INSECURE_TLS
  client.setInsecure();
#else
  // configure client.setCACert(...) or certificate bundle
#endif
```

---

### Day 12–14: Battery life tuning + documentation
Tasks:
- [ ] 16h battery test with measured current draw.
- [ ] Tune intervals, batch sizes, sleep strategies as needed.
- [ ] Document setup + troubleshooting + known limitations.

Deliverable:
- Reproducible build + setup steps + real battery numbers (not estimates).

---

## Final Pre-Flight Checklist

- [ ] No secrets stored on SD (search for `config.json` writes).
- [ ] No whole-file upload buffering (search for `malloc(` in upload paths).
- [ ] Upload target does not require URL parsing on device (host/port/path returned by backend).
- [ ] HTTP status parsing uses a timeout + status line parse (no `readStringUntil`).
- [ ] Upload request never sends `Expect: 100-continue`.
- [ ] Ingest idempotency verified with `(device_id, seq)`.
- [ ] At least one photo appears in the timeline by end of Day 3.
