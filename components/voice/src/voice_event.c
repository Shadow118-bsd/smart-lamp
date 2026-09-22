#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "voice_config.h"
#include "voice_event.h"
#include "voice_udp.h"

static const char *TAG = "VOICE_EVENT";

static QueueHandle_t s_event_queue = NULL;

esp_err_t voice_event_init(void)
{
    if (s_event_queue != NULL) {
        ESP_LOGW(TAG, "Voice Event Queue already initialized");
        return ESP_OK;
    }

    s_event_queue = xQueueCreate(VOICE_QUEUE_LENGTH, sizeof(VoiceEvent));
    if (s_event_queue == NULL) {
        ESP_LOGE(TAG, "Failed to create Voice Event Queue");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Voice Event Queue initialized (Capacity: %d)", VOICE_QUEUE_LENGTH);
    return ESP_OK;
}

esp_err_t voice_event_post(const VoiceEvent *event, TickType_t wait_ticks)
{
    if (s_event_queue == NULL || event == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    BaseType_t res = xQueueSend(s_event_queue, event, wait_ticks);
    if (res != pdTRUE) {
        ESP_LOGW(TAG, "Voice Event Queue full! Dropping event type %d", event->type);
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "Posted VoiceEvent type %d (Confidence: %.2f) to Queue", event->type, event->confidence);

    // Stream VoiceEvent JSON notification over Wi-Fi UDP to destination PC
#if CONFIG_VOICE_ENABLE_UDP_STREAM
    voice_udp_send_event(event);
#endif

    return ESP_OK;
}

esp_err_t voice_event_receive(VoiceEvent *event, TickType_t wait_ticks)
{
    if (s_event_queue == NULL || event == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    BaseType_t res = xQueueReceive(s_event_queue, event, wait_ticks);
    if (res != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    return ESP_OK;
}
