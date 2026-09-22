#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "voice.h"
#include "voice_command.h"
#include "voice_event.h"

static const char *TAG = "TEST_VOICE";

extern esp_err_t voice_parser_parse(const char *speech_text, float confidence, VoiceCommand *out_cmd);
extern void voice_sr_simulate_recognition(const char *phrase, float confidence);

void test_level_4_parser(void)
{
    ESP_LOGI(TAG, "--- RUNNING LEVEL 4 PARSER TESTS ---");

    VoiceCommand cmd;

    // Test 1: Power On
    voice_parser_parse("bật đèn", 0.95f, &cmd);
    if (cmd.type == CMD_POWER_ON) {
        ESP_LOGI(TAG, "[PASS] 'bật đèn' -> CMD_POWER_ON");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'bật đèn' parsed incorrectly");
    }

    // Test 2: Power Off
    voice_parser_parse("tắt đèn", 0.92f, &cmd);
    if (cmd.type == CMD_POWER_OFF) {
        ESP_LOGI(TAG, "[PASS] 'tắt đèn' -> CMD_POWER_OFF");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'tắt đèn' parsed incorrectly");
    }

    // Test 3: Set Brightness +10%
    voice_parser_parse("tăng sáng 10%", 0.91f, &cmd);
    if (cmd.type == CMD_SET_BRIGHTNESS && cmd.value == 10) {
        ESP_LOGI(TAG, "[PASS] 'tăng sáng 10%%' -> CMD_SET_BRIGHTNESS (10)");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'tăng sáng 10%%' parsed incorrectly");
    }

    // Test 4: Set Brightness -20%
    voice_parser_parse("giảm sáng 20 phần trăm", 0.88f, &cmd);
    if (cmd.type == CMD_SET_BRIGHTNESS && cmd.value == -20) {
        ESP_LOGI(TAG, "[PASS] 'giảm sáng 20 phần trăm' -> CMD_SET_BRIGHTNESS (-20)");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'giảm sáng 20 phần trăm' parsed incorrectly");
    }

    // Test 5: Light Mode Study
    voice_parser_parse("bật chế độ học", 0.90f, &cmd);
    if (cmd.type == CMD_SET_MODE && cmd.mode == MODE_STUDY) {
        ESP_LOGI(TAG, "[PASS] 'bật chế độ học' -> CMD_SET_MODE (MODE_STUDY)");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'bật chế độ học' parsed incorrectly");
    }

    // Test 6: Light Mode Reading
    voice_parser_parse("bật chế độ đọc sách", 0.89f, &cmd);
    if (cmd.type == CMD_SET_MODE && cmd.mode == MODE_READING) {
        ESP_LOGI(TAG, "[PASS] 'bật chế độ đọc sách' -> CMD_SET_MODE (MODE_READING)");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'bật chế độ đọc sách' parsed incorrectly");
    }

    // Test 7: CCT Warmer
    voice_parser_parse("ấm hơn", 0.85f, &cmd);
    if (cmd.type == CMD_CCT_WARMER) {
        ESP_LOGI(TAG, "[PASS] 'ấm hơn' -> CMD_CCT_WARMER");
    } else {
        ESP_LOGE(TAG, "[FAIL] 'ấm hơn' parsed incorrectly");
    }
}

void test_voice_pipeline_simulation(void)
{
    ESP_LOGI(TAG, "--- RUNNING VOICE PIPELINE INTEGRATION TEST ---");

    voice_init();
    voice_start();

    // Simulate Wake Word + Command
    voice_sr_simulate_recognition("bật đèn", 0.95f);

    VoiceEvent event;
    if (voice_get_event(&event, pdMS_TO_TICKS(1000)) == ESP_OK) {
        if (event.type == VOICE_EVENT_COMMAND && event.command.type == CMD_POWER_ON) {
            ESP_LOGI(TAG, "[PASS] Pipeline produced VOICE_EVENT_COMMAND with CMD_POWER_ON!");
        } else {
            ESP_LOGE(TAG, "[FAIL] Unexpected event output from pipeline");
        }
    } else {
        ESP_LOGE(TAG, "[FAIL] Timeout waiting for VoiceEvent");
    }
}
