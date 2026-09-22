#ifndef VOICE_UDP_H
#define VOICE_UDP_H

#include "esp_err.h"
#include "voice_event.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize Wi-Fi station and UDP Sockets for audio and event streaming.
 * @return ESP_OK on success.
 */
esp_err_t voice_udp_init(void);

/**
 * @brief Send raw PCM 16kHz audio buffer over UDP to destination PC.
 * @param pcm_data Pointer to PCM audio buffer.
 * @param len Buffer length in bytes.
 * @return ESP_OK on success.
 */
esp_err_t voice_udp_send_audio(const void *pcm_data, size_t len);

/**
 * @brief Send VoiceEvent struct / JSON notification over UDP to destination PC.
 * @param event Pointer to target VoiceEvent struct.
 * @return ESP_OK on success.
 */
esp_err_t voice_udp_send_event(const VoiceEvent *event);

#ifdef __cplusplus
}
#endif

#endif // VOICE_UDP_H
