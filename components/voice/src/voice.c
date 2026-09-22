#include <stdio.h>
#include "esp_log.h"
#include "voice.h"
#include "voice_udp.h"

static const char *TAG = "VOICE_CORE";

// External initializers for voice subcomponents
extern esp_err_t voice_input_init(void);
extern esp_err_t voice_input_start(void);
extern esp_err_t voice_input_stop(void);

extern esp_err_t voice_sr_init(void);
extern esp_err_t voice_sr_start(void);
extern esp_err_t voice_sr_stop(void);

extern esp_err_t voice_event_init(void);
extern esp_err_t voice_event_receive(VoiceEvent *event, TickType_t wait_ticks);

extern esp_err_t voice_state_init(void);
extern esp_err_t voice_session_init(void);
extern esp_err_t voice_feedback_init(void);

static bool s_voice_initialized = false;

esp_err_t voice_init(void)
{
    if (s_voice_initialized) {
        ESP_LOGW(TAG, "Voice Subsystem already initialized");
        return ESP_OK;
    }

    ESP_LOGI(TAG, "================================================");
    ESP_LOGI(TAG, "   Initializing Module 1 — Voice Interaction    ");
    ESP_LOGI(TAG, "================================================");

    esp_err_t ret = voice_event_init();
    if (ret != ESP_OK) return ret;

    ret = voice_state_init();
    if (ret != ESP_OK) return ret;

    ret = voice_session_init();
    if (ret != ESP_OK) return ret;

    ret = voice_feedback_init();
    if (ret != ESP_OK) return ret;

    ret = voice_udp_init();
    if (ret != ESP_OK) return ret;

    ret = voice_input_init();
    if (ret != ESP_OK) return ret;

    ret = voice_sr_init();
    if (ret != ESP_OK) return ret;

    s_voice_initialized = true;
    ESP_LOGI(TAG, "Voice Subsystem initialized successfully.");
    return ESP_OK;
}

esp_err_t voice_start(void)
{
    if (!s_voice_initialized) {
        ESP_LOGE(TAG, "Cannot start: Voice Subsystem is not initialized");
        return ESP_ERR_INVALID_STATE;
    }

    ESP_LOGI(TAG, "Starting Voice Subsystem tasks...");

    esp_err_t ret = voice_input_start();
    if (ret != ESP_OK) return ret;

    ret = voice_sr_start();
    if (ret != ESP_OK) return ret;

    ESP_LOGI(TAG, "Voice Subsystem running.");
    return ESP_OK;
}

esp_err_t voice_stop(void)
{
    if (!s_voice_initialized) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Stopping Voice Subsystem tasks...");
    voice_sr_stop();
    voice_input_stop();

    return ESP_OK;
}

esp_err_t voice_get_event(VoiceEvent *event, TickType_t timeout_ticks)
{
    return voice_event_receive(event, timeout_ticks);
}
