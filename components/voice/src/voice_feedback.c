#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "voice_config.h"
#include "voice_feedback.h"

static const char *TAG = "VOICE_FEEDBACK";

extern esp_err_t voice_output_init(void);
extern esp_err_t voice_output_play_pcm(const int16_t *pcm_data, size_t size_bytes);

static QueueHandle_t s_feedback_queue = NULL;
static TaskHandle_t s_feedback_task_handle = NULL;
static volatile bool s_feedback_running = false;

static const char* get_feedback_name(VoiceFeedbackType feedback)
{
    switch (feedback) {
        case VOICE_FEEDBACK_WAKE: return "WAKE ('Mình đang nghe')";
        case VOICE_FEEDBACK_SUCCESS: return "SUCCESS ('Đã thực hiện')";
        case VOICE_FEEDBACK_NOT_UNDERSTOOD: return "NOT_UNDERSTOOD ('Mình chưa nghe rõ')";
        case VOICE_FEEDBACK_LOW_CONFIDENCE: return "LOW_CONFIDENCE ('Bạn nói lại nhé')";
        case VOICE_FEEDBACK_CANCELLED: return "CANCELLED ('Đã hủy')";
        case VOICE_FEEDBACK_TIMEOUT: return "TIMEOUT ('Hết thời gian tương tác')";
        case VOICE_FEEDBACK_ERROR: return "ERROR ('Có lỗi xảy ra')";
        default: return "UNKNOWN";
    }
}

static void voice_feedback_task(void *pvParameters)
{
    ESP_LOGI(TAG, "voice_feedback_task started on core %d", xPortGetCoreID());

    VoiceFeedbackType feedback_item;
    int16_t dummy_pcm[128] = {0}; // Audio prompt buffer

    while (s_feedback_running) {
        if (xQueueReceive(s_feedback_queue, &feedback_item, pdMS_TO_TICKS(500)) == pdTRUE) {
            ESP_LOGI(TAG, "[PLAYING AUDIO FEEDBACK] %s", get_feedback_name(feedback_item));
            
            // Output audio frame to MAX98357A I2S driver
            voice_output_play_pcm(dummy_pcm, sizeof(dummy_pcm));
        }
    }

    ESP_LOGI(TAG, "voice_feedback_task exiting...");
    s_feedback_task_handle = NULL;
    vTaskDelete(NULL);
}

esp_err_t voice_feedback_init(void)
{
    voice_output_init();

    if (s_feedback_queue != NULL) {
        return ESP_OK;
    }

    s_feedback_queue = xQueueCreate(5, sizeof(VoiceFeedbackType));
    if (s_feedback_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create Feedback Queue");
        return ESP_ERR_NO_MEM;
    }

    s_feedback_running = true;
    BaseType_t res = xTaskCreatePinnedToCore(voice_feedback_task,
                                             "voice_fb_task",
                                             VOICE_FEEDBACK_TASK_STACK_SIZE,
                                             NULL,
                                             VOICE_FEEDBACK_TASK_PRIO,
                                             &s_feedback_task_handle,
                                             1);
    if (res != pdPASS) {
        s_feedback_running = false;
        ESP_LOGE(TAG, "Failed to create voice_feedback_task");
        return ESP_FAIL;
    }

    return ESP_OK;
}

esp_err_t voice_feedback_play(VoiceFeedbackType feedback)
{
    if (s_feedback_queue == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    xQueueSend(s_feedback_queue, &feedback, pdMS_TO_TICKS(10));
    return ESP_OK;
}

esp_err_t voice_feedback_stop(void)
{
    if (s_feedback_queue != NULL) {
        xQueueReset(s_feedback_queue);
    }
    return ESP_OK;
}

esp_err_t voice_send_feedback(VoiceFeedbackType feedback)
{
    return voice_feedback_play(feedback);
}
