#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <VL53L0X.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include "driver/i2s.h"
#include "sensors_manager.h"
#include "boot_voice_data.h"

Preferences preferences;

/*
 * ======================================================================================
 *   SMART DESK LAMP — INTERACTION & DASHBOARD CONTROLLER
 *   With I2C Watchdog, OLED 128x64 Dashboard & MAX98357A I2S Audio Amp
 *   (Sensors are permanently locked & managed in sensors_manager.h)
 * ======================================================================================
 */

// Hardware Pin Definitions (I2C Bus & PIR managed by sensors_manager.h)
#define PIN_I2C_SDA     8
#define PIN_I2C_SCL     9

// MAX98357A I2S Audio Amplifier Pins (I2S_NUM_1)
#define PIN_I2S_BCLK    10
#define PIN_I2S_LRC     11
#define PIN_I2S_DIN     12

// INMP441 I2S Microphone Pins (I2S_NUM_0)
#define PIN_MIC_WS      4
#define PIN_MIC_SCK     5
#define PIN_MIC_SD      6

// Rotary Encoder Pins (KY-040)
#define PIN_ROTARY_CLK  2
#define PIN_ROTARY_DT   3
#define PIN_ROTARY_SW   43 // Hardware TX pin on ESP32-S3 SuperMini

// Dual-Color LED Strip PWM (via LR7843 MOSFET Modules)
#define PIN_LED_WARM    13 // MOSFET #1 (Vàng Ấm)
#define PIN_LED_COOL    1  // MOSFET #2 (Trắng Lạnh)

#define PWM_FREQ        5000
#define PWM_RES         10
#define PWM_MAX_DUTY    1023
#define PWM_CH_WARM     2
#define PWM_CH_COOL     3

// OLED Display SSD1306 I2C (128x64)
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64
#define OLED_RESET      -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool g_oled_online = false;

// Wi-Fi Config
const char* WIFI_SSID = "Nemo 5G";
const char* WIFI_PASS = "Nemo@271105";
const IPAddress BROADCAST_IP(255, 255, 255, 255);
const uint16_t UDP_TELEMETRY_PORT = 12346;
const uint16_t UDP_AUDIO_PORT     = 12345;
const uint16_t UDP_SPEAKER_PORT   = 12347;

WiFiUDP udp;
WiFiUDP udp_audio;
WiFiUDP udp_speaker;

// 🔒 LOCKED SENSORS SUBSYSTEM INSTANCE (BME280 + VL53L0X + PIR)
SensorsManager sensors;

bool g_speaker_online = false;
bool g_mic_online = false;
volatile bool g_mic_active_listening = false;
volatile int16_t g_mic_last_peak = 0;

// Function Prototypes for Audio
void init_i2s_speaker();
void init_i2s_microphone();
static void mic_stream_task(void* pvParameters);
void play_tone(float freq_hz, uint32_t duration_ms, float volume = 0.5f);
void play_startup_chime();
void play_boot_voice();

// Performance Timers
unsigned long g_last_oled_ms = 0;

void update_oled_display();
void init_rotary_and_leds();
void update_rotary_encoder();
void apply_led_pwm();

void init_i2s_speaker() {
    Serial.println("[MAX98357A] Initializing I2S Speaker (BCLK=10, LRC=11, DIN=12)...");
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 12,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = true
    };
    i2s_pin_config_t pin_config = {
        .bck_io_num = PIN_I2S_BCLK,
        .ws_io_num = PIN_I2S_LRC,
        .data_out_num = PIN_I2S_DIN,
        .data_in_num = I2S_PIN_NO_CHANGE
    };
    esp_err_t err = i2s_driver_install(I2S_NUM_1, &i2s_config, 0, NULL);
    if (err == ESP_OK) {
        i2s_set_pin(I2S_NUM_1, &pin_config);
        i2s_zero_dma_buffer(I2S_NUM_1);

        // Ensure strong high-fidelity drive capability for I2S clocks & data on jumper wires
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_BCLK, GPIO_DRIVE_CAP_3);
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_LRC, GPIO_DRIVE_CAP_3);
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_DIN, GPIO_DRIVE_CAP_3);

        g_speaker_online = true;
        Serial.println("[MAX98357A] I2S Speaker ONLINE (Standard Drive Strength)!");
    } else {
        Serial.printf("[MAX98357A] I2S Driver install failed: %d\n", err);
    }
}

void init_i2s_microphone() {
    Serial.println("[INMP441] Initializing I2S Microphone (WS=4, SCK=5, SD=6 on I2S_NUM_0)...");
    i2s_config_t mic_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = 16000,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT, // Critical: INMP441 transmits 24-bit in 32-bit slot
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,  // L/R tied to GND
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = false
    };
    i2s_pin_config_t pin_config = {
        .bck_io_num = PIN_MIC_SCK,
        .ws_io_num = PIN_MIC_WS,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = PIN_MIC_SD
    };
    esp_err_t err = i2s_driver_install(I2S_NUM_0, &mic_config, 0, NULL);
    if (err == ESP_OK) {
        i2s_set_pin(I2S_NUM_0, &pin_config);
        i2s_zero_dma_buffer(I2S_NUM_0);
        g_mic_online = true;
        Serial.println("[INMP441] I2S Microphone ONLINE (32-bit DMA Capture)!");
    } else {
        Serial.printf("[INMP441] I2S Driver install failed: %d\n", err);
    }
}

static void mic_stream_task(void* pvParameters) {
    int32_t raw_buffer[256];
    int16_t pcm_buffer[256];
    int32_t dc_offset = 0;
    unsigned long last_dbg_ms = 0;
    unsigned long last_presence_active_ms = millis();
    unsigned long voice_hangover_until_ms = 0;
    const int16_t VAD_ENERGY_THRESHOLD = 450;

    while (1) {
        if (!g_mic_online) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        unsigned long now = millis();

        // 1. Always Active Listening: INMP441 I2S DMA operates continuously to catch wakewords and feed live audio telemetry
        bool user_present = sensors.isPresence();
        g_mic_active_listening = true; // Always active: 1.4mA digital MEMS mic uses <2% CPU DMA, zero heat, instant wakeword readiness

        size_t bytes_read = 0;
        esp_err_t res = i2s_read(I2S_NUM_0, raw_buffer, sizeof(raw_buffer), &bytes_read, pdMS_TO_TICKS(100));
        if (res == ESP_OK && bytes_read > 0) {
            size_t samples = bytes_read / 4;
            int16_t max_peak = 0;

            for (size_t i = 0; i < samples; i++) {
                // INMP441 24-bit audio in 32-bit I2S slot (MSB aligned):
                int32_t s24 = raw_buffer[i] >> 8;

                // DC Blocking Filter (alpha ~ 0.992) to eliminate static DC bias
                dc_offset = (int32_t)((dc_offset * 127 + s24) / 128);
                s24 -= dc_offset;

                // High-Gain Far-Field Boost (+12dB boost for 1m-3m sensitive capture)
                int32_t s16_calc = s24 >> 5;

                // Clamping to int16 range to prevent digital wraparound distortion
                if (s16_calc > 32767) s16_calc = 32767;
                if (s16_calc < -32768) s16_calc = -32768;

                int16_t s16 = (int16_t)s16_calc;
                pcm_buffer[i] = s16;
                int16_t abs_s = abs(s16);
                if (abs_s > max_peak) max_peak = abs_s;
            }

            g_mic_last_peak = max_peak;

            // 2. Hardware VAD: Detect voice energy above background room noise floor
            if (max_peak >= VAD_ENERGY_THRESHOLD) {
                voice_hangover_until_ms = now + 1800; // Hold open for 1.8s to capture full phrases
            }

            // Stream PCM audio chunk over Wi-Fi UDP:
            // - Active Voice: stream every single 16kHz chunk continuously (100% throughput)
            // - Room Ambient Baseline: stream 1 chunk every 100ms so dashboard always has live audio signal
            static unsigned long last_idle_audio_ms = 0;
            bool should_send_audio = false;
            if (now < voice_hangover_until_ms) {
                should_send_audio = true;
            } else if (now - last_idle_audio_ms >= 100) {
                last_idle_audio_ms = now;
                should_send_audio = true;
            }

            if (should_send_audio && WiFi.status() == WL_CONNECTED) {
                udp_audio.beginPacket(BROADCAST_IP, UDP_AUDIO_PORT);
                udp_audio.write((const uint8_t*)pcm_buffer, samples * 2);
                udp_audio.endPacket();
            }

            // Periodic heartbeat debug log every 3 seconds
            if (now - last_dbg_ms >= 3000) {
                last_dbg_ms = now;
                Serial.printf("[INMP441] VAD State: %s (peak=%d, listening=%d)\n",
                              (now < voice_hangover_until_ms) ? "VOICE TRANSMITTING" : "SILENCE (STANDBY)",
                              max_peak, g_mic_active_listening);
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

#define VOICE_RAM_BUFFER_MAX (216 * 1024) // 216KB = 7.0 seconds of 16kHz 16-bit Mono PCM (Fits DRAM0 segment)
static uint8_t s_voice_ram_buffer[VOICE_RAM_BUFFER_MAX];

void stream_audio_from_serial(uint32_t total_bytes) {
    if (!g_speaker_online) return;

    // Send ACK to Python: tell PC that ESP32 is ready to receive binary stream
    Serial.println("ACK_READY");
    Serial.flush();

    uint32_t to_receive = min(total_bytes, (uint32_t)VOICE_RAM_BUFFER_MAX);
    to_receive &= (~1); // Strict 16-bit (even number of bytes) alignment!
    uint32_t received = 0;
    unsigned long last_rx_ms = millis();

    Serial.setTimeout(500);
    // 1. Receive all audio data into RAM buffer with strict block reads (15s timeout for long sentences)
    while (received < to_receive && (millis() - last_rx_ms < 15000)) {
        size_t want = min((size_t)512, (size_t)(to_receive - received));
        size_t n = Serial.readBytes((char*)(s_voice_ram_buffer + received), want);
        if (n > 0) {
            received += n;
            last_rx_ms = millis();
        } else {
            delayMicroseconds(200);
        }
    }

    // 2. Drain any excess if total_bytes > buffer
    if (total_bytes > to_receive) {
        uint32_t excess = total_bytes - to_receive;
        while (excess > 0 && (millis() - last_rx_ms < 1200)) {
            int avail = Serial.available();
            if (avail > 0) {
                char dump[128];
                size_t n = Serial.readBytes(dump, min((size_t)avail, sizeof(dump)));
                excess -= n;
                last_rx_ms = millis();
            } else {
                delayMicroseconds(200);
            }
        }
    }

    Serial.printf("[AUDIO RX DONE] Target: %u, Got: %u bytes\n", to_receive, received);
    Serial.flush();

    if (received < 4) return;

    // 3. Play from RAM to I2S DMA with ZERO buffer underruns
    i2s_zero_dma_buffer(I2S_NUM_1);

    int16_t* pcm16 = (int16_t*)s_voice_ram_buffer;
    size_t total_samples = received / 2;
    int16_t stereo_chunk[128 * 2]; // 128 stereo samples (512 bytes)

    for (size_t i = 0; i < total_samples; i += 128) {
        size_t chunk_count = min((size_t)128, total_samples - i);
        for (size_t j = 0; j < chunk_count; j++) {
            int32_t val = (int32_t)pcm16[i + j] * 4;
            int16_t s = (int16_t)constrain(val, -29000, 29000);
            stereo_chunk[j * 2]     = s; // Left channel
            stereo_chunk[j * 2 + 1] = s; // Right channel
        }
        size_t written = 0;
        i2s_write(I2S_NUM_1, stereo_chunk, chunk_count * 4, &written, portMAX_DELAY);
    }

    delay(60);
    i2s_zero_dma_buffer(I2S_NUM_1);
}

void stream_audio_from_udp(uint32_t total_bytes) {
    if (!g_speaker_online) return;

    uint32_t to_receive = min(total_bytes, (uint32_t)VOICE_RAM_BUFFER_MAX);
    to_receive &= (~1); // Strict 16-bit (even number of bytes) alignment
    uint32_t received = 0;
    unsigned long last_rx_ms = millis();

    while (received < to_receive && (millis() - last_rx_ms < 15000)) {
        int packet_size = udp_speaker.parsePacket();
        if (packet_size > 0) {
            int want = min((int)packet_size, (int)(to_receive - received));
            int n = udp_speaker.read((char*)(s_voice_ram_buffer + received), want);
            if (n > 0) {
                received += n;
                last_rx_ms = millis();
            }
        } else {
            delayMicroseconds(200);
        }
    }

    Serial.printf("[UDP AUDIO RX DONE] Target: %u, Got: %u bytes\n", to_receive, received);

    if (received < 4) return;

    i2s_zero_dma_buffer(I2S_NUM_1);

    int16_t* pcm16 = (int16_t*)s_voice_ram_buffer;
    size_t total_samples = received / 2;
    int16_t stereo_chunk[128 * 2];

    for (size_t i = 0; i < total_samples; i += 128) {
        size_t count = min((size_t)128, total_samples - i);
        for (size_t j = 0; j < count; j++) {
            int32_t val = (int32_t)pcm16[i + j] * 4;
            int16_t s = (int16_t)constrain(val, -29000, 29000);
            stereo_chunk[j * 2]     = s; // Left
            stereo_chunk[j * 2 + 1] = s; // Right
        }
        size_t written = 0;
        i2s_write(I2S_NUM_1, stereo_chunk, count * 4, &written, portMAX_DELAY);
    }

    delay(60);
    i2s_zero_dma_buffer(I2S_NUM_1);
}

void play_tone(float freq_hz, uint32_t duration_ms, float volume) {
    if (!g_speaker_online) return;
    const uint32_t sample_rate = 16000;
    uint32_t total_samples = (sample_rate * duration_ms) / 1000;
    int16_t buffer[128 * 2]; // 128 stereo samples
    size_t bytes_written = 0;
    
    float phase = 0.0f;
    float phase_step = (2.0f * PI * freq_hz) / sample_rate;
    
    uint32_t samples_generated = 0;
    while (samples_generated < total_samples) {
        uint32_t chunk_samples = min((uint32_t)128, total_samples - samples_generated);
        for (uint32_t i = 0; i < chunk_samples; i++) {
            float env = 1.0f;
            // Smooth attack & decay to prevent clicking
            if (samples_generated + i < 150) {
                env = (float)(samples_generated + i) / 150.0f;
            } else if (total_samples - (samples_generated + i) < 150) {
                env = (float)(total_samples - (samples_generated + i)) / 150.0f;
            }
            int16_t sample = (int16_t)(sinf(phase) * 26000.0f * volume * env);
            buffer[i * 2] = sample;     // Left
            buffer[i * 2 + 1] = sample; // Right
            phase += phase_step;
            if (phase >= 2.0f * PI) phase -= 2.0f * PI;
        }
        i2s_write(I2S_NUM_1, buffer, chunk_samples * 4, &bytes_written, portMAX_DELAY);
        samples_generated += chunk_samples;
    }
    i2s_zero_dma_buffer(I2S_NUM_1);
}

void play_startup_chime() {
    if (!g_speaker_online) return;
    Serial.println("[AUDIO] Playing gentle test chime (C5 -> E5 -> G5 -> C6)...");
    play_tone(523.25f, 120, 0.85f); // C5 (85% volume)
    delay(20);
    play_tone(659.25f, 120, 0.85f); // E5 (85% volume)
    delay(20);
    play_tone(783.99f, 120, 0.85f); // G5 (85% volume)
    delay(20);
    play_tone(1046.50f, 260, 0.85f); // C6 (85% volume)
    Serial.println("[AUDIO] Startup chime completed.");
}

void play_boot_voice() {
    if (!g_speaker_online) return;
    Serial.println("[AUDIO] Playing boot voice announcement: 'Chào bạn, Tôi là trợ lý đèn thông minh'...");

    // Update OLED to display friendly greeting during voice playback
    if (g_oled_online) {
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(18, 10);
        display.println(F("SMART DESK LAMP"));
        display.drawFastHLine(10, 22, 108, SSD1306_WHITE);
        display.setCursor(16, 32);
        display.println(F("CHAO BAN!"));
        display.setCursor(8, 48);
        display.println(F("Tro ly den thong minh"));
        display.display();
    }

    i2s_zero_dma_buffer(I2S_NUM_1);

    // Stream 16kHz 16-bit Mono samples from Flash PROGMEM directly to I2S DMA in 128-sample chunks with 4.5x dynamic boost
    int16_t stereo_chunk[128 * 2];
    for (size_t i = 0; i < BOOT_VOICE_SAMPLE_COUNT; i += 128) {
        size_t count = min((size_t)128, (size_t)(BOOT_VOICE_SAMPLE_COUNT - i));
        for (size_t j = 0; j < count; j++) {
            int32_t boosted = (int32_t)boot_voice_pcm[i + j] * 4;
            int16_t s = (int16_t)constrain(boosted, -28000, 28000);
            stereo_chunk[j * 2]     = s; // Left
            stereo_chunk[j * 2 + 1] = s; // Right
        }
        size_t written = 0;
        i2s_write(I2S_NUM_1, stereo_chunk, count * 4, &written, portMAX_DELAY);
    }

    delay(60);
    i2s_zero_dma_buffer(I2S_NUM_1);
    Serial.println("[AUDIO] Boot voice finished.");
}

// -------------------------------------------------------------
// Rotary Menu & Lamp Control States
// -------------------------------------------------------------
struct LampPreset {
    const char* name;
    int brightness;
    int cct;
};

const LampPreset PRESETS[] = {
    {"Hoc Tap",   80, 5000},
    {"Doc Sach",  70, 4000},
    {"May Tinh",  40, 4000},
    {"Thu Gian",  35, 3000},
    {"Ban Dem",   10, 2700},
    {"Thu Cong",  80, 4000}
};
const int MODE_COUNT = sizeof(PRESETS) / sizeof(PRESETS[0]);

bool g_menu_active = false;
int g_menu_cursor = 0;      // 0: Do sang, 1: Nhiet mau, 2: Che do
int g_lamp_brightness = 0;  // Default Boot State: 0% (Standby OFF) until Wake Word or ON command
int g_lamp_cct = 4000;       // 2700K .. 6500K
int g_lamp_mode = 0;        // 0: Hoc Tap
unsigned long g_show_save_toast_until = 0;
bool g_is_quick_adjust = false;
bool g_oled_need_refresh = true;

// -------------------------------------------------------------
// Ben Buxton Full-Step State Machine for Rotary Encoders
// Matches hardware detent (1 click = exactly 1 step, zero reverse glitches)
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

static const unsigned char ROTARY_FULL_TABLE[7][4] = {
  // 00         01           10           11
  {R_START,    R_CW_BEGIN,  R_CCW_BEGIN, R_START},           // R_START
  {R_CW_NEXT,  R_START,     R_CW_FINAL,  R_START | DIR_CW},  // R_CW_FINAL
  {R_CW_NEXT,  R_CW_BEGIN,  R_START,     R_START},           // R_CW_BEGIN
  {R_CW_NEXT,  R_CW_BEGIN,  R_CW_FINAL,  R_START},           // R_CW_NEXT
  {R_CCW_NEXT, R_START,     R_CCW_BEGIN, R_START},           // R_CCW_BEGIN
  {R_CCW_NEXT, R_CCW_FINAL, R_START,     R_START | DIR_CCW}, // R_CCW_FINAL
  {R_CCW_NEXT, R_CCW_FINAL, R_CCW_BEGIN, R_START},           // R_CCW_NEXT
};

volatile int g_rotary_delta = 0;
volatile uint8_t s_rotary_fsm_state = R_START;

void IRAM_ATTR isr_rotary_change() {
    uint8_t pinstate = (digitalRead(PIN_ROTARY_CLK) << 1) | digitalRead(PIN_ROTARY_DT);
    s_rotary_fsm_state = ROTARY_FULL_TABLE[s_rotary_fsm_state & 0x0F][pinstate];
    uint8_t result = s_rotary_fsm_state & 0x30;
    if (result == DIR_CW) {
        g_rotary_delta += 1;
    } else if (result == DIR_CCW) {
        g_rotary_delta -= 1;
    }
}

void apply_led_pwm() {
    float scale = (float)g_lamp_brightness / 100.0f;
    float cool_ratio = (float)(g_lamp_cct - 2700) / (6500.0f - 2700.0f);
    cool_ratio = constrain(cool_ratio, 0.0f, 1.0f);
    float warm_ratio = 1.0f - cool_ratio;

    uint32_t duty_warm = (uint32_t)(warm_ratio * scale * PWM_MAX_DUTY);
    uint32_t duty_cool = (uint32_t)(cool_ratio * scale * PWM_MAX_DUTY);

    ledcWrite(PWM_CH_WARM, duty_warm);
    ledcWrite(PWM_CH_COOL, duty_cool);
}

void on_rotary_button_click() {
    if (!g_menu_active) {
        g_menu_active = true;
        g_menu_cursor = 0; // Starts pointing at Do sang
        play_tone(1000.0f, 30, 0.15f);
        Serial.println("[ROTARY] Menu Opened. Cursor at: Do sang");
    } else {
        g_menu_cursor = (g_menu_cursor + 1) % 3;
        play_tone(1200.0f, 30, 0.15f);
        Serial.printf("[ROTARY] Cursor -> %d (%s)\n", 
                      g_menu_cursor, 
                      (g_menu_cursor == 0) ? "Do sang" : (g_menu_cursor == 1) ? "Nhiet mau" : "Che do");
    }
    g_oled_need_refresh = true;
}

void on_rotary_button_long_press() {
    Serial.println("[ROTARY] Button Long-Pressed (> 1.5s)!");
    if (g_menu_active) {
        g_menu_active = false;
        g_is_quick_adjust = false;
        g_show_save_toast_until = millis() + 1200; // Show confirmation for 1.2s
        
        play_tone(880.0f, 60, 0.20f);
        delay(20);
        play_tone(1318.5f, 100, 0.20f);

        apply_led_pwm();
        Serial.printf("[ROTARY] SETTINGS SAVED: Brightness=%d%%, CCT=%dK, Mode=%s\n",
                      g_lamp_brightness, g_lamp_cct, PRESETS[g_lamp_mode].name);
    } else {
        play_tone(880.0f, 50, 0.15f);
    }
    g_oled_need_refresh = true;
}

void on_rotary_turn(int dir) {
    if (!g_menu_active) {
        // Direct knob turn on home screen: adjust brightness instantly!
        g_lamp_brightness = constrain(g_lamp_brightness + (dir * 5), 0, 100);
        g_lamp_mode = 5; // Thu Cong
        apply_led_pwm();
        g_is_quick_adjust = true;
        g_show_save_toast_until = millis() + 900;
        g_oled_need_refresh = true;
        Serial.printf("[ROTARY FAST] Brightness: %d%%\n", g_lamp_brightness);
        return;
    }

    g_is_quick_adjust = false;
    if (g_menu_cursor == 0) {
        g_lamp_brightness = constrain(g_lamp_brightness + (dir * 5), 0, 100);
        g_lamp_mode = 5; // Thu Cong
        Serial.printf("[ROTARY] Brightness: %d%%\n", g_lamp_brightness);
    } else if (g_menu_cursor == 1) {
        g_lamp_cct = constrain(g_lamp_cct + (dir * 200), 2700, 6500);
        g_lamp_mode = 5; // Thu Cong
        Serial.printf("[ROTARY] CCT: %dK\n", g_lamp_cct);
    } else if (g_menu_cursor == 2) {
        int m = g_lamp_mode + dir;
        while (m < 0) m += MODE_COUNT;
        g_lamp_mode = m % MODE_COUNT;

        if (g_lamp_mode != 5) {
            g_lamp_brightness = PRESETS[g_lamp_mode].brightness;
            g_lamp_cct = PRESETS[g_lamp_mode].cct;
        }
        Serial.printf("[ROTARY] Mode: %s (%d%%, %dK)\n", 
                      PRESETS[g_lamp_mode].name, g_lamp_brightness, g_lamp_cct);
    }

    apply_led_pwm();
    g_oled_need_refresh = true;
}

void update_rotary_encoder() {
    unsigned long now = millis();

    // Check rotary rotation delta from ISR
    if (g_rotary_delta != 0) {
        int delta = 0;
        noInterrupts();
        delta = g_rotary_delta;
        g_rotary_delta = 0;
        interrupts();

        on_rotary_turn(delta);
    }

    // Check Rotary SW Button
    static bool s_btn_last_raw = HIGH;
    static unsigned long s_btn_press_start = 0;
    static bool s_btn_is_down = false;
    static bool s_btn_long_fired = false;
    static unsigned long s_btn_last_change = 0;

    int raw_sw = digitalRead(PIN_ROTARY_SW);
    if (raw_sw != s_btn_last_raw && (now - s_btn_last_change > 30)) {
        s_btn_last_change = now;
        s_btn_last_raw = raw_sw;

        if (raw_sw == LOW) {
            s_btn_is_down = true;
            s_btn_press_start = now;
            s_btn_long_fired = false;
        } else {
            if (s_btn_is_down && !s_btn_long_fired) {
                on_rotary_button_click();
            }
            s_btn_is_down = false;
        }
    }

    // Long press > 1.5s
    if (s_btn_is_down && !s_btn_long_fired) {
        if (now - s_btn_press_start >= 1500) {
            s_btn_long_fired = true;
            on_rotary_button_long_press();
        }
    }
}

void init_rotary_and_leds() {
    Serial.println("[ROTARY & LED] Initializing pins...");

    pinMode(PIN_ROTARY_CLK, INPUT_PULLUP);
    pinMode(PIN_ROTARY_DT, INPUT_PULLUP);
    pinMode(PIN_ROTARY_SW, INPUT_PULLUP);

    s_rotary_fsm_state = R_START;

    attachInterrupt(digitalPinToInterrupt(PIN_ROTARY_CLK), isr_rotary_change, CHANGE);
    attachInterrupt(digitalPinToInterrupt(PIN_ROTARY_DT), isr_rotary_change, CHANGE);

    ledcSetup(PWM_CH_WARM, PWM_FREQ, PWM_RES);
    ledcAttachPin(PIN_LED_WARM, PWM_CH_WARM);

    ledcSetup(PWM_CH_COOL, PWM_FREQ, PWM_RES);
    ledcAttachPin(PIN_LED_COOL, PWM_CH_COOL);

    apply_led_pwm();
    Serial.printf("[ROTARY & LED] OK! CLK=%d, DT=%d, SW=%d | WARM=%d, COOL=%d\n",
                  PIN_ROTARY_CLK, PIN_ROTARY_DT, PIN_ROTARY_SW, PIN_LED_WARM, PIN_LED_COOL);
}

void draw_menu_screen() {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);

    // Header (y = 0..9)
    display.setTextSize(1);
    display.setCursor(16, 0);
    display.print(F("CAI DAT HE THONG"));
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    // Line 0 (y = 14): Do sang (Starts at x = 0 to prevent overflow)
    display.setCursor(0, 14);
    if (g_menu_cursor == 0) {
        display.print(F("->Do sang  : "));
    } else {
        display.print(F("  Do sang  : "));
    }
    if (g_lamp_brightness == 0) {
        display.print(F("0% (TAT)"));
    } else {
        display.printf("%d%%", g_lamp_brightness);
    }

    // Line 1 (y = 26): Nhiet mau (Starts at x = 0)
    display.setCursor(0, 26);
    if (g_menu_cursor == 1) {
        display.print(F("->Nhiet mau: "));
    } else {
        display.print(F("  Nhiet mau: "));
    }
    display.printf("%dK", g_lamp_cct);

    // Line 2 (y = 38): Che do (Starts at x = 0, snug padding so no text spills)
    display.setCursor(0, 38);
    if (g_menu_cursor == 2) {
        display.print(F("->Che do  : "));
    } else {
        display.print(F("  Che do  : "));
    }
    display.print(PRESETS[g_lamp_mode].name);

    // Footer divider (y = 50)
    display.drawFastHLine(0, 50, 128, SSD1306_WHITE);

    // Footer guide (y = 54)
    display.setCursor(2, 54);
    display.print(F("Xoay:Chinh | Giu:Luu"));

    display.display();
}

void draw_save_toast() {
    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);
    display.drawRoundRect(4, 4, 120, 56, 4, SSD1306_WHITE);
    display.drawRoundRect(6, 6, 116, 52, 2, SSD1306_WHITE);

    display.setTextSize(1);
    display.setCursor(18, 14);
    if (g_is_quick_adjust) {
        display.print(F("CHINH DO SANG"));
    } else {
        display.print(F("DA LUU CAI DAT!"));
    }
    display.drawFastHLine(14, 26, 100, SSD1306_WHITE);

    display.setCursor(14, 32);
    if (g_lamp_brightness == 0) {
        display.print(F("Trang thai: DA TAT"));
    } else {
        display.printf("Sang:%d%% | %dK", g_lamp_brightness, g_lamp_cct);
    }

    if (g_is_quick_adjust) {
        display.drawRect(14, 44, 100, 6, SSD1306_WHITE);
        int b_w = (g_lamp_brightness * 98) / 100;
        if (b_w > 0) display.fillRect(15, 45, b_w, 4, SSD1306_WHITE);
    } else {
        display.setCursor(14, 44);
        display.printf("Mode: %s", PRESETS[g_lamp_mode].name);
    }

    display.display();
}

void update_oled_display() {
    if (!g_oled_online) {
        static unsigned long s_last_oled_retry = 0;
        if (millis() - s_last_oled_retry >= 1500) {
            s_last_oled_retry = millis();
            if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C) || display.begin(SSD1306_SWITCHCAPVCC, 0x3D)) {
                g_oled_online = true;
                Serial.println("[OLED] SSD1306 Hot-Plug Detected & Initialized!");
            }
        }
        return;
    }

    if (millis() < g_show_save_toast_until) {
        draw_save_toast();
        return;
    }

    if (g_menu_active) {
        draw_menu_screen();
        return;
    }

    display.clearDisplay();
    display.setTextColor(SSD1306_WHITE);

    // -------------------------------------------------------------
    // Header (y = 0): "SMART LAMP" + "WF:OK" + "SPK"
    // -------------------------------------------------------------
    display.setTextSize(1);
    display.setCursor(0, 0);
    display.print(F("SMART LAMP"));

    // WiFi status badge
    display.setCursor(68, 0);
    if (WiFi.status() == WL_CONNECTED) {
        display.print(F("WF:OK"));
    } else {
        display.print(F("WF:--"));
    }

    // Mic & Speaker indicator badges [MS] ('M' = actively listening, 'm' = presence power-save standby)
    display.setCursor(102, 0);
    display.printf("[%c%c]", g_mic_online ? (g_mic_active_listening ? 'M' : 'm') : '-', g_speaker_online ? 'S' : '-');

    // Header divider line
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    // -------------------------------------------------------------
    // Line 1 (y = 12): Environment (Temp, Hum, Light Lux)
    // -------------------------------------------------------------
    display.setCursor(0, 12);
    if (sensors.isBmeOnline()) {
        if (sensors.isBh1750Online()) {
            float lux = sensors.getLux();
            if (lux >= 1000.0f) {
                display.printf("T:%.1fC H:%.0f%% Lux:%.1fk", sensors.getTemperature(), sensors.getHumidity(), lux / 1000.0f);
            } else {
                display.printf("T:%.1fC H:%.0f%% Lux:%.0f", sensors.getTemperature(), sensors.getHumidity(), lux);
            }
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
    display.printf("%2um", min((uint32_t)45, s_min));

    display.display();
}

void setup() {
    setCpuFrequencyMhz(160); // Dynamic Frequency Scaling: 160MHz eliminates overheating, cuts power ~40% while preserving full APB/I2S/PWM clock accuracy
    Serial.setRxBufferSize(16384); // Expand USB-CDC RX buffer to 16KB to prevent ANY byte loss
    Serial.begin(115200);
    delay(1000);

    Serial.println("\n========================================================");
    Serial.println("  💡 SMART DESK LAMP — SENSOR & CONTEXT ENGINE BOOTING ");
    Serial.println("========================================================");

    // 1. Initialize I2C Bus with 100ms Hardware Timeout (100kHz Standard Robust I2C)
    Serial.println("[I2C] Initializing Bus on SDA=GPIO 8, SCL=GPIO 9 (100kHz)...");
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL, 100000);
    Wire.setTimeOut(100); // Prevent bus lockup

    // 2. Probe I2C Bus
    Serial.println("[I2C] Scanning I2C Bus...");
    byte found_devices = 0;
    for (byte addr = 1; addr < 127; addr++) {
        Wire.beginTransmission(addr);
        if (Wire.endTransmission() == 0) {
            Serial.printf("  -> Detected I2C Device at: 0x%02X\n", addr);
            found_devices++;
        }
    }
    Serial.printf("[I2C] Total %d devices detected on bus.\n", found_devices);

    // 3. Initialize SSD1306 OLED (0x3C / 0x3D)
    if (display.begin(SSD1306_SWITCHCAPVCC, 0x3C) || display.begin(SSD1306_SWITCHCAPVCC, 0x3D)) {
        g_oled_online = true;
        display.clearDisplay();
        display.setTextColor(SSD1306_WHITE);
        display.setTextSize(1);
        display.setCursor(18, 12);
        display.println(F("SMART DESK LAMP"));
        display.drawFastHLine(10, 24, 108, SSD1306_WHITE);
        display.setCursor(12, 34);
        display.println(F("SYSTEM BOOTING..."));
        display.setCursor(16, 48);
        display.println(F("AI SENSORS INIT"));
        display.display();
        Serial.println("[OLED] SSD1306 128x64 ONLINE!");
    } else {
        Serial.println("[OLED] SSD1306 not found on 0x3C or 0x3D.");
    }

    // 4. 🔒 INITIALIZE LOCKED SENSORS SUBSYSTEM (BME280 + VL53L0X + PIR)
    sensors.begin(&Wire);

    // 5. Initialize MAX98357A I2S Audio Amplifier (GPIO 10=BCLK, 11=LRC, 12=DIN)
    init_i2s_speaker();
    play_startup_chime(); // Tiếng bíp
    delay(150);
    play_boot_voice();    // Nói: "Chào bạn, Tôi là trợ lý đèn thông minh" (từ test_voice.mp3)

    // 6. Initialize INMP441 I2S Microphone (WS=GPIO 4, SCK=GPIO 5, SD=GPIO 6 on I2S_NUM_0)
    init_i2s_microphone();
    xTaskCreatePinnedToCore(mic_stream_task, "mic_stream", 4096, NULL, 5, NULL, 1);

    // 7. Initialize Rotary Encoder & LED Dimming Subsystem
    init_rotary_and_leds();

    // 8. Connect Wi-Fi & Initialize UDP Speaker Receiver Port (12347)
    preferences.begin("smart_lamp", false);
    g_lamp_mode = preferences.getInt("mode", 0);
    g_lamp_cct = preferences.getInt("cct", 4000);
    
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    udp_speaker.begin(UDP_SPEAKER_PORT);
    Serial.printf("[WIFI] Connecting to '%s'... (UDP Speaker Port %d Online | NVS Mode=%d, CCT=%dK)\n", WIFI_SSID, UDP_SPEAKER_PORT, g_lamp_mode, g_lamp_cct);
}

void process_control_command(const String& cmd) {
    if (cmd.startsWith("[SET_LAMP:")) {
        int p1 = cmd.indexOf(':');
        int p2 = cmd.indexOf(':', p1 + 1);
        int p3 = cmd.indexOf(':', p2 + 1);
        int p4 = cmd.indexOf(':', p3 + 1);
        int p5 = cmd.indexOf(']', p4 + 1);
        if (p1 > 0 && p2 > 0 && p3 > 0 && p4 > 0 && p5 > 0) {
            int pwr = cmd.substring(p1 + 1, p2).toInt();
            int br  = cmd.substring(p2 + 1, p3).toInt();
            int cct = cmd.substring(p3 + 1, p4).toInt();
            int md  = cmd.substring(p4 + 1, p5).toInt();

            if (pwr == 0) {
                g_lamp_brightness = 0;
            } else {
                g_lamp_brightness = constrain(br, 1, 100);
            }
            g_lamp_cct = constrain(cct, 2400, 6500);
            g_lamp_mode = constrain(md, 0, MODE_COUNT - 1);

            // Save state to NVS Non-Volatile Memory
            preferences.putInt("mode", g_lamp_mode);
            preferences.putInt("cct", g_lamp_cct);
            if (g_lamp_brightness > 0) {
                preferences.putInt("brightness", g_lamp_brightness);
            }

            apply_led_pwm();
            g_show_save_toast_until = millis() + 1500;
            g_oled_need_refresh = true;
            Serial.printf("[LAMP CONTROL] PWR=%d, BR=%d%%, CCT=%dK, MODE=%d (NVS Saved)\n", pwr, g_lamp_brightness, g_lamp_cct, g_lamp_mode);
        }
    }
}

void loop() {
    unsigned long now = millis();

    // 🔒 Fast/Slow Sensor Polling & Hysteresis Filtering (Handled by locked module)
    sensors.update();

    // Rotary Encoder Knob & Button interaction
    update_rotary_encoder();

    // Check UDP Speaker Port (12347) for incoming voice streams or test commands over Wi-Fi
    int packet_size = udp_speaker.parsePacket();
    if (packet_size > 0) {
        char packet_buf[512];
        int len = udp_speaker.read(packet_buf, sizeof(packet_buf) - 1);
        if (len > 0) {
            packet_buf[len] = '\0';
            String pkt = String(packet_buf);
            if (pkt.startsWith("[VOICE_START:")) {
                int idx1 = pkt.indexOf(':');
                int idx2 = pkt.indexOf(':', idx1 + 1);
                int idx3 = pkt.indexOf(']', idx2 + 1);
                if (idx2 > 0 && idx3 > 0) {
                    uint32_t total_bytes = pkt.substring(idx2 + 1, idx3).toInt();
                    if (total_bytes > 0 && total_bytes < 2000000) {
                        stream_audio_from_udp(total_bytes);
                    }
                }
            } else if (pkt.startsWith("[SET_LAMP:")) {
                process_control_command(pkt);
            } else if (pkt.startsWith("PLAY_CHIME") || pkt.startsWith("TEST_AUDIO")) {
                play_startup_chime();
            }
        }
    }

    // Check Serial for voice streaming or audio test commands
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("[VOICE_START:")) {
            int idx1 = cmd.indexOf(':');
            int idx2 = cmd.indexOf(':', idx1 + 1);
            int idx3 = cmd.indexOf(']', idx2 + 1);
            if (idx2 > 0 && idx3 > 0) {
                uint32_t total_bytes = cmd.substring(idx2 + 1, idx3).toInt();
                if (total_bytes > 0 && total_bytes < 2000000) {
                    stream_audio_from_serial(total_bytes);
                }
            }
        } else if (cmd.startsWith("[SET_LAMP:")) {
            process_control_command(cmd);
        } else if (cmd == "PLAY_CHIME" || cmd == "TEST_AUDIO") {
            play_startup_chime();
        }
    }

    // OLED Display Refresh
    // Non-blocking & throttled to protect 100kHz I2C bus bandwidth for locked sensors
    uint32_t oled_idle_interval = (g_menu_active || millis() < g_show_save_toast_until) ? 120 : 300;
    if (g_oled_need_refresh || (now - g_last_oled_ms >= oled_idle_interval)) {
        if (now - g_last_oled_ms >= 40) { // Max ~25 FPS to prevent I2C congestion
            g_last_oled_ms = now;
            g_oled_need_refresh = false;
            update_oled_display();
        }
    }

    // 🔒 Broadcast Telemetry (Every 60ms via Serial and UDP)
    sensors.broadcastTelemetry(&udp, BROADCAST_IP, UDP_TELEMETRY_PORT, g_speaker_online, g_oled_online, g_mic_online, g_mic_active_listening, g_mic_last_peak);
}
