# Weather Station Wind Display - Implementation Plan

## Context

Build a GeekMagic SmallTV Ultra (ESP8266 + 240x240 LCD) display for an Ecowitt weather station, starting with wind data (speed, gusts, compass rose). Uses the same proxy-rendered architecture as the existing Agile electricity display: server renders PIL images, converts to RGB565, ESP8266 streams and pushes to LCD.

## Phase 1: Repo & Directory Setup

1. Create directory `WEATHERSTATION` alongside `AGILEOCTOPUS` in `MCP_FILESYSTEM/`
2. `git init`, create `.gitignore` (`.DS_Store`, `.pio/`, `__pycache__/`, `secrets.local`)
3. `gh repo create maxwhitby/weather-station --public --source=. --remote=origin`
4. Create `CLAUDE.md` with project docs

## Phase 2: Proxy Server (`proxy/weather_server.py`)

Clone structure from `AGILEOCTOPUS/proxy/agile_server.py`. Adapt for Ecowitt wind data.

### Ecowitt API
- Endpoint: `https://api.ecowitt.net/api/v3/device/real_time`
- Params: `application_key`, `api_key`, `mac`, `call_back=outdoor,wind`
- Response: `data.wind.wind_speed.value`, `data.wind.wind_gust.value`, `data.wind.wind_direction.value`
- Refresh: 60 seconds

### Display Layout (240x240)
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

## Phase 3: ESP8266 Firmware (`smalltv_ultra/`)

Near-direct copy of Agile firmware. Changes:

| Item | Agile | Weather |
|------|-------|---------|
| Hostname | `agile-display` | `weather-display` |
| AP SSID | `AgileDisplay-Setup` | `WeatherDisplay-Setup` |
| EEPROM magic | `0x4F` | `0x57` |
| Endpoint | `/agile.bin` | `/weather.bin` |
| Boot screen | "OCTOPUS AGILE" | "WEATHER STATION" |
| Theme color | Purple | Cyan |
| PIO env name | `agile-display` | `weather-display` |

## Phase 4: Deploy Script & Production Integration

Add weather endpoints to the production `radar_server.py` in the DISPLAYS repo.

## Phase 5: Verification

1. `python proxy/weather_server.py` → check `localhost:5050/weather.png`
2. `curl localhost:5050/weather/status`
3. `cd smalltv_ultra && pio run`
4. Flash ESP8266, confirm display
