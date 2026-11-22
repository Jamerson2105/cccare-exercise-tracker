// CCCARE Exercise Tracker - ESP32 sensor streamer
//
// The ESP32 only reads the ADXL335 accelerometer and streams the readings to the laptop
// over USB serial. Everything else (users, calibration, rep counting, voice, history)
// happens in the PC app: pc_app/app.py
//
// Serial protocol (115200 baud, one line per sample, about 50 samples per second):
//   on boot:      HELLO,CCCARE,1
//   each sample:  D,<millis>,<x_mV>,<y_mV>,<z_mV>
//
// Board: Freenove ESP32-WROVER (Tools > Board > esp32 > ESP32 Wrover Module)
//        or ESP32-S3 (ESP32S3 Dev Module) - pins are chosen automatically.
// Sensor: ADXL335 powered at 3.3 V.  VCC -> 3V3, GND -> GND
//   ESP32-WROVER: X -> 34, Y -> 35, Z -> 32  (ADC1 pins; unplug the camera module if attached)
//   ESP32-S3:     X -> 1,  Y -> 2,  Z -> 3
//
// Works with esp32 board package 3.0.7 or newer. No extra libraries needed.

// ---------- Pins ----------
#if CONFIG_IDF_TARGET_ESP32S3
const int PIN_X = 1;
const int PIN_Y = 2;
const int PIN_Z = 3;
#else
const int PIN_X = 34;
const int PIN_Y = 35;
const int PIN_Z = 32;
#endif

const unsigned long SAMPLE_MS = 20;   // 20 ms between samples = 50 Hz
const int READS_PER_SAMPLE = 4;       // average a few ADC reads to reduce noise

unsigned long lastSample = 0;

// Reads one pin several times and returns the average in millivolts
uint32_t readAveragedMilliVolts(int pin) {
  uint32_t total = 0;
  for (int i = 0; i < READS_PER_SAMPLE; i++) {
    total += analogReadMilliVolts(pin);
  }
  return total / READS_PER_SAMPLE;
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);   // 11 dB lets the ADC read the full 0-3.3 V range
  delay(100);
  Serial.println("HELLO,CCCARE,1");
  lastSample = millis();
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_MS) return;
  lastSample += SAMPLE_MS;
  if (now - lastSample > 10 * SAMPLE_MS) lastSample = now;   // don't try to "catch up" after a stall

  uint32_t x = readAveragedMilliVolts(PIN_X);
  uint32_t y = readAveragedMilliVolts(PIN_Y);
  uint32_t z = readAveragedMilliVolts(PIN_Z);

  // Send the boot line again every 5 s so the PC app can find the board even
  // if it opened the port after the ESP32 had already started.
  static unsigned long lastHello = 0;
  if (now - lastHello >= 5000) {
    lastHello = now;
    Serial.println("HELLO,CCCARE,1");
  }

  Serial.printf("D,%lu,%lu,%lu,%lu\n", now, (unsigned long)x, (unsigned long)y, (unsigned long)z);
}
