#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/i2s.h"
#include "voice_config.h"

static const char *TAG = "VOICE_OUTPUT";

static bool s_output_initialized = false;

esp_err_t voice_output_init(void)
{
    if (s_output_initialized) {
        return ESP_OK;
    }

    ESP_LOGI(TAG, "Initializing Audio Output (MAX98357A I2S Amplifier)...");

    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate = CONFIG_VOICE_I2S_AMP_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 6,
        .dma_buf_len = 256,
        .use_apll = false,
        .tx_desc_auto_clear = true
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = CONFIG_VOICE_I2S_AMP_BCLK_GPIO,
        .ws_io_num = CONFIG_VOICE_I2S_AMP_LRC_GPIO,
        .data_out_num = CONFIG_VOICE_I2S_AMP_DOUT_GPIO,
        .data_in_num = I2S_PIN_NO_CHANGE
    };

    esp_err_t ret = i2s_driver_install((i2s_port_t)CONFIG_VOICE_I2S_AMP_PORT, &i2s_config, 0, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_driver_install for AMP failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_set_pin((i2s_port_t)CONFIG_VOICE_I2S_AMP_PORT, &pin_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_set_pin for AMP failed: %s", esp_err_to_name(ret));
        return ret;
    }

    s_output_initialized = true;
    ESP_LOGI(TAG, "Audio Output MAX98357A initialized successfully");
    return ESP_OK;
}

esp_err_t voice_output_play_pcm(const int16_t *pcm_data, size_t size_bytes)
{
    if (!s_output_initialized) {
        return ESP_ERR_INVALID_STATE;
    }

    size_t bytes_written = 0;
    esp_err_t ret = i2s_write((i2s_port_t)CONFIG_VOICE_I2S_AMP_PORT,
                              pcm_data,
                              size_bytes,
                              &bytes_written,
                              pdMS_TO_TICKS(1000));
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_write failed: %s", esp_err_to_name(ret));
        return ret;
    }

    return ESP_OK;
}
