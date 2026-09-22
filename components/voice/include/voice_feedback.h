#ifndef VOICE_FEEDBACK_H
#define VOICE_FEEDBACK_H

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VOICE_FEEDBACK_WAKE,
    VOICE_FEEDBACK_SUCCESS,
    VOICE_FEEDBACK_NOT_UNDERSTOOD,
    VOICE_FEEDBACK_LOW_CONFIDENCE,
    VOICE_FEEDBACK_CANCELLED,
    VOICE_FEEDBACK_TIMEOUT,
    VOICE_FEEDBACK_ERROR
} VoiceFeedbackType;

/**
 * @brief Initialize Audio Output Abstraction for MAX98357A amplifier.
 */
esp_err_t voice_feedback_init(void);

/**
 * @brief Play voice/tone feedback based on feedback type.
 * @param feedback Feedback event type
 */
esp_err_t voice_feedback_play(VoiceFeedbackType feedback);

/**
 * @brief Stop current playing audio feedback.
 */
esp_err_t voice_feedback_stop(void);

#ifdef __cplusplus
}
#endif

#endif // VOICE_FEEDBACK_H
