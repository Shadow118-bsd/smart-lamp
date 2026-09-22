#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "voice_config.h"
#include "voice_command.h"
#include "voice_event.h"

static const char *TAG = "VOICE_SR";

// Declarations of external helper functions
extern RingbufHandle_t voice_input_get_ringbuf(void);
extern void voice_parser_process_result(const char *speech_text, float confidence);
extern void voice_state_notify_wake_detected(void);
extern void voice_state_notify_processing_complete(void);

static TaskHandle_t s_sr_task_handle = NULL;
static volatile bool s_sr_running = false;

/*
 * Simulated/Abstraction ESP-SR WakeNet & MultiNet pipeline task.
 * Note: When building with ESP-SR component enabled, this task integrates
 * esp_afe_sr_iface_t and esp_mn_iface_t.
 */
static void voice_processing_task(void *pvParameters)
{
    ESP_LOGI(TAG, "voice_processing_task started on core %d", xPortGetCoreID());

    RingbufHandle_t ringbuf = voice_input_get_ringbuf();
    if (ringbuf == NULL) {
        ESP_LOGE(TAG, "Audio RingBuffer is NULL in voice_processing_task");
        vTaskDelete(NULL);
        return;
    }

    size_t item_size = 0;

    while (s_sr_running) {
        // Retrieve PCM audio data from RingBuffer
        int16_t *audio_chunk = (int16_t *)xRingbufferReceive(ringbuf, &item_size, pdMS_TO_TICKS(500));

        if (audio_chunk != NULL && item_size > 0) {
            /*
             * Audio Preprocessing & AFE (AEC/BSS/NS/AGC) Feed Step:
             * In full ESP-SR build: afe_handle->feed(afe_data, audio_chunk);
             *
             * ESP-SR WakeNet & MultiNet state engine:
             * When WakeNet triggers "Hey Shine":
             *   -> voice_state_notify_wake_detected();
             * When MultiNet recognizes command:
             *   -> voice_parser_process_result(recognized_str, confidence);
             */

            // Return item to ringbuffer
            vRingbufferReturnItem(ringbuf, (void *)audio_chunk);
        }
    }

    ESP_LOGI(TAG, "voice_processing_task exiting...");
    s_sr_task_handle = NULL;
    vTaskDelete(NULL);
}

esp_err_t voice_sr_init(void)
{
    ESP_LOGI(TAG, "Initializing ESP-SR Framework (WakeNet: 'Hey Shine', MultiNet Speech Recognition)...");
    return ESP_OK;
}

esp_err_t voice_sr_start(void)
{
    if (s_sr_running) {
        return ESP_OK;
    }

    s_sr_running = true;
    BaseType_t res = xTaskCreatePinnedToCore(voice_processing_task,
                                             "voice_sr_task",
                                             VOICE_PROCESSING_TASK_STACK_SIZE,
                                             NULL,
                                             VOICE_PROCESSING_TASK_PRIO,
                                             &s_sr_task_handle,
                                             1);
    if (res != pdPASS) {
        s_sr_running = false;
        ESP_LOGE(TAG, "Failed to create voice_sr_task");
        return ESP_FAIL;
    }

    return ESP_OK;
}

esp_err_t voice_sr_stop(void)
{
    if (!s_sr_running) {
        return ESP_OK;
    }

    s_sr_running = false;
    int timeout = 50;
    while (s_sr_task_handle != NULL && --timeout > 0) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    return ESP_OK;
}

/**
 * @brief Helper API to simulate speech recognition input for pipeline testing
 */
void voice_sr_simulate_recognition(const char *phrase, float confidence)
{
    ESP_LOGI(TAG, "Simulating speech recognition input: '%s' (Confidence: %.2f)", phrase, confidence);
    voice_parser_process_result(phrase, confidence);
}
