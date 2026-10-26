#ifndef POWER_MANAGER_H
#define POWER_MANAGER_H

#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Preferences.h>
#include <HWCDC.h>
#include <Adafruit_INA219.h>

/**
 * ============================================================================
 * MODULE 3: POWER MANAGEMENT SUBSYSTEM (POWER_MANAGER.H)
 * Dự án: Đèn Bàn Thông Minh (Smart Desk Lamp — Hybrid Edge-First Architecture)
 * Quản lý Nguồn Pin 2 Cell (6000mAh) & Thuật toán Coulomb Counter Tự Động 100%
 * Lưu trữ trạng thái Flash NVS bảo vệ tuổi thọ chip (Delta 1% Persistence)
 * ============================================================================
 */

enum PowerSourceType {
    POWER_SRC_MAINS = 0,        // Cắm sạc qua MẠCH SẠC NHỎ (Đang nạp pin thật)
    POWER_SRC_BATTERY = 1,      // Đang chạy bằng Pin dự phòng UPS (Xả pin)
    POWER_SRC_MAINS_IDLE = 2,   // Sạc qua mạch sạc nhỏ và pin đã bão hòa 100% (No pin)
    POWER_SRC_DIRECT_DC = 3     // Cắm nguồn USB qua ESP32 (Pass-Through trực tiếp, KHÔNG sạc pin)
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

    // 🔒 Cấu hình Cặp Pin 2 Cell Panasonic NCR18650B mắc song song (1S2P)
    static constexpr float BATTERY_NOMINAL_MAH = 5300.0f; // Dung lượng chuẩn thực tế đã nghiệm thu và hiệu chuẩn (5300mAh)

    // Ngưỡng điện áp phân biệt 3 trạng thái thực nghiệm chuẩn xác:
    // 1. Sạc qua ESP32: V >= 4.90V (hoặc có tín hiệu USB CDC)
    // 2. Sạc qua Mạch sạc nhỏ: V <= 4.73V (dòng sạc ghì áp xuống 4.61V - 4.69V)
    // 3. Chạy Pin độc lập: 4.74V <= V <= 4.88V (thực tế 4.76V - 4.87V)
    static constexpr float V_THRESH_ESP32_CHARGE = 4.90f;
    static constexpr float V_THRESH_MODULE_CHARGE = 4.73f;

    // Tốc độ nạp bù ảo tương đương dòng sạc chuẩn của mạch sạc TP4056/PowerBank (~800mA)
    static constexpr float VIRTUAL_CHARGE_RATE_MA = 800.0f;

    // Hiệu suất mạch Boost 3.7V -> 5.0V (I_battery = I_load * 5.0 / (3.7 * 0.88) ≈ 1.54)
    static constexpr float BOOST_CURRENT_RATIO = 1.54f;

    // Ngưỡng bảo vệ pin yếu & ngắt khẩn cấp (% SoC)
    static const int SOC_LOW_BATTERY = 10;
    static const int SOC_CRITICAL_CUTOFF = 2;

    // Thời gian cắm sạc liên tục tối đa để tự động chốt no pin 100% (4 tiếng)
    static const uint32_t OVERNIGHT_SATURATION_TIME_MS = 14400000;

    PowerManager() 
        : m_wire(&Wire), 
          m_ina219(nullptr), 
          m_active_addr(0),
          m_rem_mah(BATTERY_NOMINAL_MAH),
          m_used_mah(0.0f),
          m_discharge_sec(0.0f),
          m_last_coulomb_ms(0),
          m_nvs_saved_percent(100),
          m_nvs_saved_mah(BATTERY_NOMINAL_MAH),
          m_last_nvs_save_ms(0),
          m_charging_start_ms(0) {}

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
     * Khởi tạo an toàn cho cảm biến INA219 & Khôi phục trạng thái từ Flash NVS
     */
    bool begin(TwoWire* wire = &Wire) {
        m_wire = wire ? wire : &Wire;
        m_last_sample_ms = millis();
        m_last_reconnect_ms = millis();
        m_last_coulomb_ms = millis();
        m_last_nvs_save_ms = millis();

        // 1. Khôi phục trạng thái % Pin và mAh đã lưu từ Flash NVS
        loadStateFromNVS();

        // 2. Quét tìm chip INA219 trên dải 0x40 - 0x4F
        uint8_t found_addr = probeIna219Address();

        if (found_addr == 0) {
            Serial.println(F("[INA219] WARNING: No chip responding in I2C range 0x40-0x4F. Running in Fallback USB-C mode."));
            m_online = false;
            m_state.hardware_online = false;
            m_state.source = POWER_SRC_MAINS;
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

        // 3. Khởi động thư viện Adafruit INA219 với địa chỉ tìm thấy
        if (!m_ina219->begin(m_wire)) {
            Serial.printf("[INA219] WARNING: begin() failed at detected 0x%02X.\n", m_active_addr);
            m_online = false;
            m_state.hardware_online = false;
            return false;
        }

        // 4. Cấu hình dải đo hiệu chuẩn (32V, 2A)
        m_ina219->setCalibration_32V_2A();
        m_online = true;
        m_state.hardware_online = true;

        Serial.printf("[INA219] INITIALIZED SUCCESSFULLY on detected address 0x%02X (Bus 100kHz).\n", m_active_addr);
        Serial.printf("[COULOMB] Restored Battery State: %d%% (%.1f / %.1f mAh)\n", 
                      m_state.battery_percent, m_rem_mah, BATTERY_NOMINAL_MAH);

        // Đọc mẫu ban đầu để lấp đầy giá trị lọc EMA
        readSensorsRaw();
        m_v_filtered = m_v_raw;
        m_i_filtered = m_i_raw;

        evaluateState(0.5f);

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

        evaluateState(0.5f);
                    }
                }
            }
            return;
        }

        // Kiểm tra chu kỳ lấy mẫu 500ms
        if (now - m_last_sample_ms < SAMPLE_INTERVAL_MS) {
            return;
        }

        // Tính khoảng thời gian delta t (giây) cho bộ tích phân Coulomb
        float delta_sec = (now - m_last_coulomb_ms) / 1000.0f;
        if (delta_sec > 10.0f || delta_sec <= 0.0f) {
            delta_sec = 0.5f; // Giới hạn biên an toàn sau delay
        }
        m_last_coulomb_ms = now;
        m_last_sample_ms = now;

        // Đọc giá trị từ phần cứng
        readSensorsRaw();

        // Lọc nhiễu Exponential Moving Average (EMA) chống gai hài PWM 5kHz
        m_v_filtered = 0.70f * m_v_raw + 0.30f * m_v_filtered;
        m_i_filtered = 0.70f * m_i_raw + 0.30f * m_i_filtered;

        // Đánh giá trạng thái nguồn, tích phân Coulomb và bảo vệ
        evaluateState(delta_sec);

        // Lưu Flash NVS theo chu kỳ an toàn (chỉ khi thay đổi >= 1%)
        saveStateToNVS();
    }

    /**
     * Calibrate thủ công hoặc tự động đặt lại 100% khi pin đã sạc đầy
     */
    void calibrateFull() {
        m_rem_mah = BATTERY_NOMINAL_MAH;
        m_used_mah = 0.0f;
        m_discharge_sec = 0.0f;
        m_state.battery_percent = 100;
        m_state.is_low_battery = false;
        m_state.is_critical = false;
        saveStateToNVS(true); // Ghi đè ngay lập tức
        Serial.println(F("[COULOMB] Battery fully calibrated to 100% & discharge counters reset!"));
    }

    // --- Các Public Getter Truy Xuất Thông Số Đo Đạc ---
    bool isOnline() const { return m_online; }
    float getVoltage() const { return m_state.bus_voltage_v; }
    float getShuntMv() const { return m_state.shunt_voltage_mv; }
    float getCurrentMa() const { return m_state.current_ma; }
    float getPowerMw() const { return m_state.power_mw; }
    int getBatteryPercent() const { return m_state.battery_percent; }
    bool isCharging() const { return m_state.is_charging; }
    bool isDirectDc() const { return m_state.source == POWER_SRC_DIRECT_DC; }
    bool isLowBattery() const { return m_state.is_low_battery; }
    bool isCritical() const { return m_state.is_critical; }
    PowerSourceType getPowerSource() const { return m_state.source; }
    float getRemainingMah() const { return m_rem_mah; }
    float getUsedMah() const { return m_used_mah; }
    uint32_t getDischargeSeconds() const { return (uint32_t)m_discharge_sec; }
    float getNominalMah() const { return BATTERY_NOMINAL_MAH; }
    void resetDischargeCounters() { m_discharge_sec = 0.0f; m_used_mah = 0.0f; saveStateToNVS(true); }

    const char* getPowerSourceStr() const {
        switch (m_state.source) {
            case POWER_SRC_MAINS: return "MAINS (Charging)";
            case POWER_SRC_BATTERY: return "BATTERY (UPS)";
            case POWER_SRC_MAINS_IDLE: return "MAINS_IDLE (Full)";
            case POWER_SRC_DIRECT_DC: return "DIRECT_DC (USB Bypass)";
            default: return "UNKNOWN";
        }
    }

    /**
     * Mức vạch pin đồ họa cho màn hình OLED (0 = Cạn/Low, 1 = 25%, 2 = 50%, 3 = 75%, 4 = 100%)
     */
    uint8_t getBatteryIconBars() const {
        if (m_state.battery_percent >= 75) return 4;
        if (m_state.battery_percent >= 50) return 3;
        if (m_state.battery_percent >= 25) return 2;
        if (m_state.battery_percent > 10) return 1;
        return 0;
    }

    /**
     * Định dạng chuỗi JSON Telemetry để gửi qua mạng UDP hoặc Serial
     */
    void formatTelemetryJson(char* buf, size_t max_len) const {
        snprintf(buf, max_len,
            "\"battery\":{\"voltage_v\":%.2f,\"current_ma\":%.1f,\"power_mw\":%.1f,"
            "\"percent\":%d,\"is_charging\":%s,\"power_source\":\"%s\",\"hardware_online\":%s,\"rem_mah\":%.0f}",
            m_state.bus_voltage_v, m_state.current_ma, m_state.power_mw,
            m_state.battery_percent, m_state.is_charging ? "true" : "false",
            (m_state.source == POWER_SRC_BATTERY) ? "BATTERY" : (m_state.source == POWER_SRC_DIRECT_DC ? "DIRECT_DC" : "MAINS"),
            m_state.hardware_online ? "true" : "false",
            m_rem_mah
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
            "\"percent\":%d,\"is_charging\":%s,\"power_source\":\"%s\",\"hardware_online\":%s,\"rem_mah\":%.0f}}",
            m_state.bus_voltage_v, m_state.current_ma, m_state.power_mw,
            m_state.battery_percent, m_state.is_charging ? "true" : "false",
            (m_state.source == POWER_SRC_BATTERY) ? "BATTERY" : (m_state.source == POWER_SRC_DIRECT_DC ? "DIRECT_DC" : "MAINS"),
            m_state.hardware_online ? "true" : "false",
            m_rem_mah
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

    /**
     * Thuật toán Coulomb Counter: Tích phân xả pin & Nạp bù tự động
     */
    void evaluateState(float delta_sec) {
        m_state.bus_voltage_v = m_v_filtered;
        m_state.shunt_voltage_mv = m_shunt_raw;
        m_state.current_ma = m_i_filtered;
        m_state.power_mw = m_p_raw;

        unsigned long now = millis();

        // 1. Nhận diện 3 trạng thái nguồn: Sạc thật qua Mạch Sạc Nhỏ vs Cắm nguồn ESP32 vs Chạy Pin
        // Nhận diện sạc qua Mạch Sạc Nhỏ (điện áp bị ghì sụt xuống 4.00V <= V <= 4.73V)
        // Bộ lọc debounce 2 mẫu liên tiếp (1 giây) chống gai sụt áp tức thời khi xoay núm LED
        if (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.00f) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        // Cắm nguồn USB qua cổng ESP32 (V >= 4.90V hoặc có tín hiệu USB CDC), KHÔNG phải sạc mạch nhỏ
        bool is_direct_usb = ((m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE) || HWCDC::isPlugged()) && !is_charging_module;

        // Lấy độ lớn dòng điện tuyệt đối |I| (chống dòng ngược âm khi chạy nguồn pin)
        float current_mag = fabsf(m_i_filtered);

        if (is_charging_module) {
            // --- ⚡ ĐANG SẠC PIN THẬT QUA MẠCH SẠC (REAL CHARGING) ---
            if (m_charging_start_ms == 0) {
                m_charging_start_ms = now; // Ghi nhận thời điểm bắt đầu cắm sạc
            }

            // Tự động nạp bù lượng điện tích theo tốc độ sạc chuẩn (~800mA)
            float charge_added_mah = (VIRTUAL_CHARGE_RATE_MA * delta_sec) / 3600.0f;
            m_rem_mah += charge_added_mah;

            // Kiểm tra bão hòa đầy 100% (Saturation Lock)
            bool time_saturated = (now - m_charging_start_ms >= OVERNIGHT_SATURATION_TIME_MS);
            if (m_rem_mah >= BATTERY_NOMINAL_MAH || time_saturated) {
                m_rem_mah = BATTERY_NOMINAL_MAH;
                m_used_mah = 0.0f;
                m_discharge_sec = 0.0f;
                m_state.battery_percent = 100;
                m_state.source = POWER_SRC_MAINS_IDLE;
            } else {
                m_state.source = POWER_SRC_MAINS;
            }
            m_state.is_charging = true; // BẬT TIA SÉT ⚡
            m_state.is_low_battery = false;
            m_state.is_critical = false;
        } else if (is_direct_usb) {
            // --- 🔌 CẮM NGUỒN TRỰC TIẾP QUA ESP32 (DIRECT DC PASS-THROUGH - KHÔNG SẠC PIN) ---
            m_charging_start_ms = 0;
            m_state.source = POWER_SRC_DIRECT_DC;
            m_state.is_charging = false; // TẮT TIA SÉT ⚡ (Hiển thị ký hiệu [DC] trên OLED)
            m_state.is_low_battery = false;
            m_state.is_critical = false;
            // ĐÓNG BĂNG DUNG LƯỢNG PIN: Không cộng mAh ảo và không trừ mAh xả pin!
        } else {
            // --- 🔋 ĐANG CHẠY BẰNG PIN (BATTERY / DISCHARGING) ---
            m_charging_start_ms = 0;
            m_state.source = POWER_SRC_BATTERY;
            m_state.is_charging = false;

            // Dòng tải thực tế của hệ thống: Lấy độ lớn tuyệt đối |I| (chống dòng ngược âm)
            float load_current = (current_mag > 5.0f) ? current_mag : 6.0f;

            // Dòng xả thực rút từ cell pin 3.7V qua mạch Boost
            float battery_draw_ma = load_current * BOOST_CURRENT_RATIO;

            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;

            // CHỈ ĐẾM THỜI GIAN VÀ DUNG LƯỢNG TEST KHI ĐÈN THỰC SỰ BẬT SÁNG (|I| >= 20mA)
            if (current_mag >= 20.0f) {
                m_used_mah += discharge_mah;
                m_discharge_sec += delta_sec;
            }

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }

            // THUẬT TOÁN 2: NEO ĐIỆN ÁP ĐÁY CHỐNG BÁO ẢO (VOLTAGE-ANCHOR BOTTOM SYNC)
            if (m_state.bus_voltage_v <= 4.20f) {
                m_rem_mah = 0.0f;
            } else if (m_state.bus_voltage_v <= 4.60f) {
                if (m_rem_mah > 0.05f * BATTERY_NOMINAL_MAH) {
                    m_rem_mah = 0.05f * BATTERY_NOMINAL_MAH;
                }
            } else if (m_state.bus_voltage_v <= 4.65f && current_mag >= 20.0f) {
                // Pin yếu: Khi bật đèn mà áp boost sụt <= 4.65V -> Ghì về tối đa 20% (1 vạch)
                if (m_rem_mah > 0.20f * BATTERY_NOMINAL_MAH) {
                    m_rem_mah = 0.20f * BATTERY_NOMINAL_MAH;
                }
            }
        }

        // 2. Tính % Dung lượng Pin (SoC) theo tỷ lệ Coulomb
        m_state.battery_percent = (int)round((m_rem_mah / BATTERY_NOMINAL_MAH) * 100.0f);
        if (m_state.battery_percent > 100) m_state.battery_percent = 100;
        if (m_state.battery_percent < 0) m_state.battery_percent = 0;

        // 3. Cơ chế cảnh báo và bảo vệ pin yếu (chỉ áp dụng khi đang xả pin)
        if (m_state.source == POWER_SRC_BATTERY) {
            m_state.is_low_battery = (m_state.battery_percent <= SOC_LOW_BATTERY);
            m_state.is_critical = (m_state.battery_percent <= SOC_CRITICAL_CUTOFF);

            // BẢO VỆ DỮ LIỆU KHẨN CẤP TRƯỚC KHI SẬP NGUỒN:
            // Khi áp sụt xuống <= 4.60V (ngưỡng sắp ngắt của mạch boost) hoặc pin <= 2%,
            // lưu đè ngay lập tức vào Flash NVS để chốt sổ chính xác thời gian và mAh cuối cùng!
            if (m_state.bus_voltage_v <= 4.60f || m_state.is_critical) {
                static unsigned long s_last_crit_save_ms = 0;
                if (now - s_last_crit_save_ms >= 5000) {
                    s_last_crit_save_ms = now;
                    saveStateToNVS(true);
                }
            }
        } else {
            m_state.is_low_battery = false;
            m_state.is_critical = false;
        }
    }

    /**
     * Khôi phục trạng thái Pin từ Flash NVS khi khởi động
     */
    void loadStateFromNVS() {
        m_prefs.begin("pwr_coulomb", true); // Mở chế độ Read-only
        bool init_done = m_prefs.getBool("init", false);
        if (init_done) {
            m_rem_mah = m_prefs.getFloat("rem_mah", BATTERY_NOMINAL_MAH);
            int saved_soc = m_prefs.getInt("soc", 100);
            m_used_mah = m_prefs.getFloat("used_mah", 0.0f);
            m_discharge_sec = m_prefs.getFloat("dis_sec", 0.0f);
            if (m_rem_mah < 0.0f || m_rem_mah > BATTERY_NOMINAL_MAH) {
                m_rem_mah = BATTERY_NOMINAL_MAH;
            }
            if (saved_soc < 0 || saved_soc > 100) {
                saved_soc = 100;
            }
            m_state.battery_percent = saved_soc;
            m_nvs_saved_percent = saved_soc;
            m_nvs_saved_mah = m_rem_mah;
        } else {
            // Lần khởi động đầu tiên chưa có dữ liệu Flash
            m_rem_mah = BATTERY_NOMINAL_MAH;
            m_state.battery_percent = 100;
            m_nvs_saved_percent = 100;
            m_nvs_saved_mah = BATTERY_NOMINAL_MAH;
            m_used_mah = 0.0f;
            m_discharge_sec = 0.0f;
        }
        m_prefs.end();
    }

    /**
     * Ghi Flash NVS an toàn: Chỉ ghi khi pin thay đổi đúng 1% (Bảo vệ tuổi thọ chip >10 năm)
     */
    void saveStateToNVS(bool force = false) {
        unsigned long now = millis();
        int delta_pct = abs(m_state.battery_percent - m_nvs_saved_percent);

        // Lưu khi % thay đổi >= 1%, hoặc định kỳ mỗi 30s khi xả pin nếu used_mah thay đổi >= 5mAh
        bool should_save = force || (delta_pct >= 1) || 
                           (now - m_last_nvs_save_ms >= 30000 && abs(m_rem_mah - m_nvs_saved_mah) >= 5.0f);

        if (!should_save) return;

        m_last_nvs_save_ms = now;
        m_nvs_saved_percent = m_state.battery_percent;
        m_nvs_saved_mah = m_rem_mah;

        m_prefs.begin("pwr_coulomb", false); // Mở chế độ Read-Write
        m_prefs.putBool("init", true);
        m_prefs.putFloat("rem_mah", m_rem_mah);
        m_prefs.putInt("soc", m_state.battery_percent);
        m_prefs.putFloat("used_mah", m_used_mah);
        m_prefs.putFloat("dis_sec", m_discharge_sec);
        m_prefs.end();

        Serial.printf("[COULOMB NVS] Saved: %d%% (Rem: %.1f mAh, Used: %.1f mAh, Tx: %.0fs)\n", 
                      m_state.battery_percent, m_rem_mah, m_used_mah, m_discharge_sec);
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

    // Biến trạng thái Coulomb Counter
    float m_rem_mah = BATTERY_NOMINAL_MAH;
    float m_used_mah = 0.0f;
    float m_discharge_sec = 0.0f;
    unsigned long m_last_coulomb_ms = 0;

    // Biến giám sát lưu Flash NVS
    Preferences m_prefs;
    int m_nvs_saved_percent = 100;
    float m_nvs_saved_mah = BATTERY_NOMINAL_MAH;
    unsigned long m_last_nvs_save_ms = 0;
    unsigned long m_charging_start_ms = 0;
    uint8_t m_low_v_charge_count = 0;

    unsigned long m_last_sample_ms = 0;
    unsigned long m_last_reconnect_ms = 0;
    unsigned long m_last_telemetry_ms = 0;
};

#endif // POWER_MANAGER_H
