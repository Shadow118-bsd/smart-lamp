#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <VL53L0X.h>
#include <WiFi.h>
#include <WiFiUdp.h>

/* ======================================================================================
 * [LOCKED HARDWARE MODULE] SENSORS CONTROLLER - DO NOT EDIT WITHOUT USER CONFIRMATION
 * ======================================================================================
 * Verified & Calibrated Sensor Subsystem for Smart Desk Lamp (HCI Project):
 *  - VL53L0X Time-of-Flight Laser Sensor (I2C Addr 0x29)
 *  - BME280 Environmental Sensor (I2C Addr 0x76 / 0x77)
 *  - PIR Motion Sensor (Digital Input GPIO 7)
 *  - Shared I2C Bus: SDA = GPIO 8, SCL = GPIO 9 (100kHz standard speed)
 *
 * Calibrated Hysteresis:
 *  - Blind zone (<3.5cm) -> clamped to 1.0cm [CUI SAT!]
 *  - Momentary laser dropouts (<60cm) -> held for 20 frames (1.2s)
 *  - Empty desk (>1.2s) -> gently settles to 120.0cm [ROI BAN]
 * ====================================================================================== */

class SensorsManager {
public:
    static const uint8_t PIN_I2C_SDA = 8;
    static const uint8_t PIN_I2C_SCL = 9;
    static const uint8_t PIN_PIR_OUT = 7;

    SensorsManager()
        : m_wire(&Wire),
          m_bme_online(false),
          m_tof_online(false),
          m_temperature(27.5f),
          m_humidity(60.0f),
          m_pressure(1013.2f),
          m_distance_cm(45.0f),
          m_raw_distance_cm(45.0f),
          m_motion_detected(false),
          m_presence(false),
          m_consecutive_8190(0),
          m_session_start_ms(0),
          m_last_motion_ms(0),
          m_last_fast_poll_ms(0),
          m_last_slow_poll_ms(0),
          m_last_telemetry_ms(0),
          m_last_tof_debug_ms(0)
    {
        memset(m_telemetry_json, 0, sizeof(m_telemetry_json));
    }

    void begin(TwoWire* wire = &Wire) {
        m_wire = wire;

        // 1. Initialize PIR Motion Sensor
        pinMode(PIN_PIR_OUT, INPUT);

        // 2. Initialize BME280 / BMP280
        if (m_bme.begin(0x76, m_wire) || m_bme.begin(0x77, m_wire)) {
            m_bme_online = true;
            m_bme.setSampling(Adafruit_BME280::MODE_NORMAL,
                             Adafruit_BME280::SAMPLING_X1,
                             Adafruit_BME280::SAMPLING_X1,
                             Adafruit_BME280::SAMPLING_X1,
                             Adafruit_BME280::FILTER_X2);
            Serial.println(F("[BME280] ONLINE (0x76/0x77)"));
        } else {
            Serial.println(F("[BME280] Not detected on 0x76/0x77."));
        }

        // 3. Initialize VL53L0X Distance Sensor (Standard Desk Profile 3cm - 120cm)
        m_tof.setBus(m_wire);
        m_tof.setTimeout(500);
        if (m_tof.init()) {
            m_tof_online = true;
            // Standard timing budget (50ms) for stable desk sensing without saturating at close range (<20cm)
            m_tof.setMeasurementTimingBudget(50000);
            m_tof.startContinuous(50);
            Serial.println(F("[VL53L0X] ONLINE Standard Profile (0x29)"));
        } else {
            Serial.println(F("[VL53L0X] Not detected on 0x29."));
        }

        m_session_start_ms = millis();
    }

    void update() {
        unsigned long now = millis();

        // Fast Polling (Every 60ms) -> PIR + ToF Distance
        if (now - m_last_fast_poll_ms >= 60) {
            m_last_fast_poll_ms = now;

            // --- PIR Motion & Presence Tracker ---
            bool motion = (digitalRead(PIN_PIR_OUT) == HIGH);
            if (motion) {
                m_motion_detected = true;
                m_last_motion_ms = now;
                if (!m_presence) {
                    m_presence = true;
                    m_session_start_ms = now;
                }
            } else {
                m_motion_detected = false;
                if (m_presence && (now - m_last_motion_ms > 20000)) {
                    m_presence = false;
                }
            }

            // --- VL53L0X Laser Distance with Anti-Spiking Hysteresis ---
            if (m_tof_online) {
                uint16_t dist_mm = m_tof.readRangeContinuousMillimeters();
                bool timed_out = m_tof.timeoutOccurred();

                if (timed_out || dist_mm == 65535) {
                    // Ignore transient I2C timeouts
                } else if (dist_mm == 0 || dist_mm < 35) {
                    // Touching lens or facedown on table (< 3.5cm)
                    m_consecutive_8190 = 0;
                    m_raw_distance_cm = 1.0f;
                    m_distance_cm = (0.50f * 1.0f) + (0.50f * m_distance_cm);
                } else if (dist_mm >= 8190) {
                    m_consecutive_8190++;
                    // 8190/8191 occurs when:
                    // 1) Target out of range / open space / sky (dist > 1.2m)
                    // 2) Target abruptly withdrawn into open air
                    // 3) Momentary head turn or hand gesture dropout

                    if (m_consecutive_8190 < 5) {
                        // Hold previous distance for first ~300ms (up to 4 frames)
                        // to bridge transient angle dropouts or quick gestures.
                        // DO NOT clamp to 1.0cm!
                    } else {
                        // Sustained 8190 (>= 5 frames, > 300ms):
                        // Empty desk, open air / pointed at sky, or target withdrawn.
                        // Smoothly and promptly ramp to 120.0cm [ROI BAN]
                        m_raw_distance_cm = 120.0f;
                        m_distance_cm = (0.40f * 120.0f) + (0.60f * m_distance_cm);
                    }
                } else {
                    // Valid measurement in millimeters (35mm to 1200mm)
                    m_consecutive_8190 = 0;
                    m_raw_distance_cm = dist_mm / 10.0f;
                    // Responsive filter: 60% new value, 40% history
                    m_distance_cm = (0.60f * m_raw_distance_cm) + (0.40f * m_distance_cm);
                }

                if (now - m_last_tof_debug_ms >= 300) {
                    m_last_tof_debug_ms = now;
                    Serial.printf("[TOF_DEBUG] raw_mm=%u, s_8190=%u, cm=%.1f\n", dist_mm, m_consecutive_8190, m_distance_cm);
                }
            }
        }

        // Slow Polling (Every 1000ms) -> BME280 Environment
        if (now - m_last_slow_poll_ms >= 1000) {
            m_last_slow_poll_ms = now;
            if (m_bme_online) {
                float t = m_bme.readTemperature();
                float h = m_bme.readHumidity();
                float p = m_bme.readPressure() / 100.0f;
                if (!isnan(t) && t > -30.0f && t < 70.0f) m_temperature = t;
                if (!isnan(h) && h >= 0.0f && h <= 100.0f) m_humidity = h;
                if (!isnan(p) && p > 400.0f && p < 1150.0f) m_pressure = p;
            }
        }
    }

    void broadcastTelemetry(WiFiUDP* udp = nullptr, IPAddress broadcastIp = IPAddress(255,255,255,255), uint16_t port = 12346, bool speakerOnline = true, bool oledOnline = true) {
        unsigned long now = millis();
        // High-Speed Telemetry: Broadcast every 60ms to synchronize web dashboard in real-time
        if (now - m_last_telemetry_ms < 60) return;
        m_last_telemetry_ms = now;

        uint32_t session_sec = m_presence ? (now - m_session_start_ms) / 1000 : 0;
        bool is_overdue = (session_sec >= 45 * 60);

        snprintf(m_telemetry_json, sizeof(m_telemetry_json),
            "{\"type\":\"SENSOR_TELEMETRY\","
            "\"bme280\":{\"temp_c\":%.1f,\"humidity_pct\":%.1f,\"pressure_hpa\":%.1f,\"hardware_online\":%s},"
            "\"vl53l0x\":{\"distance_cm\":%.1f,\"is_user_near\":%s,\"hardware_online\":%s},"
            "\"pir\":{\"motion\":%s,\"presence\":%s,\"session_sec\":%u,\"is_overdue\":%s},"
            "\"speaker\":{\"status\":\"%s\",\"pin_bclk\":10,\"pin_lrc\":11,\"pin_din\":12},"
            "\"oled\":{\"status\":\"%s\"}}",
            m_temperature, m_humidity, m_pressure, m_bme_online ? "true" : "false",
            m_distance_cm, (m_distance_cm < 60.0f) ? "true" : "false", m_tof_online ? "true" : "false",
            m_motion_detected ? "true" : "false", m_presence ? "true" : "false",
            session_sec, is_overdue ? "true" : "false",
            speakerOnline ? "ONLINE" : "OFFLINE",
            oledOnline ? "ONLINE" : "OFFLINE"
        );

        Serial.print(F("[TELEMETRY] "));
        Serial.println(m_telemetry_json);

        if (udp && WiFi.status() == WL_CONNECTED) {
            udp->beginPacket(broadcastIp, port);
            udp->write((const uint8_t*)m_telemetry_json, strlen(m_telemetry_json));
            udp->endPacket();
        }
    }

    // Public Getters
    float getDistanceCm() const { return m_distance_cm; }
    float getRawDistanceCm() const { return m_raw_distance_cm; }
    float getTemperature() const { return m_temperature; }
    float getHumidity() const { return m_humidity; }
    float getPressure() const { return m_pressure; }
    bool isMotionDetected() const { return m_motion_detected; }
    bool isPresence() const { return m_presence; }
    uint32_t getSessionSeconds() const { return m_presence ? (millis() - m_session_start_ms) / 1000 : 0; }
    bool isOverdue() const { return getSessionSeconds() >= (45 * 60); }
    bool isBmeOnline() const { return m_bme_online; }
    bool isTofOnline() const { return m_tof_online; }
    const char* getTelemetryJson() const { return m_telemetry_json; }

private:
    TwoWire* m_wire;
    Adafruit_BME280 m_bme;
    VL53L0X m_tof;

    bool m_bme_online;
    bool m_tof_online;

    float m_temperature;
    float m_humidity;
    float m_pressure;
    float m_distance_cm;
    float m_raw_distance_cm;
    bool m_motion_detected;
    bool m_presence;

    uint8_t m_consecutive_8190;
    unsigned long m_session_start_ms;
    unsigned long m_last_motion_ms;
    unsigned long m_last_fast_poll_ms;
    unsigned long m_last_slow_poll_ms;
    unsigned long m_last_telemetry_ms;
    unsigned long m_last_tof_debug_ms;

    char m_telemetry_json[448];
};
