#ifndef VOICE_COMMAND_H
#define VOICE_COMMAND_H

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CMD_NONE = 0,

    CMD_POWER_ON,
    CMD_POWER_OFF,

    CMD_SET_BRIGHTNESS,
    CMD_BRIGHTNESS_UP,
    CMD_BRIGHTNESS_DOWN,

    CMD_SET_CCT,
    CMD_CCT_WARMER,
    CMD_CCT_COOLER,

    CMD_SET_MODE,

    CMD_GET_BRIGHTNESS,
    CMD_GET_BATTERY,

    CMD_CANCEL
} CommandType;

typedef enum {
    MODE_NONE = 0,
    MODE_NORMAL,
    MODE_STUDY,
    MODE_READING,
    MODE_NIGHT
} LightMode;

typedef enum {
    PARAM_TYPE_ABSOLUTE = 0, // Target exact value (e.g. set brightness to 20%)
    PARAM_TYPE_RELATIVE = 1  // Delta adjustment (e.g. increase brightness by +20%)
} ParamType;

typedef struct {
    CommandType type;
    int value;          // Numeric parameter or delta (e.g. +10, -20)
    ParamType param_type;// ABSOLUTE vs RELATIVE
    bool is_negated;    // True if negated by words like "đừng", "không"
    LightMode mode;     // Light mode target
    float confidence;   // Confidence score (0.0 - 1.0)
    char raw_text[64];  // Raw parsed transcript
} VoiceCommand;

#ifdef __cplusplus
}
#endif

#endif // VOICE_COMMAND_H
