#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "voice.h"

static const char *TAG = "MAIN";

void app_main(void)
{
    ESP_LOGI(TAG, "Initializing Smart Lamp Voice Subsystem (INMP441 HARDWARE MIC MODE)...");

    esp_err_t ret = voice_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize Voice Subsystem: %s", esp_err_to_name(ret));
        return;
    }

    ret = voice_start();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start Voice Subsystem: %s", esp_err_to_name(ret));
        return;
    }

    ESP_LOGI(TAG, "=========================================================");
    ESP_LOGI(TAG, "  CHẾ ĐỘ THU ÂM TRỰC TIẾP TỪ MICRO phần cứng INMP441     ");
    ESP_LOGI(TAG, "=========================================================");

    VoiceEvent event;

    while (1) {
        // Poll or wait for real VoiceEvent dispatched from INMP441 audio pipeline to Interaction Layer
        if (voice_get_event(&event, pdMS_TO_TICKS(1000)) == ESP_OK) {
            ESP_LOGI(TAG, "[INMP441 MIC EVENT RECEIVED] Type: %d, Command: %d, Confidence: %.2f",
                     event.type, event.command.type, event.confidence);
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
