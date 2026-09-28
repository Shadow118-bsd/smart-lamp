#ifndef CONTEXT_ENGINE_H
#define CONTEXT_ENGINE_H

#include <stdbool.h>
#include <stdint.h>
#include "sensors.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    ENV_COMFORT_IDEAL = 0,    // Môi trường lý tưởng (22-27°C, 45-65%)
    ENV_COMFORT_HOT,          // Phòng nóng (> 28°C)
    ENV_COMFORT_COLD,         // Phòng lạnh (< 20°C)
    ENV_COMFORT_HUMID,        // Độ ẩm cao (> 70%)
    ENV_COMFORT_DRY           // Không khí khô (< 40%)
} env_comfort_t;

typedef enum {
    USER_PRESENCE_AWAY = 0,   // Không có người ở bàn
    USER_PRESENCE_ARRIVED,    // Vừa mới ngồi vào bàn
    USER_PRESENCE_STUDYING,   // Đang ngồi học / làm việc
    USER_PRESENCE_HAND_GESTURE // Đang đưa tay lại gần đèn
} user_presence_state_t;

typedef struct {
    env_comfort_t comfort;
    user_presence_state_t user_state;
    uint32_t session_duration_sec;
    bool session_alert_active;
    
    // Auto Recommendation
    bool auto_recommend_active;
    int suggested_brightness;
    int suggested_cct;
    int suggested_mode;
    char recommendation_text[128];
} context_state_t;

void context_engine_init(void);
void context_engine_update(const sensor_telemetry_t *telemetry, context_state_t *out_context);

#ifdef __cplusplus
}
#endif

#endif // CONTEXT_ENGINE_H
