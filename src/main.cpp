#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_BME280.h>
#include <VL53L0X.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "driver/i2s.h"
#include "sensors_manager.h"
#include "boot_voice_data.h"

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

// OLED Display SSD1306 I2C (128x64)
#define SCREEN_WIDTH    128
#define SCREEN_HEIGHT   64
#define OLED_RESET      -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool g_oled_online = false;

// Wi-Fi Config
const char* WIFI_SSID = "PTIT.HCM_SV";
const char* WIFI_PASS = "";
const IPAddress BROADCAST_IP(255, 255, 255, 255);
const uint16_t UDP_TELEMETRY_PORT = 12346;
const uint16_t UDP_AUDIO_PORT     = 12345;

WiFiUDP udp;
WiFiUDP udp_audio;

// 🔒 LOCKED SENSORS SUBSYSTEM INSTANCE (BME280 + VL53L0X + PIR)
SensorsManager sensors;

bool g_speaker_online = false;
bool g_mic_online = false;

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

        // Soften GPIO edge rise-time to eliminate ringing & EMI noise on jumper wires
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_BCLK, GPIO_DRIVE_CAP_1);
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_LRC, GPIO_DRIVE_CAP_1);
        gpio_set_drive_capability((gpio_num_t)PIN_I2S_DIN, GPIO_DRIVE_CAP_1);

        g_speaker_online = true;
        Serial.println("[MAX98357A] I2S Speaker ONLINE (Low-EMI Softened Clock)!");
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

    while (1) {
        if (!g_mic_online) {
            vTaskDelay(pdMS_TO_TICKS(500));
            continue;
        }

        size_t bytes_read = 0;
        esp_err_t res = i2s_read(I2S_NUM_0, raw_buffer, sizeof(raw_buffer), &bytes_read, pdMS_TO_TICKS(100));
        if (res == ESP_OK && bytes_read > 0) {
            size_t samples = bytes_read / 4;
            int16_t max_peak = 0;

            for (size_t i = 0; i < samples; i++) {
                // INMP441 24-bit in 32-bit slot:
                // Shift right by 10 (+24dB hardware-level far-field boost for 1m-3m)
                int32_t s = raw_buffer[i] >> 10;

                // DC blocking filter (alpha ~ 0.99)
                dc_offset = (int32_t)((dc_offset * 127 + s) / 128);
                s -= dc_offset;

                // Saturation clamping to int16 range to prevent digital wrap-around distortion
                if (s > 32767) s = 32767;
                if (s < -32768) s = -32768;

                int16_t s16 = (int16_t)s;
                pcm_buffer[i] = s16;
                int16_t abs_s = abs(s16);
                if (abs_s > max_peak) max_peak = abs_s;
            }

            // Stream PCM audio chunk over Wi-Fi UDP to destination PC port 12345
            if (WiFi.status() == WL_CONNECTED) {
                udp_audio.beginPacket(BROADCAST_IP, UDP_AUDIO_PORT);
                udp_audio.write((const uint8_t*)pcm_buffer, samples * 2);
                udp_audio.endPacket();
            }

            // Periodic heartbeat debug log every 3 seconds
            if (millis() - last_dbg_ms >= 3000) {
                last_dbg_ms = millis();
                Serial.printf("[INMP441] Audio streaming active (peak=%d, %d samples/frame)\n", max_peak, samples);
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }
}

#define VOICE_RAM_BUFFER_MAX (128 * 1024) // 128KB = 4 seconds of 16kHz 16-bit Mono PCM
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

    // 1. Receive all audio data into RAM buffer with strict block reads
    while (received < to_receive && (millis() - last_rx_ms < 3000)) {
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
        while (excess > 0 && (millis() - last_rx_ms < 800)) {
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
            int16_t s = pcm16[i + j];
            stereo_chunk[j * 2]     = s; // Left channel
            stereo_chunk[j * 2 + 1] = s; // Right channel
        }
        size_t written = 0;
        i2s_write(I2S_NUM_1, stereo_chunk, chunk_count * 4, &written, portMAX_DELAY);
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
            int16_t sample = (int16_t)(sinf(phase) * 16000.0f * volume * env);
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
    play_tone(523.25f, 120, 0.20f); // C5 (20% volume)
    delay(20);
    play_tone(659.25f, 120, 0.20f); // E5 (20% volume)
    delay(20);
    play_tone(783.99f, 120, 0.20f); // G5 (20% volume)
    delay(20);
    play_tone(1046.50f, 260, 0.20f); // C6 (20% volume)
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

    // Stream 16kHz 16-bit Mono samples from Flash PROGMEM directly to I2S DMA in 128-sample chunks
    int16_t stereo_chunk[128 * 2];
    for (size_t i = 0; i < BOOT_VOICE_SAMPLE_COUNT; i += 128) {
        size_t count = min((size_t)128, (size_t)(BOOT_VOICE_SAMPLE_COUNT - i));
        for (size_t j = 0; j < count; j++) {
            int16_t s = boot_voice_pcm[i + j];
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

    // Mic & Speaker indicator badges [MS]
    display.setCursor(102, 0);
    display.printf("[%c%c]", g_mic_online ? 'M' : '-', g_speaker_online ? 'S' : '-');

    // Header divider line
    display.drawFastHLine(0, 9, 128, SSD1306_WHITE);

    // -------------------------------------------------------------
    // Line 1 (y = 12): BME280 Environment (Temp, Hum, Pressure)
    // -------------------------------------------------------------
    display.setCursor(0, 12);
    if (sensors.isBmeOnline()) {
        display.printf("T:%.1fC H:%.0f%% %dhPa", sensors.getTemperature(), sensors.getHumidity(), (int)sensors.getPressure());
    } else {
        display.print(F("ENV: BME OFFLINE"));
    }

    // -------------------------------------------------------------
    // Line 2 (y = 23): VL53L0X Distance & Posture
    // -------------------------------------------------------------
    display.setCursor(0, 23);
    if (sensors.isTofOnline()) {
        float d_cm = sensors.getDistanceCm();
        display.printf("Dist:%4.1fcm ", d_cm);
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

    // 7. Connect Wi-Fi
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.printf("[WIFI] Connecting to '%s'...\n", WIFI_SSID);
}

void loop() {
    unsigned long now = millis();

    // 🔒 Fast/Slow Sensor Polling & Hysteresis Filtering (Handled by locked module)
    sensors.update();

    // Check Serial for voice streaming or audio test commands
    if (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();
        if (cmd.startsWith("[VOICE_START:")) {
            // Format: [VOICE_START:16000:71424]
            int idx1 = cmd.indexOf(':');
            int idx2 = cmd.indexOf(':', idx1 + 1);
            int idx3 = cmd.indexOf(']', idx2 + 1);
            if (idx2 > 0 && idx3 > 0) {
                uint32_t total_bytes = cmd.substring(idx2 + 1, idx3).toInt();
                if (total_bytes > 0 && total_bytes < 2000000) {
                    stream_audio_from_serial(total_bytes);
                }
            }
        } else if (cmd == "PLAY_CHIME" || cmd == "TEST_AUDIO") {
            play_startup_chime();
        }
    }

    // OLED Display Refresh (Every 200ms -> 5 FPS)
    if (now - g_last_oled_ms >= 200) {
        g_last_oled_ms = now;
        update_oled_display();
    }

    // 🔒 Broadcast Telemetry (Every 150ms via Serial and UDP)
    sensors.broadcastTelemetry(&udp, BROADCAST_IP, UDP_TELEMETRY_PORT, g_speaker_online, g_oled_online);
}
