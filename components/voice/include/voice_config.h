#ifndef VOICE_CONFIG_H
#define VOICE_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* --- CONFIDENCE THRESHOLDS --- */
#define VOICE_CONFIDENCE_HIGH       0.70f
#define VOICE_CONFIDENCE_LOW        0.40f

/* --- TIMEOUT CONFIGURATION (MS) --- */
#define VOICE_LISTEN_TIMEOUT_MS     8000
#define VOICE_SESSION_TIMEOUT_MS    15000

/* --- QUEUE & BUFFER CONFIGURATION --- */
#define VOICE_QUEUE_LENGTH          10
#define VOICE_MAX_COMMAND_LENGTH    128
#define VOICE_AUDIO_BUFFER_SIZE     1024

/* --- FREERTOS TASK CONFIGURATION --- */
#define VOICE_INPUT_TASK_STACK_SIZE      4096
#define VOICE_INPUT_TASK_PRIO            10
#define VOICE_PROCESSING_TASK_STACK_SIZE 8192
#define VOICE_PROCESSING_TASK_PRIO       9
#define VOICE_FEEDBACK_TASK_STACK_SIZE   4096
#define VOICE_FEEDBACK_TASK_PRIO         8

/* --- HARDWARE GPIO MAPPING CHO ESP32-S3 SUPERMINI / MINI ---
 * Tối ưu hóa chân GPIO 4, 5, 6 phù hợp với tất cả các dòng mạch ESP32-S3 SuperMini (13-14 chân).
 */

// I2S Microphone (INMP441)
#define CONFIG_VOICE_I2S_MIC_PORT        0       // I2S_NUM_0
#define CONFIG_VOICE_I2S_MIC_WS_GPIO     4       // WS (LRCK) -> Chân GPIO 4
#define CONFIG_VOICE_I2S_MIC_SCK_GPIO    5       // SCK (BCLK) -> Chân GPIO 5
#define CONFIG_VOICE_I2S_MIC_SD_GPIO     6       // SD (DATA) -> Chân GPIO 6
#define CONFIG_VOICE_I2S_MIC_SAMPLE_RATE 16000

// I2S Audio Amplifier (MAX98357A)
#define CONFIG_VOICE_I2S_AMP_PORT        1       // I2S_NUM_1
#define CONFIG_VOICE_I2S_AMP_BCLK_GPIO   7       // BCLK -> Chân GPIO 7
#define CONFIG_VOICE_I2S_AMP_LRC_GPIO    8       // LRC -> Chân GPIO 8
#define CONFIG_VOICE_I2S_AMP_DOUT_GPIO   9       // DOUT -> Chân GPIO 9
#define CONFIG_VOICE_I2S_AMP_SAMPLE_RATE 16000

/* --- WI-FI & UDP STREAMING CONFIGURATION --- */
#define CONFIG_VOICE_ENABLE_UDP_STREAM   1
#define CONFIG_VOICE_WIFI_SSID           "Be La"      // Wi-Fi nhà bạn
#define CONFIG_VOICE_WIFI_PASS           "13012009"  // Mật khẩu Wi-Fi
#define CONFIG_VOICE_UDP_DEST_IP         "192.168.1.42"      // IP Máy tính chạy script
#define CONFIG_VOICE_UDP_AUDIO_PORT      12345
#define CONFIG_VOICE_UDP_EVENT_PORT      12346

#ifdef __cplusplus
}
#endif

#endif // VOICE_CONFIG_H
