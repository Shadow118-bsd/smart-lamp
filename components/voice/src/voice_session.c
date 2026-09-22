#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/timers.h"
#include "esp_log.h"
#include "voice.h"
#include "voice_config.h"
#include "voice_event.h"

static const char *TAG = "VOICE_SESSION";

static TimerHandle_t s_session_timer = NULL;

extern void voice_state_set(VoiceState new_state);
extern esp_err_t voice_event_post(const VoiceEvent *event, TickType_t wait_ticks);

static void session_timer_callback(TimerHandle_t xTimer)
{
    VoiceState current_state;
    voice_get_state(&current_state);

    if (current_state != VOICE_STATE_IDLE) {
        ESP_LOGI(TAG, "Voice interaction session timed out (%d ms). Transitioning to IDLE.", VOICE_SESSION_TIMEOUT_MS);
        ESP_LOGI(TAG, "NOTE: Lamp device state is UNCHANGED.");

        voice_state_set(VOICE_STATE_IDLE);

        VoiceEvent event = {
            .type = VOICE_EVENT_TIMEOUT,
            .confidence = 1.0f
        };
        voice_event_post(&event, pdMS_TO_TICKS(50));
        voice_feedback_play(VOICE_FEEDBACK_TIMEOUT);
    }
}

esp_err_t voice_session_init(void)
{
    if (s_session_timer != NULL) {
        return ESP_OK;
    }

    s_session_timer = xTimerCreate("voice_session_timer",
                                  pdMS_TO_TICKS(VOICE_SESSION_TIMEOUT_MS),
                                  pdFALSE, // One-shot timer
                                  NULL,
                                  session_timer_callback);

    if (s_session_timer == NULL) {
        ESP_LOGE(TAG, "Failed to create session timer");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Multi-turn Voice Session timer initialized (Timeout: %d ms)", VOICE_SESSION_TIMEOUT_MS);
    return ESP_OK;
}

void voice_session_reset_timer(void)
{
    if (s_session_timer != NULL) {
        xTimerReset(s_session_timer, pdMS_TO_TICKS(50));
    }
}

void voice_session_stop_timer(void)
{
    if (s_session_timer != NULL) {
        xTimerStop(s_session_timer, pdMS_TO_TICKS(50));
    }
}
