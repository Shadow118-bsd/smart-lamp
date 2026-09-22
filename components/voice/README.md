# MODULE 1 — VOICE INTERACTION SUBSYSTEM DOCUMENTATION

## 1. Module Architecture

Module 1 — Voice Interaction Subsystem cho dự án **Smart Desk Lamp** được thiết kế dưới dạng một component ESP-IDF độc lập nằm tại `components/voice/`.

```
                    ┌────────────────────────────┐
                    │          INMP441           │
                    │       (I2S Mic DMA)        │
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │      audio_input_task      │
                    │   (FreeRTOS RingBuffer)    │
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │   voice_processing_task    │
                    │ ESP-SR AFE/WakeNet/MultiNet│
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │        voice_parser        │
                    │    Intent + Parameters     │
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │     Voice Event Queue      │
                    └─────────────┬──────────────┘
                                  │
                                  ▼
                    ┌────────────────────────────┐
                    │     Interaction Layer      │
                    │        (Module 2)          │
                    └────────────────────────────┘
```

---

## 2. Voice Pipeline & Data Flow

1. **Audio Input Layer (`voice_input.c`):**
   - Đọc PCM raw data 16-bit 16kHz từ microphone INMP441 qua I2S DMA.
   - Truyền dữ liệu vào RingBuffer 32KB không gây nghẽn (non-blocking).
2. **Speech Recognition Layer (`voice_sr.c`):**
   - Chạy `voice_processing_task` gọi ESP-SR AFE (AEC/NS/AGC), nhận diện Wake Word "Hey Shine" (WakeNet).
   - Nhận diện câu nói thành text và tính toán điểm tin cậy `confidence` (MultiNet).
3. **Command Parser Layer (`voice_parser.c` & `pc_mic_streamer.py`):**
   - **Mô hình Hybrid Command Understanding (Kép):**
     - **Fast Path (Local Regex & Synonyms):** Nhận diện tức thì (0ms, 0 token) các lệnh cố định và trích xuất số động (VD: "bật đèn", "tắt đi", "tăng sáng 30%", "chỉnh độ sáng 80").
     - **Semantic Fallback (Gemini LLM API / Local NLU Dataset):** Khi gặp các câu nói ngữ cảnh phức tạp hoặc ẩn dụ (VD: "tối quá không xem sách được", "trùm chăn đi ngủ đây"), hệ thống tự động chuyển sang LLM API (đã hỗ trợ cấu hình qua file `.env`) hoặc Intent Classifier để phân tích ý định chính xác.
4. **State Machine & Multi-turn Session (`voice_state.c`, `voice_session.c`):**
   - Chuyển đổi trạng thái từ `IDLE` -> `LISTENING` -> `PROCESSING` -> `EXECUTING` -> `FEEDBACK` -> `LISTENING`.
   - Giữ session lắng nghe nhiều câu lệnh liên tiếp (Multi-turn).
   - Khi hết thời gian session timeout (15 giây), hệ thống tự động quay về `IDLE` nhưng **tuyệt đối không reset trạng thái đèn**.
5. **Audio Output Abstraction (`voice_output.c`, `voice_feedback.c`):**
   - Phát âm thanh/tone phản hồi giọng nói qua I2S MAX98357A và Speaker theo từng usecase/sự kiện hệ thống (`VOICE_FEEDBACK_WAKE`, `VOICE_FEEDBACK_SUCCESS`, `VOICE_FEEDBACK_NOT_UNDERSTOOD`, v.v.).

---

## 3. Public API Reference (`voice.h`)

| Function Signature | Description |
|---|---|
| `esp_err_t voice_init(void)` | Khởi tạo toàn bộ subsystem (Queue, Task, I2S, State, Timer). |
| `esp_err_t voice_start(void)` | Kích hoạt audio input task và ESP-SR processing task. |
| `esp_err_t voice_stop(void)` | Tạm dừng các task voice processing. |
| `esp_err_t voice_get_state(VoiceState *state)` | Thread-safe getter lấy trạng thái hiện tại. |
| `esp_err_t voice_send_feedback(VoiceFeedbackType feedback)` | Yêu cầu phát phản hồi âm thanh. |
| `esp_err_t voice_get_event(VoiceEvent *event, TickType_t timeout_ticks)` | Nhận `VoiceEvent` dispatched sang Interaction Layer. |

---

## 4. Confidence Threshold Handling

Các ngưỡng tin cậy được cấu hình trong `voice_config.h`:
- `confidence >= VOICE_CONFIDENCE_HIGH` (0.70f): Thực thi lệnh ngay lập tức, phát feedback `VOICE_FEEDBACK_SUCCESS`, chuyển `VoiceEvent` sang Queue.
- `VOICE_CONFIDENCE_LOW <= confidence < VOICE_CONFIDENCE_HIGH` (0.40f - 0.70f): Yêu cầu người dùng nói lại (`VOICE_FEEDBACK_LOW_CONFIDENCE`), phát `VOICE_EVENT_LOW_CONFIDENCE`.
- `confidence < VOICE_CONFIDENCE_LOW` (< 0.40f): Từ chối nhận diện, phát feedback `VOICE_FEEDBACK_NOT_UNDERSTOOD`.

---

## 5. Testing Strategy (Level 1 to Level 7)

- **LEVEL 1 — Audio Input:** Kiểm tra luồng dữ liệu I2S từ INMP441 qua DMA buffer.
- **LEVEL 2 — Wake Word:** Đo tỉ lệ nhận diện Wake word "Hey Shine" và false trigger.
- **LEVEL 3 — Speech Recognition:** Test bộ các câu lệnh tiếng Việt chuẩn.
- **LEVEL 4 — Parser Unit Test:** Kiểm tra chuyển đổi text -> `VoiceCommand` (trong `components/voice/test/test_voice.c`).
- **LEVEL 5 — Confidence Handling:** Test 3 mức High/Medium/Low confidence.
- **LEVEL 6 — Multi-turn Session:** Test thực hiện chuỗi lệnh liên tiếp và timeout quay về IDLE mà giữ nguyên đèn.
- **LEVEL 7 — Speaker & Feedback:** Kiểm tra phát âm thanh qua MAX98357A và tránh nhiễu feedback âm thanh.

---

## 6. Hardware Boundaries & TODO List for Integration Phase

1. **GPIO Mapping:** Các chân I2S Mic và Amp đang sử dụng macro placeholder trong `voice_config.h`. Sẽ gán chân GPIO chính thức ở bước Hardware Integration.
2. **AEC / Speaker Echo:** Khảo sát AFE AEC của ESP-SR khi tích hợp loa và microphone trên cùng khung vỏ phần cứng.
3. **Decoupled Architecture:** Module 1 hoàn toàn độc lập với Module 2 (Interaction) và Module 3 (Lighting/Power).
