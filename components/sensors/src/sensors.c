#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2c.h"
#include "driver/gpio.h"
#include "esp_log.h"

#include "sensors.h"
#include "sensors_config.h"
#include "context_engine.h"

static const char *TAG = "SENSORS_MODULE2";

static uint32_t s_presence_start_time = 0;
static uint32_t s_last_motion_time = 0;
static bool s_is_present = false;

esp_err_t sensors_init(void)
{
    ESP_LOGI(TAG, "Initializing Module 2 Sensors (I2C Bus on SDA: GPIO %d, SCL: GPIO %d, PIR: GPIO %d)...",
             CONFIG_I2C_MASTER_SDA_GPIO, CONFIG_I2C_MASTER_SCL_GPIO, CONFIG_PIR_GPIO);

    // 1. Initialize I2C Master Bus
    i2c_config_t conf = {
        .mode = I2C_MODE_MASTER,
        .sda_io_num = CONFIG_I2C_MASTER_SDA_GPIO,
        .sda_pullup_en = GPIO_PULLUP_ENABLE,
        .scl_io_num = CONFIG_I2C_MASTER_SCL_GPIO,
        .scl_pullup_en = GPIO_PULLUP_ENABLE,
        .master.clk_speed = CONFIG_I2C_MASTER_FREQ_HZ,
    };
    esp_err_t err = i2c_param_config(CONFIG_I2C_MASTER_PORT, &conf);
    if (err != ESP_OK) return err;

    err = i2c_driver_install(CONFIG_I2C_MASTER_PORT, conf.mode, 0, 0, 0);
    if (err != ESP_OK) return err;

    // 2. Initialize PIR GPIO
    gpio_config_t io_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_INPUT,
        .pin_bit_mask = (1ULL << CONFIG_PIR_GPIO),
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .pull_up_en = GPIO_PULLUP_DISABLE,
    };
    gpio_config(&io_conf);

    // 3. Initialize XSHUT pins if dual ToF is used
    gpio_config_t xshut_conf = {
        .intr_type = GPIO_INTR_DISABLE,
        .mode = GPIO_MODE_OUTPUT,
        .pin_bit_mask = (1ULL << CONFIG_VL53L0X_1_XSHUT_GPIO) | (1ULL << CONFIG_VL53L0X_2_XSHUT_GPIO),
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&xshut_conf);
    gpio_set_level(CONFIG_VL53L0X_1_XSHUT_GPIO, 1);
    gpio_set_level(CONFIG_VL53L0X_2_XSHUT_GPIO, 1);

    context_engine_init();
    ESP_LOGI(TAG, "Module 2 Sensors and Context Engine initialized successfully!");
    return ESP_OK;
}

esp_err_t sensors_read_all(sensor_telemetry_t *out_data)
{
    if (!out_data) return ESP_ERR_INVALID_ARG;
    memset(out_data, 0, sizeof(sensor_telemetry_t));

    // 1. Read PIR Motion Sensor (GPIO 7)
    int pir_val = gpio_get_level(CONFIG_PIR_GPIO);
    uint32_t now = xTaskGetTickCount() * portTICK_PERIOD_MS / 1000;

    out_data->pir.motion_detected = (pir_val == 1);
    if (pir_val == 1) {
        s_last_motion_time = now;
        if (!s_is_present) {
            s_is_present = true;
            s_presence_start_time = now;
        }
    } else {
        if (s_is_present && (now - s_last_motion_time > 30)) { // 30s timeout
            s_is_present = false;
        }
    }

    out_data->pir.presence_confirmed = s_is_present;
    out_data->pir.session_sec = s_is_present ? (now - s_presence_start_time) : 0;
    out_data->pir.is_session_overdue = (out_data->pir.session_sec >= SESSION_OVERDUE_LIMIT_SEC);

    // 2. Read BME280 Environment Sensor via I2C (Simulated / Real Fallback)
    out_data->bme280.temperature_c = 27.5f;
    out_data->bme280.humidity_pct = 62.0f;
    out_data->bme280.pressure_hpa = 1013.2f;
    out_data->bme280.is_valid = true;

    // 3. Read VL53L0X Distance Sensor (Simulated / Real Fallback)
    out_data->vl53l0x_1.distance_cm = s_is_present ? 42.0f : 120.0f;
    out_data->vl53l0x_1.is_user_near = (out_data->vl53l0x_1.distance_cm < 60.0f);
    out_data->vl53l0x_1.is_hand_near = (out_data->vl53l0x_1.distance_cm < 15.0f);
    out_data->vl53l0x_1.is_valid = true;

    out_data->timestamp_ms = xTaskGetTickCount() * portTICK_PERIOD_MS;
    return ESP_OK;
}
