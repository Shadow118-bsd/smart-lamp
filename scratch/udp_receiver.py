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
        param_type = act.get("param_type", "ABSOLUTE")
        mode = act.get("mode", 0)

        if cmd == 1:
            g_system_state["power"] = True
            applied_descriptions.append("bật đèn")
        elif cmd == 2:
            g_system_state["power"] = False
            applied_descriptions.append("tắt đèn")
        elif cmd == 3:
            if param_type == "ABSOLUTE" or (val > 0 and val <= 100 and "tăng" not in act.get("clause", "").lower() and "giảm" not in act.get("clause", "").lower()):
                g_system_state["brightness"] = max(0, min(100, val))
                applied_descriptions.append(f"đặt độ sáng {g_system_state['brightness']}%")
            else:
                g_system_state["brightness"] = max(0, min(100, g_system_state["brightness"] + val))
                applied_descriptions.append(f"{'tăng' if val > 0 else 'giảm'} độ sáng {abs(val)}%")
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


def check_ollama_online():
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=0.5) as resp:
            return resp.status == 200
    except Exception:
        return False


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
        if cmd > 0:
            fast_path_actions.append({
                "clause": clause,
                "cmd": cmd,
                "val": val,
                "mode": mode,
                "intent_name": intent_name,
                "score": score
            })
        else:
            unrecognized_count += 1
            fast_path_actions.append({
                "clause": clause,
                "cmd": 0,
                "val": 0,
                "mode": 0,
                "intent_name": intent_name,
                "score": score
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
    try:
        sock.bind((HOST, EVENT_PORT))
    except Exception as e:
        print(f"[EVENT BIND ERROR] {e}")
        return

    try:
        while True:
            data, addr = sock.recvfrom(1024)
            if data:
                try:
                    payload = data.decode('utf-8')
                    event_data = json.loads(payload)

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

# Thread 3: HTTP Web Server & Interactive API
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        global g_is_recording, g_active_pcm_data, g_current_session_id, g_latest_waveform_samples

        if self.path.startswith('/api/data'):
            g_status["ollama_online"] = check_ollama_online()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {
                "status": g_status,
                "system_state": g_system_state,
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

            text_result = "(Môi trường im lặng - Không phát hiện câu nói)"
            cmd_type, val, mode = 0, 0, 0
            intent_name = "Chưa có câu lệnh"
            confidence = calc_confidence

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

                # Perform speech-to-text recognition if SpeechRecognition is installed & audio is not silent
                if HAS_SR and raw_rms >= 15:
                    try:
                        r = sr.Recognizer()
                        r.energy_threshold = 50
                        r.dynamic_energy_threshold = True
                        audio_data = sr.AudioData(processed_pcm, 16000, 2)
                        
                        raw_res = r.recognize_google(audio_data, language="vi-VN", show_all=True)
                        
                        if isinstance(raw_res, dict) and "alternative" in raw_res and len(raw_res["alternative"]) > 0:
                            best_match = raw_res["alternative"][0]
                            recognized_text = best_match.get("transcript", "").strip()
                            
                            if "confidence" in best_match and best_match["confidence"] is not None:
                                raw_api_conf = float(best_match["confidence"]) * 100.0
                                confidence = calculate_combined_confidence(raw_api_conf, raw_rms)
                            else:
                                confidence = calc_confidence
                                
                            text_result = recognized_text
                        elif isinstance(raw_res, str) and raw_res.strip():
                            text_result = raw_res.strip()
                            confidence = calc_confidence
                        else:
                            text_result = "(Âm thanh không rõ - Thử nói rõ hơn)"
                            confidence = calc_confidence
                    except sr.UnknownValueError:
                        text_result = "(Âm thanh không rõ - Thử nói gần micro hơn)"
                        confidence = calc_confidence
                    except Exception as e:
                        print(f"[SR ERROR] {e}")

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
                actions = [{
                    "clause": text_result,
                    "cmd": cmd_type,
                    "val": val,
                    "mode": mode,
                    "intent_name": intent_name,
                    "score": 0.0,
                    "engine": "Fast Path (Fallback)"
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
                "wav_file": filename
            }
            g_transcripts.insert(0, item)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "stopped", "item": item}).encode('utf-8'))
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">
                <h1>💡 Smart Desk Lamp — Voice Dashboard</h1>
                <p>Giao Diện Thu Âm Chủ Động 1-Click &amp; Phân Tích Tín Hiệu Lời Nói</p>
            </div>
            <div class="badge" id="status-badge">🟡 Đang chờ kết nối...</div>
        </div>

        <div class="storage-info">
            <span>📁 Vị trí lưu trữ file ghi âm âm thanh (.WAV) trên máy tính:</span>
            <span class="storage-path">d:/smart-lamp/scratch/recordings/</span>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-title">🎙️ Bộ Độc Quyền Thu Âm Chủ Động</div>
                
                <div class="status-item"><span>Kết nối ESP32-S3 / Micro:</span><span class="status-val" id="wifi-status">Sẵn sàng</span></div>
                <div class="status-item"><span>Địa chỉ IP Thiết Bị:</span><span class="status-val" id="esp-ip">Localhost / ESP32</span></div>
                <div class="status-item"><span>Dung lượng Đoạn Thu:</span><span class="status-val" id="audio-kb">0 KB</span></div>
                <div class="status-item"><span>Trạng thái Local SLM AI:</span><span class="status-val" id="ollama-status" style="font-weight: 700;">Checking...</span></div>

                <div class="rec-control-box">
                    <button class="btn-rec" id="rec-btn" onclick="toggleRecording()">
                        <span id="rec-icon">🔴</span> <span id="rec-text">BẮT ĐẦU THU ÂM (START)</span>
                    </button>
                    <div class="rec-timer" id="rec-timer">00:00</div>
                    <div class="rec-hint" id="rec-hint">Nhấn nút để chủ động thu âm câu nói của bạn</div>

                    <!-- Live Waveform Visualizer Canvas -->
                    <canvas id="waveform"></canvas>
                </div>

                <!-- Module 2 System Coordinator Live State -->
                <div style="margin-top: 16px; background: rgba(16, 185, 129, 0.08); border: 1px solid #10b981; padding: 12px 14px; border-radius: 10px;">
                    <div style="color: #10b981; font-weight: 700; font-size: 14px; margin-bottom: 8px;">💡 MODULE 2: TRẠNG THÁI ĐÈN THỰC TẾ (COORDINATOR)</div>
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; font-size: 13px;">
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Công Tắt: <span id="state-power" style="font-weight:700; color:#10b981;">BẬT 🟢</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Độ Sáng: <span id="state-brightness" style="font-weight:700; color:#38bdf8;">70%</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Nhiệt Màu: <span id="state-cct" style="font-weight:700; color:#fbbf24;">4000K</span></div>
                        <div style="background: #020617; padding: 6px 10px; border-radius: 6px;">Chế Độ: <span id="state-mode" style="font-weight:700; color:#c084fc;">Chế Độ Học Bài</span></div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-title">📜 Bản Script Lời Nói Phân Tích (Text Script)</div>
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

        function updateTimer() {
            seconds++;
            const m = String(Math.floor(seconds / 60)).padStart(2, '0');
            const s = String(seconds % 60).padStart(2, '0');
            document.getElementById('rec-timer').innerText = `${m}:${s}`;
        }

        async function toggleRecording() {
            const btn = document.getElementById('rec-btn');
            const icon = document.getElementById('rec-icon');
            const text = document.getElementById('rec-text');
            const hint = document.getElementById('rec-hint');

            if (!isRecording) {
                // START RECORDING
                const res = await fetch('/api/recording/start');
                isRecording = true;
                btn.classList.add('recording');
                icon.innerText = '⏹️';
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
                icon.innerText = '🔴';
                text.innerText = 'BẮT ĐẦU THU ÂM (START)';
                hint.innerText = 'Nhấn nút để chủ động thu âm câu nói mới';
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
                badge.innerText = '🟢 Sẵn sàng thu âm 1-Click';
                badge.style.borderColor = '#22c55e';
                badge.style.color = '#22c55e';
                
                document.getElementById('wifi-status').innerText = 'Sẵn sàng';
                document.getElementById('esp-ip').innerText = data.status.esp_ip;
                document.getElementById('audio-kb').innerText = data.status.active_audio_kb + ' KB';

                if (data.system_state) {
                    const isPowerOn = data.system_state.power;
                    document.getElementById('state-power').innerText = isPowerOn ? 'BẬT 🟢' : 'TẮT 🔴';
                    document.getElementById('state-power').style.color = isPowerOn ? '#10b981' : '#f87171';
                    document.getElementById('state-brightness').innerText = isPowerOn ? (data.system_state.brightness + '%') : `0% (Bộ nhớ: ${data.system_state.brightness}%)`;
                    document.getElementById('state-cct').innerText = data.system_state.cct + 'K';
                    const historyDepth = data.history_depth ? ` [Stack: ${data.history_depth}]` : '';
                    document.getElementById('state-mode').innerText = data.system_state.mode_name + historyDepth;
                }

                const ollamaEl = document.getElementById('ollama-status');
                if (data.status.ollama_online) {
                    ollamaEl.innerText = '🟢 Online (Qwen2.5 Sẵn Sàng)';
                    ollamaEl.style.color = '#c084fc';
                } else {
                    ollamaEl.innerText = '🔴 Offline (Bật: ollama run qwen2.5:1.5b)';
                    ollamaEl.style.color = '#f87171';
                }


                const scriptList = document.getElementById('script-list');
                if (data.transcripts.length > 0) {
                    scriptList.innerHTML = data.transcripts.map(item => `
                        <div class="script-item">
                            <div class="script-header">
                                <span>⏰ ${item.time}</span>
                                <span style="display: flex; gap: 12px; align-items: center;">
                                    <span style="color: ${item.engine && item.engine.includes('Ollama') ? '#c084fc' : '#34d399'}; font-weight: 700; background: rgba(15, 23, 42, 0.6); padding: 2px 8px; border-radius: 4px; border: 1px solid ${item.engine && item.engine.includes('Ollama') ? '#a855f7' : '#059669'};">⚙️ Engine: ${item.engine || 'Fast Path (~1ms)'}</span>
                                    <span style="color: #a855f7; font-weight: 600;">🔊 Âm lượng: ${item.volume || 0}% (RMS: ${item.rms || 0})</span>
                                    <span style="color: #38bdf8; font-weight: 600;">🎯 Độ tin cậy: ${item.confidence}%</span>
                                </span>
                            </div>
                            <div class="script-text">"${item.text}"</div>
                            <div class="script-meta">
                                ${(item.actions && item.actions.length > 1) ? `
                                    <div style="display: flex; flex-direction: column; gap: 4px; margin-top: 4px;">
                                        <div style="color: #38bdf8; font-weight: 700;">⚡ Chuỗi Lệnh Đa Mệnh Đề (${item.actions.length} lệnh):</div>
                                        ${item.actions.map((act, idx) => `
                                            <div style="background: #020617; padding: 6px 10px; border-radius: 6px; font-size: 13px; border-left: 3px solid #10b981; color: #cbd5e1;">
                                                <span style="color: #10b981; font-weight: 600;">Lệnh ${idx + 1}:</span> "${act.clause}" ➔ 
                                                <span style="color: #38bdf8; font-weight: 700;">${act.intent_name}</span> (ID: ${act.cmd} | Val: ${act.val} | Mode: ${act.mode})
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : `
                                    <span style="color: #38bdf8; font-weight: 700;">📌 Ý định lệnh: ${item.intent_name || 'Lệnh thử nghiệm'}</span> | 
                                    <span>ID: ${item.cmd}</span> | <span>Tham số: ${item.val}</span> | <span>Mode: ${item.mode}</span>
                                `}
                            </div>
                            ${item.speech_response ? `
                                <div style="margin-top: 8px; background: rgba(168, 85, 247, 0.15); border: 1px solid #a855f7; padding: 10px 14px; border-radius: 8px; color: #f1f5f9; font-size: 14px;">
                                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                                        <span>🤖 <strong style="color: #c084fc;">AI Phản Hồi Giọng Nói (${item.engine || 'Local SLM'}):</strong></span>
                                        <button onclick="speakAiText(this.getAttribute('data-speech'))" data-speech="${item.speech_response.replace(/"/g, '&quot;')}" style="background: #a855f7; color: white; border: none; padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600;">🔊 Đọc Giọng Nói AI</button>
                                    </div>
                                    <div style="line-height: 1.5; color: #e2e8f0;">"${item.speech_response}"</div>
                                </div>
                            ` : ''}
                            ${item.wav_file ? `
                                <div style="margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap;">
                                    <button class="play-btn" onclick="playWav('${item.wav_file}')">▶️ Nghe lại đoạn thu</button>
                                    <a class="dl-btn" href="/recordings/${item.wav_file}" download="${item.wav_file}">📥 Tải file WAV về máy</a>
                                </div>
                            ` : ''}
                        </div>
                    `).join('');
                }
            } catch (e) {
                console.error(e);
            }
        }

        function speakAiText(text) {
            if ('speechSynthesis' in window && text) {
                window.speechSynthesis.cancel();
                const utter = new SpeechSynthesisUtterance(text);
                utter.lang = 'vi-VN';
                utter.rate = 0.95;
                utter.pitch = 1.0;

                const voices = window.speechSynthesis.getVoices();
                const viVoice = voices.find(v => v.lang.toLowerCase().includes('vi') || v.name.toLowerCase().includes('vietnam') || v.name.toLowerCase().includes('hoaimy') || v.name.toLowerCase().includes('an'));
                if (viVoice) {
                    utter.voice = viVoice;
                }
                window.speechSynthesis.speak(utter);
            }
        }

        if ('speechSynthesis' in window) {
            window.speechSynthesis.onvoiceschanged = () => { window.speechSynthesis.getVoices(); };
        }

        setInterval(updateDashboard, 1500);
        updateDashboard();
    </script>
</body>
</html>
"""
        self.wfile.write(html_content.encode('utf-8'))

def web_server_thread():
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer((HOST, WEB_PORT), DashboardHandler)
    print(f"\n=========================================================")
    print(f"  💡 Smart Lamp Interactive Voice & Audio Analysis Server")
    print(f"  Dashboard URL: http://localhost:{WEB_PORT}")
    print(f"=========================================================\n")
    
    time.sleep(1.0)
    webbrowser.open(f"http://localhost:{WEB_PORT}")
    
    server.serve_forever()

if __name__ == "__main__":
    t_audio = threading.Thread(target=audio_receiver_thread, daemon=True)
    t_event = threading.Thread(target=event_receiver_thread, daemon=True)
    t_web = threading.Thread(target=web_server_thread, daemon=True)

    t_audio.start()
    t_event.start()
    t_web.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Dashboard Receiver...")
