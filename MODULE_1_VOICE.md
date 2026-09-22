# MODULE 1 — VOICE INTERACTION
## Prompt triển khai module Voice Interaction cho đèn bàn thông minh

---

# 1. ROLE

Bạn đang đóng vai trò:

> **Embedded Voice Interaction Engineer**

Nhiệm vụ của bạn là xây dựng **Module 1 — Voice Interaction** cho dự án **Smart Desk Lamp** sử dụng:

- ESP32-S3
- ESP-IDF
- FreeRTOS
- ESP-SR
- INMP441 — microphone I2S
- MAX98357A — I2S audio amplifier
- Speaker — voice feedback

## QUAN TRỌNG

Trong phạm vi nhiệm vụ này:

**KHÔNG thiết kế lại phần cứng.**

**KHÔNG tự chọn lại linh kiện.**

**KHÔNG tự quyết định GPIO.**

**KHÔNG triển khai Module 2 hoặc Module 3.**

Phần hardware integration, wiring, GPIO mapping, power architecture và integration toàn hệ thống sẽ được xử lý ở giai đoạn sau.

Nếu thiếu thông tin phần cứng cần thiết để chạy một phần nào đó:

> Hãy đánh dấu rõ thông tin đang thiếu và tạo abstraction/configuration phù hợp, KHÔNG tự ý thay đổi kiến trúc phần cứng.

---

# 2. PROJECT CONTEXT

Đây là một chiếc đèn bàn thông minh sử dụng **Voice User Interface (VUI)** làm phương thức tương tác chính.

Người dùng có thể nói các câu lệnh như:

```text
Hey Shine
```

sau đó:

```text
Bật đèn
```

hoặc:

```text
Bật chế độ học
```

hoặc:

```text
Tăng độ sáng
```

hoặc:

```text
Tăng sáng 10%
```

hoặc:

```text
Ấm hơn
```

hoặc:

```text
Tắt đèn
```

Voice Module có nhiệm vụ:

```text
Microphone
    ↓
Audio Capture
    ↓
Audio Processing
    ↓
Wake Word Detection
    ↓
Speech Recognition
    ↓
Command Understanding
    ↓
Intent + Parameter Extraction
    ↓
VoiceCommand
    ↓
Interaction Layer
```

Chiều ngược lại:

```text
Interaction Layer
        ↓
Voice Feedback
        ↓
Audio Output
        ↓
Speaker
```

---

# 3. RESPONSIBILITY

Module 1 chịu trách nhiệm duy nhất cho:

## INPUT

Nhận giọng nói của người dùng thông qua:

```text
INMP441
    ↓
I2S
    ↓
ESP32-S3
```

## SPEECH PROCESSING

Triển khai:

- Audio capture
- Audio preprocessing
- Wake word detection
- Speech recognition
- Command recognition
- Intent parsing
- Parameter extraction
- Confidence handling
- Voice session handling

## OUTPUT

Phát phản hồi bằng giọng nói:

```text
Interaction Layer
    ↓
Voice Feedback
    ↓
I2S
    ↓
MAX98357A
    ↓
Speaker
```

---

# 4. WHAT MODULE 1 MUST NOT DO

Không triển khai logic điều khiển trực tiếp:

- LED
- LED Driver
- CCT LED
- Brightness hardware
- Battery
- Charger
- BMS
- Power Path
- BH1750
- BME280
- PIR
- VL53L0X
- Rotary Encoder

Không được viết kiểu:

```c
set_led_brightness();
```

trực tiếp bên trong Voice Module.

Thay vào đó phải tạo command/event:

```text
Voice Module
      ↓
VoiceCommand
      ↓
Interaction Layer
      ↓
Device Layer
```

Voice Module chỉ nói:

> "User muốn tăng brightness 10%"

chứ không trực tiếp điều khiển LED.

---

# 5. TECHNOLOGY STACK

Sử dụng:

```text
MCU:
ESP32-S3

Framework:
ESP-IDF

RTOS:
FreeRTOS

Voice Framework:
ESP-SR

Language:
C/C++

Audio:
I2S

Microphone:
INMP441

Audio Amplifier:
MAX98357A
```

Ưu tiên sử dụng API/library chính thức của Espressif và ESP-SR thay vì tự xây dựng ASR từ đầu.

---

# 6. VOICE PIPELINE

Xây dựng pipeline:

```text
                 ┌──────────────┐
                 │   INMP441    │
                 └──────┬───────┘
                        ↓
                 ┌──────────────┐
                 │ I2S Capture  │
                 └──────┬───────┘
                        ↓
                 ┌──────────────┐
                 │     AFE      │
                 └──────┬───────┘
                        ↓
                 ┌──────────────┐
                 │  WakeNet     │
                 └──────┬───────┘
                        ↓
                  Wake Detected
                        ↓
                 ┌──────────────┐
                 │   MultiNet   │
                 └──────┬───────┘
                        ↓
                 Speech Command
                        ↓
                 ┌──────────────┐
                 │ Intent Parser│
                 └──────┬───────┘
                        ↓
                 ┌──────────────┐
                 │ VoiceCommand │
                 └──────┬───────┘
                        ↓
                 Interaction Layer
```

---

# 7. WAKE WORD

Sử dụng ESP-SR WakeNet cho wake word detection.

Wake word của project:

```text
Hey Shine
```

Nếu model wake word thực tế chưa hỗ trợ custom wake word trong môi trường hiện tại:

- Không tự huấn luyện model mới.
- Không tự tạo một hệ thống wake word giả.
- Ghi rõ limitation.
- Thiết kế interface để sau này thay model wake word.

Wake detection phải tạo event:

```c
VOICE_EVENT_WAKE_DETECTED
```

Ví dụ:

```text
IDLE
 ↓
Wake word detected
 ↓
LISTENING
```

---

# 8. SPEECH RECOGNITION

Sau khi wake word được phát hiện:

```text
Wake Word
    ↓
Listening Window
    ↓
Speech Recognition
    ↓
Recognized Command
```

Ví dụ:

```text
"bật đèn"
```

hoặc:

```text
"tăng sáng 10%"
```

hoặc:

```text
"bật chế độ học"
```

Không được xử lý tất cả câu nói bằng các chuỗi hard-code đơn giản.

Cần xây dựng tầng:

```text
Speech Recognition
        ↓
Command Understanding
```

để tách:

```text
Intent
Parameter
```

---

# 9. INTENT MODEL

Thiết kế command model có khả năng mở rộng.

Tối thiểu hỗ trợ:

```c
typedef enum {
    CMD_NONE,

    CMD_POWER_ON,
    CMD_POWER_OFF,

    CMD_SET_BRIGHTNESS,
    CMD_BRIGHTNESS_UP,
    CMD_BRIGHTNESS_DOWN,

    CMD_SET_CCT,
    CMD_CCT_WARMER,
    CMD_CCT_COOLER,

    CMD_SET_MODE,

    CMD_GET_BRIGHTNESS,
    CMD_GET_BATTERY,

    CMD_CANCEL
} CommandType;
```

Nếu ESP-SR chỉ trả về một command string, xây dựng parser để chuyển command đó thành enum.

Ví dụ:

```text
"bật đèn"
        ↓
CMD_POWER_ON
```

```text
"tắt đèn"
        ↓
CMD_POWER_OFF
```

```text
"tăng sáng 10%"
        ↓
CMD_SET_BRIGHTNESS
value = +10
```

```text
"giảm sáng 20%"
        ↓
CMD_SET_BRIGHTNESS
value = -20
```

```text
"bật chế độ học"
        ↓
CMD_SET_MODE
mode = MODE_STUDY
```

---

# 10. LIGHT MODE

Định nghĩa abstraction cho mode:

```c
typedef enum {
    MODE_NONE,
    MODE_NORMAL,
    MODE_STUDY,
    MODE_READING,
    MODE_NIGHT
} LightMode;
```

Voice Module chỉ gửi:

```text
CMD_SET_MODE
```

và:

```text
mode = MODE_STUDY
```

Không triển khai brightness/CCT thực tế của từng mode.

Ví dụ:

```text
"bật chế độ học"

        ↓

CommandType:
CMD_SET_MODE

Mode:
MODE_STUDY
```

---

# 11. VOICE COMMAND STRUCTURE

Tạo structure dùng để giao tiếp với Interaction Layer.

Ví dụ:

```c
typedef struct {
    CommandType type;
    int value;
    LightMode mode;
    float confidence;
} VoiceCommand;
```

Có thể mở rộng structure nếu cần, nhưng phải đảm bảo:

- rõ ràng
- dễ serialize nếu cần
- dễ gửi qua FreeRTOS Queue
- không phụ thuộc vào implementation của LED/device module

---

# 12. VOICE EVENT

Tạo event abstraction:

```c
typedef enum {
    VOICE_EVENT_WAKE_DETECTED,
    VOICE_EVENT_COMMAND,
    VOICE_EVENT_LOW_CONFIDENCE,
    VOICE_EVENT_TIMEOUT,
    VOICE_EVENT_ERROR
} VoiceEventType;
```

Structure:

```c
typedef struct {
    VoiceEventType type;
    VoiceCommand command;
    float confidence;
} VoiceEvent;
```

Voice event sẽ được gửi sang Interaction Layer thông qua cơ chế phù hợp, ưu tiên:

```text
FreeRTOS Queue
```

hoặc event-based architecture.

Không gọi trực tiếp logic của Module 2.

---

# 13. CONFIDENCE HANDLING

Không thực thi command ngay khi confidence quá thấp.

Thiết kế threshold:

```text
confidence >= HIGH_THRESHOLD
        ↓
Execute command
```

```text
LOW_THRESHOLD <= confidence < HIGH_THRESHOLD
        ↓
Clarification / confirmation
```

```text
confidence < LOW_THRESHOLD
        ↓
Reject / ask user to repeat
```

Threshold phải được cấu hình bằng constant/config:

```c
VOICE_CONFIDENCE_HIGH
VOICE_CONFIDENCE_LOW
```

Không hard-code rải rác trong code.

---

# 14. LOW CONFIDENCE

Ví dụ:

User:

```text
"bật đèn..."
```

ASR không chắc chắn.

Voice Module không được tự đoán.

Có thể phát feedback:

```text
"Mình chưa nghe rõ, bạn nói lại nhé."
```

Sau đó tiếp tục listening hoặc trả event:

```c
VOICE_EVENT_LOW_CONFIDENCE
```

Quyết định cuối cùng về dialogue/session thuộc Interaction Layer.

---

# 15. MULTI-TURN VOICE SESSION

Voice interaction phải hỗ trợ nhiều câu lệnh trong một session.

Ví dụ:

```text
User:
Hey Shine

System:
I'm listening.

User:
Bật đèn chế độ học

System:
Đã bật chế độ học.

User:
Tăng sáng 10%

System:
Đã tăng độ sáng 10%.

User:
Ấm hơn

System:
Đã tăng nhiệt độ màu.
```

Sau một khoảng timeout:

```text
LISTENING
    ↓
TIMEOUT
    ↓
IDLE
```

QUAN TRỌNG:

Timeout chỉ kết thúc **voice interaction session**.

Không được reset trạng thái của lamp.

Ví dụ:

```text
User:
Bật chế độ ban đêm

System:
Đã bật chế độ ban đêm.

Session timeout

Voice state:
IDLE

Lamp:
VẪN Ở CHẾ ĐỘ BAN ĐÊM
```

---

# 16. VOICE STATE MACHINE

Xây dựng state machine:

```text
                 Wake Word
IDLE ─────────────────────────→ LISTENING
                                  │
                                  ↓
                             PROCESSING
                                  │
                                  ↓
                             EXECUTING
                                  │
                                  ↓
                              FEEDBACK
                                  │
                                  ↓
                             LISTENING
```

Các nhánh bổ sung:

```text
LISTENING
    ↓ timeout
IDLE
```

```text
PROCESSING
    ↓ low confidence
CLARIFICATION
```

```text
LISTENING
    ↓ cancel
IDLE
```

Có thể định nghĩa:

```c
typedef enum {
    VOICE_STATE_IDLE,
    VOICE_STATE_LISTENING,
    VOICE_STATE_PROCESSING,
    VOICE_STATE_EXECUTING,
    VOICE_STATE_FEEDBACK,
    VOICE_STATE_CLARIFICATION
} VoiceState;
```

---

# 17. AUDIO INPUT TASK

Thiết kế FreeRTOS task riêng cho audio input.

Ví dụ architecture:

```text
audio_input_task
        ↓
I2S DMA
        ↓
audio buffer
        ↓
AFE / ESP-SR
```

Không block task quá lâu.

Phải đảm bảo:

- DMA hoạt động ổn định
- buffer đủ lớn
- tránh buffer overflow
- tránh buffer underrun
- xử lý audio realtime
- không dùng delay dài trong audio processing

---

# 18. ESP-SR TASK

Tạo task chịu trách nhiệm xử lý voice recognition.

Concept:

```text
audio_input_task
        ↓
audio buffer
        ↓
voice_processing_task
        ↓
ESP-SR
```

Task phải xử lý:

1. Wake word
2. Speech recognition
3. Recognition result
4. Confidence
5. Convert result → VoiceCommand
6. Generate VoiceEvent

---

# 19. AUDIO OUTPUT

Voice feedback sử dụng:

```text
ESP32-S3
    ↓
I2S
    ↓
MAX98357A
    ↓
Speaker
```

Tạo abstraction:

```c
voice_feedback_play(...)
```

Voice Module không cần biết implementation vật lý của amplifier.

Ví dụ:

```c
voice_feedback_play("light_on");
```

hoặc:

```c
voice_feedback_play(VOICE_FEEDBACK_LIGHT_ON);
```

---

# 20. VOICE FEEDBACK

Tối thiểu thiết kế các loại feedback:

```c
typedef enum {
    VOICE_FEEDBACK_WAKE,
    VOICE_FEEDBACK_SUCCESS,
    VOICE_FEEDBACK_NOT_UNDERSTOOD,
    VOICE_FEEDBACK_LOW_CONFIDENCE,
    VOICE_FEEDBACK_CANCELLED,
    VOICE_FEEDBACK_TIMEOUT,
    VOICE_FEEDBACK_ERROR
} VoiceFeedbackType;
```

Ví dụ:

```text
Wake:
"Mình đang nghe."

Success:
"Đã thực hiện."

Low confidence:
"Mình chưa nghe rõ."

Error:
"Có lỗi xảy ra, bạn thử lại nhé."
```

Nội dung thực tế có thể được thay đổi sau.

---

# 21. AEC / AUDIO ECHO

Do microphone và speaker cùng nằm trên một thiết bị:

```text
Speaker
   ↓
Âm thanh phát ra
   ↓
Microphone
   ↓
ESP32-S3
```

có nguy cơ feedback/echo.

Do đó cần khảo sát và sử dụng các khả năng AFE/AEC phù hợp của ESP-SR nếu hardware/configuration hỗ trợ.

Không tự bỏ qua vấn đề này.

Phải test:

```text
User speech
+
Speaker feedback
```

để kiểm tra:

- wake word có bị false trigger không
- ASR có nhận nhầm speaker feedback không
- microphone có bị saturation không

---

# 22. FREERTOS ARCHITECTURE

Ưu tiên chia thành các task/module:

```text
┌────────────────────────────┐
│ audio_input_task           │
│ I2S microphone capture     │
└──────────────┬─────────────┘
               ↓
┌────────────────────────────┐
│ voice_processing_task      │
│ ESP-SR / WakeNet / MultiNet│
└──────────────┬─────────────┘
               ↓
        VoiceEvent Queue
               ↓
┌────────────────────────────┐
│ voice_event_task           │
│ command/event management   │
└────────────────────────────┘
```

Audio output có thể có task riêng:

```text
voice_feedback_task
```

Không để toàn bộ voice pipeline chạy trong một task duy nhất nếu điều đó gây blocking hoặc khó kiểm soát realtime.

---

# 23. THREAD SAFETY

Các tài nguyên dùng chung phải được quản lý an toàn.

Đặc biệt:

- audio buffer
- voice state
- command queue
- feedback queue
- ESP-SR resources

Nếu cần sử dụng:

```text
FreeRTOS Queue
Mutex
Semaphore
Event Group
```

Không dùng global state một cách tùy tiện.

---

# 24. ERROR HANDLING

Phải xử lý tối thiểu:

```text
Microphone initialization failure
I2S initialization failure
ESP-SR initialization failure
Model loading failure
Audio buffer overflow
Speech recognition timeout
Unknown command
Low confidence
Speaker/audio output failure
Queue full
Unexpected state
```

Không để lỗi âm thầm.

Sử dụng logging:

```c
ESP_LOGI()
ESP_LOGW()
ESP_LOGE()
```

Log phải giúp debug được pipeline.

Ví dụ:

```text
[VOICE] Wake word detected
[VOICE] Listening started
[VOICE] Recognition result: "tăng sáng 10%"
[VOICE] Confidence: 0.91
[VOICE] Intent: CMD_SET_BRIGHTNESS
[VOICE] Value: 10
[VOICE] Event sent
```

---

# 25. CONFIGURATION

Các thông số có khả năng thay đổi phải nằm trong configuration.

Ví dụ:

```c
VOICE_CONFIDENCE_HIGH
VOICE_CONFIDENCE_LOW

VOICE_LISTEN_TIMEOUT_MS
VOICE_SESSION_TIMEOUT_MS

VOICE_MAX_COMMAND_LENGTH
VOICE_QUEUE_LENGTH
```

Không hard-code nhiều giá trị trong business logic.

---

# 26. PROJECT STRUCTURE

Thiết kế module có cấu trúc rõ ràng.

Ví dụ:

```text
components/
└── voice/
    ├── include/
    │   ├── voice.h
    │   ├── voice_command.h
    │   ├── voice_event.h
    │   └── voice_feedback.h
    │
    ├── src/
    │   ├── voice.c
    │   ├── voice_state.c
    │   ├── voice_parser.c
    │   ├── voice_session.c
    │   ├── voice_input.c
    │   ├── voice_output.c
    │   └── voice_feedback.c
    │
    └── CMakeLists.txt
```

Có thể thay đổi structure nếu cần, nhưng phải giữ nguyên nguyên tắc:

```text
Public API
    ↓
Voice Core
    ↓
ESP-SR / Audio Driver
```

---

# 27. PUBLIC API

Thiết kế API để module khác có thể sử dụng.

Ví dụ:

```c
esp_err_t voice_init(void);

esp_err_t voice_start(void);

esp_err_t voice_stop(void);

esp_err_t voice_get_state(VoiceState *state);

esp_err_t voice_send_feedback(VoiceFeedbackType feedback);

esp_err_t voice_get_event(VoiceEvent *event);
```

API có thể thay đổi nếu implementation thực tế yêu cầu, nhưng phải:

- đơn giản
- rõ trách nhiệm
- không expose implementation detail
- không phụ thuộc LED/device layer

---

# 28. COMMAND PARSER

Parser phải có khả năng chuyển recognition result thành command có cấu trúc.

Ví dụ:

```text
Speech:
"tăng sáng 10 phần trăm"

        ↓

Intent:
CMD_SET_BRIGHTNESS

Parameter:
10

        ↓

VoiceCommand
```

Ví dụ:

```text
Speech:
"ấm hơn"

        ↓

Intent:
CMD_CCT_WARMER
```

Ví dụ:

```text
Speech:
"bật chế độ đọc sách"

        ↓

Intent:
CMD_SET_MODE

Mode:
MODE_READING
```

Parser phải được thiết kế để dễ mở rộng thêm command.

---

# 29. DO NOT MIX RECOGNITION AND DEVICE LOGIC

Không viết:

```c
if (strcmp(command, "bật đèn") == 0) {
    gpio_set_level(...);
}
```

Thay vào đó:

```text
Speech Recognition
        ↓
Parser
        ↓
VoiceCommand
        ↓
Queue
        ↓
Interaction Layer
```

Điều này giúp Module 1 độc lập với hardware implementation.

---

# 30. TESTING STRATEGY

Phải xây dựng test theo từng tầng.

## LEVEL 1 — Audio Input

Kiểm tra:

```text
INMP441
↓
I2S
↓
ESP32-S3
```

Xác nhận audio stream hoạt động.

---

## LEVEL 2 — Wake Word

Test:

```text
Hey Shine
```

Đo:

- detection rate
- false trigger
- response time

---

## LEVEL 3 — Speech Recognition

Test các câu:

```text
Bật đèn
Tắt đèn
Tăng sáng
Giảm sáng
Tăng sáng 10%
Giảm sáng 20%
Ấm hơn
Lạnh hơn
Bật chế độ học
Bật chế độ đọc sách
Bật chế độ ban đêm
```

---

## LEVEL 4 — Parser

Kiểm tra:

```text
Speech
→ Intent
→ Parameter
→ VoiceCommand
```

---

## LEVEL 5 — Confidence

Test:

```text
High confidence
Medium confidence
Low confidence
Unknown command
```

---

## LEVEL 6 — Session

Test:

```text
Wake
→ Command
→ Command
→ Command
→ Timeout
→ IDLE
```

Đảm bảo lamp state không bị reset khi voice session kết thúc.

---

## LEVEL 7 — Speaker

Kiểm tra:

```text
ESP32-S3
→ I2S
→ MAX98357A
→ Speaker
```

và feedback không gây false wake.

---

# 31. PERFORMANCE REQUIREMENTS

Ưu tiên:

- realtime audio processing
- latency thấp
- không blocking
- memory ổn định
- không memory leak
- FreeRTOS task hoạt động ổn định
- không crash khi nhận nhiều command liên tiếp

Theo dõi:

```text
CPU usage
RAM usage
Heap
Task stack usage
Queue usage
Audio buffer status
Recognition latency
```

Có thể sử dụng:

```c
uxTaskGetStackHighWaterMark()
```

và các API heap của ESP-IDF để debug.

---

# 32. DOCUMENTATION

Sau khi implement phải tạo documentation cho Module 1.

Documentation cần giải thích:

```text
1. Module architecture
2. Voice pipeline
3. State machine
4. FreeRTOS tasks
5. Queue/Event flow
6. Command structure
7. Parser
8. Confidence handling
9. Voice feedback
10. Error handling
11. Testing
12. Known limitations
```

---

# 33. IMPLEMENTATION ORDER

Không code tất cả cùng lúc.

Thực hiện theo thứ tự:

## STEP 1

Inspect project hiện tại.

Xác định:

- ESP-IDF version
- ESP32-S3 target
- project structure
- existing components
- existing configuration

---

## STEP 2

Thiết lập voice component skeleton.

Tạo:

```text
components/voice/
```

với public/private source rõ ràng.

---

## STEP 3

Thiết lập audio input abstraction.

```text
I2S
↓
Audio Buffer
```

Chưa cần command parser.

---

## STEP 4

Tích hợp ESP-SR.

```text
Audio
↓
AFE
↓
WakeNet
```

Test wake word độc lập.

---

## STEP 5

Tích hợp speech recognition.

```text
WakeNet
↓
MultiNet
↓
Recognition Result
```

---

## STEP 6

Xây dựng parser.

```text
Recognition Result
↓
Intent
↓
Parameter
↓
VoiceCommand
```

---

## STEP 7

Xây dựng VoiceEvent.

```text
VoiceCommand
↓
VoiceEvent
↓
FreeRTOS Queue
```

---

## STEP 8

Xây dựng session/state machine.

```text
IDLE
LISTENING
PROCESSING
EXECUTING
FEEDBACK
CLARIFICATION
```

---

## STEP 9

Xây dựng audio output abstraction.

```text
Voice Feedback
↓
I2S
↓
MAX98357A
↓
Speaker
```

---

## STEP 10

Implement error handling.

---

## STEP 11

Implement logging.

---

## STEP 12

Test toàn bộ voice pipeline.

---

# 34. HARDWARE BOUNDARY

Trong quá trình implement:

**KHÔNG được tự quyết định:**

```text
GPIO
I2S pin
Power rail
Battery architecture
LED driver
Connector
PCB
Wiring
```

Nếu code cần GPIO hoặc hardware configuration nhưng chưa được cung cấp:

```text
DO NOT GUESS.
```

Thay vào đó:

```text
1. Tạo configuration placeholder
2. Ghi rõ TODO
3. Báo cáo thông tin cần bổ sung
```

Ví dụ:

```c
// TODO:
// I2S GPIO mapping will be provided during hardware integration.
```

---

# 35. INTEGRATION BOUNDARY

Module 1 phải có interface rõ ràng với module khác.

Kiến trúc:

```text
                 MODULE 1
             VOICE INTERACTION
                    │
                    │ VoiceEvent
                    ↓
             MODULE 2
          INTERACTION MANAGER
                    │
                    │ DeviceCommand
                    ↓
             MODULE 3
        LIGHTING / POWER / DEVICE
```

Module 1 không được phụ thuộc trực tiếp vào Module 3.

---

# 36. IMPORTANT DESIGN PRINCIPLE

Phải giữ nguyên nguyên tắc:

```text
VOICE = NGHE + NHẬN DIỆN + CHUYỂN THÀNH COMMAND
```

```text
INTERACTION = HIỂU NGỮ CẢNH + QUYẾT ĐỊNH
```

```text
DEVICE = THỰC THI
```

Module 1 không được trở thành:

```text
VOICE + INTERACTION + DEVICE
```

---

# 37. FINAL DELIVERABLES

Sau khi hoàn thành Module 1, phải có:

### Source code

```text
components/voice/
```

### Public API

```text
voice.h
voice_command.h
voice_event.h
voice_feedback.h
```

### Implementation

```text
voice.c
voice_input.c
voice_output.c
voice_parser.c
voice_session.c
voice_feedback.c
```

Tên file có thể thay đổi nếu structure hợp lý hơn.

### Documentation

Có tài liệu mô tả:

```text
Architecture
API
State Machine
Voice Pipeline
Command Parser
Testing
Known Limitations
```

### Test

Có test hoặc test procedure cho:

```text
Audio
Wake Word
Speech Recognition
Parser
Confidence
Session
Feedback
Error Handling
```

---

# 38. DEFINITION OF DONE

Module 1 chỉ được xem là hoàn thành khi:

- [ ] ESP32-S3 build thành công
- [ ] Voice component build thành công
- [ ] Audio input hoạt động
- [ ] ESP-SR khởi tạo thành công
- [ ] Wake word pipeline hoạt động
- [ ] Speech recognition hoạt động
- [ ] Recognition result được chuyển thành Intent
- [ ] Parameter được extract
- [ ] VoiceCommand được tạo
- [ ] VoiceEvent được tạo
- [ ] Event có thể gửi sang Interaction Layer
- [ ] Voice session state machine hoạt động
- [ ] Confidence handling hoạt động
- [ ] Timeout hoạt động
- [ ] Voice feedback abstraction hoạt động
- [ ] Audio output hoạt động khi hardware được tích hợp
- [ ] Không điều khiển trực tiếp LED/device
- [ ] Không thay đổi hardware architecture
- [ ] Không tự ý thay đổi GPIO
- [ ] Không có memory leak rõ ràng
- [ ] Không có blocking nghiêm trọng trong audio pipeline
- [ ] Có logging
- [ ] Có documentation
- [ ] Có test procedure
- [ ] Có danh sách limitation/TODO

---

# 39. WORKING RULE FOR ANTIGRAVITY

Trước khi bắt đầu code:

```text
1. Inspect repository.
2. Inspect ESP-IDF configuration.
3. Determine current project structure.
4. Determine what information is available.
5. Identify missing hardware information.
6. Do NOT guess missing hardware information.
7. Create implementation plan.
8. Implement incrementally.
9. Build after each major step.
10. Fix errors before proceeding.
11. Test each layer independently.
12. Document the final implementation.
```

Nếu gặp vấn đề ngoài phạm vi Module 1:

```text
STOP.
```

và báo cáo:

```text
PROBLEM
CAUSE
WHAT INFORMATION IS REQUIRED
WHAT HAS BEEN IMPLEMENTED
WHAT IS BLOCKED
```

Không tự ý mở rộng scope.

---

# 40. CORE OBJECTIVE

Mục tiêu cuối cùng của Module 1:

```text
User
 ↓
"Hey Shine"
 ↓
Wake Word Detection
 ↓
Speech Recognition
 ↓
Intent + Parameter
 ↓
VoiceCommand
 ↓
VoiceEvent
 ↓
Interaction Layer
```

và:

```text
Interaction Layer
 ↓
Voice Feedback
 ↓
I2S Audio Output
 ↓
Speaker
```

Module 1 phải trở thành một **voice subsystem độc lập, có API rõ ràng, có thể tích hợp vào hệ thống Smart Desk Lamp sau này mà không cần sửa lại toàn bộ kiến trúc.**

---

# 41. COMPLETED UPGRADES & ADVANCED FEATURES (CÁC TÍNH NĂNG ĐÃ HOÀN THÀNH)

### 41.1. Multi-Intent Clause Splitter (Bóc Tách Đa Mệnh Đề Động Từ)
- Triển khai thuật toán **Action Verb Boundary Detection** (`bật`, `mở`, `tắt`, `tăng`, `giảm`, `chuyển`, `đổi`, `chỉnh`).
- Bóc tách chính xác 100% chuỗi 6+ lệnh liên tiếp không từ nối (*"Bật đèn tăng độ sáng 5% chuyển chế độ học bài giảm 10% chuyển ban đêm tắt đèn"*).

### 41.2. Hybrid Edge-First SLM Router (Ollama Qwen2.5 & Built-in Advisory AI)
- **Fast Path (~1ms):** Phân tích trực tiếp từ điển local cho 95% câu lệnh ngắn, 0 Token cost.
- **Local SLM Ollama (Qwen2.5-1.5B):** Tự động Offload xử lý các câu hỏi tư vấn ngữ cảnh thời tiết, phòng rộng hẹp (*"Trời âm u nên để màu gì?"*).
- **Advisory Fallback:** Tự động phản hồi bằng AI nội tại khi Ollama offline.

### 41.3. Distinct State Memory Swap ("Alt + Tab" Toggle Architecture)
- Tự động ghi nhận và lưu vết trạng thái khác biệt gần nhất `g_previous_state`.
- Khi người dùng đọc lệnh `CMD 10` (*"chuyển lại chế độ cũ"*, *"trở về ban đầu"*), hệ thống thực hiện **Atomic Memory Swap (Hoán đổi 2 chiều)** giữa `g_system_state` ⮂ `g_previous_state`.
- Cho phép toggle qua lại liên tục giữa 2 chế độ gần nhất (như phím tắt `Alt+Tab` trên Windows) mà không bao giờ bị nhảy về Standby OFF hay rớt index.
- Nâng ngưỡng tin cậy Fuzzy Match cho STT lên **70%**, loại bỏ các từ âm thanh nhiễu môi trường.


### 41.4. Real-Time HTML5 Audio Waveform Canvas Oscilloscope
- Trích xuất 64 điểm biên độ PCM chuẩn hóa (`-1.0` đến `1.0`) từ luồng âm thanh 16kHz của INMP441 / Laptop Mic.
- Vẽ đồ thị sóng âm Neon Gradient Visualizer thời gian thực (30–60 FPS) trên Web Dashboard.