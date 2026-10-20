#ifndef POWER_MANAGER_H
#define POWER_MANAGER_H

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Adafruit_INA219.h>

/**
 * ============================================================================
 * MODULE 3: POWER MANAGEMENT SUBSYSTEM (POWER_MANAGER.H)
 * Dự án: Đèn Bàn Thông Minh (Smart Desk Lamp)
 * Quản lý nguồn Pin sạc Li-ion, Mạch chuyển nguồn tự động UPS và Cảm biến INA219
 * ============================================================================
 */

enum PowerSourceType {
    POWER_SRC_MAINS = 0,     // Cắm sạc USB-C (Đang cấp nguồn ngoài)
    POWER_SRC_BATTERY = 1,   // Đang chạy bằng Pin dự phòng UPS (Xả pin)
    POWER_SRC_MAINS_IDLE = 2 // Cắm sạc USB-C và pin đã đầy / cân bằng
};

struct PowerState {
    float bus_voltage_v = 0.0f;
    float shunt_voltage_mv = 0.0f;
    float current_ma = 0.0f;
    float power_mw = 0.0f;
    int battery_percent = 100;
    bool is_charging = false;
    bool is_low_battery = false;
    bool is_critical = false;
    PowerSourceType source = POWER_SRC_MAINS;
    bool hardware_online = false;
};

class PowerManager {
public:
    static const uint8_t INA219_I2C_ADDR_DEFAULT = 0x40;
    static const uint32_t SAMPLE_INTERVAL_MS = 500;
    static const uint32_t RECONNECT_INTERVAL_MS = 10000;

    // Ngưỡng dòng phân biệt trạng thái sạc / xả
    static constexpr float CHARGING_THRESHOLD_MA = 30.0f;
    static constexpr float DISCHARGING_THRESHOLD_MA = -30.0f;

    // Ngưỡng điện áp bảo vệ pin Li-ion 1S
    static constexpr float V_LOW_BATTERY = 3.35f; // Ngưỡng cảnh báo pin yếu (<=10%)
    static constexpr float V_CRITICAL_CUTOFF = 3.10f; // Ngưỡng ngắt tải khẩn cấp (<=2%)

    PowerManager() : m_wire(&Wire), m_ina219(nullptr), m_active_addr(0) {}

    ~PowerManager() {
        if (m_ina219) {
            delete m_ina219;
            m_ina219 = nullptr;
        }
    }

    /**
     * Dò tìm tự động địa chỉ I2C của chip INA219 trên toàn bộ 16 địa chỉ (0x40 -> 0x4F)
     */
    uint8_t probeIna219Address() {
        if (!m_wire) return 0;
        for (uint8_t addr = 0x40; addr <= 0x4F; addr++) {
            m_wire->beginTransmission(addr);
            if (m_wire->endTransmission() == 0) {
                return addr;
            }
        }
        return 0;
    }

    /**
     * Khởi tạo an toàn cho cảm biến INA219 trên bus I2C chung.
     * Tự động quét và khóa địa chỉ I2C thực tế (0x40 - 0x4F).
     */
    bool begin(TwoWire* wire = &Wire) {
        m_wire = wire ? wire : &Wire;
        m_last_sample_ms = millis();
        m_last_reconnect_ms = millis();

        // 1. Quét tìm chip INA219 trên dải 0x40 - 0x4F
        uint8_t found_addr = probeIna219Address();

        if (found_addr == 0) {
            Serial.println(F("[INA219] WARNING: No chip responding in I2C range 0x40-0x4F. Running in Fallback USB-C mode."));
            m_online = false;
            m_state.hardware_online = false;
            m_state.source = POWER_SRC_MAINS;
            m_state.battery_percent = 100;
            m_state.bus_voltage_v = 5.0f;
            m_state.current_ma = 0.0f;
            m_state.power_mw = 0.0f;
            m_state.is_charging = false;
            m_state.is_low_battery = false;
            m_state.is_critical = false;
            return false;
        }

        m_active_addr = found_addr;
        if (m_ina219) {
            delete m_ina219;
            m_ina219 = nullptr;
        }
        m_ina219 = new Adafruit_INA219(m_active_addr);

        // 2. Khởi động thư viện Adafruit INA219 với địa chỉ tìm thấy
        if (!m_ina219->begin(m_wire)) {
            Serial.printf("[INA219] WARNING: begin() failed at detected 0x%02X.\n", m_active_addr);
            m_online = false;
            m_state.hardware_online = false;
            return false;
        }

        // 3. Cấu hình dải đo hiệu chuẩn (32V, 2A)
        m_ina219->setCalibration_32V_2A();
        m_online = true;
        m_state.hardware_online = true;

        Serial.printf("[INA219] INITIALIZED SUCCESSFULLY on detected address 0x%02X (Bus 100kHz).\n", m_active_addr);

        // Đọc mẫu ban đầu để lấp đầy giá trị lọc EMA
        readSensorsRaw();
        m_v_filtered = m_v_raw;
        m_i_filtered = m_i_raw;
        evaluateState();

        return true;
    }

    /**
     * Cập nhật chu kỳ không chặn (Non-blocking update mỗi 500ms)
     */
    void update() {
        unsigned long now = millis();

        // Nếu offline, định kỳ ping thăm dò lại toàn dải 0x40-0x4F mỗi 10s (không chặn)
        if (!m_online) {
            if (now - m_last_reconnect_ms >= RECONNECT_INTERVAL_MS) {
                m_last_reconnect_ms = now;
                uint8_t found_addr = probeIna219Address();
                if (found_addr != 0) {
                    m_active_addr = found_addr;
                    if (m_ina219) {
                        delete m_ina219;
                        m_ina219 = nullptr;
                    }
                    m_ina219 = new Adafruit_INA219(m_active_addr);
                    if (m_ina219->begin(m_wire)) {
                        m_ina219->setCalibration_32V_2A();
                        m_online = true;
                        m_state.hardware_online = true;
                        Serial.printf("[INA219] RECONNECTED on detected address 0x%02X!\n", m_active_addr);
                        readSensorsRaw();
                        m_v_filtered = m_v_raw;
                        m_i_filtered = m_i_raw;
                        evaluateState();
                    }
                }
            }
            return;
        }

        // Kiểm tra chu kỳ lấy mẫu 500ms
        if (now - m_last_sample_ms < SAMPLE_INTERVAL_MS) {
            return;
        }
        m_last_sample_ms = now;

        // Đọc giá trị từ phần cứng
        readSensorsRaw();

        // Lọc nhiễu Exponential Moving Average (EMA) chống gai hài PWM 5kHz
        m_v_filtered = 0.70f * m_v_raw + 0.30f * m_v_filtered;
        m_i_filtered = 0.70f * m_i_raw + 0.30f * m_i_filtered;

        // Đánh giá trạng thái nguồn và bảo vệ
        evaluateState();
    }

    /**
     * Thuật toán ước lượng % Dung lượng Pin (SoC) theo đường cong xả Li-ion 1S (3.2V - 4.2V)
     * Áp dụng bảng nội suy 8 đoạn thực nghiệm chuẩn kỹ thuật.
     */
    static int calculateSoC(float v) {
        if (v >= 4.18f) return 100;
        if (v >= 4.05f) return (int)(90.0f + (v - 4.05f) / (4.18f - 4.05f) * 10.0f);
        if (v >= 3.95f) return (int)(80.0f + (v - 3.95f) / (4.05f - 3.95f) * 10.0f);
        if (v >= 3.85f) return (int)(65.0f + (v - 3.85f) / (3.95f - 3.85f) * 15.0f);
        if (v >= 3.75f) return (int)(50.0f + (v - 3.75f) / (3.85f - 3.75f) * 15.0f);
        if (v >= 3.68f) return (int)(35.0f + (v - 3.68f) / (3.75f - 3.68f) * 15.0f);
        if (v >= 3.55f) return (int)(15.0f + (v - 3.55f) / (3.68f - 3.55f) * 20.0f);
        if (v >= 3.30f) return (int)(5.0f  + (v - 3.30f) / (3.55f - 3.30f) * 10.0f);
        if (v > 3.10f)  return (int)((v - 3.10f) / (3.30f - 3.10f) * 5.0f);
        return 0;
    }

    // --- Các Public Getter Truy Xuất Thông Số Đo Đạc ---
    bool isOnline() const { return m_online; }
    float getVoltage() const { return m_state.bus_voltage_v; }
    float getShuntMv() const { return m_state.shunt_voltage_mv; }
    float getCurrentMa() const { return m_state.current_ma; }
    float getPowerMw() const { return m_state.power_mw; }
    int getBatteryPercent() const { return m_state.battery_percent; }
    bool isCharging() const { return m_state.is_charging; }
    bool isLowBattery() const { return m_state.is_low_battery; }
    bool isCritical() const { return m_state.is_critical; }
    PowerSourceType getPowerSource() const { return m_state.source; }

    const char* getPowerSourceStr() const {
        switch (m_state.source) {
            case POWER_SRC_MAINS: return "MAINS (USB-C)";
            case POWER_SRC_BATTERY: return "BATTERY (UPS)";
            case POWER_SRC_MAINS_IDLE: return "MAINS_IDLE";
            default: return "UNKNOWN";
        }
    }

    /**
     * Mức vạch pin đồ họa cho màn hình OLED (0 = Cạn, 1 = 25%, 2 = 50%, 3 = 75%, 4 = 100%)
     */
    uint8_t getBatteryIconBars() const {
        if (m_state.battery_percent >= 80) return 4;
        if (m_state.battery_percent >= 55) return 3;
        if (m_state.battery_percent >= 30) return 2;
        if (m_state.battery_percent >= 10) return 1;
        return 0;
    }

    /**
     * Định dạng chuỗi JSON Telemetry để gửi qua mạng UDP hoặc Serial
     */
    void formatTelemetryJson(char* buf, size_t max_len) const {
        snprintf(buf, max_len,
            "\"battery\":{\"voltage_v\":%.2f,\"current_ma\":%.1f,\"power_mw\":%.1f,"
            "\"percent\":%d,\"is_charging\":%s,\"power_source\":\"%s\",\"hardware_online\":%s}",
            m_state.bus_voltage_v, m_state.current_ma, m_state.power_mw,
            m_state.battery_percent, m_state.is_charging ? "true" : "false",
            (m_state.source == POWER_SRC_BATTERY) ? "BATTERY" : "MAINS",
            m_state.hardware_online ? "true" : "false"
        );
    }

    /**
     * Phát broadcast Telemetry qua UDP (Port 12346) và Serial chu kỳ 500ms
     */
    void broadcastTelemetry(WiFiUDP* udp = nullptr, IPAddress broadcastIp = IPAddress(255, 255, 255, 255), uint16_t port = 12346) {
        unsigned long now = millis();
        if (now - m_last_telemetry_ms < 500) return;
        m_last_telemetry_ms = now;

        char buf[256];
        snprintf(buf, sizeof(buf),
            "{\"type\":\"BATTERY_TELEMETRY\",\"battery\":{"
            "\"voltage_v\":%.2f,\"current_ma\":%.1f,\"power_mw\":%.1f,"
            "\"percent\":%d,\"is_charging\":%s,\"power_source\":\"%s\",\"hardware_online\":%s}}",
            m_state.bus_voltage_v, m_state.current_ma, m_state.power_mw,
            m_state.battery_percent, m_state.is_charging ? "true" : "false",
            (m_state.source == POWER_SRC_BATTERY) ? "BATTERY" : "MAINS",
            m_state.hardware_online ? "true" : "false"
        );

        Serial.print(F("[TELEMETRY] "));
        Serial.println(buf);

        if (udp && WiFi.status() == WL_CONNECTED) {
            udp->beginPacket(broadcastIp, port);
            udp->write((const uint8_t*)buf, strlen(buf));
            udp->endPacket();
        }
    }

    uint8_t getActiveAddress() const { return m_active_addr; }

private:
    void readSensorsRaw() {
        if (!m_ina219) return;
        m_v_raw = m_ina219->getBusVoltage_V();
        m_shunt_raw = m_ina219->getShuntVoltage_mV();
        m_i_raw = m_ina219->getCurrent_mA();
        m_p_raw = m_ina219->getPower_mW();
    }

    void evaluateState() {
        m_state.bus_voltage_v = m_v_filtered;
        m_state.shunt_voltage_mv = m_shunt_raw;
        m_state.current_ma = m_i_filtered;
        m_state.power_mw = m_p_raw;

        // 1. Phân biệt trạng thái nguồn sạc / xả
        if (m_state.current_ma > CHARGING_THRESHOLD_MA) {
            m_state.source = POWER_SRC_MAINS;
            m_state.is_charging = true;
        } else if (m_state.current_ma < DISCHARGING_THRESHOLD_MA) {
            m_state.source = POWER_SRC_BATTERY;
            m_state.is_charging = false;
        } else {
            m_state.source = POWER_SRC_MAINS_IDLE;
            m_state.is_charging = false;
        }

        // 2. Tính SoC phần trăm pin
        m_state.battery_percent = calculateSoC(m_state.bus_voltage_v);

        // 3. Cơ chế cảnh báo và bảo vệ pin yếu (chỉ áp dụng khi đang xả pin)
        if (m_state.source == POWER_SRC_BATTERY) {
            m_state.is_low_battery = (m_state.bus_voltage_v <= V_LOW_BATTERY || m_state.battery_percent <= 10);
            m_state.is_critical = (m_state.bus_voltage_v <= V_CRITICAL_CUTOFF || m_state.battery_percent <= 2);
        } else {
            m_state.is_low_battery = false;
            m_state.is_critical = false;
        }
    }

    Adafruit_INA219* m_ina219 = nullptr;
    uint8_t m_active_addr = 0;
    TwoWire* m_wire = nullptr;
    bool m_online = false;
    PowerState m_state;

    float m_v_raw = 0.0f;
    float m_shunt_raw = 0.0f;
    float m_i_raw = 0.0f;
    float m_p_raw = 0.0f;

    float m_v_filtered = 0.0f;
    float m_i_filtered = 0.0f;

    unsigned long m_last_sample_ms = 0;
    unsigned long m_last_reconnect_ms = 0;
    unsigned long m_last_telemetry_ms = 0;
};

#endif // POWER_MANAGER_H
