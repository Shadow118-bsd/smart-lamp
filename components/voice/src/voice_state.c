#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "voice.h"
#include "voice_config.h"
#include "voice_event.h"
#include "voice_feedback.h"

static const char *TAG = "VOICE_STATE";

static VoiceState s_current_state = VOICE_STATE_IDLE;
static SemaphoreHandle_t s_state_mutex = NULL;

extern esp_err_t voice_event_post(const VoiceEvent *event, TickType_t wait_ticks);
extern void voice_session_reset_timer(void);

esp_err_t voice_state_init(void)
{
    if (s_state_mutex != NULL) {
        return ESP_OK;
    }

    s_state_mutex = xSemaphoreCreateMutex();
    if (s_state_mutex == NULL) {
        ESP_LOGE(TAG, "Failed to create s_state_mutex");
        return ESP_ERR_NO_MEM;
    }

    s_current_state = VOICE_STATE_IDLE;
    ESP_LOGI(TAG, "Voice State Machine initialized in IDLE state");
    return ESP_OK;
}

esp_err_t voice_get_state(VoiceState *state)
{
    if (state == NULL || s_state_mutex == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    if (xSemaphoreTake(s_state_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        *state = s_current_state;
        xSemaphoreGive(s_state_mutex);
        return ESP_OK;
    }

    return ESP_ERR_TIMEOUT;
}

void voice_state_set(VoiceState new_state)
{
    if (s_state_mutex == NULL) return;

    if (xSemaphoreTake(s_state_mutex, pdMS_TO_TICKS(100)) == pdTRUE) {
        if (s_current_state != new_state) {
            ESP_LOGI(TAG, "[STATE TRANSITION] %d ---> %d", s_current_state, new_state);
            s_current_state = new_state;
        }
        xSemaphoreGive(s_state_mutex);
    }
}

void voice_state_notify_wake_detected(void)
{
    ESP_LOGI(TAG, "Wake word 'Hey Shine' detected!");
    voice_state_set(VOICE_STATE_LISTENING);
    voice_session_reset_timer();

    VoiceEvent event = {
        .type = VOICE_EVENT_WAKE_DETECTED,
        .confidence = 1.0f
    };
    voice_event_post(&event, pdMS_TO_TICKS(50));
    voice_feedback_play(VOICE_FEEDBACK_WAKE);
}

void voice_state_handle_parsed_command(const VoiceCommand *cmd, float confidence)
{
    voice_state_set(VOICE_STATE_PROCESSING);

    VoiceEvent event;
    memset(&event, 0, sizeof(VoiceEvent));
    event.command = *cmd;
    event.confidence = confidence;

    if (confidence >= VOICE_CONFIDENCE_HIGH && cmd->type != CMD_NONE) {
        // High confidence execution path
        voice_state_set(VOICE_STATE_EXECUTING);
        event.type = VOICE_EVENT_COMMAND;
        voice_event_post(&event, pdMS_TO_TICKS(50));

        voice_state_set(VOICE_STATE_FEEDBACK);
        voice_feedback_play(VOICE_FEEDBACK_SUCCESS);

        // Multi-turn session: return to LISTENING state and reset session timer
        voice_state_set(VOICE_STATE_LISTENING);
        voice_session_reset_timer();
    }
    else if (confidence >= VOICE_CONFIDENCE_LOW && cmd->type != CMD_NONE) {
        // Medium confidence clarification path
        voice_state_set(VOICE_STATE_CLARIFICATION);
        event.type = VOICE_EVENT_LOW_CONFIDENCE;
        voice_event_post(&event, pdMS_TO_TICKS(50));

        voice_feedback_play(VOICE_FEEDBACK_LOW_CONFIDENCE);

        voice_state_set(VOICE_STATE_LISTENING);
        voice_session_reset_timer();
    }
    else {
        // Reject / Unknown command path
        voice_state_set(VOICE_STATE_FEEDBACK);
        event.type = VOICE_EVENT_LOW_CONFIDENCE;
        voice_event_post(&event, pdMS_TO_TICKS(50));

        voice_feedback_play(VOICE_FEEDBACK_NOT_UNDERSTOOD);

        voice_state_set(VOICE_STATE_LISTENING);
        voice_session_reset_timer();
    }
}
