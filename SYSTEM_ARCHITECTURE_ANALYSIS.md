# 💡 BÁO CÁO PHÂN TÍCH TỔNG QUAN HỆ THỐNG & CHI TIẾT KỸ THUẬT 3 MODULE
## Dự Án: Đèn Bàn Thông Minh (Smart Desk Lamp) — Hybrid Edge-First Architecture

---

## 🏛️ 1. PHÂN TÍCH KIẾN TRÚC TỔNG QUAN HỆ THỐNG

Hệ thống **Đèn Bàn Thông Minh (Smart Desk Lamp)** được thiết kế theo kiến trúc **Embedded Edge System**, lấy vi điều khiển **ESP32-S3** làm trung tâm điều khiển phần cứng trực tiếp, kết hợp với **Server / Edge AI Engine** xử lý ngôn ngữ tự nhiên Tiếng Việt đa mệnh đề.

```text
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                     SƠ ĐỒ KIẾN TRÚC TỔNG QUAN                                    │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 🎙️ TẦNG 1: THU ÂM & PHÂN TÁCH Ý ĐỊNH (MODULE 1)                                                   │
│   • Micro I2S INMP441 ──► Software Gain Boost (8.0x) ──► Peak Normalization (26000)             │
│   • Action Verb Clause Splitter (Phân tách ranh giới động từ không cần từ nối)                   │
│   • Hybrid Edge-First SLM Router:                                                                │
│     ├── ⚡ Nhánh 1: Local Fast Path (~1ms, 0đ Token, Rule & Levenshtein Matcher)                 │
│     └── 🧠 Nhánh 2: Local SLM Gateway (Ollama Qwen2.5-1.5B / Fallback Built-in Advisory AI)      │
│                                           │ (Mảng lệnh VoiceCommandBatch - Max 8 Actions)        │
│                                           ▼                                                      │
│ 🧠 TẦNG 2: ĐIỀU PHỐI TRẠNG THÁI TRUNG TÂM (MODULE 2)                                             │
│   • Phonetic Wake Word Matcher ("Hey Shine", "Hây Xai") ──► Dual-Flow (Single-shot / Two-step)    │
│   • Negation Guard (Loại bỏ mệnh đề chứa tiền tố phủ định "đừng", "không", "chớ")               │
│   • State Coalescing Engine (Gom mảng lệnh thành 1 Trạng thái Nguyên tử GlobalSystemState)       │
│   • Single Unified Speech Response (Tạo 1 câu đáp giọng nói TTS duy nhất)                        │
│   • Distinct State Memory Swap (Hoán đổi 2 chiều Alt-Tab Toggle g_system_state <-> g_previous)   │
│                                           │ (Thông số mục tiêu Power, Brightness, CCT, Mode)     │
│                                           ▼                                                      │
│ 🔌 TẦNG 3: ĐIỀU KHUYỂN PHẦN CỨNG & NGOẠI VI (MODULE 3)                                           │
│   • Dual LEDC PWM Controller (Warm GPIO 10 / Cool GPIO 11, >5kHz Anti-Flicker, Gamma 2.2 Curve)  │
│   • I2S Audio Output Driver (Loa MAX98357A BCLK:7/LRC:8/DOUT:9, 16kHz 16-bit Mono)               │
│   • I2C Sensor Bus Drivers (BH1750 Addr 0x23 Auto-Dimming, BME280 Addr 0x76 Temp/Hum)            │
│   • GPIO Peripherals (PIR Motion GPIO 3 Auto ON/OFF Timeout, Tactile Key GPIO 0 Interrupt)       │
│   • NVS Flash Persistence Storage (nvs_commit lưu Brightness/CCT/Mode khôi phục khi mất điện)    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔍 2. BÁO CÁO CHI TIẾT CÁC CHỨC NĂNG XỬ LÝ CỦA 3 MODULE CORE

---

### 🎙️ MODULE 1: VOICE INTERACTION & HYBRID EDGE-FIRST NLU ENGINE
> **NỔI BẬT CỐT LÕI:** **Kiến Trúc Định Tuyến Hybrid Edge-First (Edge-First Offloading Router)** chia tách xử lý 2 nhánh song song: **Local Fast Path** và **Local SLM Gateway**.

```text
                                   [Văn Bản Giọng Nói Thô (STT Input)]
                                                    │
                                                    ▼
                                  [Action Verb Clause Splitter]
                           (Phân tách ranh giới động từ hành động)
                                                    │
                                                    ▼
                                    [Bộ Kiểm Tra Loại Câu Lệnh]
                                                   / \
                                                  /   \
  [Mệnh Đề Lệnh Trực Tiếp & Phù Hợp Dict]        /     \        [Câu Hỏi Tư Vấn "nên/gì/sao" OR Không Khớp]
                                                /       \
                                               ▼         ▼
    ┌──────────────────────────────────────────────┐ ┌──────────────────────────────────────────────┐
    │  NHÁNH 1: LOCAL FAST PATH                    │ │  NHÁNH 2: LOCAL SLM GATEWAY                  │
    ├──────────────────────────────────────────────┤ ├──────────────────────────────────────────────┤
    │ • Thuật toán: Rule Match & Levenshtein       │ │ • Thuật toán: Ollama Qwen2.5-1.5B (Local)    │
    │ • Độ trễ: ~1 ms (Chạy trực tiếp RAM)         │ │ • Context Injection: State JSON Current/Prev │
    │ • Chi phí: 0 VNĐ (0 Token)                   │ │ • Timeout: 12.0s ➔ Graceful Fallback Tầng 3  │
    │ • Chức năng: Xử lý 95% câu lệnh ngắn         │ │   (Built-in Advisory AI nếu Ollama Offline)  │
    └──────────────────────┬───────────────────────┘ └──────────────────────┬───────────────────────┘
                           │                                                │
                           └───────────────────────┬────────────────────────┘
                                                   │
                                                   ▼
                                 [Mảng Lệnh Đóng Gói VoiceCommandBatch]
```

#### 1. Chuỗi Xử Lý Âm Thanh Trường Xa (Far-Field Audio Pipeline)
* **Thu Âm I2S phần cứng:** Tiếp nhận luồng PCM 16kHz, 16-bit Mono từ Micro **INMP441** qua giao tiếp I2S (`WS` - GPIO 4, `SCK` - GPIO 5, `SD` - GPIO 6).
* **Software Gain Boost (`MIC_GAIN_BOOST = 8.0`):** Nhân biên độ âm thanh lên 800%, cho phép thu rõ tiếng nói từ khoảng cách xa 1–3 mét.
* **Peak Gain Normalization:** Tính toán biên độ đỉnh của khung âm thanh và chuẩn hóa về mức target (`target_peak = 26000`), khắc phục triệt để hiện tượng thu thều thào hoặc tiếng quá nhỏ khiến STT bị trượt từ.
* **Phân Tích Chỉ Số Vật Lý:** Tính công suất RMS (`pcm_rms`), % Âm lượng thực tế và `Dynamic Confidence Score` (0.0 – 1.0) đưa vào bộ Speech-to-Text.

#### 2. Thuật Toán Phân Tách Đa Mệnh Đề Liên Tục (Action Verb Clause Splitter)
* **Đầu vào:** Chuỗi văn bản giọng nói thô thu được từ STT.
* **Thuật toán ranh giới Động từ (Action Verb Boundary Detection):** Quét câu thoại theo phương pháp Regex phát hiện vị trí các động từ hành động: `bật`, `mở`, `tắt`, `tăng`, `giảm`, `chuyển`, `đổi`, `chỉnh`, `ấm`, `lạnh`, `trắng`, `vàng`.
* **Cơ chế hoạt động:** Bóc tách chuỗi câu dài chứa nhiều lệnh liên tiếp kể cả khi **người dùng nói liền mạch không có từ nối** (*"Bật đèn tăng độ sáng 20% chuyển chế độ học bài giảm 10%"* thành 4 mệnh đề lệnh riêng biệt).

#### 3. Hai Nhánh Xử Lý Chi Tiết Của Kiến Trúc Hybrid Router

##### ⚡ NHÁNH 1: LOCAL FAST PATH (~1ms, 0đ Token)
* **Điều kiện kích hoạt:** Áp dụng cho các mệnh đề điều khiển trực tiếp (bật/tắt, đặt/tăng/giảm độ sáng %, đổi tông màu hay chuyển chế độ chuẩn).
* **Thuật toán xử lý:**
  1. Duyệt danh sách từ điển `COMMAND_DICTIONARY`.
  2. Tính toán tỷ lệ tương đồng chuỗi ký tự bằng thuật toán **Levenshtein Distance** (`difflib.SequenceMatcher`).
  3. Áp dụng cơ chế thưởng điểm (Bonus Score = 0.85) nếu chứa chuỗi con (Substring Match).
  4. Phân định tham số `ParamType`: `PARAM_TYPE_ABSOLUTE` (nếu đặt độ sáng tuyệt đối như *"đặt 30%"*) và `PARAM_TYPE_RELATIVE` (nếu tăng/giảm tương đối như *"tăng 20%"*).
  5. Nếu điểm tin cậy `ratio >= 0.70` (70%), lệnh được trích xuất thành công và trả về kết quả tức thì trong **~1ms** mà không tốn Token hay cần kết nối mạng.

##### 🧠 NHÁNH 2: LOCAL SLM GATEWAY (Ollama Qwen2.5-1.5B) & DỰ PHÒNG TẦNG 3
* **Điều kiện kích hoạt:** Kích hoạt khi văn bản chứa các từ khóa nghi vấn/tư vấn (`nên`, `gì`, `sao`, `thế nào`, `tại sao`, `tư vấn`, `giúp`...) HOẶC khi Nhánh 1 phát hiện có mệnh đề không nhận dạng được (`unrecognized_count > 0`).
* **Thuật toán & Quy trình xử lý:**
  1. **System Prompt Injection:** Server đóng gói Prompt kèm ngữ cảnh hệ thống dạng JSON: Trạng thái hiện tại `g_system_state` + Trạng thái lịch sử `g_previous_state`.
  2. **Truy vấn Ollama Qwen2.5 (Local SLM on CPU):** Gửi Request tới Ollama Local Gateway (`http://localhost:11434/api/generate`) với tham số `temperature = 0.3`, `timeout = 12.0s`.
  3. **Parse JSON Response:** Mô hình AI phân tích nhu cầu chiếu sáng không gian/thời tiết và trả về JSON chuẩn gồm mảng `actions` và câu giải thích giọng nói `speech_response`.
  4. **Graceful Fallback Tầng 3 (Built-in Advisory AI):** Nếu dịch vụ Ollama offline (`check_ollama_online() == False`) hoặc bị Timeout 12.0s, hệ thống lập tức tự động fallback về bộ **Built-in Advisory AI** trên RAM. Bộ AI nội tại này sẽ phân tích ngữ cảnh dựa trên từ khóa môi trường ("âm u", "đọc sách", "mệt mỏi"...) để trả về đúng lệnh điều khiển và câu tư vấn Tiếng Việt mà không bao giờ bị dừng hay đơ hệ thống.

* **Đầu ra Module 1:** Mảng đối tượng dữ liệu cấu trúc C/Python [`VoiceCommandBatch`](file:///d:/smart-lamp/components/voice/include/voice_batch.h) chứa tối đa 8 hành động (`VoiceCommand actions[8]`), độ tin cậy `total_confidence`, văn bản gốc `raw_text`, và câu phản hồi giọng nói `ai_response`.

---

### 🧠 MODULE 2: SYSTEM COORDINATOR & INTERACTION LAYER
> **NỔI BẬT CỐT LÕI:** **Động Cơ Gom Trạng Thái Nguyên Tử (State Coalescing Engine)** và **Bộ Nhớ Hoán Đổi Alt-Tab (Distinct State Memory Swap)**.

```text
[VoiceCommandBatch] ──► [Phonetic Wake Word] ──► [Negation Guard] ──► [State Coalescing Engine] ──► [Alt-Tab Memory Swap]
```

#### 1. Khớp Từ Khóa Kích Hoạt Ngữ Âm & Luồng Tương Tác 2 Bước (Dual-Flow Engine)
* **Phonetic Wake Word Matcher:** Nhận dạng từ khóa qua thuật toán khớp ngữ âm chuẩn Việt hóa, chấp nhận các đọc linh hoạt: `"Hey Shine"`, `"Shine"`, `"Hây Xai"`, `"Hê Xai"`, `"Xi-ne"`, `"Xai ơi"`, `"Xai"`.
* **Single-Shot Flow (Luồng 1 hơi):** Khi phát hiện Wake Word kèm câu lệnh trong 1 câu $\rightarrow$ Trích xuất phần lệnh phía sau và đưa trực tiếp vào bộ gom trạng thái.
* **Two-Step Flow (Luồng 2 bước):** Khi chỉ có từ Wake Word độc lập $\rightarrow$ Gửi lệnh tới loa phát âm báo Beep chime + Kích hoạt Cửa sổ chờ **5.0 giây** (`VOICE_LISTEN_TIMEOUT_MS = 5000`) lắng nghe câu lệnh ở lượt nói kế tiếp.

#### 2. Bộ Lọc Mệnh Đề Phủ Định Boundary Check (Negation Guard)
* **Nguyên lý kiểm tra tiền tố:** Quét cửa sổ 20 ký tự đứng trước các động từ hành động trong mảng lệnh `VoiceCommandBatch`.
* **Cơ chế hủy lệnh:** Nếu phát hiện các tiền tố phủ định (`đừng`, `không`, `chớ`, `không được`, `đừng có`), gán cờ `is_negated = true` và hủy bỏ mệnh đề đó, ngăn không cho thay đổi trạng thái đèn.
* *Minh chứng thực thi:* Câu nói *"Đừng tắt đèn, tăng độ sáng 10%"* sẽ gán `is_negated = true` cho lệnh tắt đèn (giữ nguyên công tắc `Power = True`) và chỉ thực hiện cộng 10% độ sáng.

#### 3. Động Cơ Gom Trạng Thái Cuối Nguyên Tử (State Coalescing Engine)
* **Tiếp nhận & Áp dụng mảng lệnh:** Tiếp nhận mảng 1–8 hành động `VoiceCommand`, duyệt qua từng hành động và tính toán áp dụng đồng thời vào một trạng thái nháp (`draft_state`).
* **Kiểm soát ngưỡng an toàn phần cứng (Hardware Boundary Clamping):**
  * Công tắc `power`: `True` / `False`.
  * Độ sáng `brightness`: Khóa cứng trong dải `0%` đến `100%`.
  * Nhiệt màu `cct`: Khóa cứng trong dải `2400K` (Vàng ấm) đến `6500K` (Trắng lạnh).
* **Cập nhật Nguyên tử (Atomic State Update):** Thay vì gửi nhiều lệnh làm thay đổi đèn nhấp nháy liên tục, toàn bộ các thông số mục tiêu được ghi đè đồng thời vào `GlobalSystemState` đúng **1 lần duy nhất trong 1 chu kỳ**.
* **Single Unified Speech Response:** Tự động ghép nối các biến đổi trạng thái thành đúng **1 câu phản hồi Tiếng Việt ngắn gọn duy nhất** (*"Đã bật đèn, tăng độ sáng 20% và chuyển sang Chế Độ Học Bài cho bạn!"*), gửi luồng âm thanh đến Loa.

#### 4. Bộ Nhớ Hoán Đổi Alt-Tab (Distinct State Memory Swap Engine)
* **Ghi vết snapshot chuẩn (`push_state_snapshot`):** Chỉ lưu snapshot của `g_system_state` vào `g_previous_state` khi có sự thay đổi trạng thái khác biệt rõ rệt (khác Mode, khác Power, hoặc lệch độ sáng >= 5%).
* **Cơ chế Swap 2 chiều:** Khi nhận các câu lệnh khôi phục (*"chế độ cũ"*, *"trở về ban đầu"*, *"quay lại trước đó"*), Module 2 thực hiện hoán đổi vị trí trực tiếp:
  $$\text{Swap}\left(g\_system\_state \longleftrightarrow g\_previous\_state\right)$$
  Khắc phục 100% lỗi bị nhảy về trạng thái Off/Rỗng ngẫu nhiên, cho phép người dùng qua lại giữa 2 ngữ cảnh chiếu sáng gần nhất mượt mà (tương tự phím tắt `Alt + Tab`).

* **Đầu ra Module 2:** Cấu trúc trạng thái mục tiêu chuẩn hóa `GlobalSystemState` (`Power: bool`, `Brightness: 0-100%`, `CCT: 2400-6500K`, `Mode: ID/Name`) cùng câu phản hồi giọng nói TTS.

---

### 🔌 MODULE 3: HARDWARE DRIVERS & PERIPHERALS
> **NỔI BẬT CỐT LÕI:** **Driver Điều Xung Dual LEDC PWM Anti-Flicker & Thuật Toán Phối Màu CCT / Gamma Correction** cùng hệ thống giao tiếp ngoại vi I2S Loa, Bus I2C Cảm Biến và Flash NVS.

```text
                           ┌──► Dual LEDC PWM (LEDC Ch0/Ch1) ──► Warm (GPIO 10) / Cool (GPIO 11)
                           ├──► I2S Output (I2S_NUM_1)      ──► Loa MAX98357A (GPIO 7,8,9)
[GlobalSystemState] ─────  ├──► I2C Bus Master (I2C_NUM_0)   ──► BH1750 (0x23) & BME280 (0x76)
                           ├──► GPIO Interrupts             ──► PIR Motion (GPIO 3) & Key (GPIO 0)
                           └──► NVS Flash SPI Storage       ──► nvs_commit (Save Brightness/CCT)
```

#### 1. Bộ Điều Khiển Đèn LED Dual PWM (Dual LEDC PWM Controller)
* **Sơ đồ chân GPIO:** Kênh LED Warm (Vàng - 2400K) gắn chân **GPIO 10** (`LEDC Channel 0`); Kênh LED Cool (Trắng - 6500K) gắn chân **GPIO 11** (`LEDC Channel 1`).
* **Thuật toán Phối Màu CCT (Color Temperature Mixing Formula):**
  Tính toán tỷ lệ phân bổ công suất giữa 2 kênh Warm và Cool dựa trên nhiệt độ màu CCT ($2400K \le \text{CCT} \le 6500K$):
  $$\text{Ratio}_{\text{cool}} = \frac{\text{CCT} - 2400}{6500 - 2400}, \quad \text{Ratio}_{\text{warm}} = 1.0 - \text{Ratio}_{\text{cool}}$$
* **Chống nháy mắt (Flicker-Free High Frequency):** Khai báo ngoại vi `LEDC` phần cứng của ESP32-S3 ở tần số xung cao **>5 kHz** với độ phân giải 10-bit/12-bit, đảm bảo không bị nháy hình khi quay camera.
* **Đường cong hiệu chỉnh độ sáng Gamma 2.2 (Gamma Correction Curve):**
  Mắt người cảm nhận độ sáng theo dạng phi tuyến. Thuật toán áp dụng hàm Gamma 2.2 trước khi ghi Duty Cycle:
  $$\text{Duty}_{\text{warm}} = \text{Duty}_{\text{max}} \times \left(\frac{\text{Brightness}}{100}\right)^{2.2} \times \text{Ratio}_{\text{warm}}$$
  $$\text{Duty}_{\text{cool}} = \text{Duty}_{\text{max}} \times \left(\frac{\text{Brightness}}{100}\right)^{2.2} \times \text{Ratio}_{\text{cool}}$$
  Giúp ánh sáng tăng/giảm dịu nhẹ, mượt mà và tự nhiên nhất cho mắt.

#### 2. Giao Tiếp Loa & Phát Âm Thanh I2S (I2S Audio Output Driver)
* **Cấu hình phần cứng I2S:** Sử dụng bộ điều khiển `I2S_NUM_1` xuất tín hiệu ra mạch khuếch đại **MAX98357A** (Chân `BCLK` - GPIO 7, `LRC` - GPIO 8, `DOUT` - GPIO 9).
* **Xử lý RingBuffer âm thanh:** Thiết lập tần số lấy mẫu 16kHz, 16-bit Mono. Đóng gói dữ liệu phát âm báo chime Beep 1kHz (khi nhận Wake Word) và phát dữ liệu câu phản hồi TTS từ Server ra loa nội tại của đèn bàn.

#### 3. Driver Bus Cảm Biến Môi Trường I2C (I2C Sensor Bus Master)
* **Cấu hình Bus I2C:** Khai báo `I2C_NUM_0` trên chân `SDA` - GPIO 2 và `SCL` - GPIO 1 với tần số 100kHz.
* **Cảm biến Ánh sáng BH1750 (I2C Addr `0x23`):** Định kỳ đọc cường độ ánh sáng môi trường (Lux). Cung cấp dữ liệu Lux cho thuật toán **Auto-Dimming** tự động tăng sáng khi phòng tối và hạ sáng khi phòng đủ quang năng.
* **Cảm biến Nhiệt độ / Độ ẩm BME280 (I2C Addr `0x76`):** Dùng chung Bus I2C đọc các thông số môi trường phòng.

#### 4. Driver Cảm Biến Hiện Diện & Nút Bấm Vật Lý (GPIO Peripherals)
* **Cảm biến hiện diện PIR / Radar (GPIO 3):** Đọc tín hiệu Digital Input nhận diện người dùng tại bàn. Tự động bật đèn khi người ngồi vào bàn và đếm ngược tự động tắt đèn (Auto-OFF Timeout) khi người dùng rời đi.
* **Nút bấm vật lý (GPIO 0):** Cấu hình ngắt phần cứng (`Hardware Interrupt`) trên chân GPIO 0 có trở kéo nội (`Internal Pull-Up`), hỗ trợ bật/tắt hoặc nhấn giữ xoay núm điều chỉnh thủ công.

#### 5. Bộ Nhớ Lưu Trữ Cấu Hình NVS Flash (Non-Volatile Storage)
* **Xử lý Ghi dữ liệu Flash:** Sử dụng các hàm API ESP-IDF `nvs_open()`, `nvs_set_blob()`, `nvs_commit()` để lưu trữ các biến `Brightness`, `CCT`, `Mode` vào phân vùng Flash SPI của ESP32-S3 ngay khi có sự thay đổi.
* **Khôi phục khi mất điện:** Khi vừa cấp nguồn hoặc khởi động lại vi điều khiển, hàm `nvs_get_blob()` tự động đọc Flash NVS để tái lập chính xác trạng thái cài đặt cũ trước đó mà không bị lùi về mặc định.

---

## 📋 TÓM TẮT THÔNG SỐ VẬN HÀNH & KỸ THUẬT CỦA 3 MODULE

| Tiêu Chí Kỹ Thuật | Module 1 (Voice & NLU Engine) | Module 2 (System Coordinator) | Module 3 (Hardware Drivers) |
| :--- | :--- | :--- | :--- |
| **Nhiệm vụ cốt lõi** | Thu âm PCM ➔ STT ➔ Định tuyến Hybrid AI Router | Nhận mảng lệnh ➔ Gom trạng thái ➔ Alt-Tab Swap | Tiếp nhận trạng thái ➔ Điều khiển PWM/Loa/Sensors |
| **Đầu vào (Input)** | Luồng Audio PCM từ Micro I2S INMP441 | Mảng `VoiceCommandBatch` từ Module 1 | Cấu hình nguyên tử `GlobalSystemState` |
| **Đầu ra (Output)** | Mảng đối tượng `VoiceCommandBatch` (Max 8) | Cấu hình nguyên tử `GlobalSystemState` | LED Duty Cycle, Audio I2S, Lux Data, NVS Flash |
| **Đột phá kỹ thuật** | **Hybrid Router: Fast Path (~1ms) + SLM Ollama** | **State Coalescing Engine & Alt-Tab Memory Swap** | **Dual LEDC PWM >5kHz, Gamma 2.2, I2S MAX98357A** |
| **Giao thức / Phần cứng**| INMP441 I2S (GPIO 4,5,6), Gain Boost 8.0x | Algorithmic Rule Engine & Negation Guard | Dual PWM (GPIO 10,11), I2S Amp (7,8,9), I2C (1,2) |
