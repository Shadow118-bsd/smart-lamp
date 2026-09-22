# 💡 HƯỚNG DẪN SỬ DỤNG — DỰ ÁN ĐÈN BÀN THÔNG MINH (SMART DESK LAMP)

> **Kiến Trúc:** Hybrid Edge-First NLU Engine + ESP32-S3 Firmware + Multi-Intent Action Coalescing  
> **Giao Diện Dashboard:** `http://localhost:8088`  
> **Phiên Bản:** 2.0 (Module 1 & Module 2 Completed)

---

## 🚀 1. Hướng Dẫn Khởi Chạy Nhanh (Quick Start)

Bạn có thể lựa chọn 1 trong 2 phương thức khởi chạy dưới đây:

### 🐋 CÁCH 1: Khởi Chạy Bằng Docker (Khuyên Dùng — 1 Click 100% Không Lỗi)
*Yêu cầu: Máy đã cài đặt [Docker Desktop](https://www.docker.com/products/docker-desktop/).*

1. Mở Terminal tại thư mục gốc dự án và chạy dòng lệnh:
   ```bash
   docker compose up -d
   ```
2. Truy cập trình duyệt Web tại địa chỉ: **`http://localhost:8088`**
3. Để dừng hệ thống:
   ```bash
   docker compose down
   ```

---

### 💻 CÁCH 2: Khởi Chạy Local Python (Windows / Linux / macOS)
*Yêu cầu: Python 3.10+.*

1. **Cài đặt thư viện phụ thuộc:**
   ```bash
   pip install -r scratch/requirements.txt
   ```
2. **Khởi chạy ứng dụng:**
   - Trên **Windows**: Bấm đúp file [`run_app.bat`](file:///d:/smart-lamp/run_app.bat) (hoặc chạy trong CMD).
   - Trên **Linux / macOS**:
     ```bash
     python scratch/udp_receiver.py
     ```
3. Ứng dụng sẽ tự động giải phóng Cổng 8088 và mở trình duyệt **`http://localhost:8088`**.

---

## 🎤 2. Hướng Dẫn Điều Khiển Bằng Giọng Nói (Voice Command Guide)

### 🔊 2.1. Từ Khóa Kích Hoạt (Wake Word Aliases)
Hệ thống tích hợp thuật toán khớp từ khóa ngữ âm Việt hóa, cho phép đọc linh hoạt:
- **Tiếng Anh chuẩn:** `"Hey Shine"`, `"Shine"`
- **Phiên âm Việt hóa:** `"Hây Xai"`, `"Hê Xai"`, `"Xi-ne"`, `"Xai ơi"`, `"Xai"`

---

### 💬 2.2. Hai Luồng Giao Tiếp Tương Tác (Dual Flow)

#### A. Luồng Lệnh 1 Hơi (Single-Shot — Nhanh & Tự Nhiên)
Bạn có thể nói trực tiếp Wake Word kèm câu lệnh trong 1 hơi:
- 🗣️ *"Hey Shine, bật đèn học bài"*
- 🗣️ *"Hây Xai, tăng độ sáng 20% cho tôi"*
- 🔊 **Phản hồi:** Đèn thực thi ngay và phát 1 câu phản hồi TTS duy nhất.

#### B. Luồng Tương Tác 2 Bước (Two-Step — Khi Cần Ngập Ngừng)
1. Bạn nói: 🗣️ *"Hey Shine"* (sau đó im lặng).
2. Đèn phát âm thanh Beep (*"Ting! 💡"*) + bật **Window chờ 5.0 giây**.
3. Bạn tiếp lời: 🗣️ *"Chuyển chế độ đọc sách"*.
4. 🔊 **Phản hồi:** Đèn thực thi và phản hồi giọng nói.

---

### ⚡ 2.3. Các Mẫu Câu Lệnh Đa Mệnh Đề & Tính Năng Độc Đáo

#### 1. Chuỗi Đa Lệnh Nối Tiếp (Multi-Intent Actions Batching)
Bạn có thể ra chuỗi 3–6 lệnh liên tiếp không cần từ nối:
- 🗣️ *"Bật đèn tăng độ sáng 20% chuyển chế độ học bài"*
- 🔊 **Hệ thống xử lý:** Gom cả 3 lệnh thành 1 trạng thái mục tiêu duy nhất và phát 1 câu đáp:  
  *“Đã bật đèn, tăng độ sáng 20% và chuyển sang Chế Độ Học Bài cho bạn!”*

#### 2. Bộ Lọc Câu Phủ Định (Negation Guard)
Hệ thống tự động loại bỏ các lệnh bị phủ định phía trước:
- 🗣️ *"Đừng tắt đèn, tăng độ sáng 10%"* $\rightarrow$ Đèn giữ nguyên trạng thái bật và tăng thêm 10% độ sáng.

#### 3. Khôi Phục Trạng Thái Trước (Alt-Tab Memory Swap)
- 🗣️ *"Chế độ cũ"*, *"Trở về ban đầu"*, *"Quay lại chế độ trước"*
- 🔊 **Hệ thống xử lý:** Hoán đổi 2 chiều mượt mà giữa 2 trạng thái gần nhất (chuẩn phím `Alt + Tab`).

---

## 🎨 3. Danh Sách 8 Chế Độ Đèn Chuẩn (Presets Lighting Modes)

| STT | Tên Chế Độ | Nhiệt Màu (CCT) | Độ Sáng | Ngữ Cảnh Sử Dụng |
| :---: | :--- | :---: | :---: | :--- |
| **1** | **Chế Độ Thư Giãn** | `3000K` (Vàng ấm) | `50%` | Xem phim, nghe nhạc, nghỉ ngơi |
| **2** | **Chế Độ Học Bài** | `4000K` (Trắng trung tính) | `80%` | Tập trung học tập, làm việc khuya |
| **3** | **Chế Độ Đọc Sách** | `3000K` (Vàng dịu) | `70%` | Đọc sách chống mỏi mắt ban đêm |
| **4** | **Chế Độ Ban Đêm** | `2700K` (Vàng ấm nhẹ) | `15%` | Đèn ngủ, dịu mắt ban đêm |
| **5** | **Chế Độ Máy Tính** | `3500K` (Trung tính) | `60%` | Chống chói màn hình laptop/PC |
| **6** | **Chế Độ Thiết Kế** | `5000K` (High-CRI) | `90%` | Vẽ tranh, soi chuẩn màu đồ họa |
| **7** | **Chế Độ Hoàng Hôn** | `2400K` (Vàng đậm) | `35%` | Không gian lãng mạn, ấm cúng |
| **8** | **Chế Độ Tối Đa** | `5500K` (Trắng cực sáng) | `100%` | Công suất tối đa 100% |

---

## 🔌 4. Biên Dịch & Nạp ESP32-S3 Firmware (Cho Phần Cứng)

Nếu bạn có mạch vi điều khiển **ESP32-S3** và linh kiện phần cứng:

### Sơ Đồ Chân GPIO (ESP32-S3 SuperMini / Mini):
- **Micro I2S INMP441:**
  - `WS (LRCK)` $\rightarrow$ GPIO 4
  - `SCK (BCLK)` $\rightarrow$ GPIO 5
  - `SD (DATA)` $\rightarrow$ GPIO 6
- **Amplifier I2S MAX98357A (Loa 3W):**
  - `BCLK` $\rightarrow$ GPIO 7
  - `LRC` $\rightarrow$ GPIO 8
  - `DOUT` $\rightarrow$ GPIO 9

### Biên Dịch & Nạp Code:
1. Mở terminal tại thư mục gốc, cấu hình Wi-Fi trong [`components/voice/include/voice_config.h`](file:///d:/smart-lamp/components/voice/include/voice_config.h).
2. Lệnh build và nạp qua ESP-IDF:
   ```bash
   idf.py build
   idf.py -p COMx flash monitor
   ```

---

## 📊 5. Cấu Trúc Thư Mục Dự Án

```text
smart-lamp/
├── components/          # Thư viện C Firmware cho ESP32-S3 (Voice, Event, I2S, Parser)
├── main/                # Hàm main.c khởi chạy ESP32
├── scratch/             # Server Python Backend, NLU Engine & Automated Test Suite
│   ├── udp_receiver.py  # Live Server UDP, Audio Processor, Web Dashboard & NLU
│   └── test_module2_coordinator.py # Bộ unit test tự động Module 2
├── Dockerfile           # File đóng gói Container Docker
├── docker-compose.yml   # Multi-container Compose (Server + Ollama AI)
├── MODULE_1_REPORT.md   # Báo cáo tổng kết Module 1
├── MODULE_2_REPORT.md   # Báo cáo tổng kết Module 2
├── run_app.bat          # 1-Click Startup Script trên Windows
└── README.md            # Tài liệu hướng dẫn sử dụng dự án
```
