#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <stdlib.h>
#include "esp_err.h"
#include "esp_log.h"
#include "voice_config.h"
#include "voice_command.h"
#include "voice_event.h"

static const char *TAG = "VOICE_PARSER";

// External state notification function
extern void voice_state_handle_parsed_command(const VoiceCommand *cmd, float confidence);

// Lowercase string conversion helper
static void to_lower_str(char *dst, const char *src, size_t max_len)
{
    size_t i = 0;
    while (src[i] && i < max_len - 1) {
        dst[i] = (char)tolower((unsigned char)src[i]);
        i++;
    }
    dst[i] = '\0';
}

// Number extraction helper (e.g., extracts 10 from "10%" or "10 phần trăm")
static int extract_number(const char *str)
{
    const char *p = str;
    while (*p) {
        if (isdigit((unsigned char)*p)) {
            return atoi(p);
        }
        p++;
    }
    return 0;
}

esp_err_t voice_parser_parse(const char *speech_text, float confidence, VoiceCommand *out_cmd)
{
    if (speech_text == NULL || out_cmd == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(out_cmd, 0, sizeof(VoiceCommand));
    out_cmd->type = CMD_NONE;
    out_cmd->confidence = confidence;
    snprintf(out_cmd->raw_text, sizeof(out_cmd->raw_text), "%s", speech_text);

    char lower_text[128];
    to_lower_str(lower_text, speech_text, sizeof(lower_text));

    ESP_LOGI(TAG, "Parsing speech text: '%s' (Lower: '%s', Confidence: %.2f)",
             speech_text, lower_text, confidence);

    // 1. Power On / Off
    if (strstr(lower_text, "bật đèn") != NULL || strstr(lower_text, "mở đèn") != NULL) {
        if (strstr(lower_text, "chế độ") == NULL) {
            out_cmd->type = CMD_POWER_ON;
            return ESP_OK;
        }
    } else if (strstr(lower_text, "tắt đèn") != NULL || strstr(lower_text, "tắt") != NULL) {
        out_cmd->type = CMD_POWER_OFF;
        return ESP_OK;
    }

    // 2. Light Modes
    if (strstr(lower_text, "chế độ học") != NULL || strstr(lower_text, "học tập") != NULL) {
        out_cmd->type = CMD_SET_MODE;
        out_cmd->mode = MODE_STUDY;
        return ESP_OK;
    } else if (strstr(lower_text, "chế độ đọc") != NULL || strstr(lower_text, "đọc sách") != NULL) {
        out_cmd->type = CMD_SET_MODE;
        out_cmd->mode = MODE_READING;
        return ESP_OK;
    } else if (strstr(lower_text, "chế độ ban đêm") != NULL || strstr(lower_text, "ngủ") != NULL) {
        out_cmd->type = CMD_SET_MODE;
        out_cmd->mode = MODE_NIGHT;
        return ESP_OK;
    } else if (strstr(lower_text, "bình thường") != NULL) {
        out_cmd->type = CMD_SET_MODE;
        out_cmd->mode = MODE_NORMAL;
        return ESP_OK;
    }

    // 3. Brightness adjustments with parameters
    if (strstr(lower_text, "tăng sáng") != NULL || strstr(lower_text, "sáng hơn") != NULL) {
        int num = extract_number(lower_text);
        if (num > 0) {
            out_cmd->type = CMD_SET_BRIGHTNESS;
            out_cmd->value = num; // +10%
        } else {
            out_cmd->type = CMD_BRIGHTNESS_UP;
            out_cmd->value = 10; // Default +10%
        }
        return ESP_OK;
    } else if (strstr(lower_text, "giảm sáng") != NULL || strstr(lower_text, "tối hơn") != NULL) {
        int num = extract_number(lower_text);
        if (num > 0) {
            out_cmd->type = CMD_SET_BRIGHTNESS;
            out_cmd->value = -num; // -20%
        } else {
            out_cmd->type = CMD_BRIGHTNESS_DOWN;
            out_cmd->value = -10; // Default -10%
        }
        return ESP_OK;
    }

    // 4. Color Temperature (CCT)
    if (strstr(lower_text, "ấm hơn") != NULL || strstr(lower_text, "vàng hơn") != NULL) {
        out_cmd->type = CMD_CCT_WARMER;
        out_cmd->value = 10;
        return ESP_OK;
    } else if (strstr(lower_text, "lạnh hơn") != NULL || strstr(lower_text, "trắng hơn") != NULL) {
        out_cmd->type = CMD_CCT_COOLER;
        out_cmd->value = 10;
        return ESP_OK;
    }

    // 5. Query / Status commands
    if (strstr(lower_text, "pin") != NULL || strstr(lower_text, "mức pin") != NULL) {
        out_cmd->type = CMD_GET_BATTERY;
        return ESP_OK;
    }

    // 6. Cancel
    if (strstr(lower_text, "hủy") != NULL || strstr(lower_text, "bỏ qua") != NULL) {
        out_cmd->type = CMD_CANCEL;
        return ESP_OK;
    }

    ESP_LOGW(TAG, "Unrecognized speech text: '%s'", speech_text);
    return ESP_ERR_NOT_FOUND;
}

void voice_parser_process_result(const char *speech_text, float confidence)
{
    VoiceCommand cmd;
    esp_err_t err = voice_parser_parse(speech_text, confidence, &cmd);

    if (err == ESP_OK) {
        ESP_LOGI(TAG, "[PARSER SUCCESS] Type: %d, Value: %d, Mode: %d, Confidence: %.2f",
                 cmd.type, cmd.value, cmd.mode, confidence);
        voice_state_handle_parsed_command(&cmd, confidence);
    } else {
        ESP_LOGW(TAG, "[PARSER UNKNOWN COMMAND] Text: '%s'", speech_text);
        VoiceCommand unknown_cmd = { .type = CMD_NONE, .confidence = confidence };
        voice_state_handle_parsed_command(&unknown_cmd, confidence);
    }
}
