import re

# ==============================================================================
# 1. Update src/power_manager.h
# ==============================================================================
with open('src/power_manager.h', 'r', encoding='utf-8') as f:
    pwr = f.read()

# 1.1 Update BATTERY_NOMINAL_MAH & constants
pwr = re.sub(
    r'static constexpr float BATTERY_NOMINAL_MAH = 6000\.0f;[^\n]*\n\s*// Ngưỡng điện áp phân biệt 3 trạng thái thực nghiệm chuẩn xác:.*?'
    r'static constexpr float V_THRESH_ESP32_CHARGE = 4\.90f;\s*\n\s*static constexpr float V_THRESH_MODULE_CHARGE = 4\.73f;',
    '''static constexpr float BATTERY_NOMINAL_MAH = 3000.0f; // Dung lượng chuẩn quy đổi 5V (2 cell 18650 qua Boost)

    // Ngưỡng điện áp phân biệt 3 trạng thái thực nghiệm chuẩn xác:
    // 1. Sạc qua ESP32: V >= 4.90V (hoặc có tín hiệu USB CDC)
    // 2. Sạc qua Mạch sạc nhỏ: 4.50V <= V <= 4.75V KHI DÒNG TẢI THẤP (I < 25mA, đèn tắt)
    // 3. Chạy Pin độc lập: V < 4.90V (khi đèn sáng I >= 25mA, sụt áp là do xả pin, KHÔNG PHẢI SẠC)
    static constexpr float V_THRESH_ESP32_CHARGE = 4.90f;
    static constexpr float V_THRESH_MODULE_CHARGE = 4.75f;''',
    pwr, flags=re.DOTALL
)

# 1.2 Constructor initialization
pwr = pwr.replace(
    'm_rem_mah(BATTERY_NOMINAL_MAH),\n          m_last_coulomb_ms(0),',
    'm_rem_mah(BATTERY_NOMINAL_MAH),\n          m_used_mah(0.0f),\n          m_discharge_sec(0.0f),\n          m_last_coulomb_ms(0),'
)

# 1.3 CalibrateFull
pwr = pwr.replace(
    '''    void calibrateFull() {
        m_rem_mah = BATTERY_NOMINAL_MAH;
        m_state.battery_percent = 100;
        m_state.is_low_battery = false;
        m_state.is_critical = false;
        saveStateToNVS(true); // Ghi đè ngay lập tức
        Serial.println(F("[COULOMB] Battery fully calibrated to 100% (6000mAh)!"));
    }''',
    '''    void calibrateFull() {
        m_rem_mah = BATTERY_NOMINAL_MAH;
        m_used_mah = 0.0f;
        m_discharge_sec = 0.0f;
        m_state.battery_percent = 100;
        m_state.is_low_battery = false;
        m_state.is_critical = false;
        saveStateToNVS(true); // Ghi đè ngay lập tức
        Serial.println(F("[COULOMB] Battery fully calibrated to 100% & discharge counters reset!"));
    }'''
)

# 1.4 Public Getters
pwr = pwr.replace(
    '''    PowerSourceType getPowerSource() const { return m_state.source; }
    float getRemainingMah() const { return m_rem_mah; }''',
    '''    PowerSourceType getPowerSource() const { return m_state.source; }
    float getRemainingMah() const { return m_rem_mah; }
    float getUsedMah() const { return m_used_mah; }
    uint32_t getDischargeSeconds() const { return (uint32_t)m_discharge_sec; }
    float getNominalMah() const { return BATTERY_NOMINAL_MAH; }
    void resetDischargeCounters() { m_discharge_sec = 0.0f; m_used_mah = 0.0f; saveStateToNVS(true); }'''
)

# 1.5 evaluateState charging logic
old_eval = '''        // 1. Nhận diện Nguồn sạc ngoài dựa trên bản đồ điện áp thực nghiệm:
        bool is_charging_esp32 = (m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE) || HWCDC::isPlugged();

        // Nhận diện sạc qua Mạch Sạc Nhỏ (điện áp bị ghì sụt xuống <= 4.73V)
        // Bộ lọc debounce 2 mẫu liên tiếp (1 giây) chống gai sụt áp tức thời khi xoay núm LED
        if (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.00f) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        bool is_plugged_mains = is_charging_esp32 || is_charging_module;'''

new_eval = '''        // 1. Nhận diện Nguồn sạc ngoài dựa trên bản đồ điện áp & dòng điện thực nghiệm:
        // Cắm sạc qua cổng ESP32 USB: V >= 4.90V hoặc có tín hiệu USB CDC
        bool is_charging_esp32 = (m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE) || HWCDC::isPlugged();

        // Nhận diện sạc qua Mạch Sạc Nhỏ:
        // Chỉ nhận diện là sạc qua mạch nhỏ khi dòng tải THẤP (I < 25mA, tức là đèn không bật hoặc pin ngắt)
        // VÀ điện áp rơi vào dải 4.50V - 4.75V đặc trưng của mạch sạc nhỏ.
        // NẾU đèn đang bật (I >= 25mA) mà áp tụt <= 4.75V, đó là HIỆN TƯỢNG SỤT ÁP DO XẢ PIN, TUYỆT ĐỐI KHÔNG COI LÀ SẠC!
        bool is_small_charger_candidate = (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.50f && m_i_filtered < 25.0f);
        if (is_small_charger_candidate) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        bool is_plugged_mains = is_charging_esp32 || is_charging_module;'''

pwr = pwr.replace(old_eval, new_eval)

# 1.6 Discharge accumulation
old_dis = '''            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }'''

new_dis = '''            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;
            m_used_mah += discharge_mah;
            m_discharge_sec += delta_sec;

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }'''

pwr = pwr.replace(old_dis, new_dis)

# 1.7 loadStateFromNVS
old_load = '''    void loadStateFromNVS() {
        m_prefs.begin("pwr_coulomb", true); // Mở chế độ Read-only
        bool init_done = m_prefs.getBool("init", false);
        if (init_done) {
            m_rem_mah = m_prefs.getFloat("rem_mah", BATTERY_NOMINAL_MAH);
            int saved_soc = m_prefs.getInt("soc", 100);
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
        }
        m_prefs.end();
    }'''

new_load = '''    void loadStateFromNVS() {
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
    }'''

pwr = pwr.replace(old_load, new_load)

# 1.8 saveStateToNVS
old_save = '''    void saveStateToNVS(bool force = false) {
        unsigned long now = millis();
        int delta_pct = abs(m_state.battery_percent - m_nvs_saved_percent);

        // Điều kiện ghi: Hoặc bắt buộc (force), hoặc tụt/tăng đúng 1%, hoặc sau 60s có thay đổi dung lượng > 15mAh
        bool should_save = force || (delta_pct >= 1) || 
                           (now - m_last_nvs_save_ms >= 60000 && abs(m_rem_mah - m_nvs_saved_mah) >= 15.0f);

        if (!should_save) return;

        m_last_nvs_save_ms = now;
        m_nvs_saved_percent = m_state.battery_percent;
        m_nvs_saved_mah = m_rem_mah;

        m_prefs.begin("pwr_coulomb", false); // Mở chế độ Read-Write
        m_prefs.putBool("init", true);
        m_prefs.putFloat("rem_mah", m_rem_mah);
        m_prefs.putInt("soc", m_state.battery_percent);
        m_prefs.end();

        Serial.printf("[COULOMB NVS] Saved: %d%% (%.1f mAh)\\n", m_state.battery_percent, m_rem_mah);
    }'''

new_save = '''    void saveStateToNVS(bool force = false) {
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

        Serial.printf("[COULOMB NVS] Saved: %d%% (Rem: %.1f mAh, Used: %.1f mAh, Tx: %.0fs)\\n", 
                      m_state.battery_percent, m_rem_mah, m_used_mah, m_discharge_sec);
    }'''

pwr = pwr.replace(old_save, new_save)

# 1.9 Private member variables
pwr = pwr.replace(
    '''    // Biến trạng thái Coulomb Counter
    float m_rem_mah = BATTERY_NOMINAL_MAH;
    unsigned long m_last_coulomb_ms = 0;''',
    '''    // Biến trạng thái Coulomb Counter
    float m_rem_mah = BATTERY_NOMINAL_MAH;
    float m_used_mah = 0.0f;
    float m_discharge_sec = 0.0f;
    unsigned long m_last_coulomb_ms = 0;'''
)

with open('src/power_manager.h', 'w', encoding='utf-8') as f:
    f.write(pwr)
print("SUCCESS: src/power_manager.h updated!")

# ==============================================================================
# 2. Update src/main.cpp
# ==============================================================================
with open('src/main.cpp', 'r', encoding='utf-8') as f:
    main_code = f.read()

# Add macro definition if not present
if '#define OLED_MODE_BATTERY_TEST' not in main_code:
    main_code = main_code.replace(
        'void update_oled_display();',
        '''// -------------------------------------------------------------
// CHẾ ĐỘ HIỂN THỊ OLED: 
// 1 = Bật màn hình kiểm tra Telemetry Pin (Phương án A)
// 0 = Khôi phục màn hình Cảm biến & Học tập 45 phút chuẩn
// -------------------------------------------------------------
#define OLED_MODE_BATTERY_TEST 1

void update_oled_display();'''
    )

# Wrap OLED display content with #if (OLED_MODE_BATTERY_TEST == 1)
old_oled_content = '''    // -------------------------------------------------------------
    // Line 1 (y = 12): Environment (Temp, Hum, Light Lux)
    // -------------------------------------------------------------
    display.setCursor(0, 12);
    if (sensors.isBmeOnline()) {
        if (sensors.isBh1750Online()) {
            float lux = sensors.getLux();
            display.printf("T:%.0fC H:%.0f%% V:%.2fV", sensors.getTemperature(), sensors.getHumidity(), power.getVoltage());
        } else {
            display.printf("T:%.1fC H:%.0f%% %dhPa", sensors.getTemperature(), sensors.getHumidity(), (int)sensors.getPressure());
        }
    } else if (sensors.isBh1750Online()) {
        display.printf("Anh sang: %.0f Lux", sensors.getLux());
    } else {
        display.print(F("ENV: SENSORS OFFLINE"));
    }

    // -------------------------------------------------------------
    // Line 2 (y = 23): VL53L0X Distance & Posture
    // -------------------------------------------------------------
    display.setCursor(0, 23);
    if (sensors.isTofOnline()) {
        float d_cm = sensors.getDistanceCm();
        display.printf("Dist:%.1fcm ", d_cm);
        if (d_cm < 25.0f) {
            // Flash warning if posture is too close (<25cm hunching alert)
            if ((millis() / 300) % 2 == 0) {
                display.print(F("[CUI SAT!]"));
            } else {
                display.print(F("[CANH BAO]"));
            }
        } else if (d_cm <= 65.0f) {
            display.print(F("[CHUAN]"));
        } else if (d_cm <= 100.0f) {
            display.print(F("[NGOI XA]"));
        } else {
            display.print(F("[ROI BAN]"));
        }
    } else {
        display.print(F("Dist: TOF OFFLINE"));
    }

    // -------------------------------------------------------------
    // Line 3 (y = 34): PIR Motion & User Presence
    // -------------------------------------------------------------
    display.setCursor(0, 34);
    display.print(F("PIR: "));
    if (sensors.isMotionDetected()) {
        display.print(F("CO CHUYEN DONG *"));
    } else if (sensors.isPresence()) {
        display.print(F("DANG NGOI YEN"));
    } else {
        display.print(F("VANG MAT (IDLE)"));
    }

    // -------------------------------------------------------------
    // Line 4 & 5 (y = 45..63): Study Timer & Progress Bar
    // -------------------------------------------------------------
    uint32_t session_sec = sensors.getSessionSeconds();
    uint32_t s_min = session_sec / 60;
    uint32_t s_sec = session_sec % 60;

    display.setCursor(0, 45);
    if (sensors.isPresence()) {
        if (s_min >= 45) {
            // Overdue Alert
            display.printf("Hoc:%02um%02us [NGHI NGOI!]", s_min, s_sec);
        } else {
            display.printf("Hoc:%02um%02us (Max 45m)", s_min, s_sec);
        }
    } else {
        display.print(F("Hoc: San sang (Cho)"));
    }

    // Progress bar for 45-minute study limit (width 96px, height 7px at y=56)
    int progress_w = constrain((int)((session_sec * 94) / (45 * 60)), 0, 94);
    display.drawRect(0, 56, 96, 7, SSD1306_WHITE);
    if (progress_w > 0) {
        display.fillRect(1, 57, progress_w, 5, SSD1306_WHITE);
    }
    display.setCursor(100, 56);
    display.printf("%2um", min((uint32_t)45, s_min));'''

new_oled_content = '''#if (OLED_MODE_BATTERY_TEST == 1)
    // =============================================================
    // PHƯƠNG ÁN A: MÀN HÌNH KIỂM TRA NGUỒN PIN & TELEMETRY (TEST MODE)
    // =============================================================
    
    // Dòng 1 (y = 12): Điện áp 5V & Dòng tải thực tế INA219
    display.setCursor(0, 12);
    display.printf("V:%.2fV | I:%4.0fmA", power.getVoltage(), power.getCurrentMa());

    // Dòng 2 (y = 23): Dung lượng đã xả / Dung lượng định danh
    display.setCursor(0, 23);
    display.printf("Used:%4.0f/%4.0fmAh", power.getUsedMah(), power.getNominalMah());

    // Dòng 3 (y = 34): Thời gian xả pin thực tế
    uint32_t dis_sec = power.getDischargeSeconds();
    uint32_t d_hr = dis_sec / 3600;
    uint32_t d_min = (dis_sec % 3600) / 60;
    uint32_t d_s = dis_sec % 60;
    display.setCursor(0, 34);
    display.printf("T_xa: %02uh %02um %02us", d_hr, d_min, d_s);

    // Dòng 4 (y = 45): Công suất tức thời (W) & mAh còn lại
    display.setCursor(0, 45);
    float p_watt = power.getPowerMw() / 1000.0f;
    if (p_watt < 0.0f) p_watt = 0.0f;
    display.printf("P:%.2fW | Rem:%.0fmAh", p_watt, power.getRemainingMah());

    // Dòng 5 (y = 56): Thanh pin đồ họa lớn (rộng 94px) + số %
    int bat_w = constrain((bat_soc * 94) / 100, 0, 94);
    display.drawRect(0, 56, 96, 7, SSD1306_WHITE);
    if (bat_w > 0) {
        display.fillRect(1, 57, bat_w, 5, SSD1306_WHITE);
    }
    display.setCursor(100, 56);
    display.printf("%3d%%", bat_soc);

#else
    // =============================================================
    // GIAO DIỆN NGUYÊN BẢN (CHUẨN HỌC TẬP & CẢM BIẾN MÔI TRƯỜNG)
    // =============================================================
    // Line 1 (y = 12): Environment (Temp, Hum, Light Lux)
    display.setCursor(0, 12);
    if (sensors.isBmeOnline()) {
        if (sensors.isBh1750Online()) {
            float lux = sensors.getLux();
            display.printf("T:%.0fC H:%.0f%% V:%.2fV", sensors.getTemperature(), sensors.getHumidity(), power.getVoltage());
        } else {
            display.printf("T:%.1fC H:%.0f%% %dhPa", sensors.getTemperature(), sensors.getHumidity(), (int)sensors.getPressure());
        }
    } else if (sensors.isBh1750Online()) {
        display.printf("Anh sang: %.0f Lux", sensors.getLux());
    } else {
        display.print(F("ENV: SENSORS OFFLINE"));
    }

    // Line 2 (y = 23): VL53L0X Distance & Posture
    display.setCursor(0, 23);
    if (sensors.isTofOnline()) {
        float d_cm = sensors.getDistanceCm();
        display.printf("Dist:%.1fcm ", d_cm);
        if (d_cm < 25.0f) {
            // Flash warning if posture is too close (<25cm hunching alert)
            if ((millis() / 300) % 2 == 0) {
                display.print(F("[CUI SAT!]"));
            } else {
                display.print(F("[CANH BAO]"));
            }
        } else if (d_cm <= 65.0f) {
            display.print(F("[CHUAN]"));
        } else if (d_cm <= 100.0f) {
            display.print(F("[NGOI XA]"));
        } else {
            display.print(F("[ROI BAN]"));
        }
    } else {
        display.print(F("Dist: TOF OFFLINE"));
    }

    // Line 3 (y = 34): PIR Motion & User Presence
    display.setCursor(0, 34);
    display.print(F("PIR: "));
    if (sensors.isMotionDetected()) {
        display.print(F("CO CHUYEN DONG *"));
    } else if (sensors.isPresence()) {
        display.print(F("DANG NGOI YEN"));
    } else {
        display.print(F("VANG MAT (IDLE)"));
    }

    // Line 4 & 5 (y = 45..63): Study Timer & Progress Bar
    uint32_t session_sec = sensors.getSessionSeconds();
    uint32_t s_min = session_sec / 60;
    uint32_t s_sec = session_sec % 60;

    display.setCursor(0, 45);
    if (sensors.isPresence()) {
        if (s_min >= 45) {
            // Overdue Alert
            display.printf("Hoc:%02um%02us [NGHI NGOI!]", s_min, s_sec);
        } else {
            display.printf("Hoc:%02um%02us (Max 45m)", s_min, s_sec);
        }
    } else {
        display.print(F("Hoc: San sang (Cho)"));
    }

    // Progress bar for 45-minute study limit (width 96px, height 7px at y=56)
    int progress_w = constrain((int)((session_sec * 94) / (45 * 60)), 0, 94);
    display.drawRect(0, 56, 96, 7, SSD1306_WHITE);
    if (progress_w > 0) {
        display.fillRect(1, 57, progress_w, 5, SSD1306_WHITE);
    }
    display.setCursor(100, 56);
    display.printf("%2um", min((uint32_t)45, s_min));

#endif'''

# Normalize line endings before replacing
old_clean = old_oled_content.replace('\r\n', '\n')
main_clean = main_code.replace('\r\n', '\n')

if old_clean in main_clean:
    main_clean = main_clean.replace(old_clean, new_oled_content.replace('\r\n', '\n'))
    with open('src/main.cpp', 'w', encoding='utf-8') as f:
        f.write(main_clean)
    print("SUCCESS: src/main.cpp updated!")
else:
    print("WARNING: old_oled_content not found in src/main.cpp")
