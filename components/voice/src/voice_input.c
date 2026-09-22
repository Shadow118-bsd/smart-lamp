#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "driver/i2s.h"
#include "voice_config.h"
#include "voice_udp.h"

static const char *TAG = "VOICE_INPUT";

static RingbufHandle_t s_audio_ringbuf = NULL;
static TaskHandle_t s_input_task_handle = NULL;
static volatile bool s_is_running = false;

static void audio_input_task(void *pvParameters)
{
    ESP_LOGI(TAG, "audio_input_task started on core %d", xPortGetCoreID());

    size_t bytes_read = 0;
    int16_t sample_buffer[VOICE_AUDIO_BUFFER_SIZE / sizeof(int16_t)];

    while (s_is_running) {
        // Read PCM audio data from I2S INMP441 DMA
        esp_err_t ret = i2s_read((i2s_port_t)CONFIG_VOICE_I2S_MIC_PORT,
                                 sample_buffer,
                                 sizeof(sample_buffer),
                                 &bytes_read,
                                 pdMS_TO_TICKS(100));

        if (ret == ESP_OK && bytes_read > 0) {
            // Push audio frames into ringbuffer for ESP-SR task
            UBaseType_t res = xRingbufferSend(s_audio_ringbuf, sample_buffer, bytes_read, pdMS_TO_TICKS(10));
            if (res != pdTRUE) {
                ESP_LOGW(TAG, "Audio RingBuffer full! Dropping %d bytes of audio", (int)bytes_read);
            }

            // Stream PCM audio chunk over Wi-Fi UDP to destination PC
#if CONFIG_VOICE_ENABLE_UDP_STREAM
            voice_udp_send_audio(sample_buffer, bytes_read);
#endif
        } else if (ret != ESP_OK && ret != ESP_ERR_TIMEOUT) {
            ESP_LOGE(TAG, "I2S read error: %s", esp_err_to_name(ret));
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    ESP_LOGI(TAG, "audio_input_task exiting...");
    s_input_task_handle = NULL;
    vTaskDelete(NULL);
}

esp_err_t voice_input_init(void)
{
    ESP_LOGI(TAG, "Initializing Audio Input (INMP441 I2S DMA)...");

    if (s_audio_ringbuf != NULL) {
        ESP_LOGW(TAG, "Audio input already initialized");
        return ESP_OK;
    }

    // Create 32KB RingBuffer for raw PCM audio data
    s_audio_ringbuf = xRingbufferCreate(32 * 1024, RINGBUF_TYPE_BYTEBUF);
    if (s_audio_ringbuf == NULL) {
        ESP_LOGE(TAG, "Failed to create audio RingBuffer");
        return ESP_ERR_NO_MEM;
    }

    /* Configure I2S driver for INMP441 Microphone */
    i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
        .sample_rate = CONFIG_VOICE_I2S_MIC_SAMPLE_RATE,
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count = 8,
        .dma_buf_len = 512,
        .use_apll = false,
        .tx_desc_auto_clear = false,
        .fixed_mclk = 0
    };

    i2s_pin_config_t pin_config = {
        .bck_io_num = CONFIG_VOICE_I2S_MIC_SCK_GPIO,
        .ws_io_num = CONFIG_VOICE_I2S_MIC_WS_GPIO,
        .data_out_num = I2S_PIN_NO_CHANGE,
        .data_in_num = CONFIG_VOICE_I2S_MIC_SD_GPIO
    };

    esp_err_t ret = i2s_driver_install((i2s_port_t)CONFIG_VOICE_I2S_MIC_PORT, &i2s_config, 0, NULL);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_driver_install failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ret = i2s_set_pin((i2s_port_t)CONFIG_VOICE_I2S_MIC_PORT, &pin_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "i2s_set_pin failed: %s", esp_err_to_name(ret));
        return ret;
    }

    ESP_LOGI(TAG, "Audio Input initialized successfully");
    return ESP_OK;
}

esp_err_t voice_input_start(void)
{
    if (s_is_running) {
        ESP_LOGW(TAG, "Audio input task already running");
        return ESP_OK;
    }

    s_is_running = true;
    BaseType_t res = xTaskCreatePinnedToCore(audio_input_task,
                                             "audio_input_task",
                                             VOICE_INPUT_TASK_STACK_SIZE,
                                             NULL,
                                             VOICE_INPUT_TASK_PRIO,
                                             &s_input_task_handle,
                                             1);
    if (res != pdPASS) {
        s_is_running = false;
        ESP_LOGE(TAG, "Failed to create audio_input_task");
        return ESP_FAIL;
    }

    return ESP_OK;
}

esp_err_t voice_input_stop(void)
{
    if (!s_is_running) {
        return ESP_OK;
    }

    s_is_running = false;
    int timeout = 50;
    while (s_input_task_handle != NULL && --timeout > 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    return ESP_OK;
}

RingbufHandle_t voice_input_get_ringbuf(void)
{
    return s_audio_ringbuf;
}
