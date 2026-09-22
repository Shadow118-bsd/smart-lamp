#ifndef VOICE_EVENT_H
#define VOICE_EVENT_H

#include "voice_command.h"
#include "voice_batch.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    VOICE_EVENT_WAKE_DETECTED,
    VOICE_EVENT_COMMAND,
    VOICE_EVENT_LOW_CONFIDENCE,
    VOICE_EVENT_TIMEOUT,
    VOICE_EVENT_ERROR
} VoiceEventType;

typedef struct {
    VoiceEventType type;
    VoiceCommand command;       // Single primary command (backwards compatibility)
    VoiceCommandBatch batch;    // Multi-action command batch (up to 8 actions)
    float confidence;
} VoiceEvent;

#ifdef __cplusplus
}
#endif

#endif // VOICE_EVENT_H
