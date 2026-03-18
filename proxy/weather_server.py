#!/usr/bin/env python3
"""
Ecowitt Weather Station Wind Display Proxy Server

Standalone server that fetches wind data from the Ecowitt API and renders
240x240 compass rose display images for the GeekMagic SmallTV Ultra (ESP8266).

Endpoints:
  /weather.bin     - RGB565 binary image (115200 bytes) for ESP8266
  /weather.png     - PNG image for debugging/preview
  /weather/status  - JSON status with wind speed, gust, direction
  /weather/refresh - Force immediate data refresh

This is extracted from the main radar_server.py proxy for reference.
In production, these endpoints are served by the unified proxy at
130.162.190.206:5050.
"""

import io
import math
import os
import struct
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont
from flask import Flask, Response, jsonify, send_file

# Configuration
OUTPUT_SIZE = 240
ECOWITT_API_URL = 'https://api.ecowitt.net/api/v3/device/real_time'
WEATHER_REFRESH_INTERVAL = 60  # seconds

# Ecowitt credentials (loaded from secrets.local or env vars)
ECOWITT_APPLICATION_KEY = None
ECOWITT_API_KEY = None
ECOWITT_MAC = None

# Colors
WEATHER_COLORS = {
    'background': (10, 10, 20),
    'title': (220, 200, 0),           # Yellow
    'speed_text': (220, 200, 0),      # Yellow
    'gust_text': (180, 170, 60),      # Dim yellow
    'cardinal': (180, 180, 190),      # Light grey (N/S/E/W)
    'intercardinal': (80, 80, 100),   # Dim grey (NE/NW/SE/SW)
    'tick': (60, 60, 80),             # Tick marks
    'compass_ring': (40, 40, 60),     # Compass outer ring
    'footer': (160, 150, 50),         # Dim yellow
    'arrow': (0, 220, 60),            # Bright green (fixed color for arrow)
    'arrow_calm': (150, 150, 160),    # Calm wind
    'arrow_light': (0, 200, 220),     # Light wind (cyan)
    'arrow_moderate': (0, 180, 80),   # Moderate (green)
    'arrow_fresh': (220, 180, 0),     # Fresh/strong (yellow)
    'arrow_gale': (230, 120, 0),      # Gale (orange)
    'arrow_storm': (220, 40, 40),     # Storm (red)
}

# Cache
weather_cache = {
    'wind': None,
    'last_update': None,
    'error': None,
}
weather_cache_lock = threading.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def load_ecowitt_credentials():
    """Load Ecowitt credentials from secrets.local or environment variables."""
    global ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY, ECOWITT_MAC

    secrets_paths = [
        Path(__file__).parent / 'secrets.local',
        Path('/opt/radar-proxy/secrets.local'),
    ]

    for secrets_path in secrets_paths:
        if secrets_path.exists():
            try:
                for line in secrets_path.read_text().splitlines():
                    line = line.strip()
                    if '=' in line and not line.startswith('#'):
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip())
                logger.info(f"Loaded Ecowitt credentials from {secrets_path}")
                break
            except Exception as e:
                logger.warning(f"Failed to read {secrets_path}: {e}")

    ECOWITT_APPLICATION_KEY = os.environ.get('ECOWITT_APPLICATION_KEY')
    ECOWITT_API_KEY = os.environ.get('ECOWITT_API_KEY')
    ECOWITT_MAC = os.environ.get('ECOWITT_MAC')

    if not all([ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY, ECOWITT_MAC]):
        logger.warning("Ecowitt credentials incomplete - weather feature will show demo data")


def get_font(size: int):
    """Get a monospace font."""
    font_paths = [
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                continue
    return ImageFont.load_default()


def image_to_rgb565(img):
    """Convert PIL Image to RGB565 binary data."""
    data = []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = img.getpixel((x, y))[:3]
            rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            data.append(struct.pack('<H', rgb565))
    return b''.join(data)


def get_uk_time():
    """Get current UK time (handles GMT/BST)."""
    utc_now = datetime.now(timezone.utc)
    year = utc_now.year
    march_last = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    while march_last.weekday() != 6:
        march_last -= timedelta(days=1)
    october_last = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    while october_last.weekday() != 6:
        october_last -= timedelta(days=1)
    if march_last <= utc_now < october_last:
        return utc_now + timedelta(hours=1)
    return utc_now


def get_wind_color(speed_mph):
    """Get wind arrow color based on Beaufort scale with continuous gradient."""
    if speed_mph < 1:
        return WEATHER_COLORS['arrow_calm']

    # Define Beaufort-based color stops: (threshold_mph, color)
    stops = [
        (1, WEATHER_COLORS['arrow_light']),      # Light
        (7, WEATHER_COLORS['arrow_light']),       # End of light
        (8, WEATHER_COLORS['arrow_moderate']),    # Moderate
        (18, WEATHER_COLORS['arrow_moderate']),   # End of moderate
        (19, WEATHER_COLORS['arrow_fresh']),      # Fresh/strong
        (31, WEATHER_COLORS['arrow_fresh']),      # End of fresh
        (32, WEATHER_COLORS['arrow_gale']),       # Gale
        (46, WEATHER_COLORS['arrow_gale']),       # End of gale
        (47, WEATHER_COLORS['arrow_storm']),      # Storm
    ]

    if speed_mph >= 47:
        return WEATHER_COLORS['arrow_storm']

    # Find bracketing stops and interpolate
    for i in range(len(stops) - 1):
        lo_speed, lo_color = stops[i]
        hi_speed, hi_color = stops[i + 1]
        if lo_speed <= speed_mph <= hi_speed:
            if hi_speed == lo_speed:
                return lo_color
            t = (speed_mph - lo_speed) / (hi_speed - lo_speed)
            return tuple(int(lo_color[j] + t * (hi_color[j] - lo_color[j])) for j in range(3))

    return WEATHER_COLORS['arrow_moderate']


def get_beaufort_number(speed_mph):
    """Get Beaufort scale number from wind speed in mph."""
    thresholds = [1, 4, 8, 13, 19, 25, 32, 39, 47, 55, 64, 73]
    for i, threshold in enumerate(thresholds):
        if speed_mph < threshold:
            return i
    return 12


def get_compass_label(degrees):
    """Get 16-point compass label from degrees."""
    directions = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                  'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = round(degrees / 22.5) % 16
    return directions[idx]


def fetch_wind_data():
    """Fetch wind data from Ecowitt API."""
    if not all([ECOWITT_APPLICATION_KEY, ECOWITT_API_KEY, ECOWITT_MAC]):
        logger.warning("Ecowitt credentials not configured")
        return None

    try:
        params = {
            'application_key': ECOWITT_APPLICATION_KEY,
            'api_key': ECOWITT_API_KEY,
            'mac': ECOWITT_MAC,
            'call_back': 'outdoor,wind',
        }

        logger.info("Fetching Ecowitt wind data...")
        resp = requests.get(ECOWITT_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get('code') != 0:
            logger.error(f"Ecowitt API error: {data.get('msg', 'Unknown')}")
            return None

        wind = data.get('data', {}).get('wind', {})
        if not wind:
            logger.error("No wind data in response")
            return None

        result = {
            'wind_speed': float(wind.get('wind_speed', {}).get('value', 0)),
            'wind_gust': float(wind.get('wind_gust', {}).get('value', 0)),
            'wind_direction': int(float(wind.get('wind_direction', {}).get('value', 0))),
        }

        # Get outdoor temperature if available
        outdoor = data.get('data', {}).get('outdoor', {})
        if outdoor and 'temperature' in outdoor:
            result['temperature'] = float(outdoor['temperature'].get('value', 0))
            result['temp_unit'] = outdoor['temperature'].get('unit', '℉')

        logger.info(f"Wind: {result['wind_speed']} mph, gust {result['wind_gust']} mph, "
                    f"direction {result['wind_direction']}°")
        return result

    except Exception as e:
        logger.error(f"Ecowitt fetch failed: {e}")
        return None


def render_wind_display(wind_data, width=240, height=240):
    """Render wind compass rose display."""
    img = Image.new('RGB', (width, height), WEATHER_COLORS['background'])
    draw = ImageDraw.Draw(img)

    if not wind_data:
        font = get_font(24)
        draw.text((8, 100), "No wind data", font=font, fill=WEATHER_COLORS['title'])
        return img

    font_title = get_font(20)
    font_speed = get_font(26)
    font_gust = get_font(16)
    font_cardinal = get_font(14)
    font_footer = get_font(14)
    font_time = get_font(16)

    speed = wind_data['wind_speed']
    gust = wind_data['wind_gust']
    direction = wind_data['wind_direction']

    now_uk = get_uk_time()
    now_str = now_uk.strftime('%H:%M')

    # ---- Header ----
    draw.rectangle([0, 0, width, 64], fill=(15, 15, 30))

    # Title
    draw.text((8, 3), "WIND", font=font_title, fill=WEATHER_COLORS['title'])

    # Clock
    time_bbox = draw.textbbox((0, 0), now_str, font=font_time)
    time_w = time_bbox[2] - time_bbox[0]
    draw.text((width - time_w - 8, 6), now_str, font=font_time, fill=(100, 100, 120))

    # Speed + gust (mph)
    speed_str = f"{speed:.0f} mph" if speed >= 100 else f"{speed:.1f} mph"
    draw.text((8, 26), speed_str, font=font_speed, fill=WEATHER_COLORS['speed_text'])

    gust_str = f"Gust {gust:.0f}" if gust >= 100 else f"Gust {gust:.1f}"
    gust_bbox = draw.textbbox((0, 0), gust_str, font=font_gust)
    gust_w = gust_bbox[2] - gust_bbox[0]
    draw.text((width - gust_w - 8, 30), gust_str, font=font_gust, fill=WEATHER_COLORS['gust_text'])

    # km/h line
    speed_kmh = speed * 1.60934
    gust_kmh = gust * 1.60934
    kmh_speed_str = f"{speed_kmh:.0f} km/h" if speed_kmh >= 100 else f"{speed_kmh:.1f} km/h"
    kmh_gust_str = f"{gust_kmh:.0f}" if gust_kmh >= 100 else f"{gust_kmh:.1f}"
    draw.text((8, 54), kmh_speed_str, font=font_gust, fill=WEATHER_COLORS['gust_text'])
    kmh_gust_bbox = draw.textbbox((0, 0), kmh_gust_str, font=font_gust)
    kmh_gust_w = kmh_gust_bbox[2] - kmh_gust_bbox[0]
    draw.text((width - kmh_gust_w - 8, 54), kmh_gust_str, font=font_gust, fill=WEATHER_COLORS['gust_text'])

    # ---- Compass Rose ----
    cx = width // 2      # Center x
    cy = 142              # Center y (shifted down for km/h line)
    radius = 64           # Compass radius

    # Outer ring
    draw.ellipse([cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2],
                 outline=WEATHER_COLORS['compass_ring'], width=2)

    # Tick marks at 22.5-degree intervals
    for i in range(16):
        angle_deg = i * 22.5
        angle_rad = math.radians(angle_deg - 90)  # -90 so 0° = North (top)
        is_cardinal = i % 4 == 0       # N, E, S, W
        is_intercardinal = i % 2 == 0  # NE, SE, SW, NW

        if is_cardinal:
            inner_r = radius - 10
            tick_width = 2
        elif is_intercardinal:
            inner_r = radius - 6
            tick_width = 1
        else:
            inner_r = radius - 4
            tick_width = 1

        x1 = cx + int(inner_r * math.cos(angle_rad))
        y1 = cy + int(inner_r * math.sin(angle_rad))
        x2 = cx + int(radius * math.cos(angle_rad))
        y2 = cy + int(radius * math.sin(angle_rad))
        draw.line([(x1, y1), (x2, y2)], fill=WEATHER_COLORS['tick'], width=tick_width)

    # Cardinal labels (N/S/E/W)
    label_r = radius + 14
    cardinals = [(0, 'N'), (90, 'E'), (180, 'S'), (270, 'W')]
    for angle_deg, label in cardinals:
        angle_rad = math.radians(angle_deg - 90)
        lx = cx + int(label_r * math.cos(angle_rad))
        ly = cy + int(label_r * math.sin(angle_rad))
        bbox = draw.textbbox((0, 0), label, font=font_cardinal)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.text((lx - lw // 2, ly - lh // 2), label, font=font_cardinal,
                  fill=WEATHER_COLORS['cardinal'])

    # Intercardinal labels (NE/NW/SE/SW)
    intercardinals = [(45, 'NE'), (135, 'SE'), (225, 'SW'), (315, 'NW')]
    font_inter = get_font(11)
    inter_r = radius + 14
    for angle_deg, label in intercardinals:
        angle_rad = math.radians(angle_deg - 90)
        lx = cx + int(inter_r * math.cos(angle_rad))
        ly = cy + int(inter_r * math.sin(angle_rad))
        bbox = draw.textbbox((0, 0), label, font=font_inter)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.text((lx - lw // 2, ly - lh // 2), label, font=font_inter,
                  fill=WEATHER_COLORS['intercardinal'])

    # Wind direction arrow (green, red at gale force Beaufort 8 / 39+ mph)
    arrow_color = WEATHER_COLORS['arrow_storm'] if speed >= 39 else WEATHER_COLORS['arrow']
    arrow_angle_rad = math.radians(direction - 90)  # direction wind comes FROM

    # Arrow tip (pointing toward center from the FROM direction)
    # The arrow extends from outside toward center
    tip_r = 12  # How close tip gets to center
    tail_r = radius - 14  # Arrow tail near the ring

    tip_x = cx + int(tip_r * math.cos(arrow_angle_rad + math.pi))
    tip_y = cy + int(tip_r * math.sin(arrow_angle_rad + math.pi))

    tail_x = cx + int(tail_r * math.cos(arrow_angle_rad))
    tail_y = cy + int(tail_r * math.sin(arrow_angle_rad))

    # Arrow shaft
    draw.line([(tail_x, tail_y), (tip_x, tip_y)], fill=arrow_color, width=4)

    # Arrowhead (beefed up)
    head_len = 20
    head_width = 14
    # Direction from tail to tip
    dx = tip_x - tail_x
    dy = tip_y - tail_y
    length = math.sqrt(dx * dx + dy * dy)
    if length > 0:
        ux = dx / length
        uy = dy / length
        # Perpendicular
        px = -uy
        py = ux
        # Arrowhead base
        base_x = tip_x - ux * head_len
        base_y = tip_y - uy * head_len
        points = [
            (tip_x, tip_y),
            (int(base_x + px * head_width / 2), int(base_y + py * head_width / 2)),
            (int(base_x - px * head_width / 2), int(base_y - py * head_width / 2)),
        ]
        draw.polygon(points, fill=arrow_color)

    # Small tail feather at the tail end
    tail_len = 8
    tail_width = 6
    tail_base_x = tail_x - ux * tail_len
    tail_base_y = tail_y - uy * tail_len
    tail_points = [
        (tail_x, tail_y),
        (int(tail_base_x + px * tail_width / 2), int(tail_base_y + py * tail_width / 2)),
        (int(tail_base_x - px * tail_width / 2), int(tail_base_y - py * tail_width / 2)),
    ]
    draw.polygon(tail_points, fill=arrow_color)

    # Center dot
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=arrow_color)

    # ---- Footer ----
    compass_label = get_compass_label(direction)
    beaufort = get_beaufort_number(speed)

    footer_left = f"{compass_label}  {direction}\u00b0"
    footer_right = f"Beaufort {beaufort}"

    draw.text((8, 212), footer_left, font=font_footer, fill=WEATHER_COLORS['footer'])
    right_bbox = draw.textbbox((0, 0), footer_right, font=font_footer)
    right_w = right_bbox[2] - right_bbox[0]
    draw.text((width - right_w - 8, 212), footer_right, font=font_footer,
              fill=WEATHER_COLORS['footer'])

    return img


def refresh_weather_data():
    """Fetch and cache wind data."""
    try:
        wind = fetch_wind_data()
        if wind:
            with weather_cache_lock:
                weather_cache['wind'] = wind
                weather_cache['last_update'] = datetime.now(timezone.utc)
                weather_cache['error'] = None
            logger.info(f"Weather refresh: {wind['wind_speed']} mph from {wind['wind_direction']}°")
        else:
            with weather_cache_lock:
                weather_cache['error'] = 'No wind data returned'
    except Exception as e:
        logger.error(f"Weather refresh failed: {e}")
        with weather_cache_lock:
            weather_cache['error'] = str(e)


def background_weather_refresh():
    """Background thread for weather data refresh."""
    while True:
        refresh_weather_data()
        time.sleep(WEATHER_REFRESH_INTERVAL)


# ==================== FLASK ROUTES ====================

@app.route('/weather.bin')
def serve_weather_bin():
    with weather_cache_lock:
        wind = weather_cache['wind']
    if wind:
        img = render_wind_display(wind)
    else:
        img = Image.new('RGB', (OUTPUT_SIZE, OUTPUT_SIZE), WEATHER_COLORS['background'])
        draw = ImageDraw.Draw(img)
        draw.text((8, 4), "WIND", font=get_font(20), fill=WEATHER_COLORS['title'])
        draw.text((8, 100), "Loading...", font=get_font(24), fill=WEATHER_COLORS['speed_text'])
    return Response(image_to_rgb565(img), mimetype='application/octet-stream')


@app.route('/weather.png')
def serve_weather_png():
    with weather_cache_lock:
        wind = weather_cache['wind']
    if wind:
        img = render_wind_display(wind)
    else:
        img = Image.new('RGB', (OUTPUT_SIZE, OUTPUT_SIZE), WEATHER_COLORS['background'])
        draw = ImageDraw.Draw(img)
        draw.text((8, 4), "WIND", font=get_font(20), fill=WEATHER_COLORS['title'])
        draw.text((8, 100), "Loading...", font=get_font(24), fill=WEATHER_COLORS['speed_text'])
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')


@app.route('/weather/status')
def weather_status():
    with weather_cache_lock:
        wind = weather_cache['wind']
        last_update = weather_cache['last_update']
        error = weather_cache['error']
    result = {
        'last_update': last_update.isoformat() if last_update else None,
        'error': error,
        'available': wind is not None,
    }
    if wind:
        result['wind_speed'] = wind['wind_speed']
        result['wind_gust'] = wind['wind_gust']
        result['wind_direction'] = wind['wind_direction']
        result['compass'] = get_compass_label(wind['wind_direction'])
        result['beaufort'] = get_beaufort_number(wind['wind_speed'])
    return jsonify(result)


@app.route('/weather/refresh')
def weather_refresh_route():
    refresh_weather_data()
    with weather_cache_lock:
        wind = weather_cache['wind']
        error = weather_cache['error']
    return jsonify({
        'status': 'ok' if not error else 'error',
        'wind_speed': wind['wind_speed'] if wind else None,
        'wind_direction': wind['wind_direction'] if wind else None,
        'error': error,
    })


@app.route('/')
def index():
    return '<h1>Weather Station Proxy</h1><p><a href="/weather.png">Preview</a> | <a href="/weather/status">Status</a></p>'


def main():
    load_ecowitt_credentials()

    logger.info("Starting initial Ecowitt weather fetch...")
    refresh_weather_data()

    thread = threading.Thread(target=background_weather_refresh, daemon=True)
    thread.start()

    logger.info("Starting server on http://0.0.0.0:5050")
    app.run(host='0.0.0.0', port=5050, debug=False)


if __name__ == '__main__':
    main()
