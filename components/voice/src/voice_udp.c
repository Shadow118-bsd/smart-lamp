#include <stdio.h>
#include <string.h>
#include <sys/param.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/ringbuf.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/err.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"
#include <lwip/netdb.h>

#include "voice_config.h"
#include "voice_udp.h"

static const char *TAG = "VOICE_UDP";

#if CONFIG_VOICE_ENABLE_UDP_STREAM

static int s_audio_sock = -1;
static int s_event_sock = -1;
static int s_laptop_mic_sock = -1;
static struct sockaddr_in s_audio_dest_addr;
static struct sockaddr_in s_event_dest_addr;
static bool s_wifi_connected = false;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

extern RingbufHandle_t voice_input_get_ringbuf(void);

// Task receiving PCM audio stream from Laptop Mic via UDP Port 12347
static void pc_mic_udp_receiver_task(void *pvParameters)
{
    ESP_LOGI(TAG, "pc_mic_udp_receiver_task listening on UDP Port 12347...");

    struct sockaddr_in bind_addr;
    memset(&bind_addr, 0, sizeof(bind_addr));
    bind_addr.sin_family = AF_INET;
    bind_addr.sin_port = htons(12347);
    bind_addr.sin_addr.s_addr = htonl(INADDR_ANY);

    s_laptop_mic_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_laptop_mic_sock < 0) {
        ESP_LOGE(TAG, "Failed to create Laptop Mic UDP socket");
        vTaskDelete(NULL);
        return;
    }

    if (bind(s_laptop_mic_sock, (struct sockaddr *)&bind_addr, sizeof(bind_addr)) < 0) {
        ESP_LOGE(TAG, "Failed to bind Laptop Mic UDP socket to port 12347");
        close(s_laptop_mic_sock);
        vTaskDelete(NULL);
        return;
    }

    int16_t pcm_buffer[1024];
    struct sockaddr_in source_addr;
    socklen_t socklen = sizeof(source_addr);

    while (1) {
        int len = recvfrom(s_laptop_mic_sock, pcm_buffer, sizeof(pcm_buffer), 0,
                           (struct sockaddr *)&source_addr, &socklen);
        if (len > 0) {
            RingbufHandle_t ringbuf = voice_input_get_ringbuf();
            if (ringbuf != NULL) {
                xRingbufferSend(ringbuf, pcm_buffer, len, pdMS_TO_TICKS(10));
            }
            voice_udp_send_audio(pcm_buffer, len);
        }
    }

    close(s_laptop_mic_sock);
    vTaskDelete(NULL);
}

static void wifi_event_handler(void* arg, esp_event_base_t event_base,
                                int32_t event_id, void* event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "Wi-Fi disconnected. Reconnecting...");
        s_wifi_connected = false;
        esp_wifi_connect();
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t* event = (ip_event_got_ip_t*) event_data;
        ESP_LOGI(TAG, "Wi-Fi Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        s_wifi_connected = true;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);

        xTaskCreatePinnedToCore(pc_mic_udp_receiver_task, "laptop_mic_rx", 4096, NULL, 5, NULL, 1);
    }
}

esp_err_t voice_udp_init(void)
{
    ESP_LOGI(TAG, "Initializing Wi-Fi Station & UDP Broadcast Sockets...");

    // Initialize NVS Flash for Wi-Fi storage
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id;
    esp_event_handler_instance_t instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT,
                                                        ESP_EVENT_ANY_ID,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT,
                                                        IP_EVENT_STA_GOT_IP,
                                                        &wifi_event_handler,
                                                        NULL,
                                                        &instance_got_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = CONFIG_VOICE_WIFI_SSID,
            .password = CONFIG_VOICE_WIFI_PASS,
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Waiting for Wi-Fi connection to SSID: '%s'...", CONFIG_VOICE_WIFI_SSID);

    // Create UDP Sockets for Audio and Events
    s_audio_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_audio_sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP Audio socket: errno %d", errno);
        return ESP_FAIL;
    }

    s_event_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_event_sock < 0) {
        ESP_LOGE(TAG, "Failed to create UDP Event socket: errno %d", errno);
        return ESP_FAIL;
    }

    // Enable Broadcast Option so ESP32-S3 automatically connects to any PC on local Wi-Fi!
    int broadcast_enable = 1;
    setsockopt(s_audio_sock, SOL_SOCKET, SO_BROADCAST, &broadcast_enable, sizeof(broadcast_enable));
    setsockopt(s_event_sock, SOL_SOCKET, SO_BROADCAST, &broadcast_enable, sizeof(broadcast_enable));

    // Set non-blocking socket flag
    int flags = fcntl(s_audio_sock, F_GETFL, 0);
    fcntl(s_audio_sock, F_SETFL, flags | O_NONBLOCK);

    // Configure Audio Destination Address (Broadcast IP 255.255.255.255)
    memset(&s_audio_dest_addr, 0, sizeof(s_audio_dest_addr));
    s_audio_dest_addr.sin_addr.s_addr = htonl(INADDR_BROADCAST);
    s_audio_dest_addr.sin_family = AF_INET;
    s_audio_dest_addr.sin_port = htons(CONFIG_VOICE_UDP_AUDIO_PORT);

    // Configure Event Destination Address (Broadcast IP 255.255.255.255)
    memset(&s_event_dest_addr, 0, sizeof(s_event_dest_addr));
    s_event_dest_addr.sin_addr.s_addr = htonl(INADDR_BROADCAST);
    s_event_dest_addr.sin_family = AF_INET;
    s_event_dest_addr.sin_port = htons(CONFIG_VOICE_UDP_EVENT_PORT);

    ESP_LOGI(TAG, "UDP Streaming configured with BROADCAST mode (Audio Port: %d, Event Port: %d)",
             CONFIG_VOICE_UDP_AUDIO_PORT, CONFIG_VOICE_UDP_EVENT_PORT);

    return ESP_OK;
}

esp_err_t voice_udp_send_audio(const void *pcm_data, size_t len)
{
    if (!s_wifi_connected || s_audio_sock < 0 || pcm_data == NULL || len == 0) {
        return ESP_ERR_INVALID_STATE;
    }

    int err = sendto(s_audio_sock, pcm_data, len, 0,
                     (struct sockaddr *)&s_audio_dest_addr, sizeof(s_audio_dest_addr));
    if (err < 0) {
        if (errno == ENOMEM || errno == EAGAIN || errno == EWOULDBLOCK) {
            vTaskDelay(pdMS_TO_TICKS(1));
            return ESP_ERR_NO_MEM;
        }
        ESP_LOGE(TAG, "UDP sendto Audio failed: errno %d", errno);
        return ESP_FAIL;
    }

    return ESP_OK;
}

esp_err_t voice_udp_send_event(const VoiceEvent *event)
{
    if (!s_wifi_connected || s_event_sock < 0 || event == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    char json_buf[256];
    int len = snprintf(json_buf, sizeof(json_buf),
                       "{\"event_type\":%d,\"cmd_type\":%d,\"val\":%d,\"mode\":%d,\"confidence\":%.2f,\"text\":\"%s\"}",
                       event->type,
                       event->command.type,
                       event->command.value,
                       event->command.mode,
                       event->confidence,
                       event->command.raw_text);

    int err = sendto(s_event_sock, json_buf, len, 0,
                     (struct sockaddr *)&s_event_dest_addr, sizeof(s_event_dest_addr));
    if (err < 0) {
        ESP_LOGE(TAG, "UDP sendto Event failed: errno %d", errno);
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Sent Broadcast UDP VoiceEvent JSON (%d bytes)", len);
    return ESP_OK;
}

#else

esp_err_t voice_udp_init(void) { return ESP_OK; }
esp_err_t voice_udp_send_audio(const void *pcm_data, size_t len) { return ESP_OK; }
esp_err_t voice_udp_send_event(const VoiceEvent *event) { return ESP_OK; }

#endif // CONFIG_VOICE_ENABLE_UDP_STREAM
