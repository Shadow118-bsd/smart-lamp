#ifndef SENSORS_H
#define SENSORS_H

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* --- SENSOR DATA STRUCTURES --- */

typedef struct {
    float temperature_c;     // Nhiệt độ phòng (°C)
    float humidity_pct;      // Độ ẩm tương đối (%)
    float pressure_hpa;      // Áp suất khí quyển (hPa)
    bool is_valid;           // Cờ trạng thái cảm biến
} bme280_data_t;

typedef struct {
    float distance_cm;       // Khoảng cách đo được (cm)
    bool is_user_near;       // Người dùng ngồi gần bàn (< 60cm)
    bool is_hand_near;       // Đưa tay lại gần (< 15cm)
    bool is_valid;           // Cờ trạng thái cảm biến
} vl53l0x_data_t;

typedef struct {
    bool motion_detected;    // Đang có chuyển động
    bool presence_confirmed; // Xác nhận có người ngồi bàn
    uint32_t session_sec;    // Thời gian ngồi học liên tục (giây)
    bool is_session_overdue; // Cảnh báo học quá lâu (> 45 phút)
} pir_data_t;

typedef struct {
    bme280_data_t bme280;
    vl53l0x_data_t vl53l0x_1;
    vl53l0x_data_t vl53l0x_2;
    pir_data_t pir;
    uint32_t timestamp_ms;
} sensor_telemetry_t;

/* --- API PROTOTYPES --- */

esp_err_t sensors_init(void);
esp_err_t sensors_read_all(sensor_telemetry_t *out_data);
esp_err_t sensors_start_telemetry_task(void);

#ifdef __cplusplus
}
#endif

#endif // SENSORS_H
