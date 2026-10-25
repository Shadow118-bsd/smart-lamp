# ==============================================================================
# Script to apply Smart Discharge Gate and Manual 3s Rotary Reset to Smart Lamp
# ==============================================================================

# 1. Update src/power_manager.h
with open('src/power_manager.h', 'r', encoding='utf-8') as f:
    pwr = f.read()

# Only count discharge time & used mAh when lamp is actually ON (load current >= 25mA)
old_dis = '''            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;
            m_used_mah += discharge_mah;
            m_discharge_sec += delta_sec;

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }'''

new_dis = '''            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;

            // CHỈ ĐẾM THỜI GIAN VÀ DUNG LƯỢNG TEST KHI ĐÈN THỰC SỰ BẬT SÁNG (I >= 25mA)
            // Khi tắt đèn (chỉ ăn dòng tĩnh 7-10mA) hoặc khi cắm sạc: Bộ đếm ĐÓNG BĂNG đứng yên!
            if (m_i_filtered >= 25.0f) {
                m_used_mah += discharge_mah;
                m_discharge_sec += delta_sec;
            }

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }'''

pwr = pwr.replace(old_dis, new_dis)

# Emergency force save before brownout
old_crit = '''        // 3. Cơ chế cảnh báo và bảo vệ pin yếu (chỉ áp dụng khi đang xả pin)
        if (m_state.source == POWER_SRC_BATTERY) {
            m_state.is_low_battery = (m_state.battery_percent <= SOC_LOW_BATTERY);
            m_state.is_critical = (m_state.battery_percent <= SOC_CRITICAL_CUTOFF);
        } else {
            m_state.is_low_battery = false;
            m_state.is_critical = false;
        }'''

new_crit = '''        // 3. Cơ chế cảnh báo và bảo vệ pin yếu (chỉ áp dụng khi đang xả pin)
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
        }'''

pwr = pwr.replace(old_crit, new_crit)

with open('src/power_manager.h', 'w', encoding='utf-8') as f:
    f.write(pwr)
print("1. src/power_manager.h updated!")

# 2. Update src/lighting_manager.h
with open('src/lighting_manager.h', 'r', encoding='utf-8') as f:
    lm = f.read()

# Add ResetCallback definition
lm = lm.replace(
    'typedef void (*ToneCallback)(float freq_hz, uint32_t duration_ms, float volume);',
    '''typedef void (*ToneCallback)(float freq_hz, uint32_t duration_ms, float volume);
    typedef void (*ResetCallback)();'''
)

# Add member variables in constructor & LightingManager
lm = lm.replace(
    '          m_tone_cb(nullptr),',
    '''          m_tone_cb(nullptr),
          m_reset_cb(nullptr),
          m_btn_vlong_fired(false),
          m_toast_is_reset(false),'''
)

# Update begin method signature
lm = lm.replace(
    '    void begin(ToneCallback tone_cb = nullptr) {',
    '''    void begin(ToneCallback tone_cb = nullptr, ResetCallback reset_cb = nullptr) {
        m_reset_cb = reset_cb;'''
)

# Add isToastReset and clearToastReset getters
lm = lm.replace(
    '    bool isQuickAdjust() const { return m_is_quick_adjust; }',
    '''    bool isQuickAdjust() const { return m_is_quick_adjust; }
    bool isToastReset() const { return m_toast_is_reset; }
    void clearToastReset() { m_toast_is_reset = false; }'''
)

# Update update() method button handling
old_btn_update = '''            } else {
                // Button Released (Check for Short Click)
                if (m_btn_is_down && !m_btn_long_fired) {
                    onButtonClick();
                }
                m_btn_is_down = false;
            }
        }

        // 3. Process Long Press (>= 1000ms / 1.0s)
        if (m_btn_is_down && !m_btn_long_fired) {
            if (now - m_btn_press_start >= 1000) {
                m_btn_long_fired = true;
                onButtonLongPress();
            }
        }'''

new_btn_update = '''            } else {
                // Button Released (Check for Short Click)
                if (m_btn_is_down && !m_btn_long_fired && !m_btn_vlong_fired) {
                    onButtonClick();
                }
                m_btn_is_down = false;
                m_btn_long_fired = false;
                m_btn_vlong_fired = false;
            }
        }

        // 3. Process Long Press (>= 1000ms / 1.0s) & Very Long Press (>= 3000ms / 3.0s)
        if (m_btn_is_down) {
            if (!m_btn_long_fired && (now - m_btn_press_start >= 1000)) {
                m_btn_long_fired = true;
                onButtonLongPress();
            }
            if (!m_btn_vlong_fired && (now - m_btn_press_start >= 3000)) {
                m_btn_vlong_fired = true;
                onButtonVeryLongPress();
            }
        }'''

lm = lm.replace(old_btn_update, new_btn_update)

# Add onButtonVeryLongPress() and clear m_toast_is_reset in other methods
old_long_press = '''    // Thao tác Long Press (>= 1.0s): Ở Home = Mở Menu; Ở Menu = Lưu NVS & Thoát Menu
    void onButtonLongPress() {
        if (!m_menu_active) {
            // Ở Home: Nhấn giữ 1s -> Mở Menu cài đặt
            m_menu_active = true;
            m_menu_cursor = 0;
            if (m_tone_cb) m_tone_cb(1000.0f, 40, 0.18f);
            m_oled_need_refresh = true;
            Serial.println(F("[ROTARY] Menu Opened via Long-Press (1.0s). Cursor at: Do sang"));
        } else {
            // Ở trong Menu: Nhấn giữ 1s -> Lưu NVS vĩnh viễn & Thoát Menu
            m_menu_active = false;
            m_is_quick_adjust = false;
            m_show_toast_until = millis() + 1200; // Toast xác nhận 1.2s

            saveToNvs();
            applyPwm();

            if (m_tone_cb) {
                m_tone_cb(880.0f, 60, 0.20f);
                delay(20);
                m_tone_cb(1318.5f, 100, 0.20f); // Chuông đôi xác nhận
            }

            m_oled_need_refresh = true;
            Serial.printf("[ROTARY] SETTINGS SAVED TO NVS: Br=%d%%, CCT=%dK, Mode=%s\\n",
                          m_brightness, m_cct, getPreset(m_mode).name);
        }
    }'''

new_long_press = '''    // Thao tác Long Press (>= 1.0s): Ở Home = Mở Menu; Ở Menu = Lưu NVS & Thoát Menu
    void onButtonLongPress() {
        m_toast_is_reset = false;
        if (!m_menu_active) {
            // Ở Home: Nhấn giữ 1s -> Mở Menu cài đặt
            m_menu_active = true;
            m_menu_cursor = 0;
            if (m_tone_cb) m_tone_cb(1000.0f, 40, 0.18f);
            m_oled_need_refresh = true;
            Serial.println(F("[ROTARY] Menu Opened via Long-Press (1.0s). Cursor at: Do sang"));
        } else {
            // Ở trong Menu: Nhấn giữ 1s -> Lưu NVS vĩnh viễn & Thoát Menu
            m_menu_active = false;
            m_is_quick_adjust = false;
            m_show_toast_until = millis() + 1200; // Toast xác nhận 1.2s

            saveToNvs();
            applyPwm();

            if (m_tone_cb) {
                m_tone_cb(880.0f, 60, 0.20f);
                delay(20);
                m_tone_cb(1318.5f, 100, 0.20f); // Chuông đôi xác nhận
            }

            m_oled_need_refresh = true;
            Serial.printf("[ROTARY] SETTINGS SAVED TO NVS: Br=%d%%, CCT=%dK, Mode=%s\\n",
                          m_brightness, m_cct, getPreset(m_mode).name);
        }
    }

    // Thao tác Very Long Press (>= 3.0s): Reset toàn bộ bộ đếm xả pin (T_xa & Used mAh)
    void onButtonVeryLongPress() {
        m_menu_active = false;
        m_is_quick_adjust = false;
        m_toast_is_reset = true;
        m_show_toast_until = millis() + 1800; // Hiện thông báo 1.8s
        m_oled_need_refresh = true;

        if (m_reset_cb) {
            m_reset_cb();
        }

        if (m_tone_cb) {
            m_tone_cb(1500.0f, 60, 0.25f);
            delay(30);
            m_tone_cb(2000.0f, 120, 0.25f);
        }
        Serial.println(F("[ROTARY] VERY LONG PRESS (3s) -> BATTERY DISCHARGE TEST RESET!"));
    }'''

lm = lm.replace(old_long_press, new_long_press)

# Add private members
lm = lm.replace(
    '    ToneCallback m_tone_cb;',
    '''    ToneCallback m_tone_cb;
    ResetCallback m_reset_cb;
    bool m_btn_vlong_fired;
    bool m_toast_is_reset;'''
)

with open('src/lighting_manager.h', 'w', encoding='utf-8') as f:
    f.write(lm)
print("2. src/lighting_manager.h updated!")

# 3. Update src/main.cpp
with open('src/main.cpp', 'r', encoding='utf-8') as f:
    mc = f.read()

# Update draw_save_toast to handle reset toast
old_toast = '''    display.setTextSize(1);
    display.setCursor(18, 14);
    if (lighting.isQuickAdjust()) {
        display.print(F("CHINH DO SANG"));
    } else {
        display.print(F("DA LUU CAI DAT!"));
    }
    display.drawFastHLine(14, 26, 100, SSD1306_WHITE);

    display.setCursor(14, 32);
    if (lighting.getBrightness() == 0) {
        display.print(F("Trang thai: DA TAT"));
    } else {
        display.printf("Sang:%d%% | %dK", lighting.getBrightness(), lighting.getCct());
    }

    if (lighting.isQuickAdjust()) {
        display.drawRect(14, 44, 100, 6, SSD1306_WHITE);
        int b_w = (lighting.getBrightness() * 98) / 100;
        if (b_w > 0) display.fillRect(15, 45, b_w, 4, SSD1306_WHITE);
    } else {
        display.setCursor(14, 44);
        display.printf("Mode: %s", LightingManager::getPreset(lighting.getMode()).name);
    }'''

new_toast = '''    display.setTextSize(1);
    if (lighting.isToastReset()) {
        display.setCursor(14, 14);
        display.print(F("DA RESET BO DEM!"));
        display.drawFastHLine(14, 26, 100, SSD1306_WHITE);
        display.setCursor(14, 32);
        display.print(F("T_xa : 00h 00m 00s"));
        display.setCursor(14, 44);
        display.print(F("Used : 0 / 3000mAh"));
    } else {
        display.setCursor(18, 14);
        if (lighting.isQuickAdjust()) {
            display.print(F("CHINH DO SANG"));
        } else {
            display.print(F("DA LUU CAI DAT!"));
        }
        display.drawFastHLine(14, 26, 100, SSD1306_WHITE);

        display.setCursor(14, 32);
        if (lighting.getBrightness() == 0) {
            display.print(F("Trang thai: DA TAT"));
        } else {
            display.printf("Sang:%d%% | %dK", lighting.getBrightness(), lighting.getCct());
        }

        if (lighting.isQuickAdjust()) {
            display.drawRect(14, 44, 100, 6, SSD1306_WHITE);
            int b_w = (lighting.getBrightness() * 98) / 100;
            if (b_w > 0) display.fillRect(15, 45, b_w, 4, SSD1306_WHITE);
        } else {
            display.setCursor(14, 44);
            display.printf("Mode: %s", LightingManager::getPreset(lighting.getMode()).name);
        }
    }'''

mc = mc.replace(old_toast, new_toast)

# Add callback and pass to lighting.begin
mc = mc.replace(
    'lighting.begin(play_tone);',
    '''lighting.begin(play_tone, []() {
        power.resetDischargeCounters();
    });'''
)

with open('src/main.cpp', 'w', encoding='utf-8') as f:
    f.write(mc)
print("3. src/main.cpp updated!")
