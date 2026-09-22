#ifndef VOICE_BATCH_H
#define VOICE_BATCH_H

#include "voice_command.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MAX_ACTIONS_PER_BATCH 8

typedef struct {
    VoiceCommand actions[MAX_ACTIONS_PER_BATCH];
    uint8_t action_count;
    float total_confidence;
    char raw_text[128];
    char ai_response[256];
} VoiceCommandBatch;

#ifdef __cplusplus
}
#endif

#endif // VOICE_BATCH_H
