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

// SD card (XIAO ESP32S3 Sense uses SD_MMC)
// NOTE: Verify these against Seeed's board docs/examples if SD mount fails.
#define SD_MMC_CLK_PIN      7
#define SD_MMC_CMD_PIN      9
#define SD_MMC_D0_PIN       8
#define SD_MMC_D1_PIN       4
#define SD_MMC_D2_PIN       6
#define SD_MMC_D3_PIN       5

// UI GPIOs (choose available ones)
#define SETUP_BUTTON_PIN    1
#define LED_RED_PIN         2
#define LED_GREEN_PIN       3

// Microphone pins (XIAO ESP32S3 Sense mic is PDM)
// PDM: MIC_CLK_PIN = 42, MIC_DATA_PIN = 41
#define MIC_DATA_PIN        41
#define MIC_CLK_PIN         42
#define MIC_BCLK_PIN        -1
#define MIC_WS_PIN          -1
