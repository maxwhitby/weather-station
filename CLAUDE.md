# Ecowitt Weather Station Wind Display

## Overview

Displays real-time wind data from an Ecowitt weather station on a GeekMagic SmallTV Ultra (ESP8266 + 240x240 LCD). Uses the same proxy-rendered architecture as the Agile electricity display: server renders PIL images, converts to RGB565, ESP8266 streams and pushes to LCD.

## Current Status

**Fully deployed and operational** as of 2026-03-18.

- Ecowitt API credentials configured (local + production server)
- Proxy server deployed to Oracle Cloud (weather endpoints live)
- First ESP8266 device flashed via USB serial (`/dev/cu.usbserial-0001`)
- Device MAC: `08:3A:8D:D5:FB:29`
- Firmware size: 366KB flash (35%), 36KB RAM (44%)

### Recent Changes

- **2026-03-18**: Deployed weather endpoints to production proxy server. Added Ecowitt credentials to both local `secrets.local` and production `/opt/radar-proxy/secrets.local`. Flashed first SmallTV Ultra device.

## Hardware Platform

### GeekMagic SmallTV Ultra

| Property | Value |
|----------|-------|
| **MCU** | ESP8266 (ESP-12F) |
| **Display** | ST7789V 240x240 LCD |
| **Framework** | Arduino/PlatformIO with TFT_eSPI |
| **WiFi** | On-board |
| **Rendering** | Server-side (proxy renders 240x240 RGB565 images) |
| **Firmware** | `smalltv_ultra/src/main.cpp` |
| **Hostname** | `weather-display.local` |
| **AP SSID** | `WeatherDisplay-Setup` |
| **Device MAC** | `08:3A:8D:D5:FB:29` |

#### ESP8266 Pinout

| Function | GPIO | Physical | Notes |
|----------|------|----------|-------|
| D/C | GPIO0 | D3 | Also flash mode (LOW at boot = flash) |
| Reset | GPIO2 | D4 | Display reset, also has pull-up |
| **Backlight** | **GPIO5** | **D1** | **ACTIVE LOW (P-MOSFET) - LOW=ON, HIGH=OFF** |
| MOSI | GPIO13 | D7 | SPI data to display |
| SCK | GPIO14 | D5 | SPI clock |
| CS | GND | — | Directly wired to ground |

## Architecture

```
Ecowitt API ──► Oracle Cloud Proxy ──► ESP8266 ──► LCD
                Flask :5050             streams RGB565
                /weather.bin
```

## Data Source

**Ecowitt API** (requires credentials):
```
https://api.ecowitt.net/api/v3/device/real_time
```

- Params: `application_key`, `api_key`, `mac`, `call_back=outdoor,wind`
- Returns: `wind_speed`, `wind_gust`, `wind_direction` (degrees)
- Refresh: 60 seconds (well within 10,000 calls/day API limit)

### Credentials

Stored in `proxy/secrets.local` (gitignored) or env vars:
```
ECOWITT_APPLICATION_KEY=xxx    # From ecowitt.net → API Keys page (app: ecowitt_display_app_01)
ECOWITT_API_KEY=xxx            # User API key (key: ecowitt_display_api_01)
ECOWITT_MAC=3C:8A:1F:26:B5:4B  # Ecowitt gateway MAC
```

On the production server, credentials are in `/opt/radar-proxy/secrets.local` (deployed 2026-03-18).

## Proxy Server

**Live:** `http://130.162.190.206:5050/`

The weather endpoints are part of the unified radar proxy server in the DISPLAYS repo. A standalone version is included in `proxy/weather_server.py` for reference.

### Production Server

| Item | Path |
|------|------|
| **Production source** | `../DISPLAYS/METOFFICE/radar_proxy/radar_server.py` |
| **SSH key** | `../DISPLAYS/ssh-key-2025-12-23.key` |
| **Server user** | `ubuntu@130.162.190.206` |
| **Remote path** | `/opt/radar-proxy/radar_server.py` |
| **Systemd service** | `radar-proxy` (`sudo systemctl restart radar-proxy`) |

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `/weather.bin` | 240x240 RGB565 binary (115200 bytes) |
| `/weather.png` | PNG preview (for debugging) |
| `/weather/status` | JSON: wind_speed, wind_gust, direction, beaufort |
| `/weather/refresh` | Force immediate data refresh |

### Refresh Intervals

| Component | Interval |
|-----------|----------|
| Proxy API fetch | 60 seconds |
| ESP8266 display refresh | 60 seconds |

## Display Design (240x240)

```
+------------------------------------------+
|  WIND                          16:34     |  Title + clock (y=3)
|  12.4 mph        Gust 18.7              |  Speed + gust (y=26)
|                                          |
|              N                           |
|         NW       NE                      |
|                                          |
|      W      [compass]     E              |  ~144px diameter rose
|              [arrow]                     |  Arrow shows wind FROM direction
|         SW       SE                      |
|              S                           |
|                                          |
|  SSW  213°            Beaufort 4         |  Footer (y=212)
+------------------------------------------+
```

### Compass Rose

- Center: (120, 132), radius: 72px
- Arrow: triangular, colored by Beaufort scale, points FROM wind direction
- Cardinal labels (N/S/E/W) white, intercardinals dim grey
- Tick marks at 22.5-degree intervals (16 points)
- Center dot in wind color

### Wind Speed Colors (Beaufort scale)

| Range | Color | RGB |
|-------|-------|-----|
| Calm (<1 mph) | Light grey | (150, 150, 160) |
| Light (1-7) | Cyan | (0, 200, 220) |
| Moderate (8-18) | Green | (0, 180, 80) |
| Fresh/Strong (19-31) | Yellow | (220, 180, 0) |
| Gale (32-46) | Orange | (230, 120, 0) |
| Storm (47+) | Red | (220, 40, 40) |

Bottom of display is left black as the SmallTV Ultra frame masks the bottom ~6px.

## Building and Flashing

### SmallTV Ultra (ESP8266)

```bash
cd smalltv_ultra

# Build
pio run

# First-time flash (USB, GPIO0 grounded)
pio run -t upload --upload-port /dev/cu.usbserial-XXXX

# OTA flash (after first flash)
pio run -t upload --upload-port weather-display.local
```

### Alternative: Flash with esptool directly
```bash
esptool.py --port /dev/cu.usbserial-XXXX --baud 921600 write_flash 0x0 .pio/build/weather-display/firmware.bin
```

## Web Interface

| Endpoint | Purpose |
|----------|---------|
| `/` | Status page (auto-refreshes every 10s) |
| `/wifi` | WiFi configuration |
| `/update` | Firmware upload (OTA) |

## Deploying Proxy Changes

```bash
# Option 1: Use the deploy script (updates production server)
./deploy.sh

# Option 2: Manual
scp -i ../DISPLAYS/ssh-key-2025-12-23.key \
    ../DISPLAYS/METOFFICE/radar_proxy/radar_server.py \
    ubuntu@130.162.190.206:/tmp/radar_server.py
ssh -i ../DISPLAYS/ssh-key-2025-12-23.key ubuntu@130.162.190.206 \
    "sudo cp /tmp/radar_server.py /opt/radar-proxy/ && sudo systemctl restart radar-proxy"

# Verify
curl http://130.162.190.206:5050/weather/status
```

## Directory Structure

```
.
├── CLAUDE.md                      # This file
├── .gitignore
├── deploy.sh                      # Deploy proxy changes to Oracle Cloud
├── docs/
│   └── IMPLEMENTATION_PLAN.md     # Original implementation plan
├── proxy/
│   ├── weather_server.py          # Standalone reference proxy
│   └── secrets.local              # Ecowitt credentials (gitignored)
└── smalltv_ultra/
    ├── platformio.ini
    └── src/main.cpp
```

## GeekMagic SmallTV Ultra - Device Initialization Guide

### Hardware
- ESP8266 (ESP-12F module) with ST7789V 240x240 LCD
- USB-serial connection via CH340 (or similar) — no built-in USB on the ESP8266
- Backlight: GPIO5, **active LOW** (P-MOSFET) — LOW=ON, HIGH=OFF

### First-Time Flash (USB)
1. **Open the case** — 4 screws on the back, lift off the rear panel
2. **Connect USB-serial adapter** — TX→RX, RX→TX, GND→GND, 3.3V→3.3V (NOT 5V)
3. **Enter flash mode** — hold GPIO0 to GND while powering on (or press reset while GPIO0 grounded)
4. **Identify serial port** — `ls /dev/cu.usbserial-*` (macOS) or `ls /dev/ttyUSB*` (Linux)
5. **Flash**:
   ```bash
   cd smalltv_ultra
   pio run -t upload --upload-port /dev/cu.usbserial-XXXX
   ```
6. **Release GPIO0** and reset — device boots into firmware

### First Boot Sequence
1. Device shows "WEATHER STATION" boot screen (cyan theme)
2. No saved WiFi → enters **AP mode**
3. Screen shows cyan "WeatherDisplay-Setup" SSID and IP 192.168.4.1
4. Connect phone/laptop to `WeatherDisplay-Setup` WiFi network
5. Browse to `http://192.168.4.1`
6. Enter home WiFi SSID and password → Save
7. Device reboots, connects to home WiFi
8. Begins fetching weather display from proxy server

### After First Flash: OTA Updates
Once on WiFi, no USB needed:
```bash
# Via PlatformIO (mDNS)
pio run -t upload --upload-port weather-display.local

# Via web browser
# Browse to http://weather-display.local/update
# Upload the .bin file from .pio/build/weather-display/firmware.bin
```

### Troubleshooting
- **No serial port detected**: Check USB-serial adapter drivers (CH340/CP2102)
- **Flash fails**: Ensure GPIO0 is grounded during flash, try lower baud (`upload_speed = 115200`)
- **Boot loop**: Check 3.3V supply is stable (ESP8266 draws ~300mA peaks)
- **Black screen after flash**: Verify backlight pin (GPIO5 LOW = on), check SPI pins match platformio.ini
- **OTA fails**: Device must be on same network, check `weather-display.local` resolves (try IP directly)
- **AP mode not appearing**: Hold reset for 5s, or re-flash via USB

## Commands

- `/upspeed` - Read project docs to get up to speed
- `/update` - Update documentation and push to GitHub
