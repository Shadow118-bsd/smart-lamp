# BÁO CÁO TỔNG KẾT TRIỂN KHAI & HOÀN THÀNH MODULE 2: SYSTEM COORDINATOR & INTERACTION LAYER

**Dự án:** Đèn Bàn Thông Minh (Smart Desk Lamp)  
**Module:** Module 2 — Trạm Điều Phối Trung Tâm & Tầng Tương Tác Hệ Thống (System Coordinator & Interaction Layer)  
**Tích Hợp:** Bàn Giao Dữ Liệu Từ Module 1 (Voice & Hybrid NLU) ➔ Điều Phối Trạng Thái Toàn Cục ➔ Chuẩn Bị Tín Hiệu Xuống Module 3 (Hardware Drivers)

---

## 📐 1. Kiến Trúc & Ranh Giới Hệ Thống

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MODULE 1: VOICE & HYBRID NLU                          │
│                                                                             │
│  [Audio Ingest] ──► [Wake Word Phonetic Matcher] ──► [Action Clause Parser]│
│                     ("Hey Shine", "Hây Xai")         (Fast Path & Ollama)   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Mảng VoiceCommandBatch (Max 8 Actions)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                 MODULE 2: SYSTEM COORDINATOR (TRẠM ĐIỀU PHỐI)               │
│                                                                             │
│  - Receiving & Filtering Negated Clauses (Negation Guard Prefix Check)      │
│  - Action Coalescing Engine: Gom mảng lệnh thành 1 Target State duy nhất   │
│  - GlobalSystemState Manager (Power, Brightness 0-100%, CCT 2400-6500K)     │
│  - Distinct Memory Swap (Alt-Tab Toggle g_system_state <-> g_previous_state) │
│  - Single Unified Response Speech Generator (Tạo 1 câu TTS ngắn gọn)       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Target Hardware Signals
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     MODULE 3: HARDWARE DRIVERS (BƯỚC TIẾP THEO)             │
│  - LED PWM Dual Warm/Cool, Ambient Light Sensor, NVS Persistence Flash      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ 2. Các Thành Phần Kỹ Thuật Đã Triển Khai & Hoàn Thành 100%

### 📦 2.1. Chuẩn Hóa Cấu Trúc Dữ Liệu C-Side (`VoiceCommandBatch`)
- **Khắc phục triệt để:** Loại bỏ hạn chế truyền lệnh đơn lẻ bằng việc tạo ra header [`components/voice/include/voice_batch.h`](file:///d:/smart-lamp/components/voice/include/voice_batch.h).
- **Cấu trúc `VoiceCommandBatch`:** Hỗ trợ mảng tối đa 8 hành động (`MAX_ACTIONS_PER_BATCH = 8`) cùng thông số độ tin cậy `total_confidence`, văn bản gốc `raw_text`, và câu phản hồi AI `ai_response`.
- **Phân định thuộc tính `ParamType`:** Bổ sung enum `PARAM_TYPE_ABSOLUTE` (đặt độ sáng 20%) và `PARAM_TYPE_RELATIVE` (tăng độ sáng 20%) trong [`components/voice/include/voice_command.h`](file:///d:/smart-lamp/components/voice/include/voice_command.h).

---

### 🧠 2.2. Động Cơ Gom Trạng Thái Cuối (State Coalescing Engine)
- **Giải quyết bài toán nhiễu âm thanh:** Thay vì phát câu phản hồi cho từng hành động nhỏ lẻ làm loạn tiếng loa, Module 2 tiếp nhận mảng 1–8 hành động và áp dụng đồng thời vào một trạng thái tạm `draft_state`.
- **Cập nhật 1 lần duy nhất (Atomic State Update):** Mọi sự thay đổi về công tắc `power`, độ sáng `brightness`, nhiệt màu `cct`, và chế độ `mode` được cập nhật đồng thời trong 1 chu kỳ duy nhất.
- **Tạo 1 câu đáp TTS duy nhất:** Tự động tổng hợp kết quả biến đổi thành một câu phản hồi Tiếng Việt ngắn gọn, tự nhiên (*"Đã bật đèn, tăng độ sáng 20% và chuyển sang Chế Độ Học Bài cho bạn!"*).

---

### 🛡️ 2.3. Bộ Lọc Phủ Định Boundary Guard (Negation Guard)
- **Quy tắc kiểm tra tiền tố:** Quét cửa sổ 20 ký tự đứng trước các động từ hành động. Nếu phát hiện các tiền tố phủ định (`đừng`, `không`, `chớ`, `không được`, `đừng có`), mệnh đề đó lập tức bị hủy bỏ (`is_negated: true`).
- **Minh chứng kiểm thử:** Câu nói *"Đừng tắt đèn, tăng độ sáng 10%"* được bóc tách chính xác: giữ nguyên trạng thái `Power = True` và thực thi cộng thêm 10% độ sáng.

---

### 🎙️ 2.4. Khớp Từ Khóa Kích Hoạt Ngữ Âm Việt Hóa (Phonetic Wake Word Aliases)
- **Hỗ trợ phát âm đa dạng:** Cho phép nhận diện linh hoạt các biến thể đọc sai từ "Shine / Hey Shine":
  - Standard: `"Hey Shine"`, `"Shine"`
  - Việt hóa: `"Hây Xai"`, `"Hê Xai"`, `"Xi-ne"`, `"Xai ơi"`, `"Xai"`
- **Hỗ trợ Dual-Flow:**
  - **Single-Shot (Nói liền 1 câu):** *"Hey Shine bật đèn học bài"* $\rightarrow$ Xử lý tức thì trong 1 hơi.
  - **Two-Step (2 bước):** Nói *"Hey Shine"* $\rightarrow$ Đèn Beep phát tiếng chime $\rightarrow$ Bật Window 5s chờ người dùng ra lệnh.

---

### 🔄 2.5. Khôi Phục Bộ Nhớ Khác Biệt (Alt-Tab Toggle Memory Swap)
- Khi nhận câu lệnh khôi phục (*"chế độ cũ"*, *"trở về ban đầu"*, *"quay lại chế độ cũ"*), Module 2 thực hiện hoán đổi vị trí trực tiếp giữa `g_system_state` và `g_previous_state`.
- Khắc phục 100% lỗi nhảy về trạng thái rỗng hoặc Off ngẫu nhiên, cho phép quay qua quay lại 2 chế độ gần nhất chuẩn phím tắt `Alt + Tab`.

---

## 📊 3. Bảng Kiểm Thử Thực Tế (Test Verification Results)

Tất cả các kịch bản kiểm thử tự động trong script [`scratch/test_module2_coordinator.py`](file:///d:/smart-lamp/scratch/test_module2_coordinator.py) đã vượt qua **100% test cases**:

| Test Case | Câu Nói Thử Nghiệm | Kết Quả Thực Thi | Trạng Thái |
| :--- | :--- | :--- | :---: |
| **1. Wake Word Match** | `"Hây Xai tăng độ sáng 20%"` | Match được `"hây xai"`, lấy phần lệnh `"tăng độ sáng 20%"` | **PASSED** |
| **2. Multi-Action Batch**| `"Bật đèn tăng độ sáng 20% chuyển chế độ học bài"` | Gom 3 lệnh ➔ Target State: `Power: ON, Brightness: 80%, CCT: 4000K, Mode: Study` | **PASSED** |
| **3. Negation Guard** | `"Đừng tắt đèn, tăng độ sáng 10%"` | Loại bỏ mệnh đề tắt đèn, giữ `Power: ON`, tăng brightness | **PASSED** |
| **4. Absolute vs Relative**| `"Đặt độ sáng 30%"` vs `"Tăng 20%"` | Abs sets `val = 30`, Rel sets `val = +20` | **PASSED** |
| **5. Alt-Tab Memory Swap**| `"Chế độ cũ"` | Swap `g_system_state` $\leftrightarrow$ `g_previous_state` hoàn hảo | **PASSED** |

---

## 🎯 4. Kết Luận Báo Cáo & Sẵn Sàng Sang Module 3

Module 2 (System Coordinator & Interaction Layer) đã hoàn thiện **100% các mục tiêu kiến trúc**, vận hành ổn định bộ điều phối mảng lệnh đa hành động, gom trạng thái cuối duy nhất, lọc câu phủ định và hỗ trợ đầy đủ các từ khóa Wake Word ngữ âm.

Sẵn sàng chuyển giao toàn bộ các thông số trạng thái thực tế (`g_system_state`) sang **Module 3 (Hardware Drivers & Peripherals)** để điều khiển trực tiếp phần cứng đèn vật lý (LED Warm/Cool PWM, Cảm biến ánh sáng, Lưu Flash NVS)!
