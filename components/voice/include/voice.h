#ifndef VOICE_H
#define VOICE_H

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "voice_config.h"
#include "voice_command.h"
#include "voice_event.h"
#include "voice_feedback.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VOICE_STATE_IDLE,
    VOICE_STATE_LISTENING,
    VOICE_STATE_PROCESSING,
    VOICE_STATE_EXECUTING,
    VOICE_STATE_FEEDBACK,
    VOICE_STATE_CLARIFICATION
} VoiceState;

/**
 * @brief Initialize the Voice Interaction Subsystem (Tasks, Queues, ESP-SR, I2S).
 * @return ESP_OK on success, appropriate esp_err_t error code otherwise.
 */
esp_err_t voice_init(void);

/**
 * @brief Start voice processing tasks and mic audio stream capture.
 * @return ESP_OK on success.
 */
esp_err_t voice_start(void);

/**
 * @brief Stop voice processing tasks and suspend audio stream capture.
 * @return ESP_OK on success.
 */
esp_err_t voice_stop(void);

/**
 * @brief Thread-safe getter for current Voice Subsystem state.
 * @param[out] state Pointer to store the current state.
 * @return ESP_OK on success.
 */
esp_err_t voice_get_state(VoiceState *state);

/**
 * @brief Trigger audio voice feedback output.
 * @param feedback Feedback type to play.
 * @return ESP_OK on success.
 */
esp_err_t voice_send_feedback(VoiceFeedbackType feedback);

/**
 * @brief Retrieve a VoiceEvent dispatched by Voice Subsystem (non-blocking or blocking with timeout).
 * @param[out] event Pointer to target VoiceEvent struct.
 * @param timeout_ticks Ticks to wait for an event.
 * @return ESP_OK if event retrieved, ESP_ERR_TIMEOUT if timed out.
 */
esp_err_t voice_get_event(VoiceEvent *event, TickType_t timeout_ticks);

#ifdef __cplusplus
}
#endif

#endif // VOICE_H
