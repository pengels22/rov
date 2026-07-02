#include <Arduino.h>
#include "esp_wifi.h"
#include <WebServer.h>
#include <VL53L1X.h>
#include <Wire.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <Preferences.h>
// =========================
// Wi-Fi config
// =========================
// Option A: connect to your ROV network/router
const char* WIFI_SSID = "Atlantis";
const char* WIFI_PASS = "NerdsRock!";

// Option B: if STA fails, XIAO creates this fallback AP
const char* AP_SSID = "ROV-Turret";
const char* AP_PASS = "turret1234";

// =========================
// ToF range sensor config
// =========================
#define TOF_I2C_ADDR 0x29
#define TOF_TIMEOUT_MS 200
#define TOF_CONTINUOUS_PERIOD_MS 100
#define TOF_TIMING_BUDGET_US 50000

// =========================
// I2C config
// =========================
#define I2C_SDA_PIN SDA
#define I2C_SCL_PIN SCL

// =========================
// XIAO ESP32S3 Sense camera pins
// From Seeed camera mapping
// =========================
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     10
#define SIOD_GPIO_NUM     40
#define SIOC_GPIO_NUM     39

#define Y9_GPIO_NUM       48
#define Y8_GPIO_NUM       11
#define Y7_GPIO_NUM       12
#define Y6_GPIO_NUM       14
#define Y5_GPIO_NUM       16
#define Y4_GPIO_NUM       18
#define Y3_GPIO_NUM       17
#define Y2_GPIO_NUM       15
#define VSYNC_GPIO_NUM    38
#define HREF_GPIO_NUM     47
#define PCLK_GPIO_NUM     13

WebServer server(80);
WiFiServer streamServer(81);

// =========================
// Status flags
// =========================
bool wifi_ok = false;
bool camera_ok = false;
bool accel_ok = false;
bool tof_ok = false;
int cameraBrightness = 0;

String wifi_mode = "NONE";
String accel_type = "NONE";
String last_error = "";

// Preferences for storing WiFi credentials
Preferences prefs;
VL53L1X tofSensor;
bool i2c_started = false;


// =========================
// Sensor values
// =========================
float ax_g = 0.0;
float ay_g = 0.0;
float az_g = 1.0;
float pitch_deg = 0.0;
float roll_deg = 0.0;
float range_in = -1.0;

unsigned long lastSensorMs = 0;
unsigned long lastSerialStatusMs = 0;

// =========================
// Stream state (non-blocking)
// =========================
WiFiClient streamClient;
bool streamActive = false;
unsigned long lastFrameMs = 0;
const unsigned long FRAME_INTERVAL_MS = 80; // ~12 FPS
const framesize_t STREAM_FRAME_SIZE_PSRAM = FRAMESIZE_SVGA;  // 800x600, matches chassis stream
const int STREAM_JPEG_QUALITY_PSRAM = 10;

// =========================
// Error helper
// =========================
void setError(const String& msg) {
  last_error = msg;
  Serial.print("ERR,");
  Serial.println(msg);
}

// =========================
// Camera setup
// =========================
bool initCamera() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    // Match the chassis stream dimensions while keeping enough headroom for
    // smooth capture and network delivery.
    config.frame_size = STREAM_FRAME_SIZE_PSRAM;
    config.jpeg_quality = 10;
    config.fb_count = 2;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_QQVGA;  // 160x120 fallback, fits in DRAM
    config.jpeg_quality = 15;
    config.fb_count = 1;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    camera_ok = false;
    setError("CAMERA_INIT_FAIL");
    return false;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    if (psramFound()) {
      s->set_framesize(s, STREAM_FRAME_SIZE_PSRAM);
      s->set_quality(s, STREAM_JPEG_QUALITY_PSRAM);
    } else {
      // No PSRAM: buffer is QQVGA-sized DRAM. Keep sensor at QQVGA to match.
      s->set_framesize(s, FRAMESIZE_QQVGA);
      s->set_quality(s, 15);
    }
    // Bias the OV2640 a bit toward crisper edges and less smearing in dim scenes.
    s->set_brightness(s, cameraBrightness);
    s->set_contrast(s, 1);
    s->set_saturation(s, 0);
    s->set_sharpness(s, 2);
    s->set_denoise(s, 0);
    s->set_gain_ctrl(s, 1);
    s->set_exposure_ctrl(s, 1);
    s->set_whitebal(s, 1);
    s->set_awb_gain(s, 1);
    s->set_aec2(s, 1);
    s->set_ae_level(s, 0);
    s->set_gainceiling(s, GAINCEILING_8X);
    s->set_bpc(s, 1);
    s->set_wpc(s, 1);
    s->set_raw_gma(s, 1);
    s->set_lenc(s, 1);
    s->set_vflip(s, 1);
    s->set_hmirror(s, 0);
  }

  camera_ok = true;
  return true;
}

bool applyCameraBrightness(int level) {
  sensor_t* s = esp_camera_sensor_get();
  if (!s) {
    setError("CAMERA_SENSOR_MISSING");
    return false;
  }

  level = constrain(level, -2, 2);
  if (s->set_brightness(s, level) != 0) {
    setError("CAMERA_BRIGHTNESS_FAIL");
    return false;
  }

  cameraBrightness = level;
  return true;
}

// =========================
// MJPEG stream
// =========================
void handleRoot() {
  // Serve a full-screen canvas HUD page.
  // Stream (port 81) is embedded in <img>; a <canvas> floats on top.
  // JS polls /status every 250 ms and redraws the overlay — no page reload needed.
  String h = "";
  h += "<!DOCTYPE html><html><head>";
  h += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
  h += "<title>ROV Turret</title>";
  h += "<style>";
  h += "*{box-sizing:border-box;margin:0;padding:0}";
  h += "body{background:#111;display:flex;align-items:center;justify-content:center;height:100vh;overflow:hidden}";
  h += "#w{position:relative;display:inline-block}";
  h += "#s{display:block;width:250px;height:250px;object-fit:cover}";
  h += "#hud{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none}";
  h += "</style></head><body>";
  h += "<div id='w'><img id='s' alt='stream'><canvas id='hud'></canvas></div>";
  h += "<script>";
  // Use window.location.hostname so the page works in both STA and AP mode
  h += "var img=document.getElementById('s');";
  h += "var cv=document.getElementById('hud');";
  h += "var cx=cv.getContext('2d');";
  h += "var sd={};";
  h += "img.src='http://'+location.hostname+':81';";
  // Keep canvas dimensions matched to the actual rendered img size
  h += "function sync(){";
  h +=   "cv.width=img.offsetWidth||320;";
  h +=   "cv.height=img.offsetHeight||240;";
  h += "}";
  h += "img.addEventListener('load',sync);";
  h += "window.addEventListener('resize',function(){sync();draw();});";
  // Draw HUD overlay
  h += "function draw(){";
  h +=   "sync();";
  h +=   "cx.clearRect(0,0,cv.width,cv.height);";
  h +=   "var fh=Math.max(11,Math.floor(cv.height/18));";
  h +=   "var lh=Math.floor(fh*1.55);";
  h +=   "var px=10,py=fh+8;";
  h +=   "var deg='\\u00b0';";
  h +=   "var r=sd.range_in>0?sd.range_in.toFixed(1)+' in':'NO RTN';";
  h +=   "var lines=[";
  h +=     "'\\u25c6 ROV TURRET',";
  h +=     "'PITCH : '+((sd.pitch_deg!==undefined)?sd.pitch_deg.toFixed(1)+deg:'---'),";
  h +=     "'ROLL  : '+((sd.roll_deg!==undefined)?sd.roll_deg.toFixed(1)+deg:'---'),";
  h +=     "'RANGE : '+r,";
  h +=     "'AX '+((sd.ax||0).toFixed(2))+'  AY '+((sd.ay||0).toFixed(2))+'  AZ '+((sd.az||0).toFixed(2)),";
  h +=     "(sd.camera_ok?'CAM OK':'CAM ERR')+'  '+(sd.wifi_mode||''),";
  h +=     "(sd.last_error&&sd.last_error.length?'! '+sd.last_error:'')";
  h +=   "];";
  h +=   "cx.font='bold '+fh+'px monospace';";
  h +=   "var mw=0;";
  h +=   "lines.forEach(function(l){var w=cx.measureText(l).width;if(w>mw)mw=w;});";
  h +=   "cx.fillStyle='rgba(0,0,0,0.55)';";
  h +=   "cx.fillRect(px-6,py-fh,mw+18,lines.length*lh+10);";
  h +=   "lines.forEach(function(l,i){";
  h +=     "if(!l)return;";
  h +=     "cx.fillStyle=(i===0)?'#fff':(i>=lines.length-2?'#aaa':'#0f0');";
  h +=     "cx.fillText(l,px,py+i*lh);";
  h +=   "});";
  h += "}";
  // Poll /status; same-origin so no CORS needed
  h += "function poll(){";
  h +=   "fetch('/status')";
  h +=     ".then(function(r){return r.json();})";
  h +=     ".then(function(j){sd=j;draw();})";
  h +=     ".catch(function(){draw();});";
  h +=   "setTimeout(poll,250);";
  h += "}";
  h += "poll();";
  h += "</script></body></html>";
  server.send(200, "text/html", h);
}

void handleStatus() {
  String json = "{";
  json += "\"wifi_ok\":" + String(wifi_ok ? "true" : "false") + ",";
  json += "\"wifi_mode\":\"" + wifi_mode + "\",";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"camera_ok\":" + String(camera_ok ? "true" : "false") + ",";
  json += "\"accel_ok\":" + String(accel_ok ? "true" : "false") + ",";
  json += "\"accel_type\":\"" + accel_type + "\",";
  json += "\"tof_ok\":" + String(tof_ok ? "true" : "false") + ",";
  json += "\"ultrasonic_ok\":" + String(tof_ok ? "true" : "false") + ",";
  json += "\"range_in\":" + String(range_in, 2) + ",";
  json += "\"ax\":" + String(ax_g, 3) + ",";
  json += "\"ay\":" + String(ay_g, 3) + ",";
  json += "\"az\":" + String(az_g, 3) + ",";
  json += "\"pitch_deg\":" + String(pitch_deg, 2) + ",";
  json += "\"roll_deg\":" + String(roll_deg, 2) + ",";
  json += "\"last_error\":\"" + last_error + "\"";
  json += "}";
  server.send(200, "application/json", json);
}

// Non-blocking MJPEG server on port 81.
// Called every loop() — sends at most one frame per FRAME_INTERVAL_MS,
// then returns immediately so server.handleClient() and sensor reads keep running.
void runStreamServer() {
  // A browser refresh can leave the ESP32 side of the old TCP connection
  // looking alive for a long time. Let the newest connection take over so
  // the dashboard can recover without rebooting the turret.
  WiFiClient pendingClient = streamServer.available();
  if (pendingClient) {
    if (streamActive) {
      streamClient.stop();
      streamActive = false;
    }
    streamClient = pendingClient;
  }

  // Accept a new client when idle
  if (!streamActive) {
    if (!streamClient) return;

    if (!camera_ok) {
      streamClient.print("HTTP/1.1 503 Service Unavailable\r\n"
                         "Content-Type: text/plain\r\n\r\n"
                         "Camera not available");
      streamClient.stop();
      return;
    }

    streamClient.print("HTTP/1.1 200 OK\r\n"
                       "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                       "Access-Control-Allow-Origin: *\r\n"
                       "Cache-Control: no-cache, no-store\r\n"
                       "\r\n");
    streamActive = true;
    lastFrameMs = 0; // send first frame immediately
    return;
  }

  // Drop client if it disconnected
  if (!streamClient.connected()) {
    streamClient.stop();
    streamActive = false;
    return;
  }

  // Rate-limit: one frame per FRAME_INTERVAL_MS
  unsigned long now = millis();
  if (now - lastFrameMs < FRAME_INTERVAL_MS) return;
  lastFrameMs = now;

  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    setError("CAMERA_FRAME_FAIL");
    return;
  }

  streamClient.print("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ");
  streamClient.print(fb->len);
  streamClient.print("\r\n\r\n");
  size_t written = streamClient.write(fb->buf, fb->len);
  streamClient.print("\r\n");
  esp_camera_fb_return(fb);

  if (written == 0) {
    streamClient.stop();
    streamActive = false;
  }
}

// =========================
// Wi-Fi setup
// =========================
void scanWiFiNetworks() {
  Serial.println("INFO,WIFI_SCAN_START");

  int n = WiFi.scanNetworks();

  if (n <= 0) {
    Serial.println("ERR,WIFI_SCAN_NONE");
    return;
  }

  for (int i = 0; i < n; i++) {
    Serial.print("WIFI,");
    Serial.print(WiFi.SSID(i));
    Serial.print(",");
    Serial.print(WiFi.RSSI(i));
    Serial.print(",");
    Serial.print(WiFi.channel(i));
    Serial.print(",");
    Serial.println(WiFi.encryptionType(i));
  }

  Serial.println("INFO,WIFI_SCAN_DONE");
}
void initWiFi() {
  // Initialize preferences and attempt to use saved credentials
  prefs.begin("turret", false);

  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false);
  delay(500);

  esp_wifi_set_ps(WIFI_PS_NONE);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);

  scanWiFiNetworks();

  // Prefer saved credentials if present
  String saved_ssid = prefs.getString("ssid", "");
  String saved_pass = prefs.getString("pass", "");

  if (saved_ssid.length() > 0) {
    Serial.print("INFO,WIFI_CONNECTING_SAVED,");
    Serial.print(saved_ssid);
    Serial.print(",PASS_LEN=");
    Serial.println(saved_pass.length());
    WiFi.begin(saved_ssid.c_str(), saved_pass.c_str(), 6);
  } else {
    Serial.print("INFO,WIFI_CONNECTING,");
    Serial.print(WIFI_SSID);
    Serial.print(",PASS_LEN=");
    Serial.println(strlen(WIFI_PASS));
    WiFi.begin(WIFI_SSID, WIFI_PASS,6);
  }

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 30000) {
    delay(500);
    Serial.print("INFO,WIFI_STATUS,");
    Serial.println(WiFi.status());
  }

  if (WiFi.status() == WL_CONNECTED) {
    wifi_ok = true;
    wifi_mode = "STA";
    Serial.print("OK,WIFI_STA,");
    Serial.print(WiFi.localIP());
    Serial.print(",RSSI=");
    Serial.println(WiFi.RSSI());

    // Announce IP in a simple, easy-to-parse line for the Pi
    Serial.print("IP,");
    Serial.println(WiFi.localIP().toString());
    return;
  }

  wifi_ok = false;
  wifi_mode = "STA_FAIL";

  Serial.print("ERR,WIFI_STA_FAIL,STATUS=");
  Serial.println(WiFi.status());
}



// =========================
// Accelerometer support
// MPU6050 and ADXL345, no external libraries
// =========================
bool i2cWrite8(uint8_t addr, uint8_t reg, uint8_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool i2cReadBytes(uint8_t addr, uint8_t reg, uint8_t* buf, uint8_t len) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;

  uint8_t got = Wire.requestFrom(addr, len);
  if (got != len) return false;

  for (uint8_t i = 0; i < len; i++) {
    buf[i] = Wire.read();
  }
  return true;
}

bool i2cDevicePresent(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

void ensureI2C() {
  if (i2c_started) return;

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setClock(400000);
  delay(100);
  i2c_started = true;
}

bool initAccel() {
  ensureI2C();

  // MPU6050 at 0x68 or 0x69
  if (i2cDevicePresent(0x68) || i2cDevicePresent(0x69)) {
    uint8_t addr = i2cDevicePresent(0x68) ? 0x68 : 0x69;
    if (i2cWrite8(addr, 0x6B, 0x00)) { // wake from sleep
      accel_ok = true;
      accel_type = (addr == 0x68) ? "MPU6050_0x68" : "MPU6050_0x69";
      Serial.print("OK,ACCEL,");
      Serial.println(accel_type);
      return true;
    }
  }

  // ADXL345 at 0x53
  if (i2cDevicePresent(0x53)) {
    // POWER_CTL measure bit
    bool ok1 = i2cWrite8(0x53, 0x2D, 0x08);
    // DATA_FORMAT full resolution, +/- 2g
    bool ok2 = i2cWrite8(0x53, 0x31, 0x08);

    if (ok1 && ok2) {
      accel_ok = true;
      accel_type = "ADXL345_0x53";
      Serial.println("OK,ACCEL,ADXL345_0x53");
      return true;
    }
  }

  accel_ok = false;
  accel_type = "NONE";
  setError("ACCEL_NOT_FOUND");
  return false;
}

bool readAccel() {
  if (!accel_ok) return false;

  if (accel_type.startsWith("MPU6050")) {
    uint8_t addr = accel_type.endsWith("0x69") ? 0x69 : 0x68;
    uint8_t data[6];

    if (!i2cReadBytes(addr, 0x3B, data, 6)) {
      accel_ok = false;
      setError("ACCEL_READ_FAIL");
      return false;
    }

    int16_t rawX = (int16_t)((data[0] << 8) | data[1]);
    int16_t rawY = (int16_t)((data[2] << 8) | data[3]);
    int16_t rawZ = (int16_t)((data[4] << 8) | data[5]);

    ax_g = rawX / 16384.0;
    ay_g = rawY / 16384.0;
    az_g = rawZ / 16384.0;
  }
  else if (accel_type.startsWith("ADXL345")) {
    uint8_t data[6];

    if (!i2cReadBytes(0x53, 0x32, data, 6)) {
      accel_ok = false;
      setError("ACCEL_READ_FAIL");
      return false;
    }

    int16_t rawX = (int16_t)((data[1] << 8) | data[0]);
    int16_t rawY = (int16_t)((data[3] << 8) | data[2]);
    int16_t rawZ = (int16_t)((data[5] << 8) | data[4]);

    // Sensor is mounted 90 degrees counterclockwise around Z relative to the
    // chassis, so remap X/Y into chassis coordinates before computing angles.
    int16_t mappedX = rawY;
    int16_t mappedY = -rawX;

    // ADXL345 full-res is about 256 LSB/g
    ax_g = mappedX / 256.0;
    ay_g = mappedY / 256.0;
    az_g = rawZ / 256.0;
  }
  else {
    return false;
  }

  pitch_deg = atan2(-ax_g, sqrt(ay_g * ay_g + az_g * az_g)) * 180.0 / PI;
  roll_deg  = atan2(ay_g, az_g) * 180.0 / PI;

  return true;
}

// =========================
// ToF range sensor
// =========================
bool initTof() {
  ensureI2C();

  if (!i2cDevicePresent(TOF_I2C_ADDR)) {
    tof_ok = false;
    setError("TOF_NOT_FOUND");
    return false;
  }

  tofSensor.setAddress(TOF_I2C_ADDR);
  tofSensor.setTimeout(TOF_TIMEOUT_MS);

  if (!tofSensor.init()) {
    tof_ok = false;
    setError("TOF_INIT_FAIL");
    return false;
  }

  // The VL53L1X module the turret uses is a 4 m sensor, so prefer long range.
  tofSensor.setDistanceMode(VL53L1X::Long);
  tofSensor.setMeasurementTimingBudget(TOF_TIMING_BUDGET_US);
  tofSensor.startContinuous(TOF_CONTINUOUS_PERIOD_MS);
  tof_ok = true;
  return true;
}

bool readTof() {
  uint16_t range_mm = tofSensor.readRangeContinuousMillimeters();

  if (tofSensor.timeoutOccurred()) {
    tof_ok = false;
    range_in = -1.0;
    return false;
  }

  range_in = range_mm / 25.4;
  tof_ok = true;
  return true;
}

// =========================
// Serial command handling
// =========================
void printTelemetry() {
  // T,<ax>,<ay>,<az>,<range_in>,<pitch_deg>,<roll_deg>,<flags>
  String flags = "";
  flags += camera_ok ? "C1" : "C0";
  flags += accel_ok ? "_A1" : "_A0";
  // Keep the legacy U flag so existing Pi-side parsers do not need to change.
  flags += tof_ok ? "_U1" : "_U0";
  flags += wifi_ok ? "_W1" : "_W0";

  Serial.print("T,");
  Serial.print(ax_g, 3); Serial.print(",");
  Serial.print(ay_g, 3); Serial.print(",");
  Serial.print(az_g, 3); Serial.print(",");
  Serial.print(range_in, 2); Serial.print(",");
  Serial.print(pitch_deg, 2); Serial.print(",");
  Serial.print(roll_deg, 2); Serial.print(",");
  Serial.println(flags);
}

void handleSerialCommand(const String& cmd) {
  if (cmd == "Q") {
    printTelemetry();
  }
  else if (cmd == "PING") {
    Serial.println("OK,TURRET");
  }
  else if (cmd == "STATUS") {
    Serial.print("STATUS,");
    Serial.print(wifi_mode); Serial.print(",");
    Serial.print(WiFi.localIP()); Serial.print(",");
    Serial.print(camera_ok ? "CAM_OK" : "CAM_ERR"); Serial.print(",");
    Serial.print(accel_ok ? accel_type : "ACCEL_ERR"); Serial.print(",");
    // Preserve the legacy field name in the STATUS response for compatibility.
    Serial.println(tof_ok ? "US_OK" : "US_ERR");
  }
  else if (cmd == "ACCEL_REINIT") {
    initAccel();
    Serial.println(accel_ok ? "OK,ACCEL_REINIT" : "ERR,ACCEL_REINIT");
  }
  else if (cmd == "TOF_REINIT") {
    initTof();
    Serial.println(tof_ok ? "OK,TOF_REINIT" : "ERR,TOF_REINIT");
  }
  else if (cmd == "CAM_REINIT") {
    camera_ok = initCamera();
    Serial.println(camera_ok ? "OK,CAM_REINIT" : "ERR,CAM_REINIT");
  }
  else if (cmd.startsWith("CAM_BRIGHTNESS,")) {
    int comma = cmd.indexOf(',');
    int level = cmd.substring(comma + 1).toInt();
    bool ok = applyCameraBrightness(level);
    if (ok) {
      Serial.print("OK,CAM_BRIGHTNESS,");
      Serial.println(cameraBrightness);
    } else {
      Serial.println("ERR,CAM_BRIGHTNESS");
    }
  }
  else if (cmd.startsWith("SET_WIFI,")) {
    // Format: SET_WIFI,<ssid>,<pass>
    int first = cmd.indexOf(',');
    int second = cmd.indexOf(',', first + 1);
    if (second <= first) {
      Serial.println("ERR,WIFI_SET_BAD_FORMAT");
      return;
    }

    String ssid = cmd.substring(first + 1, second);
    String pass = cmd.substring(second + 1);

    // Save credentials
    prefs.putString("ssid", ssid);
    prefs.putString("pass", pass);

    Serial.print("INFO,WIFI_SAVED,");
    Serial.print(ssid);
    Serial.print(",PASS_LEN=");
    Serial.println(pass.length());

    // Attempt to connect
    WiFi.begin(ssid.c_str(), pass.c_str());
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < 15000) {
      delay(200);
    }

    if (WiFi.status() == WL_CONNECTED) {
      Serial.print("OK,WIFI_SET,IP,");
      Serial.println(WiFi.localIP().toString());
    } else {
      Serial.println("ERR,WIFI_SET_CONNECT_FAIL");
    }
  }
  else if (cmd == "TILT_ZERO") {
    // Radxa can treat current pitch as zero.
    // XIAO currently just confirms command.
    Serial.println("OK,TILT_ZERO");
  }
  else {
    Serial.print("ERR,UNKNOWN,");
    Serial.println(cmd);
  }
}

// =========================
// Setup / loop
// =========================
void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println("OK,TURRET_BOOT");
  Serial.printf("INFO,PSRAM,%s,SIZE=%u\n", psramFound() ? "YES" : "NO", (unsigned)ESP.getPsramSize());

  initWiFi();

  camera_ok = initCamera();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.begin();
  streamServer.begin();

  Serial.println("OK,HTTP_SERVER_STARTED");

  initAccel();
  initTof();

  // Take first readings
  readAccel();
  if (tof_ok) {
    readTof();
  }

  Serial.println("OK,TURRET_READY");

  // Print an opening burst of telemetry for the Pi to consume
  // Provides a few quick samples so the backend can read all sensor fields
  for (int i = 0; i < 3; i++) {
    printTelemetry();
    delay(100);
  }
}

void loop() {
  server.handleClient();
  runStreamServer();

  unsigned long now = millis();

  if (now - lastSensorMs >= 100) {
    lastSensorMs = now;

    if (accel_ok) {
      readAccel();
    }

    // Keep trying to recover the ToF sensor after transient startup/read timeouts.
    static unsigned long lastTofInitAttemptMs = 0;
    if (!tof_ok && now - lastTofInitAttemptMs >= 2000) {
      lastTofInitAttemptMs = now;
      initTof();
    }

    bool tof_was_online = tof_ok;
    bool tof_read_ok = tof_ok ? readTof() : false;
    if (!tof_read_ok) {
      // Do not spam serial every 100ms.
      static unsigned long lastTofErrMs = 0;
      if (now - lastTofErrMs > 2000) {
        lastTofErrMs = now;
        setError(tof_was_online ? "TOF_READ_FAIL" : "TOF_OFFLINE");
      }
    }
  }

  while (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() > 0) {
      handleSerialCommand(cmd);
    }
  }

  // Optional heartbeat every 5 seconds
  if (now - lastSerialStatusMs >= 5000) {
    lastSerialStatusMs = now;
    Serial.println("HB,TURRET");
  }
}
