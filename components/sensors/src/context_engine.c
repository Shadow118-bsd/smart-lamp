#include <stdio.h>
#include <string.h>
#include "context_engine.h"

void context_engine_init(void)
{
    // Context engine initialization
}

void context_engine_update(const sensor_telemetry_t *telemetry, context_state_t *out_context)
{
    if (!telemetry || !out_context) return;
    memset(out_context, 0, sizeof(context_state_t));

    // 1. Evaluate Environmental Comfort (BME280)
    float temp = telemetry->bme280.temperature_c;
    float hum = telemetry->bme280.humidity_pct;

    if (temp > 29.0f) {
        out_context->comfort = ENV_COMFORT_HOT;
    } else if (temp < 20.0f) {
        out_context->comfort = ENV_COMFORT_COLD;
    } else if (hum > 75.0f) {
        out_context->comfort = ENV_COMFORT_HUMID;
    } else if (hum < 35.0f) {
        out_context->comfort = ENV_COMFORT_DRY;
    } else {
        out_context->comfort = ENV_COMFORT_IDEAL;
    }

    // 2. Evaluate User Presence (PIR + VL53L0X Fusion)
    if (telemetry->vl53l0x_1.is_hand_near) {
        out_context->user_state = USER_PRESENCE_HAND_GESTURE;
    } else if (telemetry->pir.presence_confirmed && telemetry->vl53l0x_1.is_user_near) {
        out_context->user_state = USER_PRESENCE_STUDYING;
    } else if (telemetry->pir.motion_detected) {
        out_context->user_state = USER_PRESENCE_ARRIVED;
    } else {
        out_context->user_state = USER_PRESENCE_AWAY;
    }

    // 3. Session Tracking & Break Reminder (HCI Session Tracker)
    out_context->session_duration_sec = telemetry->pir.session_sec;
    out_context->session_alert_active = telemetry->pir.is_session_overdue;

    // 4. Intelligent Lighting Recommendation (Decision Engine)
    out_context->auto_recommend_active = true;
    if (out_context->user_state == USER_PRESENCE_STUDYING) {
        out_context->suggested_brightness = 80;
        out_context->suggested_cct = 4000;
        out_context->suggested_mode = 2; // STUDY
        snprintf(out_context->recommendation_text, sizeof(out_context->recommendation_text),
                 "Phát hiện người dùng đang ngồi học. Đề xuất Chế độ Học bài (80%%, 4000K).");
    } else if (out_context->user_state == USER_PRESENCE_ARRIVED) {
        out_context->suggested_brightness = 50;
        out_context->suggested_cct = 3000;
        out_context->suggested_mode = 1; // RELAX
        snprintf(out_context->recommendation_text, sizeof(out_context->recommendation_text),
                 "Chào mừng bạn đến bàn làm việc. Đang bật ánh sáng đón tiếp (50%%, 3000K).");
    } else {
        out_context->suggested_brightness = 0;
        out_context->suggested_cct = 2700;
        out_context->suggested_mode = 0; // OFF
        snprintf(out_context->recommendation_text, sizeof(out_context->recommendation_text),
                 "Không có người ở bàn. Đèn đang ở chế độ Standby tiết kiệm năng lượng.");
    }
}
