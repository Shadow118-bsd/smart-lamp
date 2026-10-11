#pragma once
#include <Arduino.h>
#include <Preferences.h>

/* ======================================================================================
 *   SMART DESK LAMP — MODULE 3: DEVICE / LIGHTING & POWER CONTROLLER
 *   Dual-Channel LEDC PWM (5000Hz, 10-bit), CCT (2700K-6500K), Gamma 2.2 Curve,
 *   Ben Buxton Full-Step FSM Rotary Encoder (KY-040) & Flash NVS Persistence.
 * ====================================================================================== */

struct LampPreset {
    const char* name;
    int brightness;
    int cct;
};

class LightingManager {
public:
    // Hardware Pin Assignments (Locked according to RULE.md)
    static const uint8_t PIN_LED_WARM   = 13; // MOSFET #1 (Vàng Ấm - 2700K)
    static const uint8_t PIN_LED_COOL   = 1;  // MOSFET #2 (Trắng Lạnh - 6500K)
    static const uint8_t PIN_ROTARY_CLK = 2;  // KY-040 CLK
    static const uint8_t PIN_ROTARY_DT  = 3;  // KY-040 DT
    static const uint8_t PIN_ROTARY_SW  = 43; // KY-040 SW (TX pin on ESP32-S3 SuperMini)

    // Hardware PWM Timer Configuration (LEDC)
    static const uint32_t PWM_FREQ     = 5000; // 5kHz Anti-Flicker & Camera safe
    static const uint8_t  PWM_RES      = 10;   // 10-bit resolution (0..1023)
    static const uint32_t PWM_MAX_DUTY = 1023;
    static const uint8_t  PWM_CH_WARM  = 2;    // LEDC Channel 2
    static const uint8_t  PWM_CH_COOL  = 3;    // LEDC Channel 3

    // 6 Ergonomic / Clinical Lighting Presets
    static const int MODE_COUNT = 6;

    typedef void (*ToneCallback)(float freq_hz, uint32_t duration_ms, float volume);

    LightingManager()
        : m_brightness(0),
          m_saved_brightness(80),
          m_cct(4000),
          m_mode(0),
          m_menu_active(false),
          m_menu_cursor(0),
          m_is_quick_adjust(false),
          m_show_toast_until(0),
          m_oled_need_refresh(true),
          m_tone_cb(nullptr),
          m_btn_last_raw(HIGH),
          m_btn_press_start(0),
          m_btn_is_down(false),
          m_btn_long_fired(false),
          m_btn_last_change(0)
    {
        s_instance = this;
    }

    void begin(ToneCallback tone_cb = nullptr) {
        m_tone_cb = tone_cb;

        Serial.println(F("[LIGHTING] Initializing Dual-Channel LED PWM & Rotary Encoder..."));

        // 1. Configure Hardware LEDC Timers & Pins
        ledcSetup(PWM_CH_WARM, PWM_FREQ, PWM_RES);
        ledcAttachPin(PIN_LED_WARM, PWM_CH_WARM);

        ledcSetup(PWM_CH_COOL, PWM_FREQ, PWM_RES);
        ledcAttachPin(PIN_LED_COOL, PWM_CH_COOL);

        // 2. Configure Rotary Pins & Interrupts
        pinMode(PIN_ROTARY_CLK, INPUT_PULLUP);
        pinMode(PIN_ROTARY_DT, INPUT_PULLUP);
        pinMode(PIN_ROTARY_SW, INPUT_PULLUP);

        s_rotary_fsm_state = R_START;
        s_rotary_delta = 0;

        attachInterrupt(digitalPinToInterrupt(PIN_ROTARY_CLK), isr_rotary_change, CHANGE);
        attachInterrupt(digitalPinToInterrupt(PIN_ROTARY_DT), isr_rotary_change, CHANGE);

        // 3. Load Saved Settings from Flash NVS
        loadFromNvs();

        // 4. Default Boot State: Standby OFF (0%)
        m_brightness = 0;
        applyPwm();

        Serial.printf("[LIGHTING] ONLINE! WARM=GPIO%d, COOL=GPIO%d (5kHz 10-bit) | NVS Mode=%d, CCT=%dK, SavedBr=%d%%\n",
                      PIN_LED_WARM, PIN_LED_COOL, m_mode, m_cct, m_saved_brightness);
    }

    void update() {
        unsigned long now = millis();

        // 1. Process Rotary Rotation Delta from ISR
        int delta = 0;
        if (s_rotary_delta != 0) {
            noInterrupts();
            delta = s_rotary_delta;
            s_rotary_delta = 0;
            interrupts();

            onRotaryTurn(delta);
        }

        // 2. Process Rotary SW Button with Debounce
        int raw_sw = digitalRead(PIN_ROTARY_SW);
        if (raw_sw != m_btn_last_raw && (now - m_btn_last_change > 30)) {
            m_btn_last_change = now;
            m_btn_last_raw = raw_sw;

            if (raw_sw == LOW) {
                // Button Pressed Down
                m_btn_is_down = true;
                m_btn_press_start = now;
                m_btn_long_fired = false;
            } else {
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
        }
    }

    // Mathematical Optical Core: Gamma 2.2 Curve & CCT Kelvin Mixing
    void applyPwm() {
        if (m_brightness == 0) {
            // Standby OFF: Cut all duty to zero
            ledcWrite(PWM_CH_WARM, 0);
            ledcWrite(PWM_CH_COOL, 0);
            return;
        }

        // Gamma 2.2 Photometric Curve: Eliminates harsh stepping in low light (1% - 20%)
        float normalized_b = (float)m_brightness / 100.0f;
        float scale = powf(normalized_b, 2.2f);

        // CCT Kelvin Mixing (Physical Range: 2700K .. 6500K)
        float cool_ratio = (float)(m_cct - 2700) / (6500.0f - 2700.0f);
        cool_ratio = constrain(cool_ratio, 0.0f, 1.0f);
        float warm_ratio = 1.0f - cool_ratio;

        // Calculate 10-bit integer Duty Cycles
        uint32_t duty_warm = (uint32_t)roundf(warm_ratio * scale * PWM_MAX_DUTY);
        uint32_t duty_cool = (uint32_t)roundf(cool_ratio * scale * PWM_MAX_DUTY);

        ledcWrite(PWM_CH_WARM, duty_warm);
        ledcWrite(PWM_CH_COOL, duty_cool);
    }

    // Toggle Power: Turn lamp ON (restore saved state) or OFF (save current state & 0% Standby)
    void togglePower() {
        if (m_brightness > 0) {
            // Lamp is currently ON -> TURN OFF
            m_saved_brightness = m_brightness;
            m_brightness = 0;
            saveToNvs();
            applyPwm();

            m_is_quick_adjust = false;
            m_show_toast_until = millis() + 1000;
            m_oled_need_refresh = true;

            if (m_tone_cb) m_tone_cb(600.0f, 40, 0.15f);
            Serial.printf("[LIGHTING] POWER OFF (Saved Brightness: %d%%)\n", m_saved_brightness);
        } else {
            // Lamp is currently OFF -> TURN ON
            m_brightness = (m_saved_brightness > 0) ? m_saved_brightness : 80;
            applyPwm();

            m_is_quick_adjust = false;
            m_show_toast_until = millis() + 1000;
            m_oled_need_refresh = true;

            if (m_tone_cb) m_tone_cb(1200.0f, 40, 0.20f);
            Serial.printf("[LIGHTING] POWER ON -> Restored: %d%%, %dK, Mode: %s\n",
                          m_brightness, m_cct, getPreset(m_mode).name);
        }
    }

    // Set Lamp State directly (used by online commands or external events)
    void setLampState(int power, int brightness, int cct, int mode, bool commitNvs = true) {
        if (power == 0) {
            if (m_brightness > 0) m_saved_brightness = m_brightness;
            m_brightness = 0;
        } else {
            m_brightness = constrain(brightness, 1, 100);
            m_saved_brightness = m_brightness;
        }
        m_cct = constrain(cct, 2700, 6500);
        m_mode = constrain(mode, 0, MODE_COUNT - 1);

        applyPwm();

        if (commitNvs) {
            saveToNvs();
        }

        m_is_quick_adjust = false;
        m_show_toast_until = millis() + 1500;
        m_oled_need_refresh = true;

        Serial.printf("[LIGHTING] SET STATE: Pwr=%d, Br=%d%%, CCT=%dK, Mode=%d (NVS Commit: %s)\n",
                      power, m_brightness, m_cct, m_mode, commitNvs ? "YES" : "NO");
    }

    // Flash NVS Non-Volatile Persistence
    void loadFromNvs() {
        m_prefs.begin("smart_lamp", false);
        m_mode = m_prefs.getInt("mode", 0);
        m_cct = m_prefs.getInt("cct", 4000);
        m_saved_brightness = m_prefs.getInt("brightness", 80);

        m_mode = constrain(m_mode, 0, MODE_COUNT - 1);
        m_cct = constrain(m_cct, 2700, 6500);
        m_saved_brightness = constrain(m_saved_brightness, 1, 100);
        m_prefs.end();
    }

    void saveToNvs() {
        m_prefs.begin("smart_lamp", false);
        m_prefs.putInt("mode", m_mode);
        m_prefs.putInt("cct", m_cct);
        if (m_brightness > 0) {
            m_prefs.putInt("brightness", m_brightness);
        } else if (m_saved_brightness > 0) {
            m_prefs.putInt("brightness", m_saved_brightness);
        }
        m_prefs.end();
        Serial.printf("[LIGHTING NVS] Committed: Mode=%d, CCT=%dK, Br=%d%%\n",
                      m_mode, m_cct, (m_brightness > 0) ? m_brightness : m_saved_brightness);
    }

    // Public Getters for OLED & Coordinator integration
    int getBrightness() const { return m_brightness; }
    int getSavedBrightness() const { return m_saved_brightness; }
    int getCct() const { return m_cct; }
    int getMode() const { return m_mode; }
    bool isMenuActive() const { return m_menu_active; }
    int getMenuCursor() const { return m_menu_cursor; }
    bool isQuickAdjust() const { return m_is_quick_adjust; }
    unsigned long getShowToastUntil() const { return m_show_toast_until; }
    bool needDisplayRefresh() const { return m_oled_need_refresh; }
    void clearDisplayRefresh() { m_oled_need_refresh = false; }
    void requestDisplayRefresh() { m_oled_need_refresh = true; }

    static const LampPreset& getPreset(int idx) {
        static const LampPreset s_presets[MODE_COUNT] = {
            {"Hoc Tap",   80, 5000},  // 0: Học Tập
            {"Doc Sach",  70, 4000},  // 1: Đọc Sách
            {"May Tinh",  40, 4000},  // 2: Máy Tính
            {"Thu Gian",  35, 3000},  // 3: Thư Giãn
            {"Ban Dem",   10, 2700},  // 4: Ban Đêm
            {"Thu Cong",  80, 4000}   // 5: Thủ Công
        };
        int safe_idx = constrain(idx, 0, MODE_COUNT - 1);
        return s_presets[safe_idx];
    }

private:
    int m_brightness;
    int m_saved_brightness;
    int m_cct;
    int m_mode;

    bool m_menu_active;
    int  m_menu_cursor; // 0: Độ sáng, 1: Nhiệt màu, 2: Chế độ
    bool m_is_quick_adjust;
    unsigned long m_show_toast_until;
    bool m_oled_need_refresh;

    ToneCallback m_tone_cb;
    Preferences  m_prefs;

    // Button Debounce & Timing
    bool m_btn_last_raw;
    unsigned long m_btn_press_start;
    bool m_btn_is_down;
    bool m_btn_long_fired;
    unsigned long m_btn_last_change;

    // -------------------------------------------------------------
    // Ben Buxton Full-Step FSM Table
    // -------------------------------------------------------------
    #define DIR_NONE      0x0
    #define DIR_CW        0x10
    #define DIR_CCW       0x20

    #define R_START       0x0
    #define R_CW_FINAL    0x1
    #define R_CW_BEGIN    0x2
    #define R_CW_NEXT     0x3
    #define R_CCW_BEGIN   0x4
    #define R_CCW_FINAL   0x5
    #define R_CCW_NEXT    0x6

    static volatile int s_rotary_delta;
    static volatile uint8_t s_rotary_fsm_state;
    static LightingManager* s_instance;

    static void IRAM_ATTR isr_rotary_change() {
        static const unsigned char ROTARY_TABLE[7][4] = {
          {R_START,    R_CW_BEGIN,  R_CCW_BEGIN, R_START},
          {R_CW_NEXT,  R_START,     R_CW_FINAL,  R_START | DIR_CW},
          {R_CW_NEXT,  R_CW_BEGIN,  R_START,     R_START},
          {R_CW_NEXT,  R_CW_BEGIN,  R_CW_FINAL,  R_START},
          {R_CCW_NEXT, R_START,     R_CCW_BEGIN, R_START},
          {R_CCW_NEXT, R_CCW_FINAL, R_START,     R_START | DIR_CCW},
          {R_CCW_NEXT, R_CCW_FINAL, R_CCW_BEGIN, R_START},
        };

        uint8_t pinstate = (digitalRead(PIN_ROTARY_CLK) << 1) | digitalRead(PIN_ROTARY_DT);
        s_rotary_fsm_state = ROTARY_TABLE[s_rotary_fsm_state & 0x0F][pinstate];
        uint8_t result = s_rotary_fsm_state & 0x30;
        if (result == DIR_CW) {
            s_rotary_delta += 1;
        } else if (result == DIR_CCW) {
            s_rotary_delta -= 1;
        }
    }

    // Thao tác Single Click: Bật/Tắt nguồn khi ở Home; Đổi Cursor khi ở Menu
    void onButtonClick() {
        if (!m_menu_active) {
            // Ở màn hình Home: Click = Toggle Bật/Tắt đèn
            togglePower();
        } else {
            // Ở trong Menu: Click = Chuyển mục cài đặt (Cursor 0 -> 1 -> 2 -> 0)
            m_menu_cursor = (m_menu_cursor + 1) % 3;
            if (m_tone_cb) m_tone_cb(1200.0f, 30, 0.15f);
            Serial.printf("[ROTARY] Cursor -> %d (%s)\n",
                          m_menu_cursor,
                          (m_menu_cursor == 0) ? "Do sang" : (m_menu_cursor == 1) ? "Nhiet mau" : "Che do");
            m_oled_need_refresh = true;
        }
    }

    // Thao tác Long Press (>= 1.0s): Ở Home = Mở Menu; Ở Menu = Lưu NVS & Thoát Menu
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
            Serial.printf("[ROTARY] SETTINGS SAVED TO NVS: Br=%d%%, CCT=%dK, Mode=%s\n",
                          m_brightness, m_cct, getPreset(m_mode).name);
        }
    }

    // Thao tác Xoay núm vặn
    void onRotaryTurn(int dir) {
        if (!m_menu_active) {
            // Ở màn hình Home: Quick Adjust độ sáng trực tiếp
            if (m_brightness == 0 && dir > 0) {
                // Đèn đang tắt, xoay CW -> Bật đèn lên 5%
                m_brightness = 5;
            } else {
                m_brightness = constrain(m_brightness + (dir * 5), 0, 100);
            }
            if (m_brightness > 0) m_saved_brightness = m_brightness;
            m_mode = 5; // Tự động chuyển mode sang Thủ Công

            applyPwm();
            m_is_quick_adjust = true;
            m_show_toast_until = millis() + 900; // Toast 900ms
            m_oled_need_refresh = true;

            Serial.printf("[ROTARY FAST] Brightness: %d%%\n", m_brightness);
            return;
        }

        // Ở trong Menu: Điều chỉnh theo con trỏ Cursor
        m_is_quick_adjust = false;
        if (m_menu_cursor == 0) {
            // Chỉnh Độ Sáng (±5%)
            m_brightness = constrain(m_brightness + (dir * 5), 0, 100);
            if (m_brightness > 0) m_saved_brightness = m_brightness;
            m_mode = 5;
            Serial.printf("[ROTARY MENU] Brightness: %d%%\n", m_brightness);
        } else if (m_menu_cursor == 1) {
            // Chỉnh Nhiệt Màu (±200K, dải 2700K - 6500K)
            m_cct = constrain(m_cct + (dir * 200), 2700, 6500);
            m_mode = 5;
            Serial.printf("[ROTARY MENU] CCT: %dK\n", m_cct);
        } else if (m_menu_cursor == 2) {
            // Chuyển 6 Chế độ Presets
            int m = m_mode + dir;
            while (m < 0) m += MODE_COUNT;
            m_mode = m % MODE_COUNT;

            if (m_mode != 5) {
                m_brightness = getPreset(m_mode).brightness;
                m_cct = getPreset(m_mode).cct;
                m_saved_brightness = m_brightness;
            }
            Serial.printf("[ROTARY MENU] Preset: %s (%d%%, %dK)\n",
                          getPreset(m_mode).name, m_brightness, m_cct);
        }

        applyPwm();
        m_oled_need_refresh = true;
    }
};

// Static member variable definitions
inline volatile int LightingManager::s_rotary_delta = 0;
inline volatile uint8_t LightingManager::s_rotary_fsm_state = 0;
inline LightingManager* LightingManager::s_instance = nullptr;
