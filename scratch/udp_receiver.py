import socket
import threading
import wave
import json
import time
import os
import sys
import http.server
import socketserver
import webbrowser

# Fix Windows console UTF-8 output encoding
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Try importing sounddevice & speech_recognition for local active mic capture
try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    HAS_SR = False

# Configuration
AUDIO_PORT = 12345
EVENT_PORT = 12346
WEB_PORT = 8088
HOST = '0.0.0.0'
RECORDINGS_DIR = 'recordings'
OUTPUT_TRANSCRIPT_FILE = 'transcript_history.txt'

if not os.path.exists(RECORDINGS_DIR):
    os.makedirs(RECORDINGS_DIR)

# Global active recording state
g_is_recording = False
g_current_session_id = None
g_active_pcm_data = bytearray()
g_transcripts = []
g_status = {"wifi_connected": False, "esp_ip": "Waiting...", "is_recording": False, "active_audio_kb": 0.0}

# Module 2 System Coordinator: Global System State & Distinct State Memory (Alt-Tab Toggle)
g_system_state = {
    "power": False, # Default Boot State: Standby OFF
    "brightness": 70, # Saved NVS Memory Brightness
    "cct": 4000, # Saved NVS Memory Color Temp
    "mode": 2,
    "mode_name": "Chế Độ Học Bài"
}

g_previous_state = {
    "power": True,
    "brightness": 70,
    "cct": 4000,
    "mode": 2,
    "mode_name": "Chế Độ Học Bài"
}

# Module 2 Sensor Telemetry & Context Engine Live State
g_session_start_time = time.time()
g_sensor_data = {
    "bme280": {
        "temp_c": 27.5,
        "humidity_pct": 62.0,
        "pressure_hpa": 1013.2,
        "comfort_status": "Lý tưởng (Dễ chịu)",
        "pin_info": "I2C Bus: SDA GPIO 8 | SCL GPIO 9 (Addr 0x76)",
        "status": "ONLINE"
    },
    "vl53l0x": {
        "distance_cm": 42.0,
        "is_user_near": True,
        "is_hand_near": False,
        "proximity_desc": "Đang ngồi gần bàn (< 60cm)",
        "pin_info": "I2C Bus: SDA GPIO 8 | SCL GPIO 9 (Addr 0x29)",
        "status": "ONLINE"
    },
    "pir": {
        "motion": True,
        "presence": True,
        "session_seconds": 1280,
        "session_formatted": "21 phút 20 giây",
        "is_overdue": False,
        "pin_info": "GPIO 7 (Digital Input)",
        "status": "ONLINE"
    },
    "speaker": {
        "status": "ONLINE (Hardware)",
        "pin_info": "I2S Bus: BCLK GPIO 10 | LRC GPIO 11 | DIN GPIO 12",
        "sample_rate": 16000
    },
    "oled": {
        "status": "ONLINE",
        "resolution": "128x64",
        "driver": "SSD1306",
        "pin_info": "I2C Bus: SDA GPIO 8 | SCL GPIO 9 (Addr 0x3C)"
    },
    "context_engine": {
        "user_state": "STUDYING (Đang ngồi học bài)",
        "env_summary": "Nhiệt độ phòng mát mẻ & Ánh sáng môi trường ổn định",
        "suggested_mode": "Chế Độ Học Bài",
        "suggested_mode_id": 2,
        "suggested_brightness": 80,
        "suggested_cct": 4000,
        "health_alert": "Bình thường (Đã học 21 phút)",
        "recommendation_text": "Phát hiện người dùng đang ngồi học. Đề xuất Chế Độ Học Bài (80%, 4000K) để bảo vệ mắt tối ưu."
    }
}

g_last_telemetry_time = 0.0

def get_current_sensor_telemetry():
    global g_sensor_data, g_session_start_time, g_last_telemetry_time
    # If no live hardware packet in last 3 seconds, keep current state or format session
    if time.time() - g_last_telemetry_time > 3.0:
        elapsed = g_sensor_data["pir"]["session_seconds"]
        mins = elapsed // 60
        secs = elapsed % 60
        g_sensor_data["pir"]["session_formatted"] = f"{mins} phút {secs} giây"
        g_sensor_data["pir"]["is_overdue"] = (elapsed >= 45 * 60)
        
    return g_sensor_data

g_latest_waveform_samples = [0.0] * 64 # Live 64-point normalized PCM audio waveform

def push_state_snapshot():
    """
    Saves a snapshot of g_system_state into g_previous_state BEFORE mutating to a new distinct state.
    Only updates when the prior state is distinctly different.
    """
    global g_previous_state, g_system_state
    if (g_previous_state.get("mode") != g_system_state["mode"] or
        g_previous_state.get("power") != g_system_state["power"] or
        abs(g_previous_state.get("brightness", 0) - g_system_state["brightness"]) >= 5):
        g_previous_state = dict(g_system_state)

NEGATION_PREFIXES = ["đừng", "không", "chớ", "không được", "đừng có", "chớ có"]
WAKE_WORD_PATTERNS = [
    r"\bhey\s+shine\b",
    r"\bshine\b",
    r"\bhây\s+xai\b",
    r"\bhê\s+xai\b",
    r"\bxi\s*ne\b",
    r"\bxai\s+ơi\b",
    r"\bxai\b"
]

def check_wake_word(speech_text):
    text_lower = speech_text.lower().strip()
    for pat in WAKE_WORD_PATTERNS:
        match = re.search(pat, text_lower)
        if match:
            wake_matched = match.group(0)
            remaining = text_lower[match.end():].strip()
            remaining = re.sub(r"^[,\.\s\-\?\!]+", "", remaining).strip()
            return True, remaining, wake_matched
    return False, text_lower, None

def update_system_state(actions):
    """
    Module 2 Event Coordinator: Applies VoiceCommands to Global System State.
    Uses State Coalescing Engine & Alt-Tab Memory Swap (CMD 10).
    Returns a unified response speech string.
    """
    global g_system_state, g_previous_state

    if not actions:
        return "Chưa nhận diện được hành động nào."

    # Check for Alt-Tab Revert Command (cmd == 10 or 'cũ'/'trở về')
    has_revert = any(
        (act.get("cmd") == 10 or
         "cũ" in act.get("clause", "").lower() or
         "trở về" in act.get("clause", "").lower() or
         "quay lại" in act.get("clause", "").lower()) and
        not act.get("is_negated")
        for act in actions
    )

    if has_revert:
        temp = dict(g_system_state)
        g_system_state.clear()
        g_system_state.update(g_previous_state)
        g_previous_state = temp

        prev_name = g_system_state.get("mode_name", "Trạng Thái Trước")
        speech = f"Tôi đã khôi phục lại {prev_name} (Độ sáng {g_system_state.get('brightness')}%, {g_system_state.get('cct')}K) cho bạn!"
        for act in actions:
            act["cmd"] = 10
            act["intent_name"] = f"Khôi Phục {prev_name}"
        return speech

    push_state_snapshot()
    applied_descriptions = []

    for act in actions:
        if act.get("is_negated") or act.get("ignore"):
            continue

        cmd = act.get("cmd", 0)
        val = act.get("val", 0)
        mode = act.get("mode", 0)
        clause_str = act.get("clause", "").lower()

        # Robustly determine param_type
        if "param_type" in act and act["param_type"]:
            param_type = act["param_type"]
        elif any(w in clause_str for w in ["tăng", "giảm", "thêm", "bớt", "hơn", "nữa", "lên", "xuống"]) or val < 0:
            param_type = "RELATIVE"
        else:
            param_type = "ABSOLUTE"

        if cmd == 1:
            g_system_state["power"] = True
            applied_descriptions.append("bật đèn")
        elif cmd == 2:
            g_system_state["power"] = False
            applied_descriptions.append("tắt đèn")
        elif cmd == 3:
            # Auto-turn on lamp when user commands brightness
            g_system_state["power"] = True

            if param_type == "RELATIVE" or any(w in clause_str for w in ["tăng", "giảm", "thêm", "bớt"]) or val < 0:
                current_br = g_system_state.get("brightness", 70)
                if current_br <= 0:
                    current_br = g_previous_state.get("brightness", 70)
                    if current_br <= 0:
                        current_br = 70

                new_br = max(5, min(100, current_br + val))
                g_system_state["brightness"] = new_br
                action_text = "tăng" if val > 0 else "giảm"
                applied_descriptions.append(f"{action_text} độ sáng {abs(val)}% (xuống {new_br}%)" if val < 0 else f"{action_text} độ sáng {abs(val)}% (lên {new_br}%)")
            else:
                new_br = max(0, min(100, abs(val)))
                g_system_state["brightness"] = new_br
                applied_descriptions.append(f"đặt độ sáng {new_br}%")
        elif cmd == 7:
            g_system_state["cct"] = max(2400, min(6500, g_system_state["cct"] - 500))
            applied_descriptions.append("chỉnh màu ấm hơn")
        elif cmd == 8:
            g_system_state["cct"] = max(2400, min(6500, g_system_state["cct"] + 500))
            applied_descriptions.append("chỉnh màu trắng hơn")
        elif cmd == 9:
            g_system_state["mode"] = mode
            if mode == 1:
                g_system_state["mode_name"] = "Chế Độ Thư Giãn"
                g_system_state["cct"], g_system_state["brightness"] = 3000, 50
            elif mode == 2:
                g_system_state["mode_name"] = "Chế Độ Học Bài"
                g_system_state["cct"], g_system_state["brightness"] = 4000, 80
            elif mode == 3:
                g_system_state["mode_name"] = "Chế Độ Đọc Sách"
                g_system_state["cct"], g_system_state["brightness"] = 3000, 70
            elif mode == 4:
                g_system_state["mode_name"] = "Chế Độ Ban Đêm"
                g_system_state["cct"], g_system_state["brightness"] = 2700, 15
            elif mode == 5:
                g_system_state["mode_name"] = "Chế Độ Dùng Máy Tính"
                g_system_state["cct"], g_system_state["brightness"] = 3500, 60
            elif mode == 6:
                g_system_state["mode_name"] = "Chế Độ Thiết Kế / High-CRI"
                g_system_state["cct"], g_system_state["brightness"] = 5000, 90
            elif mode == 7:
                g_system_state["mode_name"] = "Chế Độ Hoàng Hôn"
                g_system_state["cct"], g_system_state["brightness"] = 2400, 35
            elif mode == 8:
                g_system_state["mode_name"] = "Chế Độ Tối Đa 100%"
                g_system_state["cct"], g_system_state["brightness"] = 5500, 100

            applied_descriptions.append(f"chuyển sang {g_system_state['mode_name']}")

    if not applied_descriptions:
        return "Đã nhận câu lệnh của bạn."
    elif len(applied_descriptions) == 1:
        return f"Đã {applied_descriptions[0]} cho bạn!"
    else:
        desc_summary = ", ".join(applied_descriptions[:-1]) + " và " + applied_descriptions[-1]
        return f"Đã {desc_summary} cho bạn!"

print("=========================================================")
print("  Smart Lamp Interactive Voice & Audio Analysis Server   ")
print("=========================================================")

import math
import struct
import re

def calculate_combined_confidence(raw_api_confidence, pcm_rms):
    """
    Combines AI Speech API probability with Physical Audio RMS Energy factor (SNR/Volume).
    Supports Far-Field distant speech capture.
    """
    if pcm_rms < 15:
        signal_factor = 0.3
    elif pcm_rms < 100: # Far-field distant speech (RMS 15-100) -> Factor 0.65 - 0.85
        signal_factor = 0.65 + ((pcm_rms - 15) / 85.0) * 0.20
    elif pcm_rms < 1000: # Near speech (RMS 100-1000) -> Factor 0.85 - 0.95
        signal_factor = 0.85 + ((pcm_rms - 100) / 900.0) * 0.10
    else: # Close loud speech (RMS > 1000) -> Factor 0.95 - 1.00
        signal_factor = 0.95 + min(0.05, ((pcm_rms - 1000) / 3000.0) * 0.05)

    combined = raw_api_confidence * signal_factor
    return round(max(5.0, min(99.5, combined)), 1)


def calculate_audio_metrics(pcm_bytes):
    """
    Calculate real RMS volume amplitude, Peak Level, and SNR-based Signal Confidence Score (0-100%)
    from 16-bit 16kHz PCM audio bytes.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return 0.0, 0.0, 0.0

    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f"{num_samples}h", pcm_bytes[:num_samples * 2])

    if not samples:
        return 0.0, 0.0, 0.0

    sum_squares = sum(float(s) * float(s) for s in samples)
    rms = math.sqrt(sum_squares / len(samples))
    volume_percent = min(100.0, round((rms / 2500.0) * 100.0, 1))

    # Dynamic Confidence calculation based on Signal-to-Noise Ratio & Energy
    if rms < 15:
        confidence = round(max(5.0, (rms / 15.0) * 20.0), 1)
    elif rms < 100:
        confidence = round(50.0 + ((rms - 15) / (100 - 15)) * 30.0, 1)
    elif rms < 1000:
        confidence = round(80.0 + ((rms - 100) / (1000 - 100)) * 15.0, 1)
    else:
        confidence = round(95.0 + min(4.5, ((rms - 1000) / 10000.0) * 4.5), 1)

    return round(rms, 1), volume_percent, confidence


def normalize_pcm_gain(pcm_bytes, target_peak=24000):
    """
    Boosts PCM 16-bit audio amplitude so peak volume reaches target_peak (75% max int16).
    Prevents quiet recording files while avoiding clipping.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return pcm_bytes

    num_samples = len(pcm_bytes) // 2
    samples = struct.unpack(f"{num_samples}h", pcm_bytes[:num_samples * 2])
    max_sample = max(abs(s) for s in samples)

    if max_sample < 50 or max_sample >= target_peak:
        return pcm_bytes

    factor = target_peak / float(max_sample)
    normalized = [int(max(-32768, min(32767, s * factor))) for s in samples]
    return struct.pack(f"{len(normalized)}h", *normalized)


def extract_waveform_samples(pcm_bytes, num_points=64):
    """
    Extracts downsampled normalized float samples (-1.0 to 1.0) from 16-bit PCM bytes
    for real-time HTML5 Oscilloscope Visualizer rendering.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return [0.0] * num_points

    num_samples = len(pcm_bytes) // 2
    if num_samples < num_points:
        samples = struct.unpack(f"{num_samples}h", pcm_bytes[:num_samples * 2])
        res = [round(s / 32768.0, 3) for s in samples]
        return res + [0.0] * (num_points - len(res))

    step = num_samples // num_points
    res = []
    for i in range(num_points):
        offset = i * step * 2
        chunk = pcm_bytes[offset:offset + 2]
        if len(chunk) == 2:
            val = struct.unpack("h", chunk)[0]
            res.append(round(val / 32768.0, 3))
        else:
            res.append(0.0)
    return res



import difflib

COMMAND_DICTIONARY = [
    # (Phrases List, CmdType, DefaultVal, DefaultMode, DisplayName)
    (["bật đèn", "mở đèn", "sáng đèn", "bật sáng", "cho đèn sáng", "bặt đèn", "bật lên"], 1, 0, 0, "Bật Đèn"),
    (["tắt đèn", "tắt đi", "tắt hết", "tắt sáng", "tắt đền", "tắt máy"], 2, 0, 0, "Tắt Đèn"),
    
    # Revert / History Stack State Restorations (CMD 10)
    (["chế độ cũ", "chuyển lại chế độ cũ", "trở về chế độ cũ", "quay lại chế độ cũ", "trở về trạng thái ban đầu", "khôi phục trạng thái", "trở về ban đầu", "chế độ trước", "quay lại ban đầu", "về chế độ cũ"], 10, 0, 0, "Khôi Phục Trạng Thái Trước"),

    # 8 Rich Dynamic Modes
    (["thư giãn", "chế độ thư giãn", "nghỉ ngơi", "xem phim"], 9, 0, 1, "Chế Độ Thư Giãn (3000K, 50%)"),
    (["học", "chế độ học", "học bài", "làm việc", "chế độ làm việc", "tập trung"], 9, 0, 2, "Chế Độ Học Bài (4000K, 80%)"),
    (["đọc", "chế độ đọc", "đọc sách", "chế độ đọc sách"], 9, 0, 3, "Chế Độ Đọc Sách (3000K, 70%)"),
    (["ngủ", "chế độ ngủ", "ban đêm", "đèn ngủ", "chế độ ban đêm"], 9, 0, 4, "Chế Độ Ban Đêm (2700K, 15%)"),
    (["máy tính", "dùng máy tính", "màn hình", "chống chói"], 9, 0, 5, "Chế Độ Dùng Máy Tính (3500K, 60%)"),
    (["vẽ tranh", "thiết kế", "đồ họa", "chụp ảnh"], 9, 0, 6, "Chế Độ Thiết Kế / High-CRI (5000K, 90%)"),
    (["hoàng hôn", "ấm cúng", "lãng mạn"], 9, 0, 7, "Chế Độ Hoàng Hôn (2400K, 35%)"),
    (["cực sáng", "tối đa", "100%", "hết cỡ"], 9, 0, 8, "Chế Độ Tối Đa 100% (5500K, 100%)"),
    
    # Continuous Controls
    (["đặt độ sáng", "để độ sáng", "chỉnh độ sáng", "độ sáng", "mức sáng", "đặt sáng"], 3, 50, 0, "Đặt Độ Sáng"),
    (["tăng sáng", "sáng hơn", "tăng độ sáng", "sáng thêm", "tăng"], 3, 10, 0, "Tăng Độ Sáng"),
    (["giảm sáng", "tối hơn", "giảm độ sáng", "tối bớt", "giảm"], 3, -10, 0, "Giảm Độ Sáng"),
    (["ấm hơn", "vàng hơn", "tăng màu ấm", "ấm lên", "màu ấm"], 7, 10, 0, "Tăng Màu Vàng Ấm"),
    (["lạnh hơn", "trắng hơn", "tăng màu trắng", "trắng lên", "màu trắng"], 8, 10, 0, "Tăng Màu Trắng Mát"),
]


def parse_vietnamese_command(speech_text):
    text = speech_text.lower().strip()
    if not text:
        return 0, 0, 0, "Chưa có lời nói", 0.0

    # Extract numeric values (e.g. "tăng sáng 20%")
    numbers = re.findall(r'\d+', text)
    custom_val = int(numbers[0]) if numbers else None

    best_match_ratio = 0.0
    best_result = (0, 0, 0, "Lời nói thử nghiệm (Chưa thuộc danh mục đèn)", 0.0)

    for phrases, cmd, val, mode, name in COMMAND_DICTIONARY:
        for phrase in phrases:
            # Fuzzy string similarity ratio (0.0 to 1.0) using Levenshtein distance algorithm
            ratio = difflib.SequenceMatcher(None, phrase, text).ratio()

            # Substring match bonus
            if phrase in text or text in phrase:
                ratio = max(ratio, 0.85)

            if ratio > best_match_ratio:
                best_match_ratio = ratio
                final_val = custom_val if (custom_val is not None and cmd == 3) else val
                if cmd == 3 and ("giảm" in text or "tối" in text) and final_val > 0:
                    final_val = -final_val
                best_result = (cmd, final_val, mode, name, round(ratio * 100.0, 1))

    if best_match_ratio >= 0.70:
        return best_result
    else:
        return 0, 0, 0, "Lời nói thử nghiệm (Chưa thuộc danh mục đèn)", round(best_match_ratio * 100.0, 1)



import urllib.request
import urllib.parse

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:1.5b"


_last_ollama_check = 0.0
_ollama_online_cache = False

def check_ollama_online():
    global _last_ollama_check, _ollama_online_cache
    now = time.time()
    if now - _last_ollama_check < 10.0:
        return _ollama_online_cache
    _last_ollama_check = now
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.05)
        res = s.connect_ex(('127.0.0.1', 11434))
        s.close()
        _ollama_online_cache = (res == 0)
    except Exception:
        _ollama_online_cache = False
    return _ollama_online_cache


def generate_builtin_advisory_response(speech_text):
    """
    Built-in Advisory AI Response Generator when external Ollama is not installed/offline.
    Provides instant intelligent context advice and intent mapping.
    """
    text = speech_text.lower().strip()

    # Revert / Return to previous mode query ("chế độ cũ", "trở về", "trạng thái cũ")
    if "cũ" in text or "trở về" in text or "quay lại" in text or "ban đầu" in text:
        prev_name = g_previous_state.get("mode_name", "Trạng Thái Trước")
        actions = [{
            "clause": speech_text,
            "cmd": 10,
            "val": 0,
            "mode": 0,
            "intent_name": f"Khôi Phục {prev_name}",
            "score": 95.0
        }]
        speech = f"Tôi đã khôi phục lại {prev_name} trước đó cho bạn!"
        return actions, speech


    # Weather / Dim ambient light query ("âm u", "u u")
    if "âm u" in text or "u u" in text or "u mây" in text or "tối trời" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 2,
            "intent_name": "Ánh Sáng Trời Âm U (4000K, 80%)",
            "score": 95.0
        }]
        speech = "Trời âm u thiếu ánh sáng tự nhiên. Bạn nên mở ánh sáng trắng trung tính (4000K) ở mức 80% để duy trì sự tỉnh táo và chống mỏi mắt. Đã bật đèn hỗ trợ cho bạn!"
        return actions, speech

    # Daylight / Bright ambient light query ("trời sáng")
    elif "trời sáng" in text or "sáng rồi" in text or "ban ngày" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 3,
            "val": -30,
            "mode": 0,
            "intent_name": "Tiết Kiệm Điện - Sáng Nhẹ 30%",
            "score": 95.0
        }]
        speech = "Khi xung quanh đã có ánh sáng tự nhiên, bạn chỉ nên giữ độ sáng 30% để bù góc tối trên bàn. Tôi đã điều chỉnh độ sáng 30% cho bạn!"
        return actions, speech

    # Reading advisory
    elif "đọc" in text or "sách" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 3,
            "intent_name": "Chế Độ Đọc Sách",
            "score": 95.0
        }]
        speech = "Khi đọc sách ban đêm, bạn nên dùng ánh sáng vàng ấm (3000K) độ sáng 70% để dịu mắt. Tôi đã tự động chuyển sang Chế độ Đọc sách cho bạn!"
        return actions, speech

    # Studying / Work advisory
    elif "học" in text or "làm việc" in text or "mệt" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 2,
            "intent_name": "Chế Độ Học Bài",
            "score": 95.0
        }]
        speech = "Khi học bài hoặc làm việc khuya, ánh sáng trắng trung tính (4000K) giúp tăng tập trung và chống buồn ngủ. Tôi đã bật Chế độ Học bài!"
        return actions, speech

    # Relaxation / Night advisory
    elif "ngủ" in text or "nghỉ" in text or "ấm cúng" in text or "thư giãn" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 4,
            "intent_name": "Chế Độ Ban Đêm",
            "score": 95.0
        }]
        speech = "Để nghỉ ngơi và thư giãn ban đêm, ánh sáng vàng nhẹ 2700K 20% công suất là phù hợp nhất. Đã chuyển sang Chế độ Ban đêm!"
        return actions, speech

    # Color selection query ("màu gì")
    elif "màu gì" in text or "chọn màu" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 7,
            "val": 10,
            "mode": 0,
            "intent_name": "Chuyển Tông Màu Vàng Ấm (3000K)",
            "score": 92.0
        }]
        speech = "Nên chọn màu vàng ấm (3000K) khi nghỉ ngơi đọc sách, hoặc màu trắng (4000K) khi tập trung học tập. Tôi đã chỉnh ánh sáng phù hợp cho bạn!"
        return actions, speech

    # Brightness adjustment query ("như thế nào", "hợp lý")
    elif "như thế nào" in text or "hợp lý" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 2,
            "intent_name": "Độ Sáng Chuẩn 70% (4000K)",
            "score": 90.0
        }]
        speech = "Khuyến nghị độ sáng hợp lý là 70% với nhiệt độ màu 4000K để bảo vệ thị lực tốt nhất. Tôi đã tự động chỉnh đèn cho bạn!"
        return actions, speech

    actions = [{
        "clause": speech_text,
        "cmd": 1,
        "val": 0,
        "mode": 0,
        "intent_name": "Bật Đèn Tự Động",
        "score": 90.0
    }]
    speech = f"Tôi đã phân tích nhu cầu của bạn: '{speech_text}' và tự động tối ưu hóa ánh sáng đèn cho bạn!"
    return actions, speech


def query_local_slm_intent(speech_text):
    """
    Queries Local SLM Gateway (Ollama Qwen2.5) if online, otherwise falls back to Built-in Advisory AI Generator.
    Returns: (actions_list, speech_response)
    """
    prev_state_summary = g_previous_state
    system_prompt = (
        f"You are an expert AI Smart Lamp Assistant. Current Lamp State: {json.dumps(g_system_state)}, Previous State: {json.dumps(prev_state_summary)}. "
        "Analyze the user's Vietnamese request in detail. "
        f"If user asks to return to previous mode ('chế độ cũ', 'trở về ban đầu'), return action with cmd=10, mode=0, intent_name='Khôi Phục {prev_state_summary.get('mode_name', 'Trạng Thái Trước')}'. "
        "Return ONLY a valid JSON object without markdown or code fences. Format:\n"
        "{\n"
        '  "actions": [\n'
        '    {"clause": "user clause", "cmd": 10, "val": 0, "mode": 0, "intent_name": "Khôi Phục Trạng Thái Trước"}\n'
        '  ],\n'
        '  "speech_response": "Short natural Vietnamese explanation of what mode was restored or adjusted."\n'
        "}\n"
        "Commands guide: CMD 1=Power ON, CMD 2=Power OFF, CMD 3=Set Brightness, CMD 7=CCT Warmer, CMD 8=CCT Cooler, CMD 9=Set Mode, CMD 10=Revert Previous State."
    )


    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\nUser Speech: \"{speech_text}\"\nJSON:",
        "stream": False,
        "options": {"temperature": 0.3, "max_tokens": 250}
    }

    try:
        req = urllib.request.Request(
            OLLAMA_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        # Timeout 12.0 seconds for Ollama LLM text generation on CPU
        with urllib.request.urlopen(req, timeout=12.0) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            response_text = data.get("response", "").strip()

            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                actions = parsed.get("actions", [])
                speech_resp = parsed.get("speech_response", "")
                if actions and speech_resp:
                    return actions, speech_resp
    except Exception as e:
        print(f"[OLLAMA TIMEOUT/ERROR] {e}")

    # Built-in Advisory AI Fallback when Ollama is not installed/online or times out
    return generate_builtin_advisory_response(speech_text)


def parse_multi_intent_speech(speech_text):
    """
    Edge-First Offloading Router (Phương Pháp 3):
    1. Local Fast Path (Rule Engine): Tốn ~1ms cho 95% câu lệnh trực tiếp ("Bật đèn", "Tắt đèn", "Tăng sáng 20%").
    2. Local SLM Gateway (Ollama Qwen2.5): Tự động Offload cho các câu thoại mở, tư vấn ngữ cảnh hoặc câu phức tạp.
    3. Graceful Fallback: Trả về kết quả Rule Engine nếu Ollama offline.
    """
    text = speech_text.lower().strip()
    if not text:
        return []

    # Check if input text is an Advisory/Question Query (e.g., contains "nên", "gì", "sao", "thế nào", "tại sao", "tư vấn")
    question_keywords = [r'\bnên\b', r'\bgì\b', r'\bsao\b', r'\bthế nào\b', r'\bnhư thế nào\b', r'\btại sao\b', r'\btư vấn\b', r'\bhỏi\b', r'\bcó nên\b', r'\bgiúp\b']
    is_question_query = any(re.search(pat, text) for pat in question_keywords)

    # Fast Path Clause Splitter: Split by conjunctions OR Action Verb Boundaries
    pattern = r'[,;]|\b(?:rồi|sau đó|tiếp theo|và|kèm|đồng thời)\b|(?=\b(?:bật|mở|tắt|tăng|giảm|chuyển|đổi|chỉnh|ấm|lạnh|trắng|vàng)\b)'
    raw_clauses = re.split(pattern, text)
    clauses = [c.strip() for c in raw_clauses if c.strip() and len(c.strip()) > 1]

    fast_path_actions = []
    unrecognized_count = 0

    for clause in clauses:
        cmd, val, mode, intent_name, score = parse_vietnamese_command(clause)
        clause_lower = clause.lower()
        param_type = "ABSOLUTE"
        if cmd == 3:
            if any(w in clause_lower for w in ["tăng", "giảm", "thêm", "bớt", "hơn", "nữa"]) or val < 0:
                param_type = "RELATIVE"
            else:
                param_type = "ABSOLUTE"

        if cmd > 0:
            fast_path_actions.append({
                "clause": clause,
                "cmd": cmd,
                "val": val,
                "mode": mode,
                "intent_name": intent_name,
                "score": score,
                "param_type": param_type
            })
        else:
            unrecognized_count += 1
            fast_path_actions.append({
                "clause": clause,
                "cmd": 0,
                "val": 0,
                "mode": 0,
                "intent_name": intent_name,
                "score": score,
                "param_type": "ABSOLUTE"
            })

    # If it is an Advisory/Question Query OR has unrecognized phrases -> Offload to Local SLM (Ollama / Built-in Advisory AI)
    if is_question_query or unrecognized_count > 0:
        slm_actions, slm_speech = query_local_slm_intent(text)
        if slm_actions is not None and len(slm_actions) > 0:
            print(f"[LOCAL SLM OFFLOAD SUCCESS] Speech: '{slm_speech}'")
            engine_label = "Local SLM Ollama (Qwen2.5)" if check_ollama_online() else "Built-in Advisory SLM AI"
            for act in slm_actions:
                act["speech_response"] = slm_speech
                act["engine"] = engine_label
            return slm_actions

    # Direct Commands: Return Fast Path immediately (~1ms latency)
    for act in fast_path_actions:
        act["engine"] = "Local Fast Path (~1ms)"
    return fast_path_actions

g_last_udp_time = 0.0

# Thread 1: Listen for PCM Audio Stream over UDP
def audio_receiver_thread():
    global g_status, g_active_pcm_data, g_last_udp_time, g_latest_waveform_samples

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, AUDIO_PORT))
    except Exception as e:
        print(f"[AUDIO BIND ERROR] {e}")
        return

    try:
        while True:
            data, addr = sock.recvfrom(2048)
            if data:
                g_last_udp_time = time.time()
                if not g_status["wifi_connected"]:
                    g_status["wifi_connected"] = True
                    g_status["esp_ip"] = addr[0]

                g_latest_waveform_samples = extract_waveform_samples(data, 64)

                if g_is_recording:
                    g_active_pcm_data.extend(data)
                    g_status["active_audio_kb"] = round(len(g_active_pcm_data) / 1024.0, 1)

    except Exception as e:
        print(f"[AUDIO THREAD ERROR] {e}")
    finally:
        sock.close()

# Thread 2: Listen for VoiceEvent JSON Notifications
def event_receiver_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
def process_incoming_sensor_telemetry(event_data):
    global g_sensor_data, g_last_telemetry_time
    g_last_telemetry_time = time.time()
    
    if "bme280" in event_data:
        bme = event_data["bme280"]
        g_sensor_data["bme280"]["temp_c"] = float(bme.get("temp_c", g_sensor_data["bme280"]["temp_c"]))
        g_sensor_data["bme280"]["humidity_pct"] = float(bme.get("humidity_pct", g_sensor_data["bme280"]["humidity_pct"]))
        g_sensor_data["bme280"]["pressure_hpa"] = float(bme.get("pressure_hpa", g_sensor_data["bme280"]["pressure_hpa"]))
        is_hw = bme.get("hardware_online", True)
        g_sensor_data["bme280"]["status"] = "ONLINE (Hardware)" if is_hw else "ONLINE"
        
        # Calculate comfort status
        t = g_sensor_data["bme280"]["temp_c"]
        h = g_sensor_data["bme280"]["humidity_pct"]
        if 22 <= t <= 28 and 45 <= h <= 65:
            g_sensor_data["bme280"]["comfort_status"] = "Lý tưởng (Dễ chịu)"
        elif t > 28:
            g_sensor_data["bme280"]["comfort_status"] = "Hơi nóng"
        elif t < 22:
            g_sensor_data["bme280"]["comfort_status"] = "Hơi lạnh"
        else:
            g_sensor_data["bme280"]["comfort_status"] = "Bình thường"

    if "vl53l0x" in event_data:
        vl = event_data["vl53l0x"]
        dist = float(vl.get("distance_cm", g_sensor_data["vl53l0x"]["distance_cm"]))
        g_sensor_data["vl53l0x"]["distance_cm"] = dist
        g_sensor_data["vl53l0x"]["is_user_near"] = (dist < 60.0)
        g_sensor_data["vl53l0x"]["is_hand_near"] = (dist < 15.0)
        is_hw = vl.get("hardware_online", True)
        g_sensor_data["vl53l0x"]["status"] = "ONLINE (Hardware)" if is_hw else "ONLINE"
        
        if dist < 15.0:
            g_sensor_data["vl53l0x"]["proximity_desc"] = "Đưa tay lại gần (< 15cm)"
            g_sensor_data["context_engine"]["user_state"] = "GESTURE (Đang tương tác tay)"
            g_sensor_data["context_engine"]["suggested_mode"] = "Chế Độ Đọc Sách"
            g_sensor_data["context_engine"]["suggested_mode_id"] = 3
            g_sensor_data["context_engine"]["suggested_brightness"] = 70
            g_sensor_data["context_engine"]["suggested_cct"] = 3000
            g_sensor_data["context_engine"]["recommendation_text"] = "Phát hiện tay ở cự ly gần (<15cm). Đề xuất chuyển Chế Độ Đọc Sách dịu mắt."
        elif dist < 60.0:
            g_sensor_data["vl53l0x"]["proximity_desc"] = "Đang ngồi gần bàn (< 60cm)"
            g_sensor_data["context_engine"]["user_state"] = "STUDYING (Đang ngồi học)"
            g_sensor_data["context_engine"]["suggested_mode"] = "Chế Độ Học Bài"
            g_sensor_data["context_engine"]["suggested_mode_id"] = 2
            g_sensor_data["context_engine"]["suggested_brightness"] = 80
            g_sensor_data["context_engine"]["suggested_cct"] = 4000
            g_sensor_data["context_engine"]["recommendation_text"] = "Phát hiện người dùng đang ngồi học (<60cm). Đề xuất Chế Độ Học Bài (80%, 4000K) chống mỏi mắt."
        else:
            g_sensor_data["vl53l0x"]["proximity_desc"] = "Rời khỏi bàn (> 100cm)"
            g_sensor_data["context_engine"]["user_state"] = "AWAY (Vắng mặt)"
            g_sensor_data["context_engine"]["suggested_mode"] = "Tiết Kiệm Năng Lượng"
            g_sensor_data["context_engine"]["suggested_mode_id"] = 4
            g_sensor_data["context_engine"]["suggested_brightness"] = 15
            g_sensor_data["context_engine"]["suggested_cct"] = 2700
            g_sensor_data["context_engine"]["recommendation_text"] = "Không phát hiện người ở cự ly gần. Đề xuất giảm sáng 15% để tiết kiệm điện."

    if "pir" in event_data:
        pir = event_data["pir"]
        g_sensor_data["pir"]["motion"] = bool(pir.get("motion", False))
        g_sensor_data["pir"]["presence"] = bool(pir.get("presence", False))
        sec = int(pir.get("session_sec", g_sensor_data["pir"]["session_seconds"]))
        mins = sec // 60
        s = sec % 60
        g_sensor_data["pir"]["session_seconds"] = sec
        g_sensor_data["pir"]["session_formatted"] = f"{mins} phút {s} giây"
        g_sensor_data["pir"]["is_overdue"] = (sec >= 45 * 60)
        g_sensor_data["pir"]["status"] = "ONLINE"
        
        if g_sensor_data["pir"]["is_overdue"]:
            g_sensor_data["context_engine"]["health_alert"] = f"CẢNH BÁO: Đã ngồi liên tục {mins} phút! Hãy đứng dậy nghỉ ngơi 5 phút."
        else:
            g_sensor_data["context_engine"]["health_alert"] = f"Bình thường (Đã ngồi {mins} phút)"

    if "speaker" in event_data:
        spk = event_data["speaker"]
        g_sensor_data["speaker"]["status"] = spk.get("status", "ONLINE")

    if "oled" in event_data:
        oled = event_data["oled"]
        g_sensor_data["oled"]["status"] = oled.get("status", "ONLINE")

def event_receiver_thread():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, EVENT_PORT))
    except Exception as e:
        print(f"[EVENT BIND ERROR] {e}")
        return

    try:
        while True:
            data, addr = sock.recvfrom(2048)
            if data:
                try:
                    payload = data.decode('utf-8', errors='ignore')
                    event_data = json.loads(payload)

                    if event_data.get("type") == "SENSOR_TELEMETRY" or "bme280" in event_data or "vl53l0x" in event_data or "pir" in event_data:
                        process_incoming_sensor_telemetry(event_data)
                        continue

                    timestamp_str = time.strftime("%H:%M:%S")
                    raw_text = event_data.get('text', '')
                    cmd_type = event_data.get('cmd_type', 0)
                    val = event_data.get('val', 0)
                    mode = event_data.get('mode', 0)
                    confidence = event_data.get('confidence', 0.0)
                    if confidence <= 1.0:
                        confidence *= 100.0

                    item = {
                        "id": str(int(time.time())),
                        "time": timestamp_str,
                        "text": raw_text if raw_text else "Lệnh nhận diện giọng nói",
                        "cmd": cmd_type,
                        "val": val,
                        "mode": mode,
                        "confidence": round(confidence, 1),
                        "wav_file": ""
                    }
                    g_transcripts.insert(0, item)
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"[EVENT THREAD ERROR] {e}")
    finally:
        sock.close()

def serial_receiver_thread():
    """Background listener for USB Serial (COM3) to receive sensor telemetry directly."""
g_serial_active = True
g_serial_obj = None

def serial_receiver_thread():
    """Background listener for USB Serial (COM3) to receive sensor telemetry directly."""
    global g_serial_active, g_serial_obj
    try:
        import serial
    except ImportError:
        return

    port = "COM3"
    while True:
        if not g_serial_active:
            time.sleep(0.5)
            continue
        try:
            g_serial_obj = serial.Serial(port, 115200, timeout=1.0)
            print(f"[SERIAL THREAD] Connected to ESP32-S3 on {port}!")
            while g_serial_active and g_serial_obj and g_serial_obj.is_open:
                line = g_serial_obj.readline().decode('utf-8', errors='ignore').strip()
                if line and "[TELEMETRY]" in line:
                    idx = line.find("[TELEMETRY]")
                    json_str = line[idx + len("[TELEMETRY]"):].strip()
                    try:
                        telemetry_obj = json.loads(json_str)
                        process_incoming_sensor_telemetry(telemetry_obj)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            time.sleep(1.5)
        finally:
            if g_serial_obj:
                try:
                    g_serial_obj.close()
                except Exception:
                    pass
                g_serial_obj = None


try:
    from esp32_flasher import (
        patch_and_flash_wifi,
        get_available_com_ports,
        get_local_ip,
        get_current_wifi_ssid
    )
except ImportError:
    import sys
    sys.path.append(os.path.dirname(__file__))
    from esp32_flasher import (
        patch_and_flash_wifi,
        get_available_com_ports,
        get_local_ip,
        get_current_wifi_ssid
    )

g_speaker_voice_lock = threading.Lock()

def play_voice_on_speaker(text, voice="vi-VN-HoaiMyNeural"):
    """
    Synthesize high-fidelity Vietnamese AI voice using Edge TTS,
    decode directly to 16kHz 16-bit Mono PCM, and stream to ESP32 MAX98357A.
    """
    if not text or not text.strip():
        return
    clean_text = text.strip()

    def _worker():
        global g_serial_obj, g_serial_active
        with g_speaker_voice_lock:
            try:
                import edge_tts
                import miniaudio
                import asyncio

                comm = edge_tts.Communicate(clean_text, voice)
                buf = bytearray()
                async def _fetch():
                    async for c in comm.stream():
                        if c['type'] == 'audio':
                            buf.extend(c['data'])
                asyncio.run(_fetch())

                if len(buf) == 0:
                    return

                # Decode to 16kHz Mono 16-bit PCM
                decoded = miniaudio.decode(bytes(buf), nchannels=1, sample_rate=16000)
                import numpy as np
                raw_samples = np.frombuffer(decoded.samples, dtype=np.int16)
                # Attenuate to 28% volume to completely eliminate MAX98357A Class-D clipping distortion
                clean_samples = (raw_samples * 0.28).astype(np.int16)
                pcm_bytes = clean_samples.tobytes()
                total_bytes = len(pcm_bytes)

                if g_serial_obj and g_serial_obj.is_open:
                    # 1. Clear pending input buffer
                    try:
                        g_serial_obj.reset_input_buffer()
                    except Exception:
                        pass

                    # 2. Send header
                    header = f"[VOICE_START:16000:{total_bytes}]\n".encode('utf-8')
                    g_serial_obj.write(header)
                    g_serial_obj.flush()

                    # 3. Wait for ACK_READY handshake from ESP32
                    t_ack = time.time()
                    while time.time() - t_ack < 1.5:
                        try:
                            line = g_serial_obj.readline().decode('utf-8', errors='ignore').strip()
                            if "ACK_READY" in line:
                                break
                        except Exception:
                            break

                    # 4. Stream binary data in 512-byte chunks with 3ms pacing
                    chunk_size = 512
                    for offset in range(0, total_bytes, chunk_size):
                        chunk = pcm_bytes[offset:offset+chunk_size]
                        g_serial_obj.write(chunk)
                        time.sleep(0.003)

                    g_serial_obj.flush()
                    print(f"[SPEAKER STREAM SUCCESS] Spoke '{clean_text[:40]}...' ({total_bytes} bytes, 0 drop) on MAX98357A!")
            except Exception as e:
                print(f"[SPEAKER STREAM ERROR] {e}")

    threading.Thread(target=_worker, daemon=True).start()

# Thread 3: HTTP Web Server & Interactive API
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        if self.path.startswith('/api/context/apply'):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8'))
                push_state_snapshot()
                if "power" in data:
                    g_system_state["power"] = bool(data["power"])
                if "brightness" in data:
                    g_system_state["brightness"] = max(0, min(100, int(data["brightness"])))
                if "cct" in data:
                    g_system_state["cct"] = max(2400, min(6500, int(data["cct"])))
                if "mode" in data:
                    g_system_state["mode"] = int(data["mode"])
                    g_system_state["mode_name"] = data.get("mode_name", "Chế Độ Học Bài")

                print(f"[CONTEXT APPLY] Applied target state: {g_system_state}")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "system_state": g_system_state}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
            return

        if self.path.startswith('/api/sensors/override'):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8'))
                if "distance_cm" in data:
                    dist = float(data["distance_cm"])
                    g_sensor_data["vl53l0x"]["distance_cm"] = dist
                    g_sensor_data["vl53l0x"]["is_user_near"] = (dist < 60.0)
                    g_sensor_data["vl53l0x"]["is_hand_near"] = (dist < 15.0)
                    if dist < 15.0:
                        g_sensor_data["vl53l0x"]["proximity_desc"] = "Đưa tay lại gần (< 15cm)"
                        g_sensor_data["context_engine"]["user_state"] = "GESTURE (Đang tương tác tay)"
                    elif dist < 60.0:
                        g_sensor_data["vl53l0x"]["proximity_desc"] = "Đang ngồi gần bàn (< 60cm)"
                        g_sensor_data["context_engine"]["user_state"] = "STUDYING (Đang ngồi học)"
                    else:
                        g_sensor_data["vl53l0x"]["proximity_desc"] = "Rời khỏi bàn (> 100cm)"
                        g_sensor_data["context_engine"]["user_state"] = "AWAY (Vắng mặt)"

                if "motion" in data:
                    g_sensor_data["pir"]["motion"] = bool(data["motion"])
                    g_sensor_data["pir"]["presence"] = bool(data["motion"])

                if "temp_c" in data:
                    g_sensor_data["bme280"]["temp_c"] = float(data["temp_c"])
                if "humidity_pct" in data:
                    g_sensor_data["bme280"]["humidity_pct"] = float(data["humidity_pct"])

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "sensors": get_current_sensor_telemetry()}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
            return

        if self.path.startswith('/api/wifi/flash'):
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            global g_serial_active, g_serial_obj
            try:
                data = json.loads(body.decode('utf-8'))
                port = data.get('port', 'COM3')
                ssid = data.get('ssid', '').strip()
                password = data.get('password', '').strip()
                ip = data.get('ip', None)
                if ip:
                    ip = ip.strip()

                print(f"[WIFI API] Temporarily pausing Serial connection on {port} to allow flashing...")
                g_serial_active = False
                if g_serial_obj and g_serial_obj.is_open:
                    try:
                        g_serial_obj.close()
                    except Exception:
                        pass
                    g_serial_obj = None
                time.sleep(1.2) # Allow OS kernel to release COM port handle

                print(f"[WIFI API] Flashing Wi-Fi SSID '{ssid}' via port {port}...")
                result = patch_and_flash_wifi(port=port, new_ssid=ssid, new_pass=password, new_ip=ip)
                
                time.sleep(1.0)
                g_serial_active = True
                print(f"[WIFI API] Resumed Serial listener on {port}.")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                g_serial_active = True
                print(f"[WIFI API ERROR] {e}")
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/speaker/test':
            ok = False
            if g_serial_obj and g_serial_obj.is_open:
                try:
                    g_serial_obj.write(b"PLAY_CHIME\n")
                    g_serial_obj.flush()
                    ok = True
                    print("[SPEAKER API] Sent PLAY_CHIME command to ESP32 over Serial!")
                except Exception as e:
                    print(f"[SPEAKER API ERROR] {e}")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok}).encode('utf-8'))
            return

        if self.path == '/api/speaker/speak':
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode('utf-8'))
                text = data.get('text', '').strip()
                if text:
                    play_voice_on_speaker(text)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode('utf-8'))
            return

    def do_GET(self):
        global g_is_recording, g_active_pcm_data, g_current_session_id, g_latest_waveform_samples

        if self.path.startswith('/api/tts'):
            parsed_url = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_url.query)
            text = params.get('text', [''])[0].strip()
            voice = params.get('voice', ['vi-VN-HoaiMyNeural'])[0].strip()
            if not text:
                self.send_error(400, "Missing text parameter")
                return

            # 1. First Priority: Microsoft Azure Neural AI Voice (Edge TTS - High Quality Human Voice)
            try:
                import asyncio
                import edge_tts
                comm = edge_tts.Communicate(text, voice)
                audio_buffer = bytearray()
                async def generate_neural_audio():
                    async for chunk in comm.stream():
                        if chunk["type"] == "audio":
                            audio_buffer.extend(chunk["data"])
                
                asyncio.run(generate_neural_audio())
                if len(audio_buffer) > 0:
                    self.send_response(200)
                    self.send_header('Content-type', 'audio/mpeg')
                    self.send_header('Content-Length', str(len(audio_buffer)))
                    self.send_header('Cache-Control', 'public, max-age=86400')
                    self.end_headers()
                    self.wfile.write(audio_buffer)
                    return
            except Exception as e:
                print(f"[EDGE-TTS ERROR] {e}")

            # 2. Secondary Fallback: Google Translate Vietnamese TTS
            try:
                encoded = urllib.parse.quote(text)
                req_url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q={encoded}"
                req = urllib.request.Request(req_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                with urllib.request.urlopen(req, timeout=3.0) as resp:
                    data = resp.read()
                    self.send_response(200)
                    self.send_header('Content-type', 'audio/mpeg')
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            except Exception as e2:
                print(f"[GOOGLE TTS FALLBACK ERROR] {e2}")
                self.send_error(500, "TTS generation failed")
                return

        if self.path.startswith('/api/wifi/info'):
            com_ports = get_available_com_ports()
            pc_wifi = get_current_wifi_ssid()
            local_ip = get_local_ip()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "com_ports": com_ports,
                "pc_wifi": pc_wifi,
                "local_ip": local_ip
            }).encode('utf-8'))
            return

        if self.path.startswith('/api/data'):
            g_status["ollama_online"] = check_ollama_online()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {
                "status": g_status,
                "system_state": g_system_state,
                "sensors": get_current_sensor_telemetry(),
                "history_depth": 1 if g_previous_state else 0,
                "waveform_samples": g_latest_waveform_samples if g_is_recording or (time.time() - g_last_udp_time < 2.0) else [0.0] * 64,
                "transcripts": g_transcripts
            }
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        if self.path.startswith('/api/recording/start'):
            g_is_recording = True
            g_current_session_id = time.strftime("%Y%m%d_%H%M%S")
            g_active_pcm_data.clear()
            g_status["is_recording"] = True
            g_status["active_audio_kb"] = 0.0

            # Start local microphone stream if sounddevice is available and ESP32 is not streaming UDP
            if HAS_SOUNDDEVICE:
                def local_mic_record():
                    try:
                        MIC_GAIN_BOOST = 8.0 # 800% Software Gain Amplification for Far-Field Laptop Mic
                        def mic_callback(indata, frames, time_info, status):
                            global g_latest_waveform_samples
                            if g_is_recording and (time.time() - g_last_udp_time > 2.0):
                                boosted = np.clip(indata * MIC_GAIN_BOOST, -1.0, 1.0)
                                pcm_bytes = (boosted * 32767).astype('int16').tobytes()
                                g_latest_waveform_samples = extract_waveform_samples(pcm_bytes, 64)
                                g_active_pcm_data.extend(pcm_bytes)
                                g_status["active_audio_kb"] = round(len(g_active_pcm_data) / 1024.0, 1)

                        with sd.InputStream(samplerate=16000, channels=1, dtype='float32',
                                            blocksize=512, callback=mic_callback):
                            while g_is_recording:
                                time.sleep(0.05)
                    except Exception as e:
                        pass
                threading.Thread(target=local_mic_record, daemon=True).start()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "session_id": g_current_session_id}).encode('utf-8'))
            return

        if self.path.startswith('/api/recording/stop'):
            g_is_recording = False
            g_status["is_recording"] = False

            filename = f"rec_{g_current_session_id if g_current_session_id else int(time.time())}.wav"
            filepath = os.path.join(RECORDINGS_DIR, filename)

            # 1. Measure RAW physical audio metrics BEFORE software gain boost
            raw_pcm = bytes(g_active_pcm_data)
            raw_rms, raw_vol, calc_confidence = calculate_audio_metrics(raw_pcm)

            # 2. Apply Peak Gain Normalization for STT & File Save
            processed_pcm = normalize_pcm_gain(raw_pcm, target_peak=26000)

            # Save PCM data to WAV file
            if len(raw_pcm) > 0:
                wav_file = wave.open(filepath, 'wb')
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(processed_pcm if raw_rms >= 15 else raw_pcm)
                wav_file.close()

            is_silence = False
            recognized_text = ""
            confidence = 0.0

            # Guard: check for silence / no speech recorded
            if len(raw_pcm) == 0 or raw_rms < 18:
                is_silence = True
            else:
                # Perform speech-to-text recognition if SpeechRecognition is installed & audio is not silent
                if HAS_SR:
                    try:
                        r = sr.Recognizer()
                        r.energy_threshold = 50
                        r.dynamic_energy_threshold = True
                        audio_data = sr.AudioData(processed_pcm, 16000, 2)
                        
                        raw_res = r.recognize_google(audio_data, language="vi-VN", show_all=True)
                        
                        if isinstance(raw_res, dict) and "alternative" in raw_res and len(raw_res["alternative"]) > 0:
                            best_match = raw_res["alternative"][0]
                            cand = best_match.get("transcript", "").strip()
                            if cand:
                                recognized_text = cand
                                if "confidence" in best_match and best_match["confidence"] is not None:
                                    raw_api_conf = float(best_match["confidence"]) * 100.0
                                    confidence = calculate_combined_confidence(raw_api_conf, raw_rms)
                                else:
                                    confidence = calc_confidence
                            else:
                                is_silence = True
                        elif isinstance(raw_res, str) and raw_res.strip():
                            recognized_text = raw_res.strip()
                            confidence = calc_confidence
                        else:
                            is_silence = True
                    except sr.UnknownValueError:
                        is_silence = True
                    except Exception as e:
                        print(f"[SR ERROR] {e}")
                        is_silence = True
                else:
                    is_silence = True

            if is_silence or not recognized_text:
                text_result = "Không thu được"
                cmd_type, val, mode = 0, 0, 0
                intent_name = "Không có lệnh"
                actions = []
                speech_resp = ""
                engine_name = "Không có lệnh"
                confidence = 0.0
            else:
                text_result = recognized_text
                # Extract multi-clause actions & apply to Global System State
                actions = parse_multi_intent_speech(text_result)
                unified_speech = update_system_state(actions)
                speech_resp = unified_speech if unified_speech else ""
                engine_name = "Local Fast Path (~1ms)"
                if actions and len(actions) > 0:
                    primary = actions[0]
                    cmd_type = primary["cmd"]
                    val = primary["val"]
                    mode = primary["mode"]
                    intent_name = primary["intent_name"]
                    if not speech_resp and "speech_response" in primary:
                        speech_resp = primary["speech_response"]
                    engine_name = primary.get("engine", "Local Fast Path (~1ms)")
                else:
                    cmd_type, val, mode, intent_name, _ = parse_vietnamese_command(text_result)
                    param_type = "RELATIVE" if (any(w in text_result.lower() for w in ["tăng", "giảm", "thêm", "bớt"]) or val < 0) else "ABSOLUTE"
                    actions = [{
                        "clause": text_result,
                        "cmd": cmd_type,
                        "val": val,
                        "mode": mode,
                        "intent_name": intent_name,
                        "score": 0.0,
                        "engine": "Fast Path (Fallback)",
                        "param_type": param_type
                    }]

            timestamp_str = time.strftime("%H:%M:%S")
            item = {
                "id": str(int(time.time())),
                "time": timestamp_str,
                "text": text_result,
                "cmd": cmd_type,
                "val": val,
                "mode": mode,
                "intent_name": intent_name,
                "actions": actions,
                "engine": engine_name,
                "speech_response": speech_resp,
                "confidence": confidence,
                "rms": raw_rms,
                "volume": raw_vol,
                "wav_file": filename if len(raw_pcm) > 0 else ""
            }
            g_transcripts.insert(0, item)
            if not is_silence and speech_resp:
                play_voice_on_speaker(speech_resp)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "stopped",
                "is_silent": is_silence,
                "text": text_result,
                "actions": actions,
                "item": item
            }).encode('utf-8'))
            return

        # Serve recorded WAV files
        if self.path.startswith('/recordings/'):
            fname = os.path.basename(self.path)
            fpath = os.path.join(RECORDINGS_DIR, fname)
            if os.path.exists(fpath):
                self.send_response(200)
                self.send_header('Content-type', 'audio/wav')
                self.send_header('Content-Disposition', f'attachment; filename="{fname}"')
                self.send_header('Content-Length', str(os.path.getsize(fpath)))
                self.end_headers()
                with open(fpath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Recording file not found")
            return

        # HTML Interactive Page Output
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()

        html_content = """<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Desk Lamp — Active Voice Analysis Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Outfit', sans-serif; }
        body { background: #0f172a; color: #f8fafc; padding: 24px; min-height: 100vh; }
        .container { max-width: 1050px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 20px 0; border-bottom: 1px solid #334155; }
        .title h1 { font-size: 26px; color: #38bdf8; font-weight: 700; }
        .title p { color: #94a3b8; font-size: 14px; margin-top: 4px; }
        .badge { background: #1e293b; border: 1px solid #38bdf8; color: #38bdf8; padding: 8px 18px; border-radius: 20px; font-weight: 600; font-size: 14px; }
        
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 24px; align-items: stretch; }
        .card { background: #1e293b; border-radius: 20px; padding: 24px; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.4); display: flex; flex-direction: column; }
        .card-title { font-size: 18px; font-weight: 600; color: #f1f5f9; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; }
        
        /* Interactive 1-Click Recording Button */
        .rec-control-box { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 26px 20px; background: #0f172a; border-radius: 16px; border: 1px solid #334155; margin-top: 14px; }
        .btn-rec { background: #ef4444; color: #fff; border: none; padding: 16px 36px; border-radius: 50px; font-size: 16px; font-weight: 700; cursor: pointer; display: flex; align-items: center; gap: 10px; box-shadow: 0 0 20px rgba(239, 68, 68, 0.4); transition: all 0.3s ease; }
        .btn-rec:hover { transform: scale(1.05); box-shadow: 0 0 30px rgba(239, 68, 68, 0.6); }
        .btn-rec.recording { background: #3b82f6; box-shadow: 0 0 25px rgba(59, 130, 246, 0.6); animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }

        .rec-timer { font-size: 28px; font-weight: 700; color: #38bdf8; margin-top: 14px; font-mono: monospace; }
        .rec-hint { font-size: 13px; color: #94a3b8; margin-top: 8px; }

        canvas { width: 100%; height: 75px; background: #020617; border-radius: 10px; margin-top: 14px; border: 1px solid #1e293b; }

        .status-item { display: flex; justify-content: space-between; padding: 9px 0; border-bottom: 1px solid #334155; color: #cbd5e1; font-size: 14px; }
        .status-val { font-weight: 600; color: #38bdf8; }

        .script-list { flex: 1; min-height: 500px; max-height: 560px; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; padding-right: 6px; }
        .script-item { background: #0f172a; padding: 16px; border-radius: 14px; border-left: 4px solid #38bdf8; position: relative; }
        .script-header { display: flex; justify-content: space-between; color: #64748b; font-size: 12px; margin-bottom: 6px; }
        .script-text { font-size: 17px; font-weight: 700; color: #f8fafc; }
        .script-meta { font-size: 13px; color: #94a3b8; margin-top: 6px; }
        
        .play-btn { background: #0284c7; color: #fff; border: none; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: background 0.2s; }
        .play-btn:hover { background: #0369a1; }
        .dl-btn { background: #10b981; color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: background 0.2s; }
        .dl-btn:hover { background: #059669; }
        .storage-info { background: #0f172a; border: 1px dashed #38bdf8; padding: 12px 18px; border-radius: 12px; margin-top: 18px; font-size: 13px; color: #94a3b8; display: flex; align-items: center; justify-content: space-between; }
        .storage-path { color: #38bdf8; font-family: monospace; font-weight: 600; background: #1e293b; padding: 4px 8px; border-radius: 6px; }

        /* Wi-Fi Provisioning Card Styles */
        .wifi-card { background: linear-gradient(135deg, rgba(30, 41, 59, 0.95), rgba(15, 23, 42, 0.95)); border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 20px; padding: 24px 28px; margin-top: 22px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); }
        .wifi-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 24px; margin-top: 16px; }
        .input-group { display: flex; flex-direction: column; gap: 7px; }
        .input-label { font-size: 13px; color: #cbd5e1; font-weight: 600; display: flex; justify-content: space-between; align-items: center; min-height: 20px; }
        .input-field-wrap { display: flex; gap: 8px; align-items: stretch; }
        .input-text { flex: 1; min-width: 0; height: 44px; background: #020617; border: 1px solid #334155; color: #f8fafc; padding: 0 14px; border-radius: 10px; font-size: 14px; outline: none; transition: border-color 0.2s; }
        .input-text:focus { border-color: #38bdf8; }
        .input-btn { height: 44px; min-width: 80px; background: #1e293b; border: 1px solid #38bdf8; color: #38bdf8; padding: 0 16px; border-radius: 10px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.2s; white-space: nowrap; display: inline-flex; align-items: center; justify-content: center; }
        .input-btn:hover { background: #38bdf8; color: #0f172a; }
        .btn-flash { grid-column: 1 / -1; margin-top: 6px; height: 48px; background: linear-gradient(135deg, #0284c7, #2563eb); color: #fff; border: none; border-radius: 12px; font-size: 15px; font-weight: 700; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; box-shadow: 0 0 20px rgba(37, 99, 235, 0.4); transition: all 0.3s; }
        .btn-flash:hover { transform: translateY(-1px); box-shadow: 0 0 30px rgba(56, 189, 248, 0.6); }
        .btn-flash:disabled { background: #475569; cursor: not-allowed; transform: none; box-shadow: none; }
        .wifi-log { grid-column: 1 / -1; margin-top: 10px; background: #020617; border: 1px solid #1e293b; padding: 12px 16px; border-radius: 10px; font-family: monospace; font-size: 12px; color: #94a3b8; min-height: 48px; max-height: 120px; overflow-y: auto; white-space: pre-wrap; line-height: 1.5; }
        /* Sensor Grid & Card Styles */
        .sensor-section-title { font-size: 19px; font-weight: 700; color: #f8fafc; margin: 26px 0 14px 0; display: flex; align-items: center; justify-content: space-between; }
        .sensor-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(310px, 1fr)); gap: 18px; }
        .sensor-card { background: #1e293b; border-radius: 18px; padding: 20px; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3); display: flex; flex-direction: column; position: relative; overflow: hidden; }
        .sensor-card::before { content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px; }
        .sensor-card.bme::before { background: linear-gradient(90deg, #38bdf8, #0284c7); }
        .sensor-card.vl53::before { background: linear-gradient(90deg, #c084fc, #a855f7); }
        .sensor-card.pir::before { background: linear-gradient(90deg, #34d399, #10b981); }
        .sensor-card.speaker::before { background: linear-gradient(90deg, #fbbf24, #f59e0b); }
        .sensor-card.oled::before { background: linear-gradient(90deg, #06b6d4, #0ea5e9); }
        .sensor-card.context { grid-column: 1 / -1; background: linear-gradient(135deg, rgba(30, 41, 59, 0.98), rgba(15, 23, 42, 0.98)); border-color: #fbbf24; }
        .sensor-card.context::before { background: linear-gradient(90deg, #fbbf24, #f59e0b, #ec4899); }
        
        .sensor-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
        .sensor-title { font-size: 16px; font-weight: 700; color: #f1f5f9; display: flex; align-items: center; gap: 8px; }
        .sensor-badge { font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 600; font-family: monospace; background: #0f172a; border: 1px solid #334155; }
        
        .metric-list { display: flex; flex-direction: column; gap: 8px; }
        .metric-row { display: flex; justify-content: space-between; align-items: center; background: #0f172a; padding: 8px 12px; border-radius: 10px; font-size: 13px; }
        .metric-label { color: #94a3b8; }
        .metric-val { font-weight: 700; color: #f8fafc; }
        
        /* Distance Bar */
        .dist-bar-track { width: 100%; height: 10px; background: #0f172a; border-radius: 10px; overflow: hidden; margin-top: 6px; border: 1px solid #334155; }
        .dist-bar-fill { height: 100%; width: 40%; background: linear-gradient(90deg, #c084fc, #38bdf8); border-radius: 10px; transition: width 0.3s ease; }
        
        .btn-apply-ctx { background: linear-gradient(135deg, #f59e0b, #d97706); color: #000; font-weight: 700; border: none; padding: 10px 18px; border-radius: 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: all 0.2s; font-size: 13px; }
        .btn-apply-ctx:hover { transform: scale(1.03); box-shadow: 0 0 15px rgba(245, 158, 11, 0.5); }
        .sim-btn-group { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
        .sim-btn { background: #0f172a; border: 1px solid #334155; color: #cbd5e1; font-size: 11px; padding: 4px 10px; border-radius: 6px; cursor: pointer; transition: all 0.2s; }
        .sim-btn:hover { border-color: #38bdf8; color: #38bdf8; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">
                <h1>Smart Desk Lamp — Voice &amp; Context Dashboard</h1>
                <p>Giao Diện Thu Âm Chủ Động 1-Click, Giám Sát Cảm Biến &amp; Cấu Hình Mạch ESP32</p>
            </div>
            <div class="badge" id="status-badge">Đang chờ kết nối...</div>
        </div>

        <!-- Section 1: Wi-Fi Provisioning for ESP32 -->
        <div class="wifi-card">
            <div class="card-title" style="margin-bottom: 8px;">
                <span>Cấu Hình Wi-Fi &amp; Nạp Vào Mạch ESP32 (COM Provisioning)</span>
                <span style="font-size: 12px; font-weight: 400; color: #94a3b8;">Cắm ESP32 qua cáp USB để nạp</span>
            </div>
            <p style="font-size: 13px; color: #94a3b8; line-height: 1.5;">Nhập thông tin mạng Wi-Fi (2.4GHz) để nạp trực tiếp vào chip ESP32 qua cổng COM. ESP32 sẽ tự khởi động lại và kết nối!</p>
            
            <div class="wifi-grid">
                <!-- Row 1: COM Port & Computer IP -->
                <div class="input-group">
                    <label class="input-label">Cổng COM kết nối:</label>
                    <div class="input-field-wrap">
                        <select id="wifi-port" class="input-text">
                            <option value="COM3">COM3 (ESP32-S3)</option>
                        </select>
                        <button class="input-btn" type="button" onclick="loadWifiInfo()" title="Quét lại cổng COM">Quét</button>
                    </div>
                </div>

                <div class="input-group">
                    <label class="input-label">IP Máy Tính (UDP Dest):</label>
                    <div class="input-field-wrap">
                        <input id="wifi-ip" type="text" class="input-text" placeholder="192.168.1.16" value="192.168.1.16">
                    </div>
                </div>

                <!-- Row 2: Wi-Fi SSID & Password -->
                <div class="input-group">
                    <label class="input-label">
                        <span>Tên Wi-Fi (SSID 2.4GHz):</span>
                        <a href="javascript:void(0)" onclick="usePcWifi()" style="color: #38bdf8; text-decoration: none; font-size: 11px;">[Lấy Wi-Fi máy]</a>
                    </label>
                    <div class="input-field-wrap">
                        <input id="wifi-ssid" type="text" class="input-text" placeholder="Ví dụ: Nemo (2.4G)" value="Be La">
                    </div>
                </div>

                <div class="input-group">
                    <label class="input-label">Mật khẩu Wi-Fi:</label>
                    <div class="input-field-wrap">
                        <input id="wifi-pass" type="password" class="input-text" placeholder="Nhập mật khẩu Wi-Fi">
                        <button class="input-btn" id="btn-toggle-pass" type="button" onclick="toggleWifiPass()">Hiện</button>
                    </div>
                </div>

                <!-- Row 3 & 4: Flash Button & Log -->
                <button class="btn-flash" id="btn-flash-wifi" onclick="flashWifiToEsp32()">
                    <span>GHI CẤU HÌNH VÀO MẠCH ESP32 (FLASH FIRMWARE)</span>
                </button>

                <div class="wifi-log" id="wifi-log">Hệ thống sẵn sàng. Vui lòng nhập Wi-Fi rồi bấm 'GHI CẤU HÌNH VÀO MẠCH ESP32'.</div>
            </div>
        </div>

        <!-- Section 2: Module 2 Dedicated Sensor Blocks & Context Engine -->
        <div class="sensor-section-title">
            <span>MODULE 2: GIÁM SÁT CẢM BIẾN &amp; BỘ NÃO NGỮ CẢNH (CONTEXT ENGINE)</span>
            <span style="font-size: 12px; font-weight: 400; color: #34d399;">Live Telemetry Active</span>
        </div>

        <div class="sensor-grid">
            <!-- Block 1: BME280 Environment -->
            <div class="sensor-card bme">
                <div class="sensor-header">
                    <div class="sensor-title">CẢM BIẾN MÔI TRƯỜNG (BME280)</div>
                    <span class="sensor-badge" style="color: #38bdf8; border-color: #0284c7;" id="bme-badge">I2C: SDA 8 / SCL 9 (0x76)</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Nhiệt độ phòng:</span>
                        <span class="metric-val" id="sensor-temp" style="color: #38bdf8; font-size: 16px;">27.5 °C</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Độ ẩm không khí:</span>
                        <span class="metric-val" id="sensor-hum" style="color: #38bdf8; font-size: 16px;">62.0 %</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Áp suất khí quyển:</span>
                        <span class="metric-val" id="sensor-press">1013.2 hPa</span>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #38bdf8;">
                        <span class="metric-label">Đánh giá tiện nghi:</span>
                        <span class="metric-val" id="sensor-comfort" style="color: #34d399;">Lý tưởng (Dễ chịu)</span>
                    </div>
                </div>
                <div class="sim-btn-group">
                    <span style="font-size: 11px; color: #64748b; align-self: center;">Mô phỏng:</span>
                    <button class="sim-btn" onclick="overrideSensor({temp_c: 31.0, humidity_pct: 78})">Nóng bức (31°C)</button>
                    <button class="sim-btn" onclick="overrideSensor({temp_c: 24.5, humidity_pct: 55})">Mát mẻ (24.5°C)</button>
                </div>
            </div>

            <!-- Block 2: VL53L0X ToF Distance -->
            <div class="sensor-card vl53">
                <div class="sensor-header">
                    <div class="sensor-title">CẢM BIẾN KHOẢNG CÁCH (ToF VL53L0X)</div>
                    <span class="sensor-badge" style="color: #c084fc; border-color: #a855f7;" id="vl53-badge">I2C: SDA 8 / SCL 9 (0x29)</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Khoảng cách người/tay:</span>
                        <span class="metric-val" id="sensor-dist" style="color: #c084fc; font-size: 18px;">42.0 cm</span>
                    </div>
                    <div style="background: #0f172a; padding: 8px 12px; border-radius: 10px;">
                        <div style="display: flex; justify-content: space-between; font-size: 11px; color: #94a3b8;">
                            <span>0 cm</span>
                            <span id="dist-status-text">Ngồi gần bàn</span>
                            <span>120 cm</span>
                        </div>
                        <div class="dist-bar-track">
                            <div class="dist-bar-fill" id="dist-bar" style="width: 35%;"></div>
                        </div>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #c084fc;">
                        <span class="metric-label">Vị trí tương tác:</span>
                        <span class="metric-val" id="sensor-prox" style="color: #e2e8f0;">Đang ngồi gần bàn (&lt; 60cm)</span>
                    </div>
                </div>
                <div class="sim-btn-group">
                    <span style="font-size: 11px; color: #64748b; align-self: center;">Mô phỏng:</span>
                    <button class="sim-btn" onclick="overrideSensor({distance_cm: 12.0})">Đưa tay gần (12cm)</button>
                    <button class="sim-btn" onclick="overrideSensor({distance_cm: 45.0})">Ngồi học (45cm)</button>
                    <button class="sim-btn" onclick="overrideSensor({distance_cm: 110.0})">Rời bàn (110cm)</button>
                </div>
            </div>

            <!-- Block 3: PIR Motion & Session Tracker -->
            <div class="sensor-card pir">
                <div class="sensor-header">
                    <div class="sensor-title">CẢM BIẾN HIỆN DIỆN (PIR MOTION)</div>
                    <span class="sensor-badge" style="color: #34d399; border-color: #10b981;">GPIO 7 (Digital In)</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Tín hiệu chuyển động:</span>
                        <span class="metric-val" id="sensor-motion" style="color: #10b981; font-weight: 700;">ĐANG CÓ CHUYỂN ĐỘNG</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Thời gian ngồi học liên tục:</span>
                        <span class="metric-val" id="sensor-session" style="color: #fbbf24; font-family: monospace; font-size: 15px;">21 phút 20 giây</span>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #10b981;">
                        <span class="metric-label">Cảnh báo sức khỏe mắt:</span>
                        <span class="metric-val" id="sensor-alert" style="color: #34d399;">Bình thường (Chưa quá 45 phút)</span>
                    </div>
                </div>
                <div class="sim-btn-group">
                    <span style="font-size: 11px; color: #64748b; align-self: center;">Mô phỏng:</span>
                    <button class="sim-btn" onclick="overrideSensor({motion: true})">Có người</button>
                    <button class="sim-btn" onclick="overrideSensor({motion: false})">Vắng mặt</button>
                </div>
            </div>

            <!-- Block 4: MAX98357A I2S Audio Amp / Speaker -->
            <div class="sensor-card speaker">
                <div class="sensor-header">
                    <div class="sensor-title">LOA PHẢN HỒI (MAX98357A I2S)</div>
                    <span class="sensor-badge" style="color: #fbbf24; border-color: #f59e0b;" id="speaker-badge">I2S: BCLK 10 / LRC 11 / DIN 12</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Trạng thái phần cứng:</span>
                        <span class="metric-val" id="speaker-status" style="color: #10b981; font-weight: 700;">ONLINE (Hardware)</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Cấu hình chân I2S:</span>
                        <span class="metric-val" style="color: #cbd5e1; font-size: 13px;">BCLK: 10 | LRC: 11 | DIN: 12</span>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #fbbf24;">
                        <span class="metric-label">Chuẩn khuếch đại:</span>
                        <span class="metric-val" style="color: #fbbf24;">Mono 3W Class-D (+12dB)</span>
                    </div>
                </div>
                <div class="sim-btn-group" style="justify-content: flex-end;">
                    <button class="sim-btn" onclick="testSpeakerChime(this)" style="background: #f59e0b; color: #020617; font-weight: 700; border-color: #fbbf24;">Thử Phát Nhạc Chuông Loa</button>
                </div>
            </div>

            <!-- Block 5: OLED Display SSD1306 -->
            <div class="sensor-card oled">
                <div class="sensor-header">
                    <div class="sensor-title">MÀN HÌNH OLED 0.96" (SSD1306)</div>
                    <span class="sensor-badge" style="color: #06b6d4; border-color: #0891b2;" id="oled-badge">I2C: SDA 8 / SCL 9 (0x3C)</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Trạng thái phần cứng:</span>
                        <span class="metric-val" id="oled-status" style="color: #10b981; font-weight: 700;">ONLINE (128x64)</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Tốc độ quét I2C:</span>
                        <span class="metric-val" style="color: #cbd5e1; font-size: 13px;">400kHz Fast I2C (5 FPS)</span>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #06b6d4;">
                        <span class="metric-label">Nội dung hiển thị:</span>
                        <span class="metric-val" style="color: #06b6d4; font-size: 12px;">Nhiệt độ, Độ ẩm, Cự ly, PIR &amp; Bộ đếm 45m</span>
                    </div>
                </div>
            </div>

            <!-- Block 6: Context Engine & Auto Decision Maker -->
            <div class="sensor-card context">
                <div class="sensor-header">
                    <div class="sensor-title" style="color: #fbbf24;">BỘ NÃO NGỮ CẢNH (CONTEXT ENGINE) &amp; RA QUYẾT ĐỊNH TỰ ĐỘNG</div>
                    <span class="sensor-badge" style="color: #fbbf24; border-color: #f59e0b;">Multi-Sensor Fusion Engine</span>
                </div>
                <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 14px; align-items: center;">
                    <div>
                        <div style="font-size: 14px; color: #cbd5e1; line-height: 1.6;" id="ctx-recommendation">
                            "Phát hiện người dùng đang ngồi học bài. Đề xuất kích hoạt <strong>Chế Độ Học Bài (Độ sáng 80%, Nhiệt màu 4000K)</strong> để bảo vệ mắt tối ưu."
                        </div>
                        <div style="margin-top: 8px; font-size: 12px; color: #94a3b8;" id="ctx-env-summary">
                            Ngữ cảnh môi trường: Phòng mát mẻ &amp; Ánh sáng ổn định | Trạng thái: STUDYING
                        </div>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 8px; align-items: flex-end;">
                        <button class="btn-apply-ctx" id="btn-apply-ctx" onclick="applyContextSuggestion()">
                            <span>ÁP DỤNG ĐỀ XUẤT NGAY</span>
                        </button>
                    </div>
                </div>
            </div>
        </div>

        <div class="storage-info">
            <span>Vị trí lưu trữ file ghi âm âm thanh (.WAV) trên máy tính:</span>
            <span class="storage-path">d:/smart-lamp/scratch/recordings/</span>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">Bộ Thu Âm Chủ Động</div>
                
                <div class="status-item"><span>Kết nối ESP32-S3 / Micro:</span><span class="status-val" id="wifi-status">Sẵn sàng</span></div>
                <div class="status-item"><span>Địa chỉ IP Thiết Bị:</span><span class="status-val" id="esp-ip">Localhost / ESP32</span></div>
                <div class="status-item"><span>Dung lượng Đoạn Thu:</span><span class="status-val" id="audio-kb">0 KB</span></div>
                <div class="status-item"><span>Trạng thái Local SLM AI:</span><span class="status-val" id="ollama-status" style="font-weight: 700;">Checking...</span></div>

                <div class="rec-control-box">
                    <button class="btn-rec" id="rec-btn" onclick="toggleRecording()">
                        <span id="rec-text">BẮT ĐẦU THU ÂM (START)</span>
                    </button>
                    <div class="rec-timer" id="rec-timer">00:00</div>
                    <div class="rec-hint" id="rec-hint">Nhấn nút để chủ động thu âm câu nói của bạn</div>

                    <!-- Live Waveform Visualizer Canvas -->
                    <canvas id="waveform"></canvas>
                </div>

                <!-- Module 2 System Coordinator Live State -->
                <div style="margin-top: 16px; background: rgba(16, 185, 129, 0.08); border: 1px solid #10b981; padding: 12px 14px; border-radius: 10px;">
                    <div style="color: #10b981; font-weight: 700; font-size: 14px; margin-bottom: 8px;">MODULE 2: TRẠNG THÁI ĐÈN THỰC TẾ (COORDINATOR)</div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px;">
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Công Tắt: <span id="state-power" style="font-weight:700; color:#10b981;">BẬT</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Độ Sáng: <span id="state-brightness" style="font-weight:700; color:#38bdf8;">70%</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Nhiệt Màu: <span id="state-cct" style="font-weight:700; color:#fbbf24;">4000K</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Chế Độ: <span id="state-mode" style="font-weight:700; color:#c084fc;">Chế Độ Học Bài</span></div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-title">Bản Script Lời Nói Phân Tích (Text Script)</div>
                <div class="script-list" id="script-list">
                    <div style="color: #64748b; text-align: center; padding: 60px 0;">Hãy bấm BẮT ĐẦU THU ÂM ở bên trái để phát bản Script...</div>
                </div>
            </div>
        </div>
    </div>

    <script>
        let isRecording = false;
        let timerInterval = null;
        let seconds = 0;
        let g_detectedPcWifi = '';
        let g_currentContextSuggestion = {
            power: true,
            brightness: 80,
            cct: 4000,
            mode: 2,
            mode_name: 'Chế Độ Học Bài'
        };

        async function overrideSensor(data) {
            try {
                await fetch('/api/sensors/override', {
                    method: 'POST',
                    headers: { 'Content-type': 'application/json' },
                    body: JSON.stringify(data)
                });
                updateDashboard();
            } catch (e) {
                console.error(e);
            }
        }

        async function applyContextSuggestion() {
            try {
                const btn = document.getElementById('btn-apply-ctx');
                btn.disabled = true;
                btn.innerText = 'Đang áp dụng...';
                await fetch('/api/context/apply', {
                    method: 'POST',
                    headers: { 'Content-type': 'application/json' },
                    body: JSON.stringify(g_currentContextSuggestion)
                });
                btn.innerText = 'Đã áp dụng thành công!';
                setTimeout(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<span>ÁP DỤNG ĐỀ XUẤT NGAY</span>';
                }, 1500);
                updateDashboard();
            } catch (e) {
                console.error(e);
            }
        }

        async function loadWifiInfo() {
            try {
                const res = await fetch('/api/wifi/info');
                const data = await res.json();
                
                const portSel = document.getElementById('wifi-port');
                portSel.innerHTML = '';
                if (data.com_ports && data.com_ports.length > 0) {
                    data.com_ports.forEach(p => {
                        const opt = document.createElement('option');
                        opt.value = p;
                        opt.innerText = `${p} (ESP32-S3)`;
                        portSel.appendChild(opt);
                    });
                } else {
                    const opt = document.createElement('option');
                    opt.value = 'COM3';
                    opt.innerText = 'COM3 (Mặc định)';
                    portSel.appendChild(opt);
                }

                if (data.local_ip) {
                    document.getElementById('wifi-ip').value = data.local_ip;
                }

                if (data.pc_wifi) {
                    g_detectedPcWifi = data.pc_wifi;
                }
            } catch (e) {
                console.error('Error loading wifi info:', e);
            }
        }

        function usePcWifi() {
            if (g_detectedPcWifi) {
                // Remove 5G suffix if any to suggest 2.4G equivalent
                let suggested = g_detectedPcWifi;
                if (suggested.endsWith(' 5G') || suggested.endsWith('_5G') || suggested.endsWith('-5G')) {
                    suggested = suggested.replace(/\\s*5G|_5G|-5G/i, '');
                }
                document.getElementById('wifi-ssid').value = suggested;
            }
        }

        function toggleWifiPass() {
            const passInput = document.getElementById('wifi-pass');
            const btn = document.getElementById('btn-toggle-pass');
            if (passInput.type === 'password') {
                passInput.type = 'text';
                if (btn) btn.innerText = 'Ẩn';
            } else {
                passInput.type = 'password';
                if (btn) btn.innerText = 'Hiện';
            }
        }

        async function flashWifiToEsp32() {
            const port = document.getElementById('wifi-port').value;
            const ssid = document.getElementById('wifi-ssid').value.trim();
            const password = document.getElementById('wifi-pass').value.trim();
            const ip = document.getElementById('wifi-ip').value.trim();
            const btn = document.getElementById('btn-flash-wifi');
            const logEl = document.getElementById('wifi-log');

            if (!ssid) {
                alert('Vui lòng nhập Tên Wi-Fi (SSID)!');
                return;
            }

            btn.disabled = true;
            btn.innerHTML = '<span>ĐANG NẠP CẤU HÌNH VÀO ESP32 (Vui lòng đợi ~5s)...</span>';
            logEl.innerText = `[*] Bắt đầu vá cấu hình SSID='${ssid}' vào Firmware...\n[*] Kết nối cổng ${port} và ghi Flash...`;

            try {
                const res = await fetch('/api/wifi/flash', {
                    method: 'POST',
                    headers: { 'Content-type': 'application/json' },
                    body: JSON.stringify({ port, ssid, password, ip })
                });
                const result = await res.json();
                
                if (result.success) {
                    logEl.innerText = `[+] THÀNH CÔNG! ${result.message}\n[+] ESP32 đã khởi động lại và đang kết nối tới Wi-Fi '${ssid}'.\n[+] Bạn có thể nói vào Micro của ESP32 ngay bây giờ!`;
                    logEl.style.color = '#34d399';
                } else {
                    logEl.innerText = `[-] THẤT BẠI: ${result.error || 'Lỗi không xác định'}\n[!] Vui lòng kiểm tra lại cổng COM và thử lại.`;
                    logEl.style.color = '#f87171';
                }
            } catch (e) {
                logEl.innerText = `[-] LỖI GIAO TIẾP: ${e.message}`;
                logEl.style.color = '#f87171';
            } finally {
                btn.disabled = false;
                btn.innerHTML = '<span>GHI CẤU HÌNH VÀO MẠCH ESP32 (FLASH FIRMWARE)</span>';
            }
        }

        function updateTimer() {
            seconds++;
            const m = String(Math.floor(seconds / 60)).padStart(2, '0');
            const s = String(seconds % 60).padStart(2, '0');
            document.getElementById('rec-timer').innerText = `${m}:${s}`;
        }

        async function toggleRecording() {
            const btn = document.getElementById('rec-btn');
            const text = document.getElementById('rec-text');
            const hint = document.getElementById('rec-hint');

            if (!isRecording) {
                // START RECORDING
                const res = await fetch('/api/recording/start');
                isRecording = true;
                btn.classList.add('recording');
                text.innerText = 'DỪNG & PHÂN TÍCH (STOP)';
                hint.innerText = 'Đang thu âm giọng nói của bạn... Hãy nói vào Micro!';
                
                seconds = 0;
                document.getElementById('rec-timer').innerText = '00:00';
                timerInterval = setInterval(updateTimer, 1000);
                drawWaveformAnimation();
            } else {
                // STOP RECORDING
                btn.disabled = true;
                text.innerText = 'ĐANG PHÂN TÍCH GIỌNG NÓI...';
                clearInterval(timerInterval);

                const res = await fetch('/api/recording/stop');
                const data = await res.json();
                
                isRecording = false;
                btn.classList.remove('recording');
                text.innerText = 'BẮT ĐẦU THU ÂM (START)';
                if (data.is_silent || (data.item && data.item.text === "Không thu được")) {
                    hint.innerText = 'Không thu được (Môi trường im lặng). Nhấn nút để thu âm lại.';
                    hint.style.color = '#cbd5e1';
                } else {
                    hint.innerText = 'Nhấn nút để chủ động thu âm câu nói mới';
                    hint.style.color = '#94a3b8';
                }
                btn.disabled = false;

                updateDashboard();
            }
        }

        function playWav(filename) {
            if (!filename) return;
            const audio = new Audio('/recordings/' + filename);
            audio.play();
        }

        // Real-Time HTML5 Audio Oscilloscope & Waveform Visualizer
        const canvas = document.getElementById('waveform');
        const ctx = canvas.getContext('2d');
        let currentWaveformSamples = [];

        function drawWaveformAnimation() {
            if (!canvas) return;
            ctx.fillStyle = '#020617';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            // Oscilloscope Grid Lines
            ctx.lineWidth = 1;
            ctx.strokeStyle = '#1e293b';
            ctx.beginPath();
            ctx.moveTo(0, canvas.height / 2);
            ctx.lineTo(canvas.width, canvas.height / 2);
            ctx.stroke();

            const samples = currentWaveformSamples && currentWaveformSamples.length > 0 ? currentWaveformSamples : [];
            const count = samples.length > 0 ? samples.length : 64;

            // Glowing Neon Line Effect
            ctx.shadowBlur = isRecording ? 12 : 2;
            ctx.shadowColor = '#38bdf8';
            ctx.lineWidth = 2.5;

            const gradient = ctx.createLinearGradient(0, 0, canvas.width, 0);
            gradient.addColorStop(0, '#38bdf8');
            gradient.addColorStop(0.5, '#c084fc');
            gradient.addColorStop(1, '#34d399');
            ctx.strokeStyle = gradient;

            ctx.beginPath();
            const sliceWidth = canvas.width / (count - 1);
            let x = 0;

            for (let i = 0; i < count; i++) {
                let sampleVal = samples.length > 0 ? samples[i] : 0.0;
                if (!isRecording && samples.length === 0) {
                    sampleVal = (Math.sin(i * 0.2 + Date.now() * 0.003) * 0.05);
                }
                const y = (canvas.height / 2) - (sampleVal * (canvas.height * 0.45));
                if (i === 0) ctx.moveTo(x, y);
                else ctx.lineTo(x, y);
                x += sliceWidth;
            }
            ctx.stroke();
            ctx.shadowBlur = 0; // Reset glow
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();

                if (data.waveform_samples) {
                    currentWaveformSamples = data.waveform_samples;
                    drawWaveformAnimation();
                }

                const badge = document.getElementById('status-badge');
                badge.innerText = 'Sẵn sàng thu âm 1-Click';
                badge.style.borderColor = '#22c55e';
                badge.style.color = '#22c55e';
                
                document.getElementById('wifi-status').innerText = 'Sẵn sàng';
                document.getElementById('esp-ip').innerText = data.status.esp_ip;
                document.getElementById('audio-kb').innerText = data.status.active_audio_kb + ' KB';

                if (data.system_state) {
                    const isPowerOn = data.system_state.power;
                    document.getElementById('state-power').innerText = isPowerOn ? 'BẬT' : 'TẮT';
                    document.getElementById('state-power').style.color = isPowerOn ? '#10b981' : '#f87171';
                    document.getElementById('state-brightness').innerText = isPowerOn ? (data.system_state.brightness + '%') : `0% (Bộ nhớ: ${data.system_state.brightness}%)`;
                    document.getElementById('state-cct').innerText = data.system_state.cct + 'K';
                    const historyDepth = data.history_depth ? ` [Stack: ${data.history_depth}]` : '';
                    document.getElementById('state-mode').innerText = data.system_state.mode_name + historyDepth;
                }

                // Update Module 2 Sensors Live Telemetry
                if (data.sensors) {
                    const bme = data.sensors.bme280;
                    if (bme) {
                        document.getElementById('sensor-temp').innerText = bme.temp_c.toFixed(1) + ' °C';
                        document.getElementById('sensor-hum').innerText = bme.humidity_pct.toFixed(1) + ' %';
                        document.getElementById('sensor-press').innerText = bme.pressure_hpa.toFixed(1) + ' hPa';
                        document.getElementById('sensor-comfort').innerText = bme.comfort_status || 'Lý tưởng';
                    }

                    const vl = data.sensors.vl53l0x;
                    if (vl) {
                        document.getElementById('sensor-dist').innerText = vl.distance_cm.toFixed(1) + ' cm';
                        document.getElementById('sensor-prox').innerText = vl.proximity_desc;
                        const barPct = Math.min(100, Math.max(5, (vl.distance_cm / 120.0) * 100));
                        document.getElementById('dist-bar').style.width = barPct + '%';
                        if (vl.distance_cm < 15) {
                            document.getElementById('dist-status-text').innerText = 'Tương tác gần';
                        } else if (vl.distance_cm < 60) {
                            document.getElementById('dist-status-text').innerText = 'Ngồi gần bàn';
                        } else {
                            document.getElementById('dist-status-text').innerText = 'Đứng xa';
                        }
                    }

                    const pir = data.sensors.pir;
                    if (pir) {
                        const motionEl = document.getElementById('sensor-motion');
                        motionEl.innerText = pir.motion ? 'ĐANG CÓ CHUYỂN ĐỘNG' : 'KHÔNG CÓ CHUYỂN ĐỘNG';
                        motionEl.style.color = pir.motion ? '#10b981' : '#94a3b8';
                        document.getElementById('sensor-session').innerText = pir.session_formatted || '0 phút';
                        const alertEl = document.getElementById('sensor-alert');
                        if (pir.is_overdue) {
                            alertEl.innerText = 'Quá 45 phút! Nên nghỉ ngơi';
                            alertEl.style.color = '#ef4444';
                        } else {
                            alertEl.innerText = 'An toàn (Chưa quá 45 phút)';
                            alertEl.style.color = '#34d399';
                        }
                    }

                    const oled = data.sensors.oled;
                    if (oled && document.getElementById('oled-status')) {
                        const oledEl = document.getElementById('oled-status');
                        oledEl.innerText = oled.status || 'ONLINE (128x64)';
                        oledEl.style.color = (oled.status && oled.status.includes('OFFLINE')) ? '#ef4444' : '#10b981';
                    }

                    const ctxEng = data.sensors.context_engine;
                    if (ctxEng) {
                        document.getElementById('ctx-recommendation').innerHTML = `"${ctxEng.recommendation_text || ''}"`;
                        document.getElementById('ctx-env-summary').innerText = `Ngữ cảnh: ${ctxEng.env_summary} | Trạng thái: ${ctxEng.user_state}`;
                        g_currentContextSuggestion = {
                            power: true,
                            brightness: ctxEng.suggested_brightness || 80,
                            cct: ctxEng.suggested_cct || 4000,
                            mode: ctxEng.suggested_mode_id || 2,
                            mode_name: ctxEng.suggested_mode || 'Chế Độ Học Bài'
                        };
                    }
                }

                const ollamaEl = document.getElementById('ollama-status');
                if (data.status.ollama_online) {
                    ollamaEl.innerText = 'Online (Qwen2.5 Sẵn Sàng)';
                    ollamaEl.style.color = '#c084fc';
                } else {
                    ollamaEl.innerText = 'Offline (Bật: ollama run qwen2.5:1.5b)';
                    ollamaEl.style.color = '#f87171';
                }

                const scriptList = document.getElementById('script-list');
                if (data.transcripts.length > 0) {
                    scriptList.innerHTML = data.transcripts.map(item => {
                        if (item.text === "Không thu được") {
                            return `
                                <div class="script-item" style="border-left: 3px solid #64748b; background: rgba(30, 41, 59, 0.4);">
                                    <div class="script-header">
                                        <span>${item.time}</span>
                                        <span style="display: flex; gap: 12px; align-items: center;">
                                            <span style="color: #94a3b8; font-weight: 600; background: rgba(15, 23, 42, 0.6); padding: 2px 8px; border-radius: 4px; border: 1px solid #334155;">Không có lệnh</span>
                                            <span style="color: #64748b; font-weight: 500;">Âm lượng: ${item.volume || 0}% (RMS: ${item.rms || 0})</span>
                                        </span>
                                    </div>
                                    <div class="script-text" style="color: #94a3b8; font-style: italic; font-weight: 500;">"Không thu được"</div>
                                    <div class="script-meta" style="color: #64748b; font-size: 13px;">
                                        <span>(Môi trường im lặng hoặc không phát hiện câu nói — Giữ nguyên trạng thái đèn)</span>
                                    </div>
                                    ${item.wav_file ? `
                                        <div style="margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap;">
                                            <button class="play-btn" onclick="playWav('${item.wav_file}')">Nghe lại đoạn thu</button>
                                            <a class="dl-btn" href="/recordings/${item.wav_file}" download="${item.wav_file}">Tải file WAV về máy</a>
                                        </div>
                                    ` : ''}
                                </div>
                            `;
                        }

                        return `
                        <div class="script-item">
                            <div class="script-header">
                                <span>${item.time}</span>
                                <span style="display: flex; gap: 12px; align-items: center;">
                                    <span style="color: ${item.engine && item.engine.includes('Ollama') ? '#c084fc' : '#34d399'}; font-weight: 700; background: rgba(15, 23, 42, 0.6); padding: 2px 8px; border-radius: 4px; border: 1px solid ${item.engine && item.engine.includes('Ollama') ? '#a855f7' : '#059669'};">Engine: ${item.engine || 'Fast Path (~1ms)'}</span>
                                    <span style="color: #a855f7; font-weight: 600;">Âm lượng: ${item.volume || 0}% (RMS: ${item.rms || 0})</span>
                                    <span style="color: #38bdf8; font-weight: 600;">Độ tin cậy: ${item.confidence}%</span>
                                </span>
                            </div>
                            <div class="script-text">"${item.text}"</div>
                            <div class="script-meta">
                                ${(item.actions && item.actions.length > 1) ? `
                                    <div style="display: flex; flex-direction: column; gap: 4px; margin-top: 4px;">
                                        <div style="color: #38bdf8; font-weight: 700;">Chuỗi Lệnh Đa Mệnh Đề (${item.actions.length} lệnh):</div>
                                        ${item.actions.map((act, idx) => `
                                            <div style="background: #020617; padding: 6px 10px; border-radius: 6px; font-size: 13px; border-left: 3px solid #10b981; color: #cbd5e1;">
                                                <span style="color: #10b981; font-weight: 600;">Lệnh ${idx + 1}:</span> "${act.clause}" ➔ 
                                                <span style="color: #38bdf8; font-weight: 700;">${act.intent_name}</span> (ID: ${act.cmd} | Val: ${act.val} | Mode: ${act.mode})
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : `
                                    <span style="color: #38bdf8; font-weight: 700;">Ý định lệnh: ${item.intent_name || 'Lệnh thử nghiệm'}</span> | 
                                    <span>ID: ${item.cmd}</span> | <span>Tham số: ${item.val}</span> | <span>Mode: ${item.mode}</span>
                                `}
                            </div>
                            ${item.speech_response ? `
                                <div style="margin-top: 8px; background: rgba(168, 85, 247, 0.15); border: 1px solid #a855f7; padding: 10px 14px; border-radius: 8px; color: #f1f5f9; font-size: 14px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                        <span><strong style="color: #c084fc;">AI Phản Hồi Giọng Nói (${item.engine || 'Local SLM'}):</strong></span>
                                        <button onclick="speakAiText(this.getAttribute('data-speech'), this)" data-speech="${item.speech_response.replace(/"/g, '&quot;')}" style="background: #a855f7; color: white; border: none; padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">Đọc Giọng Nói AI</button>
                                    </div>
                                    <div style="line-height: 1.5; color: #e2e8f0;">"${item.speech_response}"</div>
                                </div>
                            ` : ''}
                            ${item.wav_file ? `
                                <div style="margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap;">
                                    <button class="play-btn" onclick="playWav('${item.wav_file}')">Nghe lại đoạn thu</button>
                                    <a class="dl-btn" href="/recordings/${item.wav_file}" download="${item.wav_file}">Tải file WAV về máy</a>
                                </div>
                            ` : ''}
                        </div>
                    `;
                    }).join('');
                }
            } catch (e) {
                console.error(e);
            }
        }

        let g_ttsAudio = null;
        function speakAiText(text, btnElement) {
            if (!text) return;
            if (g_ttsAudio) {
                g_ttsAudio.pause();
                g_ttsAudio = null;
            }
            if ('speechSynthesis' in window) {
                window.speechSynthesis.cancel();
            }

            if (btnElement) {
                btnElement.innerText = 'Đang phát giọng đọc...';
                btnElement.disabled = true;
            }

            // Stream audio directly to ESP32 MAX98357A physical speaker
            fetch('/api/speaker/speak', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text: text })
            }).catch(e => console.error("Speaker stream err:", e));

            // Also play in browser audio for dual monitoring
            const audioUrl = '/api/tts?voice=vi-VN-HoaiMyNeural&text=' + encodeURIComponent(text);
            const audio = new Audio(audioUrl);
            g_ttsAudio = audio;

            audio.onended = () => {
                g_ttsAudio = null;
                if (btnElement) {
                    btnElement.innerText = 'Đọc Giọng Nói AI';
                    btnElement.disabled = false;
                }
            };

            const fallbackWebSpeech = () => {
                if ('speechSynthesis' in window) {
                    const utter = new SpeechSynthesisUtterance(text);
                    utter.lang = 'vi-VN';
                    utter.rate = 0.95;
                    utter.onend = () => {
                        if (btnElement) {
                            btnElement.innerText = 'Đọc Giọng Nói AI';
                            btnElement.disabled = false;
                        }
                    };
                    window.speechSynthesis.speak(utter);
                } else if (btnElement) {
                    btnElement.innerText = 'Đọc Giọng Nói AI';
                    btnElement.disabled = false;
                }
            };

            audio.onerror = fallbackWebSpeech;
            audio.play().catch(fallbackWebSpeech);
        }

        function testSpeakerChime(btn) {
            if (btn) {
                btn.innerText = "Đang phát chuông...";
                btn.disabled = true;
            }
            fetch('/api/speaker/test', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    setTimeout(() => {
                        if (btn) {
                            btn.innerText = "Thử Phát Nhạc Chuông Loa";
                            btn.disabled = false;
                        }
                    }, 1200);
                })
                .catch(e => {
                    if (btn) {
                        btn.innerText = "Thử Phát Nhạc Chuông Loa";
                        btn.disabled = false;
                    }
                });
        }

        if ('speechSynthesis' in window) {
            window.speechSynthesis.onvoiceschanged = () => { window.speechSynthesis.getVoices(); };
        }

        loadWifiInfo();
        setInterval(updateDashboard, 1500);
        updateDashboard();
    </script>
</body>
</html>
"""
        self.wfile.write(html_content.encode('utf-8'))

def web_server_thread():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer((HOST, WEB_PORT), DashboardHandler)
    print(f"\n=========================================================")
    print(f"  Smart Lamp Interactive Voice & Audio Analysis Server")
    print(f"  Dashboard URL: http://localhost:{WEB_PORT}")
    print(f"=========================================================\n")
    
    server.serve_forever()

if __name__ == "__main__":
    t_audio = threading.Thread(target=audio_receiver_thread, daemon=True)
    t_event = threading.Thread(target=event_receiver_thread, daemon=True)
    t_serial = threading.Thread(target=serial_receiver_thread, daemon=True)
    t_web = threading.Thread(target=web_server_thread, daemon=True)

    t_audio.start()
    t_event.start()
    t_serial.start()
    t_web.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Dashboard Receiver...")

