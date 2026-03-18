/*
 * Weather Station Wind Display - GeekMagic SmallTV Ultra
 * Shows wind speed, gust, and compass rose from Ecowitt weather station
 *
 * Features:
 * - Fetches rendered wind display from proxy server
 * - Refreshes every 60 seconds (proxy caches API data for 60s)
 * - AP mode for WiFi configuration
 * - OTA firmware updates
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ESP8266WebServer.h>
#include <ESP8266mDNS.h>
#include <ArduinoOTA.h>
#include <EEPROM.h>
#include <SPI.h>
#include <TFT_eSPI.h>

// Proxy server (Oracle Cloud)
const char* PROXY_HOST = "130.162.190.206";
const int PROXY_PORT = 5050;

// Device identity
const char* hostname = "weather-display";
const char* ap_ssid = "WeatherDisplay-Setup";

// Display settings
#define FRAME_SIZE 115200             // 240x240x2 bytes RGB565
#define REFRESH_INTERVAL_MS 60000     // Refresh every 60 seconds

// Streaming buffer (4KB = 2048 pixels)
#define STREAM_BUFFER_SIZE 4096
uint8_t streamBuffer[STREAM_BUFFER_SIZE];

// EEPROM Configuration
#define EEPROM_SIZE 100
#define WIFI_MAGIC 0x57  // 'W' for Weather

struct WiFiConfig {
  uint8_t magic;
  char ssid[32];
  char password[64];
};

bool apMode = false;
bool lastFetchOK = false;
unsigned long lastDisplayUpdate = 0;
int fetchCount = 0;
int failCount = 0;

ESP8266WebServer server(80);
TFT_eSPI tft = TFT_eSPI();
WiFiClient wifiClient;

// Forward declarations
void handleRoot();
void handleWiFiConfig();
void handleWiFiSave();
void handleUpdate();
void handleDoUpdate();
void setupOTA();
void showOTAProgress(unsigned int progress, unsigned int total);
bool fetchAndDisplay();
void showError(const char* msg);
bool loadWiFiConfig(WiFiConfig &config);
void saveWiFiConfig(const char* ssid, const char* password);
bool tryConnect(const char* ssid, const char* password, int maxAttempts);
void startAPMode();
void showAPMode();
bool checkProxyStatus();
void responsiveDelay(unsigned long ms);

// ==================== Display Functions ====================

bool fetchAndDisplay() {
  HTTPClient http;
  String url = String("http://") + PROXY_HOST + ":" + PROXY_PORT + "/weather.bin";

  Serial.print("Fetching weather: ");
  Serial.println(url);

  http.begin(wifiClient, url);
  http.setTimeout(15000);

  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    Serial.printf("HTTP error: %d\n", httpCode);
    http.end();
    return false;
  }

  int contentLength = http.getSize();
  if (contentLength != FRAME_SIZE) {
    Serial.printf("Wrong size: %d (expected %d)\n", contentLength, FRAME_SIZE);
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();

  tft.startWrite();
  tft.setAddrWindow(0, 0, 240, 240);

  int totalRead = 0;
  while (totalRead < contentLength) {
    int toRead = min((int)STREAM_BUFFER_SIZE, contentLength - totalRead);
    int bytesRead = stream->readBytes(streamBuffer, toRead);

    if (bytesRead <= 0) {
      Serial.println("Stream read failed");
      tft.endWrite();
      http.end();
      return false;
    }

    tft.pushColors((uint16_t*)streamBuffer, bytesRead / 2);
    totalRead += bytesRead;
    yield();
  }

  tft.endWrite();
  http.end();

  fetchCount++;
  Serial.printf("Displayed weather chart: %d bytes (fetch #%d)\n", totalRead, fetchCount);
  return true;
}

void showError(const char* msg) {
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(4);
  tft.setTextSize(1);
  tft.setTextColor(TFT_RED, TFT_BLACK);
  tft.drawString("Unavailable", 120, 80);
  tft.setTextColor(TFT_YELLOW, TFT_BLACK);
  tft.drawString(msg, 120, 120);
  tft.setTextColor(TFT_DARKGREY, TFT_BLACK);
  tft.drawString("Retrying...", 120, 160);
}

void responsiveDelay(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) {
    server.handleClient();
    ArduinoOTA.handle();
    delay(10);
  }
}

// ==================== Setup ====================

void setup() {
  Serial.begin(115200);
  Serial.println("\n\nWeather Station Display");

  // Backlight ON (GPIO5) - P-MOSFET = inverted
  pinMode(5, OUTPUT);
  digitalWrite(5, LOW);

  EEPROM.begin(EEPROM_SIZE);

  tft.init();
  tft.setRotation(0);
  tft.setSwapBytes(true);  // Required for RGB565 streaming

  // Boot screen
  tft.fillScreen(TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextColor(TFT_CYAN, TFT_BLACK);
  tft.setTextSize(2);
  tft.drawString("WEATHER", 120, 70);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);
  tft.drawString("STATION", 120, 110);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(1);
  tft.drawString("Connecting...", 120, 170);

  // Connect WiFi
  WiFiConfig config;
  bool connected = false;

  if (loadWiFiConfig(config)) {
    tft.setTextColor(TFT_CYAN, TFT_BLACK);
    tft.drawString(config.ssid, 120, 150);
    connected = tryConnect(config.ssid, config.password, 20);
  }

  if (connected) {
    Serial.println("\nConnected!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());

    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_GREEN, TFT_BLACK);
    tft.setTextSize(4);
    tft.drawString("Connected", 120, 30);
    tft.setTextColor(TFT_YELLOW, TFT_BLACK);
    tft.setTextSize(3);
    tft.drawString(WiFi.localIP().toString(), 120, 80);

    // Check proxy status
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextSize(2);
    tft.drawString("Checking proxy...", 120, 130);

    bool proxyOK = checkProxyStatus();

    tft.fillRect(0, 115, 240, 40, TFT_BLACK);
    tft.setTextSize(2);
    if (proxyOK) {
      tft.setTextColor(TFT_GREEN, TFT_BLACK);
      tft.drawString("Proxy: OK", 120, 130);
    } else {
      tft.setTextColor(TFT_RED, TFT_BLACK);
      tft.drawString("Proxy: OFFLINE", 120, 130);
    }

    delay(1500);

    WiFi.hostname(hostname);
    MDNS.begin(hostname);
    setupOTA();

    server.on("/", handleRoot);
    server.on("/wifi", handleWiFiConfig);
    server.on("/wifisave", handleWiFiSave);
    server.on("/update", HTTP_GET, handleUpdate);
    server.on("/update", HTTP_POST, []() {
      server.sendHeader("Connection", "close");
      server.send(200, "text/plain", Update.hasError() ? "FAIL" : "OK");
      delay(500);
      ESP.restart();
    }, handleDoUpdate);

    MDNS.addService("http", "tcp", 80);

    // First fetch
    Serial.println("Initial fetch...");
    if (!fetchAndDisplay()) {
      showError("Weather Data");
    } else {
      lastFetchOK = true;
    }
    lastDisplayUpdate = millis();

  } else {
    startAPMode();
  }

  server.begin();
}

void loop() {
  if (!apMode) {
    // Check WiFi and reconnect if needed
    if (WiFi.status() != WL_CONNECTED) {
      Serial.println("WiFi disconnected, reconnecting...");
      WiFi.reconnect();
      unsigned long start = millis();
      while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
        delay(500);
        yield();
      }
      if (WiFi.status() == WL_CONNECTED) {
        Serial.println("WiFi reconnected");
      }
    }

    ArduinoOTA.handle();
    MDNS.update();

    // Refresh display periodically
    if (millis() - lastDisplayUpdate >= REFRESH_INTERVAL_MS) {
      bool success = fetchAndDisplay();
      if (success) {
        lastFetchOK = true;
        failCount = 0;
      } else {
        failCount++;
        lastFetchOK = false;
        if (failCount >= 10) {
          showError("Too many failures");
          delay(5000);
          ESP.restart();
        }
      }
      lastDisplayUpdate = millis();
    }
  }
  server.handleClient();
  yield();
}

// ==================== Web Handlers ====================

void handleRoot() {
  String html = "<!DOCTYPE html><html><head>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<meta http-equiv='refresh' content='10'>";
  html += "<style>body{font-family:Arial;text-align:center;padding:20px;background:#1a1a2e;color:white;}";
  html += ".card{background:#2d2d44;padding:15px;border-radius:10px;margin:10px 0;}";
  html += ".ok{color:#00c8dc;}.err{color:#ff6b6b;}";
  html += "a{color:#00c8dc;}</style></head>";
  html += "<body><h1 style='color:#00c8dc;'>Weather Station</h1>";

  html += "<div class='card'>";
  html += "<p>Refresh: every " + String(REFRESH_INTERVAL_MS / 1000) + "s</p>";
  html += "<p>Fetches: " + String(fetchCount) + "</p>";
  html += "<p>Last fetch: <span class='" + String(lastFetchOK ? "ok" : "err") + "'>";
  html += lastFetchOK ? "OK" : "FAILED";
  html += "</span></p>";
  html += "</div>";

  html += "<div class='card'>";
  html += "<p>IP: " + WiFi.localIP().toString() + "</p>";
  html += "<p>Hostname: " + String(hostname) + ".local</p>";
  html += "<p>RSSI: " + String(WiFi.RSSI()) + " dBm</p>";
  html += "<p>Free heap: " + String(ESP.getFreeHeap()) + " bytes</p>";
  html += "</div>";

  html += "<p><a href='/wifi'>WiFi</a> | <a href='/update'>Update</a></p>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

// ==================== OTA ====================

void setupOTA() {
  ArduinoOTA.setHostname(hostname);
  ArduinoOTA.onStart([]() {
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextSize(2);
    tft.drawString("OTA Update", 120, 60);
    tft.drawRect(20, 120, 200, 20, TFT_WHITE);
  });
  ArduinoOTA.onEnd([]() {
    tft.fillScreen(TFT_GREEN);
    tft.setTextColor(TFT_BLACK, TFT_GREEN);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(4);
    tft.setTextSize(2);
    tft.drawString("Done!", 120, 110);
  });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    showOTAProgress(progress, total);
  });
  ArduinoOTA.onError([](ota_error_t error) {
    tft.fillScreen(TFT_RED);
    tft.setTextColor(TFT_WHITE, TFT_RED);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(4);
    tft.setTextSize(2);
    tft.drawString("Failed!", 120, 110);
  });
  ArduinoOTA.begin();
}

void showOTAProgress(unsigned int progress, unsigned int total) {
  unsigned int percent = (progress * 100) / total;
  int barWidth = (progress * 196) / total;
  tft.fillRect(22, 122, barWidth, 16, TFT_YELLOW);
  tft.fillRect(70, 155, 100, 40, TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextDatum(MC_DATUM);
  tft.setTextFont(2);
  tft.setTextSize(2);
  tft.drawString(String(percent) + "%", 120, 170);
}

void handleUpdate() {
  String html = "<!DOCTYPE html><html><head>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:Arial;text-align:center;padding:20px;background:#1a1a2e;color:white;}";
  html += "button{font-size:20px;padding:15px 30px;background:#00c8dc;border:none;border-radius:5px;}";
  html += "a{color:#00c8dc;}</style></head>";
  html += "<body><h1>Firmware Update</h1>";
  html += "<form method='POST' action='/update' enctype='multipart/form-data'>";
  html += "<input type='file' name='update' accept='.bin' style='color:white;margin:20px;'><br>";
  html += "<button type='submit'>Upload</button></form>";
  html += "<p><a href='/'>Back</a></p></body></html>";
  server.send(200, "text/html", html);
}

void handleDoUpdate() {
  HTTPUpload& upload = server.upload();
  static uint32_t totalSize = 0;

  if (upload.status == UPLOAD_FILE_START) {
    totalSize = 0;
    tft.fillScreen(TFT_BLACK);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextSize(2);
    tft.drawString("Web Update", 120, 60);
    tft.drawRect(20, 120, 200, 20, TFT_WHITE);
    uint32_t maxSketchSpace = (ESP.getFreeSketchSpace() - 0x1000) & 0xFFFFF000;
    Update.begin(maxSketchSpace);
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    Update.write(upload.buf, upload.currentSize);
    totalSize += upload.currentSize;
    unsigned int percent = min(99u, (totalSize * 100) / 400000);
    int barWidth = (percent * 196) / 100;
    tft.fillRect(22, 122, barWidth, 16, TFT_YELLOW);
    tft.fillRect(70, 155, 100, 40, TFT_BLACK);
    tft.setTextColor(TFT_WHITE, TFT_BLACK);
    tft.setTextDatum(MC_DATUM);
    tft.setTextFont(2);
    tft.setTextSize(2);
    tft.drawString(String(percent) + "%", 120, 170);
  } else if (upload.status == UPLOAD_FILE_END) {
    if (Update.end(true)) {
      tft.fillRect(22, 122, 196, 16, TFT_YELLOW);
      tft.fillRect(70, 155, 100, 40, TFT_BLACK);
      tft.drawString("100%", 120, 170);
      delay(500);
      tft.fillScreen(TFT_GREEN);
      tft.setTextColor(TFT_BLACK, TFT_GREEN);
      tft.setTextDatum(MC_DATUM);
      tft.setTextFont(4);
      tft.setTextSize(2);
      tft.drawString("Done!", 120, 110);
    } else {
      tft.fillScreen(TFT_RED);
      tft.setTextColor(TFT_WHITE, TFT_RED);
      tft.setTextDatum(MC_DATUM);
      tft.setTextFont(4);
      tft.setTextSize(2);
      tft.drawString("Failed!", 120, 110);
    }
  }
}

// ==================== WiFi ====================

bool loadWiFiConfig(WiFiConfig &config) {
  EEPROM.get(0, config);
  return config.magic == WIFI_MAGIC;
}

void saveWiFiConfig(const char* ssid, const char* password) {
  WiFiConfig config;
  config.magic = WIFI_MAGIC;
  strncpy(config.ssid, ssid, sizeof(config.ssid) - 1);
  strncpy(config.password, password, sizeof(config.password) - 1);
  EEPROM.put(0, config);
  EEPROM.commit();
}

bool tryConnect(const char* ssid, const char* password, int maxAttempts) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < maxAttempts) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  return WiFi.status() == WL_CONNECTED;
}

bool checkProxyStatus() {
  HTTPClient http;
  String url = String("http://") + PROXY_HOST + ":" + PROXY_PORT + "/status";

  Serial.print("Checking proxy: ");
  Serial.println(url);

  http.begin(wifiClient, url);
  http.setTimeout(5000);

  int httpCode = http.GET();
  http.end();

  if (httpCode == HTTP_CODE_OK) {
    Serial.println("Proxy OK");
    return true;
  } else {
    Serial.printf("Proxy check failed: %d\n", httpCode);
    return false;
  }
}

void startAPMode() {
  apMode = true;
  WiFi.mode(WIFI_AP);
  WiFi.softAP(ap_ssid);
  showAPMode();
  server.on("/", handleWiFiConfig);
  server.on("/wifisave", handleWiFiSave);
}

void showAPMode() {
  tft.fillScreen(TFT_CYAN);
  tft.setTextColor(TFT_BLACK, TFT_CYAN);
  tft.setTextDatum(MC_DATUM);
  tft.setTextSize(4);
  tft.drawString("WiFi", 120, 30);
  tft.setTextSize(3);
  tft.drawString("Connect:", 120, 80);
  tft.setTextColor(TFT_NAVY, TFT_CYAN);
  tft.setTextSize(2);
  tft.drawString(ap_ssid, 120, 120);
  tft.setTextColor(TFT_BLACK, TFT_CYAN);
  tft.setTextSize(3);
  tft.drawString("Browse:", 120, 160);
  tft.setTextColor(TFT_NAVY, TFT_CYAN);
  tft.drawString("192.168.4.1", 120, 200);
}

void handleWiFiConfig() {
  String html = "<!DOCTYPE html><html><head>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1'>";
  html += "<style>body{font-family:Arial;text-align:center;padding:20px;background:#1a1a2e;color:white;}";
  html += "input{font-size:18px;padding:10px;margin:5px;width:200px;border-radius:5px;border:none;}";
  html += "button{font-size:20px;padding:15px 30px;background:#00c8dc;color:#1a1a2e;border:none;border-radius:5px;}</style></head>";
  html += "<body><h1>WiFi Setup</h1><form action='/wifisave' method='POST'>";
  html += "<p>SSID:</p><input type='text' name='ssid' required><br>";
  html += "<p>Password:</p><input type='password' name='pass'><br><br>";
  html += "<button type='submit'>Save & Connect</button></form>";
  if (!apMode) html += "<p><a href='/' style='color:#00c8dc;'>Back</a></p>";
  html += "</body></html>";
  server.send(200, "text/html", html);
}

void handleWiFiSave() {
  String newSSID = server.arg("ssid");
  String newPass = server.arg("pass");
  if (newSSID.length() > 0) {
    saveWiFiConfig(newSSID.c_str(), newPass.c_str());
    server.send(200, "text/html", "<html><body style='background:#1a1a2e;color:white;text-align:center;padding:50px;'><h1>Saved!</h1><p>Rebooting...</p></body></html>");
    tft.fillScreen(TFT_GREEN);
    tft.setTextColor(TFT_BLACK, TFT_GREEN);
    tft.setTextDatum(MC_DATUM);
    tft.setTextSize(4);
    tft.drawString("Saved!", 120, 100);
    delay(2000);
    ESP.restart();
  } else {
    server.sendHeader("Location", "/");
    server.send(302);
  }
}
