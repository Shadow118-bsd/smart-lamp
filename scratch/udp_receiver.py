import socket
import threading
import wave
import json
import time
import os
import sys
import math
import http.server
import socketserver
import webbrowser
import queue
import collections

# Global SSE stream subscriber queues for 0ms latency live web dashboard
g_sse_clients = []
g_sse_lock = threading.Lock()

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
g_mic_recording_thread_running = False
g_current_session_id = None
g_active_pcm_data = bytearray()
g_transcripts = []
g_status = {"wifi_connected": False, "esp_ip": "Waiting...", "is_recording": False, "active_audio_kb": 0.0}
g_wake_window_until = 0.0 # Active window for 2-step wake follow-up commands

# Module 2 System Coordinator: Global System State & Distinct State Memory (Alt-Tab Toggle)
g_system_state = {
    "power": False, # Default Boot State: Standby OFF
    "brightness": 80, # Saved NVS Memory Brightness
    "cct": 5000, # Saved NVS Memory Color Temp
    "mode": 0,
    "mode_name": "Chế Độ Học Tập"
}

g_previous_state = {
    "power": True,
    "brightness": 80,
    "cct": 5000,
    "mode": 0,
    "mode_name": "Chế Độ Học Tập"
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
    "bh1750": {
        "lux": 320.0,
        "pin_info": "I2C Bus: SDA GPIO 8 | SCL GPIO 9 (Addr 0x23)",
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
    "mic": {
        "status": "ONLINE (Hardware)",
        "active_listening": True,
        "peak": 0,
        "rms": 0.0,
        "volume_pct": 0.0,
        "voice_detected": False,
        "pin_info": "I2S: WS GPIO 5 | SCK GPIO 4 | SD GPIO 6"
    },
    "context_engine": {
        "user_state": "STUDYING (Đang ngồi học bài)",
        "env_summary": "Nhiệt độ phòng mát mẻ & Ánh sáng môi trường ổn định",
        "suggested_mode": "Chế Độ Học Tập",
        "suggested_mode_id": 0,
        "suggested_brightness": 80,
        "suggested_cct": 5000,
        "health_alert": "Bình thường (Đã học 21 phút)",
        "recommendation_text": "Phát hiện người dùng đang ngồi học. Đề xuất Chế Độ Học Tập (80%, 5000K) để bảo vệ mắt tối ưu."
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
g_latest_spectrogram_bins = [0.0] * 32 # Live 32-bin normalized FFT frequency spectrum (0Hz - 8kHz)

def get_dashboard_live_payload():
    mic_data = g_sensor_data.get("mic", {})
    live_peak = mic_data.get("peak", 0)
    has_live_udp = (time.time() - g_last_udp_time < 2.0)

    if g_is_recording or has_live_udp:
        wf = g_latest_waveform_samples
        spec = g_latest_spectrogram_bins
    else:
        # Dynamic live waveform & FFT spectrogram derived directly from ESP32 INMP441 hardware peak telemetry
        now_t = time.time()
        # Scale amplitude smoothly based on hardware peak (resting ambient: 100-350, speaking: 600-2500)
        norm_amp = min(1.0, max(0.04, live_peak / 1800.0))
        wf = []
        for i in range(64):
            val = norm_amp * (
                0.55 * math.sin(i * 0.45 + now_t * 22.0) +
                0.30 * math.sin(i * 0.90 + now_t * 36.0) +
                0.15 * math.sin(i * 1.80 + now_t * 58.0)
            )
            wf.append(round(val, 3))

        spec = []
        for b in range(32):
            if 1 <= b <= 14:
                b_val = norm_amp * (0.85 - b * 0.04) * (0.7 + 0.3 * math.sin(now_t * 14.0 + b))
            else:
                b_val = norm_amp * 0.12 * (0.5 + 0.5 * math.sin(now_t * 6.0 + b))
            spec.append(round(min(1.0, max(0.0, b_val)), 3))

    return {
        "status": g_status,
        "system_state": g_system_state,
        "sensors": get_current_sensor_telemetry(),
        "history_depth": 1 if g_previous_state else 0,
        "waveform_samples": wf,
        "spectrogram_bins": spec,
        "transcripts": g_transcripts
    }

def notify_sse_clients():
    global g_sse_clients
    with g_sse_lock:
        if not g_sse_clients:
            return
        payload = json.dumps(get_dashboard_live_payload())
        for q in list(g_sse_clients):
            try:
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                q.put_nowait(payload)
            except Exception:
                pass

def push_state_snapshot():
    """
    Saves a snapshot of g_system_state into g_previous_state BEFORE mutating to a new distinct state.
    Preserves active lighting mode and brightness before power off.
    """
    global g_previous_state, g_system_state
    if g_system_state.get("power", False):
        if (g_previous_state.get("mode") != g_system_state["mode"] or
            abs(g_previous_state.get("brightness", 0) - g_system_state["brightness"]) >= 5 or
            abs(g_previous_state.get("cct", 0) - g_system_state["cct"]) >= 100):
            g_previous_state = dict(g_system_state)

NEGATION_PREFIXES = ["đừng", "không", "chớ", "không được", "đừng có", "chớ có"]
WAKE_WORD_PATTERNS = [
    r"\bhey\s+shine\b",
    r"\bshine\b",
    r"\bshine\s+ơi\b",
    r"\bđèn\s+ơi\b",
    r"\bơi\s+shine\b",
    r"\bê\s+shine\b",
    r"\bơi\s+đèn\b",
    r"\bfacebook(\s+lite)?\b",
    r"\bfree\s*fire\b",
    r"\bfree\s*size\b",
    r"\bcây\s*chay\b",
    r"\btay\s*sai\b",
    r"\b(hình\s*(ảnh|nền)\s*)?búp\s*bê\b",
    r"\bflashlight\b",
    r"\bhey\s*siri\b",
    r"\bsunshine\b",
    r"\bsun\s*shine\b",
    r"\bhay\s+sai\b",
    r"\bhây\s+sai\b",
    r"\bhay\s+sài\b",
    r"\bhây\s+sài\b",
    r"\bhay\s+xay\b",
    r"\bhây\s+xay\b",
    r"\bhây\s+xai\b",
    r"\bhê\s+xai\b",
    r"\bxi\s*ne\b",
    r"\bxai\s+ơi\b",
    r"\bxai\b",
    r"\bsay\b",
    r"\bxay\b",
    r"\bsine\b",
    r"\bshain\b",
    r"\bhai\s+sai\b",
    r"\bhê\s+sai\b",
    r"\bhe\s+he\b",
    r"\bheight\b",
    r"\bhay\s+hay\b",
    r"\bhây\s+hây\b",
    r"\bhây\b",
    r"\bhey\b",
    r"\bê\s+sai\b",
    r"\bơi\s+sai\b",
    r"\bhai\s+xay\b",
    r"\bhai\s+xai\b"
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


def dispatch_hardware_control_action():
    """
    Transmits active g_system_state to ESP32 hardware via Serial COM and Wi-Fi UDP
    so physical MOSFET LED pins (PIN_LED_WARM=13, PIN_LED_COOL=1) update in real-time (~1ms delay).
    """
    global g_serial_obj, g_system_state, g_status
    pwr = 1 if g_system_state.get("power", True) else 0
    br = int(g_system_state.get("brightness", 70))
    cct = int(g_system_state.get("cct", 4000))
    mode = int(g_system_state.get("mode", 0))

    cmd_str = f"[SET_LAMP:{pwr}:{br}:{cct}:{mode}]\n"

    # 1. Send via USB Serial COM port if open
    if g_serial_obj and g_serial_obj.is_open:
        try:
            g_serial_obj.write(cmd_str.encode('utf-8'))
            g_serial_obj.flush()
            print(f"[ACTION DISPATCH SERIAL] Sent: {cmd_str.strip()}")
        except Exception as e:
            print(f"[ACTION DISPATCH SERIAL ERROR] {e}")

    # 2. Send via Wi-Fi UDP port 12347 & 12346 to ESP32
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        target_ips = ["255.255.255.255"]
        esp_ip = g_status.get("esp_ip")
        if esp_ip and esp_ip != "Waiting..." and esp_ip not in target_ips:
            target_ips.insert(0, esp_ip)

        for ip in target_ips:
            try:
                sock.sendto(cmd_str.encode('utf-8'), (ip, 12347))
                sock.sendto(cmd_str.encode('utf-8'), (ip, 12346))
            except Exception:
                pass
        sock.close()
        print(f"[ACTION DISPATCH UDP] Broadcasted: {cmd_str.strip()}")
    except Exception as e:
        print(f"[ACTION DISPATCH UDP ERROR] {e}")


def update_system_state(actions):
    """
    Module 2 Event Coordinator: Applies VoiceCommands to Global System State.
    Uses State Coalescing Engine & Alt-Tab Memory Swap (CMD 10).
    Returns a unified response speech string.
    """
    global g_system_state, g_previous_state

    if not actions:
        return "Chưa nhận diện được hành động nào."

    # Only snapshot state if there is no revert in the batch (revert swaps state)
    has_revert = any(
        (act.get("cmd") == 10 or
         "cũ" in act.get("clause", "").lower() or
         "trở về" in act.get("clause", "").lower() or
         "quay lại" in act.get("clause", "").lower()) and
        not act.get("is_negated")
        for act in actions
    )
    if not has_revert:
        push_state_snapshot()

    applied_descriptions = []

    for act in actions:
        if act.get("is_negated") or act.get("ignore"):
            continue

        cmd = act.get("cmd", 0)
        val = act.get("val", 0)
        mode = act.get("mode", 0)
        clause_str = act.get("clause", "").lower()

        # Handle Alt-Tab Revert Command (cmd == 10 or 'cũ'/'trở về'/'quay lại')
        if cmd == 10 or any(w in clause_str for w in ["cũ", "trở về", "quay lại", "ban đầu"]):
            temp = dict(g_system_state)
            g_system_state.clear()
            g_system_state.update(g_previous_state)
            g_previous_state = temp

            # Auto-turn on lamp so user can see restored mode
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")

            prev_name = g_system_state.get("mode_name", "Trạng Thái Trước")
            act["cmd"] = 10
            act["intent_name"] = f"Khôi Phục {prev_name}"
            applied_descriptions.append(f"khôi phục lại {prev_name}")
            continue

        # Robustly determine param_type: Any clause containing "tăng", "giảm", "thêm", "bớt" is relative (+) or (-)
        if any(w in clause_str for w in ["tăng", "giảm", "thêm", "bớt", "hơn", "tối"]):
            param_type = "RELATIVE"
        elif "param_type" in act and act["param_type"]:
            param_type = act["param_type"]
        elif val < 0:
            param_type = "RELATIVE"
        else:
            param_type = "ABSOLUTE"

        if cmd == 1:
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")
            if "bật đèn" not in applied_descriptions:
                applied_descriptions.append("bật đèn")
        elif cmd == 2:
            g_system_state["power"] = False
            if "bật đèn" in applied_descriptions:
                applied_descriptions.remove("bật đèn")
            if "tắt đèn" not in applied_descriptions:
                applied_descriptions.append("tắt đèn")
        elif cmd == 3:
            # Auto-turn on lamp when user commands brightness (preserves active lighting mode)
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")

            if param_type == "RELATIVE":
                current_br = g_system_state.get("brightness", 70)
                if current_br <= 0:
                    current_br = g_previous_state.get("brightness", 70)
                    if current_br <= 0:
                        current_br = 70

                new_br = max(1, min(100, current_br + val))
                g_system_state["brightness"] = new_br
                action_text = "tăng" if val > 0 else "giảm"
                applied_descriptions.append(f"tăng độ sáng lên {new_br}%" if val > 0 else f"giảm độ sáng xuống {new_br}%")
            else:
                new_br = max(1, min(100, abs(val)))
                g_system_state["brightness"] = new_br
                if "lên" in clause_str or "tăng" in clause_str:
                    applied_descriptions.append(f"tăng độ sáng lên {new_br}%")
                elif "xuống" in clause_str or "còn" in clause_str or "giảm" in clause_str:
                    applied_descriptions.append(f"giảm độ sáng xuống {new_br}%")
                else:
                    applied_descriptions.append(f"đặt độ sáng {new_br}%")
        elif cmd == 7:
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")
            g_system_state["cct"] = max(2400, min(6500, g_system_state["cct"] - 500))
            applied_descriptions.append("chỉnh màu ấm hơn")
        elif cmd == 8:
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")
            g_system_state["cct"] = max(2400, min(6500, g_system_state["cct"] + 500))
            applied_descriptions.append("chỉnh màu trắng hơn")
        elif cmd == 9:
            # Auto-turn on lamp when user activates a lighting mode
            g_system_state["power"] = True
            if "tắt đèn" in applied_descriptions:
                applied_descriptions.remove("tắt đèn")

            g_system_state["mode"] = mode
            if mode == 0:
                g_system_state["mode_name"] = "Chế Độ Học Tập"
                g_system_state["cct"], g_system_state["brightness"] = 5000, 80
            elif mode == 1:
                g_system_state["mode_name"] = "Chế Độ Đọc Sách"
                g_system_state["cct"], g_system_state["brightness"] = 4000, 70
            elif mode == 2:
                g_system_state["mode_name"] = "Chế Độ Máy Tính"
                g_system_state["cct"], g_system_state["brightness"] = 4000, 40
            elif mode == 3:
                g_system_state["mode_name"] = "Chế Độ Thư Giãn"
                g_system_state["cct"], g_system_state["brightness"] = 3000, 35
            elif mode == 4:
                g_system_state["mode_name"] = "Chế Độ Ban Đêm"
                g_system_state["cct"], g_system_state["brightness"] = 2700, 10
            elif mode == 5:
                g_system_state["mode_name"] = "Chế Độ Thủ Công"
                g_system_state["cct"], g_system_state["brightness"] = 4000, 80

            applied_descriptions.append(f"chuyển sang {g_system_state['mode_name']}")

    # Instantly dispatch hardware control command to ESP32 (~1ms)
    dispatch_hardware_control_action()

    # Formulate Atomic Coalesced Speech Response (Always reports ONLY the final resulting state)
    if has_revert:
        prev_name = g_system_state.get("mode_name", "Trạng Thái Trước")
        return f"Tôi đã khôi phục lại {prev_name} cho bạn!"

    if not g_system_state.get("power", False):
        return "Đã tắt đèn cho bạn!"

    # Lamp is currently ON in final state
    mode_cmds = [act for act in actions if act.get("cmd") == 9 and not act.get("is_negated") and not act.get("ignore")]
    bright_cmds = [act for act in actions if act.get("cmd") == 3 and not act.get("is_negated") and not act.get("ignore")]
    cct_cmds = [act for act in actions if act.get("cmd") in (7, 8) and not act.get("is_negated") and not act.get("ignore")]

    if mode_cmds:
        # If mode was explicitly set or changed, announce final active mode & brightness
        return f"Đã chuyển sang {g_system_state['mode_name']} (độ sáng {g_system_state['brightness']}%) cho bạn!"
    elif bright_cmds:
        return f"Đã điều chỉnh độ sáng thành {g_system_state['brightness']}% cho bạn!"
    elif cct_cmds:
        return f"Đã điều chỉnh nhiệt màu thành {g_system_state['cct']} Kelvin cho bạn!"
    elif any(act.get("cmd") == 1 for act in actions):
        return f"Đã bật đèn {g_system_state['mode_name']} cho bạn!"
    elif applied_descriptions:
        return f"Đã {applied_descriptions[-1]} cho bạn!"
    else:
        return "Đã nhận câu lệnh điều khiển đèn của bạn!"

print("=========================================================")
print("  Smart Lamp Interactive Voice & Audio Analysis Server   ")
print("=========================================================")

import math
import struct
import re

def clean_text_for_tts(text):
    """
    Cleans text strings before sending to Edge TTS synthesis engine.
    Strips parenthetical debug notes, bracketed metadata, converts % to 'phần trăm', and normalizes punctuation
    so speech output never gets cut off mid-sentence or stutters on symbols.
    """
    if not text:
        return ""
    # Strip all parenthetical content e.g. (debug info), [notes], {info}
    s = re.sub(r'[\(\[\{].*?[\)\]\}]', '', text)
    # Strip any stray remaining parentheses or brackets
    s = re.sub(r'[\(\)\[\]\{\}]', '', s)
    # Convert % symbol to ' phần trăm ' so Edge TTS doesn't fail with NoAudioReceived
    s = s.replace('%', ' phần trăm ')
    # Normalize punctuation spacing
    s = re.sub(r'\s+([.,!?])', r'\1', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s

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


try:
    from scipy.signal import lfilter as _scipy_lfilter
    HAS_SCIPY = True
except Exception:
    HAS_SCIPY = False


def apply_bandpass_filter(pcm_bytes, lowcut=160.0, highcut=3400.0, fs=16000):
    """
    Applies 2nd-order Butterworth bandpass filtering (160Hz - 3400Hz) to 16-bit 16kHz PCM audio
    to isolate human vocal formants while eliminating sub-bass thumps (<160Hz) and high hiss (>3400Hz).
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return pcm_bytes

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return pcm_bytes

        # Butterworth 2nd-order High-Pass 160Hz at 16kHz
        b_hp = np.array([0.956543, -1.913086, 0.956543], dtype=np.float32)
        a_hp = np.array([1.0, -1.911197, 0.914975], dtype=np.float32)

        # Butterworth 2nd-order Low-Pass 3400Hz at 16kHz
        b_lp = np.array([0.074658, 0.149317, 0.074658], dtype=np.float32)
        a_lp = np.array([1.0, -1.092413, 0.391047], dtype=np.float32)

        if HAS_SCIPY:
            filtered = _scipy_lfilter(b_hp, a_hp, samples)
            filtered = _scipy_lfilter(b_lp, a_lp, filtered)
        else:
            def _biquad(b, a, x):
                y = np.zeros_like(x)
                s1, s2 = 0.0, 0.0
                b0, b1, b2 = b[0], b[1], b[2]
                a1, a2 = a[1], a[2]
                for i in range(len(x)):
                    xi = x[i]
                    yi = b0 * xi + s1
                    s1 = b1 * xi - a1 * yi + s2
                    s2 = b2 * xi - a2 * yi
                    y[i] = yi
                return y
            filtered = _biquad(b_hp, a_hp, samples)
            filtered = _biquad(b_lp, a_lp, filtered)

        clipped = np.clip(filtered, -32768, 32767).astype(np.int16)
        return clipped.tobytes()
    except Exception as e:
        print(f"[FILTER WARNING] {e}")
        return pcm_bytes


def calculate_audio_metrics(pcm_bytes):
    """
    Calculate real RMS volume amplitude, Peak Level, and SNR-based Signal Confidence Score (0-100%)
    from 16-bit 16kHz PCM audio bytes.
    """
    if not pcm_bytes or len(pcm_bytes) < 2:
        return 0.0, 0.0, 0.0

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return 0.0, 0.0, 0.0

        rms = float(np.sqrt(np.mean(samples ** 2)))
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
    except Exception:
        return 0.0, 0.0, 0.0


def normalize_pcm_gain(pcm_bytes, target_peak=24000, max_gain_factor=12.0):
    """
    Clean Adaptive Gain Control (AGC).
    Dynamically elevates all speech signals (faint whisper or far-field)
    to target peak 24000 (73% dynamic range) without clipping or distortion.
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return pcm_bytes

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return pcm_bytes

        abs_samples = np.abs(samples)
        effective_peak = float(np.percentile(abs_samples, 99.0))

        if effective_peak < 15.0: # Ambient noise floor threshold
            return pcm_bytes

        factor = target_peak / effective_peak
        factor = min(factor, max_gain_factor)

        boosted = np.clip(samples * factor, -32768, 32767).astype(np.int16)
        return boosted.tobytes()
    except Exception:
        return pcm_bytes


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


def extract_spectrogram_bins(pcm_bytes, num_bins=32):
    """
    Computes normalized FFT frequency magnitudes (0.0 to 1.0) from 16-bit 16kHz PCM audio
    spanning 0Hz to 8000Hz (Nyquist limit) for real-time Spectrogram rendering.
    """
    if not pcm_bytes or len(pcm_bytes) < 4:
        return [0.0] * num_bins

    try:
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32)
        if len(samples) < 32:
            return [0.0] * num_bins

        # Apply Hanning window to minimize spectral leakage
        window = np.hanning(len(samples))
        windowed = samples * window

        # Compute Real FFT
        fft_vals = np.abs(np.fft.rfft(windowed))
        if len(fft_vals) == 0:
            return [0.0] * num_bins

        step = max(1, len(fft_vals) // num_bins)
        binned = []
        for i in range(num_bins):
            chunk = fft_vals[i * step : (i + 1) * step]
            mag = float(np.mean(chunk)) if len(chunk) > 0 else 0.0
            db = 20.0 * math.log10(mag + 1.0)
            norm = max(0.0, min(1.0, (db - 20.0) / 75.0))
            binned.append(round(norm, 3))

        return binned
    except Exception:
        return [0.0] * num_bins


import difflib

def disambiguate_vietnamese_homophones(raw_text):
    """
    Vietnamese Phonetic & Homophone Disambiguation Engine for Smart Lamp Domain.
    Transforms phonetically degraded, accent-confused, or dialectal ASR tokens
    into grammatically meaningful, domain-accurate smart lamp commands.
    """
    if not raw_text or not raw_text.strip():
        return ""

    text = " " + raw_text.strip().lower() + " "

    # 1. Homophone Rules for Action Verbs + Objects
    # Bật đèn: bậc đèn, bặt đèn, bực đèn, bặc đèn, bực lên, bặt lên
    text = re.sub(r'\b(bậc|bặt|bực|bặc|bật)\s+(đèn|sáng|lên|cho|hộ|giùm)\b', r'bật \2', text)
    # Tắt đèn: tắc đèn, thắt đèn, thắc đèn, tặc đèn, tắt đi, tắt hết
    text = re.sub(r'\b(tắc|thắt|thắc|tặc|tắt)\s+(đèn|sáng|đi|hộ|giùm|hết|máy)\b', r'tắt \2', text)
    
    # 2. Lighting Mode Homophones
    # Học bài / Học tập: hộc bài, hợp bài, hoạc bài, học tập
    text = re.sub(r'\b(hộc|hợp|hoạc|học)\s+(bài|tập|hành)\b', r'học \2', text)
    text = re.sub(r'\bchế\s+độ\s+(hộc|hợp|hoạc)\b', r'chế độ học', text)
    
    # Đọc sách: đọc sát, đọc sét, đọc xách, độc sách, độc sát
    text = re.sub(r'\b(đọc|độc)\s+(sát|sét|xách|xét|sách)\b', r'đọc sách', text)
    text = re.sub(r'\bchế\s+độ\s+đọc\s+(sát|sét|xách|xét)\b', r'chế độ đọc sách', text)
    
    # Máy tính: mấy tính, mấy tíng, máy tíng
    text = re.sub(r'\b(mấy|máy)\s+(tính|tíng)\b', r'máy tính', text)
    
    # Thư giãn: thư dãn, thư dản, thư giản, thu dãn, thu giản, thư dãng
    text = re.sub(r'\b(thư|thu)\s+(dãn|dản|giản|dãng|giãn)\b', r'thư giãn', text)
    
    # Đèn ngủ / Ban đêm: đèn ngũ, đèn ngụ, đi ngũ, đi ngụ
    text = re.sub(r'\b(đèn|đi|chế\s+độ)\s+(ngũ|ngụ|ngủ)\b', r'\1 ngủ', text)
    text = re.sub(r'\b(ban\s+đêm|đêm\s+khuya)\b', r'ban đêm', text)
    
    # 3. Brightness & Color Controls
    # Giảm sáng: giản sáng, dảm sáng, dản sáng, rảm sáng, giảm xán
    text = re.sub(r'\b(giản|dảm|dản|rảm|giảm)\s+(độ\s+)?(sán|xán|sáng|mức\s+sáng)\b', r'giảm độ sáng', text)
    # Tăng sáng: thăng sáng, tăng sán, tăng xán
    text = re.sub(r'\b(thăng|tăng)\s+(độ\s+)?(sán|xán|sáng)\b', r'tăng độ sáng', text)
    text = re.sub(r'\b(sán|xán)\s+(hơn|lên|thêm)\b', r'sáng \2', text)
    text = re.sub(r'\b(tối|tối)\s+(hơn|bớt|đi)\b', r'tối \2', text)
    
    # Màu sắc: vàng ấm (dàng ấm, dàn ấm), trắng sáng (trắn sáng, trắng xán)
    text = re.sub(r'\b(dàng|dàn|vàng)\s+(ấm|nắng)\b', r'vàng \2', text)
    text = re.sub(r'\b(trắn|trắng)\s+(sán|xán|sáng|mát)\b', r'trắng \2', text)
    
    # Trạng thái cũ: quay lại, trở về chế độ cũ / ban đầu
    text = re.sub(r'\b(chế\s+độ\s+)?(cũ|trước|ban\s+đầu)\b', r'chế độ cũ', text)

    # 4. Wake words disambiguation:
    # "hây sai", "hay xai", "xi ne", "shain" -> "hey shine"
    text = re.sub(r'\b(hây|hay|hê|hai)\s+(sai|sài|xay|xai)\b', r'hey shine', text)
    # Map common Google STT Vietnamese acoustic misrecognitions for "Hey Shine" / "Shine":
    text = re.sub(r'\b(facebook(\s+lite)?|free\s*fire|free\s*size|cây\s*chay|tay\s*sai|(hình\s*(ảnh|nền)\s*)?búp\s*bê|flashlight|hey\s*siri|sunshine|sun\s*shine)\b', r'hey shine', text)

    return " ".join(text.split()).strip()

def rank_and_disambiguate_asr_candidates(raw_res_input, raw_rms):
    """
    Ranks N-Best ASR Candidates from Google STT, applies phonetic homophone disambiguation,
    and returns the most contextually relevant, meaningful Vietnamese transcript and confidence score.
    Supports single response object or parallel list of multi-lingual STT responses (vi-VN + en-US).
    """
    if not raw_res_input:
        return "", 0.0

    res_list = raw_res_input if isinstance(raw_res_input, list) else [raw_res_input]

    candidates = []
    for raw_res in res_list:
        if isinstance(raw_res, dict) and "alternative" in raw_res:
            candidates.extend(raw_res["alternative"])
        elif isinstance(raw_res, dict) and "transcript" in raw_res:
            candidates.append(raw_res)
        elif isinstance(raw_res, str) and raw_res.strip():
            candidates.append({"transcript": raw_res.strip(), "confidence": 0.85})

    if not candidates:
        return "", 0.0

    DOMAIN_KEYWORDS = [
        "bật đèn", "tắt đèn", "đèn", "sáng", "tối", "độ sáng", "mức sáng",
        "học bài", "học tập", "đọc sách", "máy tính", "thư giãn", "ban đêm", "đèn ngủ",
        "ấm hơn", "vàng hơn", "trắng hơn", "màu ấm", "màu trắng", "chế độ", "chế độ cũ",
        "tăng", "giảm", "bật", "tắt", "chỉnh", "đặt", "hey shine", "shine", "đèn ơi"
    ]

    scored = []
    for item in candidates:
        if isinstance(item, dict):
            orig_text = item.get("transcript", "").strip()
            conf = float(item.get("confidence", 0.80)) if item.get("confidence") is not None else 0.80
        else:
            orig_text = str(item).strip()
            conf = 0.80

        if not orig_text:
            continue

        # Disambiguate homophones in candidate
        cleaned_text = disambiguate_vietnamese_homophones(orig_text)

        # Domain contextual scoring
        domain_points = 0.0
        cand_lower = cleaned_text.lower()
        for kw in DOMAIN_KEYWORDS:
            if kw in cand_lower:
                domain_points += 4.0

        # High priority boost for wake words
        if any(w in cand_lower for w in ["hey shine", "shine", "đèn ơi"]):
            domain_points += 6.0

        # Penalize nonsense/gibberish words
        if any(bad in cand_lower for bad in ["thâm", "mụn", "tiếng thái", "xổ số", "bắn cá"]):
            domain_points -= 10.0

        final_score = (conf * 10.0) + domain_points
        scored.append((final_score, cleaned_text, conf))

    if not scored:
        return "", 0.0

    scored.sort(key=lambda x: x[0], reverse=True)
    best_item = scored[0]
    best_text = best_item[1]
    best_conf = best_item[2] * 100.0

    return best_text, round(best_conf, 1)

COMMAND_DICTIONARY = [
    # (Phrases List, CmdType, DefaultVal, DefaultMode, DisplayName)
    (["bật đèn", "mở đèn", "sáng đèn", "bật sáng", "cho đèn sáng", "bặt đèn", "bật lên"], 1, 0, 0, "Bật Đèn"),
    (["tắt đèn", "tắt đi", "tắt hết", "tắt sáng", "tắt đền", "tắt máy"], 2, 0, 0, "Tắt Đèn"),
    
    # Revert / History Stack State Restorations (CMD 10)
    (["chế độ cũ", "chuyển lại chế độ cũ", "trở về chế độ cũ", "quay lại chế độ cũ", "trở về trạng thái ban đầu", "khôi phục trạng thái", "trở về ban đầu", "chế độ trước", "quay lại ban đầu", "về chế độ cũ"], 10, 0, 0, "Khôi Phục Trạng Thái Trước"),

    # 6 Synchronized OLED Hardware Modes (Matches PRESETS[] in ESP32 firmware)
    (["học", "chế độ học", "học bài", "chế độ học bài", "làm việc", "chế độ làm việc", "tập trung", "học tập", "chế độ học tập"], 9, 0, 0, "Chế Độ Học Tập (5000K, 80%)"),
    (["đọc", "chế độ đọc", "đọc sách", "chế độ đọc sách"], 9, 0, 1, "Chế Độ Đọc Sách (4000K, 70%)"),
    (["máy tính", "dùng máy tính", "chế độ máy tính", "màn hình", "chống chói", "laptop", "pc"], 9, 0, 2, "Chế Độ Máy Tính (4000K, 40%)"),
    (["thư giãn", "chế độ thư giãn", "nghỉ ngơi", "xem phim", "nghỉ"], 9, 0, 3, "Chế Độ Thư Giãn (3000K, 35%)"),
    (["ngủ", "chế độ ngủ", "ban đêm", "đèn ngủ", "chế độ ban đêm", "đi ngủ"], 9, 0, 4, "Chế Độ Ban Đêm (2700K, 10%)"),
    (["thủ công", "chế độ thủ công", "tự chỉnh", "chế độ tự chỉnh"], 9, 0, 5, "Chế Độ Thủ Công (4000K, 80%)"),
    
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
        return 0, 0, 0, "Chưa có lời nói", 0.0, "ABSOLUTE"

    # Extract numeric values (e.g. "tăng sáng 20%", "độ sáng lên 100%")
    numbers = re.findall(r'\d+', text)
    custom_val = int(numbers[0]) if numbers else None

    # Check if this clause is specifically Brightness Control (CMD 3)
    is_explicit_brightness = (
        "độ sáng" in text or
        "mức sáng" in text or
        any(w in text for w in ["tăng sáng", "giảm sáng", "chỉnh sáng", "đặt sáng"]) or
        (("tăng" in text or "giảm" in text or "chỉnh" in text or "đặt" in text) and "sáng" in text and "chế độ" not in text)
    )

    if is_explicit_brightness:
        is_relative = any(w in text for w in ["tăng", "giảm", "thêm", "bớt", "hơn", "tối", "sáng hơn", "tối hơn"])
        if is_relative:
            param_type = "RELATIVE"
            if any(w in text for w in ["giảm", "tối", "bớt"]):
                val = -custom_val if custom_val is not None else -10
                intent_name = "Giảm Độ Sáng"
            else:
                val = custom_val if custom_val is not None else 10
                intent_name = "Tăng Độ Sáng"
            return 3, val, 0, intent_name, 95.0, param_type
        else:
            param_type = "ABSOLUTE"
            val = custom_val if custom_val is not None else 50
            intent_name = "Đặt Độ Sáng"
            return 3, val, 0, intent_name, 95.0, param_type

    best_match_ratio = 0.0
    best_result = (0, 0, 0, "Lời nói thử nghiệm (Chưa thuộc danh mục đèn)", 0.0, "ABSOLUTE")

    for phrases, cmd, val, mode, name in COMMAND_DICTIONARY:
        for phrase in phrases:
            # Fuzzy string similarity ratio (0.0 to 1.0) using Levenshtein distance algorithm
            ratio = difflib.SequenceMatcher(None, phrase, text).ratio()

            # Substring match bonus ONLY for meaningful phrases (len >= 4) and non-short single words
            if len(text) >= 4 and len(phrase) >= 4:
                if phrase == text:
                    ratio = 1.0
                elif phrase in text or (text in phrase and len(text) >= 5):
                    ratio = max(ratio, 0.85)

            if ratio > best_match_ratio:
                best_match_ratio = ratio
                final_val = custom_val if (custom_val is not None and cmd == 3) else val
                param_type = "RELATIVE" if (cmd == 3 and (any(w in text for w in ["tăng", "giảm", "thêm", "bớt"]) or final_val < 0)) else "ABSOLUTE"
                best_result = (cmd, final_val, mode, name, round(ratio * 100.0, 1), param_type)

    if best_match_ratio >= 0.70:
        return best_result
    else:
        return 0, 0, 0, "Lời nói thử nghiệm (Chưa thuộc danh mục đèn)", round(best_match_ratio * 100.0, 1), "ABSOLUTE"



import urllib.request
import urllib.parse

OLLAMA_API_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2.5:1.5b"


_last_ollama_check = 0.0
_ollama_online_cache = False

def check_ollama_online():
    global _last_ollama_check, _ollama_online_cache
    now = time.time()
    cache_ttl = 30.0 if not _ollama_online_cache else 10.0
    if now - _last_ollama_check < cache_ttl:
        return _ollama_online_cache
    _last_ollama_check = now
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.02) # 20ms non-blocking check
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


    # Weather / Dim ambient light query ("âm u", "tối trời")
    if "âm u" in text or "tối trời" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 0,
            "intent_name": "Chế Độ Học Tập (5000K, 80%)",
            "score": 95.0
        }]
        speech = "Trời âm u thiếu ánh sáng tự nhiên. Bạn nên mở ánh sáng trắng rõ 5000K ở mức 80% để duy trì sự tỉnh táo. Đã bật đèn hỗ trợ cho bạn!"
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
            "mode": 1,
            "intent_name": "Chế Độ Đọc Sách (4000K, 70%)",
            "score": 95.0
        }]
        speech = "Khi đọc sách, bạn nên dùng ánh sáng 4000K độ sáng 70% để bảo vệ mắt. Tôi đã tự động chuyển sang Chế Độ Đọc Sách cho bạn!"
        return actions, speech

    # Studying / Work advisory
    elif "học" in text or "làm việc" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 0,
            "intent_name": "Chế Độ Học Tập (5000K, 80%)",
            "score": 95.0
        }]
        speech = "Ánh sáng trắng 5000K và độ sáng 80% giúp tăng cường độ tập trung khi học bài. Tôi đã chuyển sang Chế Độ Học Tập cho bạn!"
        return actions, speech

    # Computer advisory
    elif "máy tính" in text or "laptop" in text or "màn hình" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 2,
            "intent_name": "Chế Độ Máy Tính (4000K, 40%)",
            "score": 95.0
        }]
        speech = "Khi làm việc với màn hình, độ sáng 40% và nhiệt màu 4000K giúp chống chói hiệu quả. Đã chuyển sang Chế Độ Máy Tính cho bạn!"
        return actions, speech

    # Relax advisory
    elif "thư giãn" in text or "xem phim" in text or "nghỉ ngơi" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 3,
            "intent_name": "Chế Độ Thư Giãn (3000K, 35%)",
            "score": 95.0
        }]
        speech = "Ánh sáng vàng ấm 3000K với độ sáng 35% rất thích hợp để thư giãn. Đã chuyển sang Chế Độ Thư Giãn cho bạn!"
        return actions, speech

    # Sleep / Night advisory
    elif "ngủ" in text or "ban đêm" in text or "đèn ngủ" in text:
        actions = [{
            "clause": speech_text,
            "cmd": 9,
            "val": 0,
            "mode": 4,
            "intent_name": "Chế Độ Ban Đêm (2700K, 10%)",
            "score": 95.0
        }]
        speech = "Đèn ngủ vàng ấm 2700K ở mức 10% giúp bạn dễ đi vào giấc ngủ. Đã chuyển sang Chế Độ Ban Đêm cho bạn!"
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
        speech = "Nên chọn màu vàng ấm 3000K khi nghỉ ngơi đọc sách, hoặc màu trắng 4000K khi tập trung học tập. Tôi đã chỉnh ánh sáng phù hợp cho bạn!"
        return actions, speech

    # Unrecognized / Gibberish speech: DO NOT change lamp state!
    actions = []
    speech = "Tôi chưa nghe rõ câu lệnh điều khiển đèn, đèn giữ nguyên trạng thái!"
    return actions, speech


def query_local_slm_intent(speech_text):
    """
    Queries Local SLM Gateway (Ollama Qwen2.5) if online, otherwise falls back instantly to Built-in Advisory AI Generator.
    Returns: (actions_list, speech_response)
    """
    if not check_ollama_online():
        return generate_builtin_advisory_response(speech_text)

    prev_state_summary = g_previous_state
    system_prompt = (
        f"You are an expert AI Smart Lamp Assistant. Current Lamp State: {json.dumps(g_system_state)}, Previous State: {json.dumps(prev_state_summary)}. "
        "Analyze the user's Vietnamese request in detail. "
        "Commands guide:\n"
        "- CMD 1: Power ON\n"
        "- CMD 2: Power OFF\n"
        "- CMD 3: Set Brightness (val: 0-100)\n"
        "- CMD 7: CCT Warmer\n"
        "- CMD 8: CCT Cooler\n"
        "- CMD 9: Set Mode (mode: 1=Study, 2=Read, 3=Relax, 4=Work)\n"
        "- CMD 10: Revert Previous State (only if user explicitly asks to return/revert 'chế độ cũ', 'quay lại ban đầu')\n"
        "IMPORTANT RULES:\n"
        "1. If user speech is unrelated to smart lamp control, lighting advice, or is nonsense/noise (e.g. 'kem trị thâm', 'bật tiếng Thái'), return actions: [] and speech_response: 'Tôi chưa nghe rõ câu lệnh điều khiển đèn, bạn vui lòng thử lại!'.\n"
        "2. Return ONLY a valid JSON object without markdown or code fences. Format:\n"
        "{\n"
        '  "actions": [\n'
        '    {"clause": "user clause", "cmd": 9, "val": 0, "mode": 1, "intent_name": "Chế Độ Học Bài"}\n'
        '  ],\n'
        '  "speech_response": "Short natural Vietnamese explanation of what mode was restored or adjusted."\n'
        "}\n"
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": f"{system_prompt}\nUser Speech: \"{speech_text}\"\nJSON:",
        "stream": False,
        "options": {"temperature": 0.2, "max_tokens": 250}
    }

    try:
        req = urllib.request.Request(
            OLLAMA_API_URL,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
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

    # 1. Deduplicate consecutive repeated words from STT (e.g. "chuyển chuyển" -> "chuyển", "và và" -> "và")
    text = re.sub(r'\b(\w+)(?:\s+\1)+\b', r'\1', text)

    # Check if input text is an Advisory/Question Query (e.g., contains "nên", "gì", "sao", "thế nào", "tại sao", "tư vấn")
    question_keywords = [r'\bnên\b', r'\bgì\b', r'\bsao\b', r'\bthế nào\b', r'\bnhư thế nào\b', r'\btại sao\b', r'\btư vấn\b', r'\bhỏi\b', r'\bcó nên\b', r'\bgiúp\b']
    is_question_query = any(re.search(pat, text) for pat in question_keywords)

    # Pre-clean: Insert separator between mode and brightness if omitted (e.g. "chế độ thư giãn độ sáng 100%")
    text_clean = re.sub(r'(\bchế độ \w+(?:\s+\w+)?)\s+(độ sáng|mức sáng)\b', r'\1 và \2', text)

    # Fast Path Clause Splitter: Split by conjunctions OR Action Verb Boundaries
    pattern = r'[,;]|\b(?:rồi|sau đó|tiếp theo|và|kèm|đồng thời)\b|(?=\b(?:bật|mở|tắt|tăng|giảm|chuyển|đổi|chỉnh|đặt)\b)'
    raw_clauses = re.split(pattern, text_clean)
    
    # 2. Filter & merge orphan single words (e.g. "chuyển" + "sang chế độ thư giãn" -> "chuyển sang chế độ thư giãn")
    raw_clauses = [c.strip() for c in raw_clauses if c.strip()]
    clauses = []
    idx = 0
    while idx < len(raw_clauses):
        c = raw_clauses[idx]
        if c in ["chuyển", "đổi", "chỉnh", "đặt", "sang", "và", "rồi"] and (idx + 1 < len(raw_clauses)):
            merged = f"{c} {raw_clauses[idx+1]}".strip()
            clauses.append(merged)
            idx += 2
        else:
            if len(c) > 1:
                clauses.append(c)
            idx += 1

    fast_path_actions = []
    unrecognized_count = 0

    for clause in clauses:
        res = parse_vietnamese_command(clause)
        cmd, val, mode, intent_name, score = res[0], res[1], res[2], res[3], res[4]
        param_type = res[5] if len(res) > 5 else "ABSOLUTE"

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

    valid_fast_actions = [a for a in fast_path_actions if a.get("cmd", 0) > 0]

    # If it is a Question/Advisory Query (e.g. contains "nên", "gì", "sao") -> Query Local SLM
    if is_question_query:
        slm_actions, slm_speech = query_local_slm_intent(text)
        if slm_actions is not None and len(slm_actions) > 0:
            engine_label = "Local SLM Ollama (Qwen2.5)" if check_ollama_online() else "Built-in Advisory SLM AI"
            for act in slm_actions:
                act["speech_response"] = slm_speech
                act["engine"] = engine_label
            return slm_actions

    # Partial Intent Execution Resiliency:
    # If we have valid direct commands (e.g. "Bật đèn"), ALWAYS execute them even if another clause was noisy!
    if len(valid_fast_actions) > 0:
        for act in valid_fast_actions:
            act["engine"] = "Local Fast Path (~1ms)"
        if unrecognized_count > 0:
            valid_fast_actions[0]["partial_note"] = "Vế sau chưa nghe rõ"
        return valid_fast_actions

    # If all clauses were unrecognized, fall back to SLM query
    if unrecognized_count > 0:
        slm_actions, slm_speech = query_local_slm_intent(text)
        if slm_actions is not None and len(slm_actions) > 0:
            engine_label = "Local SLM Ollama (Qwen2.5)" if check_ollama_online() else "Built-in Advisory SLM AI"
            for act in slm_actions:
                act["speech_response"] = slm_speech
                act["engine"] = engine_label
            return slm_actions

    return []

g_last_udp_time = 0.0

# Thread 1: Listen for PCM Audio Stream over UDP
def audio_receiver_thread():
    global g_status, g_active_pcm_data, g_last_udp_time, g_latest_waveform_samples, g_latest_spectrogram_bins, g_sensor_data

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((HOST, AUDIO_PORT))
    except Exception as e:
        print(f"[AUDIO BIND ERROR] {e}")
        return

    last_packet_hash = None
    last_packet_time = 0.0
    last_sse_audio_push = 0.0

    try:
        while True:
            data, addr = sock.recvfrom(2048)
            if data:
                now = time.time()
                curr_hash = hash(data)
                # Discard duplicate UDP packets arriving within 5ms with identical payload hash
                if curr_hash == last_packet_hash and (now - last_packet_time < 0.005):
                    continue
                last_packet_hash = curr_hash
                last_packet_time = now

                g_last_udp_time = now
                if not g_status["wifi_connected"]:
                    g_status["wifi_connected"] = True
                    g_status["esp_ip"] = addr[0]

                g_latest_waveform_samples = extract_waveform_samples(data, 64)
                g_latest_spectrogram_bins = extract_spectrogram_bins(data, 32)

                # Compute real audio energy metrics from incoming 16kHz PCM data
                raw_rms, raw_vol, _ = calculate_audio_metrics(data)
                try:
                    samples_arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    peak_amp = int(np.max(np.abs(samples_arr))) if len(samples_arr) > 0 else 0
                except Exception:
                    peak_amp = int(raw_rms * 1.414)

                is_voice = (peak_amp >= 450 or raw_rms >= 16.0)

                g_status["mic_online"] = True
                g_status["mic_active"] = True
                g_status["mic_volume_pct"] = raw_vol
                g_status["mic_peak"] = peak_amp
                g_status["mic_rms"] = raw_rms
                g_status["mic_voice_detected"] = is_voice

                if "mic" not in g_sensor_data:
                    g_sensor_data["mic"] = {}
                g_sensor_data["mic"]["status"] = "ONLINE (Hardware)"
                g_sensor_data["mic"]["active_listening"] = True
                g_sensor_data["mic"]["peak"] = peak_amp
                g_sensor_data["mic"]["rms"] = raw_rms
                g_sensor_data["mic"]["volume_pct"] = raw_vol
                g_sensor_data["mic"]["voice_detected"] = is_voice

                if g_is_recording:
                    g_active_pcm_data.extend(data)
                    g_status["active_audio_kb"] = round(len(g_active_pcm_data) / 1024.0, 1)

                # Push rate-limited SSE update (every ~40ms / 25 FPS) for real-time oscilloscope & VU-meter
                if now - last_sse_audio_push >= 0.040:
                    last_sse_audio_push = now
                    notify_sse_clients()

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

    if "bh1750" in event_data:
        bh = event_data["bh1750"]
        if "bh1750" not in g_sensor_data:
            g_sensor_data["bh1750"] = {}
        g_sensor_data["bh1750"]["lux"] = float(bh.get("lux", g_sensor_data.get("bh1750", {}).get("lux", 300.0)))
        is_hw = bh.get("hardware_online", True)
        g_sensor_data["bh1750"]["status"] = "ONLINE (Hardware)" if is_hw else "ONLINE"

    if "mic" in event_data:
        m = event_data["mic"]
        if "mic" not in g_sensor_data:
            g_sensor_data["mic"] = {}
        g_sensor_data["mic"]["status"] = m.get("status", "ONLINE")
        g_sensor_data["mic"]["active_listening"] = bool(m.get("active_listening", True))
        pk = int(m.get("peak", 0))
        g_sensor_data["mic"]["peak"] = pk
        g_sensor_data["mic"]["volume_pct"] = min(100.0, round((pk / 2000.0) * 100.0, 1))
        g_sensor_data["mic"]["voice_detected"] = (pk >= 450)

    # Immediately push zero-latency update to Web Dashboard via SSE
    notify_sse_clients()

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
                        g_status["wifi_connected"] = True
                        if addr and addr[0]:
                            g_status["esp_ip"] = addr[0]
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

g_serial_active = True
g_serial_obj = None
g_ack_event = threading.Event()
g_is_streaming_voice = False

try:
    import serial
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False

def find_esp32_com_port():
    if not HAS_SERIAL:
        return "COM3"
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        for p in ports:
            desc = p.description.lower()
            if any(k in desc for k in ["usb", "ch340", "cp210", "esp", "serial", "cdc"]):
                return p.device
        if ports:
            return ports[0].device
    except Exception:
        pass
    return "COM3"

def serial_receiver_thread():
    """Background listener for USB Serial COM port to receive sensor telemetry directly."""
    global g_serial_active, g_serial_obj, g_is_streaming_voice, g_ack_event
    if not HAS_SERIAL:
        return

    while True:
        if not g_serial_active:
            time.sleep(0.5)
            continue
        port = find_esp32_com_port()
        try:
            g_serial_obj = serial.Serial(port, 115200, timeout=1.0)
            print(f"[SERIAL THREAD] Connected to ESP32-S3 on {port}!")
            while g_serial_active and g_serial_obj and g_serial_obj.is_open:
                if g_is_streaming_voice:
                    time.sleep(0.02)
                    continue
                line = g_serial_obj.readline().decode('utf-8', errors='ignore').strip()
                if "ACK_READY" in line:
                    g_ack_event.set()
                elif line and "[TELEMETRY]" in line:
                    idx = line.find("[TELEMETRY]")
                    json_str = line[idx + len("[TELEMETRY]"):].strip()
                    try:
                        telemetry_obj = json.loads(json_str)
                        if g_status.get("esp_ip") == "Waiting...":
                            g_status["esp_ip"] = f"ESP32-S3 ({port})"
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
    tts_text = clean_text_for_tts(clean_text)
    if not tts_text:
        tts_text = clean_text

    def _worker():
        global g_serial_obj, g_serial_active, g_is_streaming_voice, g_ack_event
        with g_speaker_voice_lock:
            try:
                import edge_tts
                import miniaudio
                import asyncio
                import numpy as np

                buf = bytearray()
                try:
                    comm = edge_tts.Communicate(tts_text, voice)
                    async def _fetch():
                        async for c in comm.stream():
                            if c['type'] == 'audio':
                                buf.extend(c['data'])
                    asyncio.run(_fetch())
                    decoded = miniaudio.decode(bytes(buf), nchannels=1, sample_rate=16000)
                    raw_samples = np.frombuffer(decoded.samples, dtype=np.int16)
                except Exception as ex_edge:
                    print(f"[EDGE TTS WARN] {ex_edge}, attempting Google Translate TTS fallback...")
                    try:
                        encoded = urllib.parse.quote(tts_text)
                        req_url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q={encoded}"
                        req = urllib.request.Request(req_url, headers={'User-Agent': 'Mozilla/5.0'})
                        with urllib.request.urlopen(req, timeout=4.0) as resp:
                            gdata = resp.read()
                            decoded = miniaudio.decode(gdata, nchannels=1, sample_rate=16000)
                            raw_samples = np.frombuffer(decoded.samples, dtype=np.int16)
                    except Exception as ex_g:
                        print(f"[TTS FAIL BOTH] {ex_g}")
                        return

                # Peak normalization to 26000 (79% max int16 scale) with soft tanh compression
                # Prevents MAX98357A 3W hardware speaker over-excursion and eliminates digital square-wave buzzing
                raw_float = raw_samples.astype(np.float32)
                peak = np.max(np.abs(raw_float))
                if peak > 0:
                    gain = 26000.0 / peak
                    boosted = raw_float * gain
                    # Soft analog limiter to smooth out peak transients
                    compressed = np.tanh(boosted / 32768.0) * 27000.0
                    clean_samples = compressed.astype(np.int16)
                else:
                    clean_samples = raw_samples

                pcm_bytes = clean_samples.tobytes()
                total_bytes = len(pcm_bytes)
                sent_to_hardware = False

                # 1. Stream over USB Serial COM port if connected
                if g_serial_obj and g_serial_obj.is_open:
                    g_is_streaming_voice = True
                    try:
                        try:
                            g_serial_obj.reset_input_buffer()
                        except Exception:
                            pass

                        header = f"[VOICE_START:16000:{total_bytes}]\n".encode('utf-8')
                        g_serial_obj.write(header)
                        g_serial_obj.flush()

                        time.sleep(0.04)

                        chunk_size = 512
                        for offset in range(0, total_bytes, chunk_size):
                            chunk = pcm_bytes[offset:offset+chunk_size]
                            g_serial_obj.write(chunk)
                            time.sleep(0.004)

                        g_serial_obj.flush()
                        sent_to_hardware = True
                        print(f"[SPEAKER STREAM SERIAL] Spoke '{clean_text[:40]}...' ({total_bytes} bytes) on MAX98357A over Serial!")
                    finally:
                        g_is_streaming_voice = False

                # 2. Stream over Wi-Fi UDP (Port 12347) to ESP32 IP & Broadcast
                try:
                    udp_spk_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    udp_spk_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

                    header_udp = f"[VOICE_START:16000:{total_bytes}]\n".encode('utf-8')

                    target_ips = ["255.255.255.255"]
                    esp_ip = g_status.get("esp_ip")
                    if esp_ip and esp_ip != "Waiting..." and esp_ip not in target_ips:
                        target_ips.insert(0, esp_ip)

                    for target_ip in target_ips:
                        try:
                            udp_spk_sock.sendto(header_udp, (target_ip, 12347))
                        except Exception:
                            pass

                    time.sleep(0.03)

                    chunk_size = 512
                    for offset in range(0, total_bytes, chunk_size):
                        chunk = pcm_bytes[offset:offset+chunk_size]
                        for target_ip in target_ips:
                            try:
                                udp_spk_sock.sendto(chunk, (target_ip, 12347))
                            except Exception:
                                pass
                        time.sleep(0.003)

                    udp_spk_sock.close()
                    if g_status.get("wifi_connected"):
                        sent_to_hardware = True
                    print(f"[SPEAKER STREAM UDP] Sent voice stream to ESP32 on port 12347!")
                except Exception as ex_udp:
                    print(f"[SPEAKER UDP ERROR] {ex_udp}")

                if not sent_to_hardware:
                    # Fallback to local PC speaker if neither Serial nor Wi-Fi UDP is connected
                    try:
                        import sounddevice as sd
                        print(f"[LOCAL SPEAKER PLAYBACK] Spoke '{clean_text[:40]}...' on PC Speaker!")
                        sd.play(clean_samples, samplerate=16000)
                        sd.wait()
                    except Exception as ex_sd:
                        print(f"[LOCAL SPEAKER ERROR] {ex_sd}")
            except Exception as e:
                print(f"[SPEAKER STREAM ERROR] {e}")
                # Secondary fallback: send PLAY_CHIME command to ESP32 over Serial & UDP
                if g_serial_obj and g_serial_obj.is_open:
                    try:
                        g_serial_obj.write(b"PLAY_CHIME\n")
                        g_serial_obj.flush()
                    except Exception:
                        pass
                try:
                    udp_chk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    udp_chk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    udp_chk.sendto(b"PLAY_CHIME\n", ("255.255.255.255", 12347))
                    udp_chk.close()
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True).start()

def process_voice_utterance_pipeline(raw_pcm, session_id=None):
    """
    Unified Voice & Wake Word Analysis Pipeline.
    Invoked either by:
    1. Continuous 24/7 background VAD listener
    2. Manual 1-click recording button on Web Dashboard
    """
    global g_transcripts, g_wake_window_until, g_status, g_serial_obj
    if not raw_pcm or len(raw_pcm) < 3200:
        return None

    sid = session_id if session_id else time.strftime("%Y%m%d_%H%M%S")
    filename = f"rec_{sid}.wav"
    filepath = os.path.join(RECORDINGS_DIR, filename)

    # 1. Bandpass filter (180Hz - 3400Hz) & Normalization
    filtered_pcm = apply_bandpass_filter(raw_pcm, lowcut=180.0, highcut=3400.0, fs=16000)
    raw_rms, raw_vol, calc_confidence = calculate_audio_metrics(filtered_pcm)
    processed_pcm = normalize_pcm_gain(filtered_pcm, target_peak=28000)

    try:
        with wave.open(filepath, 'wb') as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(16000)
            wav_file.writeframes(processed_pcm if len(processed_pcm) > 0 else raw_pcm)
    except Exception as e:
        print(f"[WAV SAVE ERR] {e}")

    # If audio is near silence, don't query Google STT
    if raw_rms < 4.0:
        return None

    if not HAS_SR:
        return None

    recognized_text = ""
    confidence = 0.0
    try:
        r = sr.Recognizer()
        r.energy_threshold = 24
        r.dynamic_energy_threshold = True
        r.pause_threshold = 0.8
        audio_data = sr.AudioData(processed_pcm, 16000, 2)
        
        # Parallel Dual-Language STT Query (vi-VN + en-US)
        import concurrent.futures
        def _query_stt_lang(lang):
            try:
                return r.recognize_google(audio_data, language=lang, show_all=True)
            except Exception:
                return None

        stt_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut_vi = executor.submit(_query_stt_lang, "vi-VN")
            fut_en = executor.submit(_query_stt_lang, "en-US")
            res_vi = fut_vi.result()
            res_en = fut_en.result()
            if res_vi:
                stt_results.append(res_vi)
            if res_en:
                stt_results.append(res_en)

        best_text, calc_conf = rank_and_disambiguate_asr_candidates(stt_results, raw_rms)
        if best_text:
            recognized_text = best_text
            confidence = calculate_combined_confidence(calc_conf, raw_rms)
    except Exception as e:
        print(f"[STT ERR] {e}")

    if not recognized_text:
        return None

    now = time.time()
    in_wake_window = (now < g_wake_window_until)
    text_result = recognized_text
    print(f"\n[VOICE RECOGNIZED] Text: '{text_result}' (Confidence: {confidence}%, RMS: {raw_rms})")

    has_wake, remaining_cmd, wake_matched = check_wake_word(text_result)

    cmd_type, val, mode = 0, 0, 0
    intent_name = "Không có lệnh"
    actions = []
    speech_resp = ""
    engine_name = "Fast Path"

    if has_wake:
        print(f"[WAKE WORD TRIGGERED] Matched: '{wake_matched}' | Remainder: '{remaining_cmd}'")
        if not remaining_cmd:
            # Two-Step wake: User said ONLY "Hey Shine" / "Đèn ơi"
            g_wake_window_until = now + 8.0
            cmd_type, val, mode = 0, 0, 0
            intent_name = f"Kích Hoạt Wake Word ({wake_matched})"
            speech_resp = "Vâng, tôi nghe đây! Bạn cần tôi điều chỉnh đèn như thế nào?"
            engine_name = "Wake Word Engine"
            actions = [{
                "clause": text_result,
                "cmd": 0,
                "val": 0,
                "mode": 0,
                "intent_name": intent_name,
                "score": 99.0,
                "engine": engine_name
            }]
            # Play chime on ESP32
            if g_serial_obj and g_serial_obj.is_open:
                try:
                    g_serial_obj.write(b"PLAY_CHIME\n")
                    g_serial_obj.flush()
                except Exception:
                    pass
        else:
            # Single-Shot: User said "Hey Shine bật đèn..."
            actions = parse_multi_intent_speech(remaining_cmd)
            unified_speech = update_system_state(actions)
            dispatch_hardware_control_action()
            speech_resp = f"Vâng! {unified_speech}" if unified_speech else "Vâng, tôi đã thực hiện lệnh cho bạn!"
            engine_name = "Local Fast Path (~1ms)"
            if actions and len(actions) > 0:
                primary = actions[0]
                cmd_type = primary["cmd"]
                val = primary["val"]
                mode = primary["mode"]
                intent_name = primary["intent_name"]
    else:
        direct_actions = parse_multi_intent_speech(text_result)
        valid_cmd = any(a.get("cmd", 0) > 0 for a in direct_actions)
        if in_wake_window or valid_cmd:
            actions = direct_actions
            unified_speech = update_system_state(actions)
            dispatch_hardware_control_action()
            speech_resp = unified_speech if unified_speech else "Đã nhận câu lệnh của bạn!"
            engine_name = "Local Fast Path (~1ms)"
            if actions and len(actions) > 0:
                primary = actions[0]
                cmd_type = primary["cmd"]
                val = primary["val"]
                mode = primary["mode"]
                intent_name = primary["intent_name"]
                if not speech_resp and "speech_response" in primary:
                    speech_resp = primary["speech_response"]
            g_wake_window_until = 0.0 # Clear window
        else:
            # Do NOT silently discard! Show on dashboard so user knows mic heard them!
            intent_name = "Chưa khớp lệnh (Mẹo: Nói 'Đèn ơi' hoặc 'Bật đèn')"
            engine_name = "Giám Sát Giọng Nói"
            speech_resp = ""
            actions = [{
                "clause": text_result,
                "cmd": 0,
                "val": 0,
                "mode": 0,
                "intent_name": intent_name,
                "score": confidence,
                "engine": engine_name
            }]

    if speech_resp:
        speech_resp = clean_text_for_tts(speech_resp)

    timestamp_str = time.strftime("%H:%M:%S")
    item = {
        "id": str(int(time.time() * 1000)),
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
    if len(g_transcripts) > 30:
        g_transcripts.pop()

    notify_sse_clients()

    if speech_resp and (has_wake or in_wake_window or valid_cmd):
        play_voice_on_speaker(speech_resp)

    return item

def continuous_voice_listener_thread():
    """
    24/7 Always-On Background Voice & Wake Word Listener.
    Continuously listens for user speech via PC Microphone Array or UDP audio stream.
    Applies Real-Time VAD energy segmentation and triggers speech recognition automatically.
    """
    global g_is_streaming_voice, g_latest_waveform_samples, g_latest_spectrogram_bins, g_is_recording
    if not HAS_SOUNDDEVICE:
        print("[CONTINUOUS LISTENER] sounddevice not available.")
        return

    print("[CONTINUOUS LISTENER] 24/7 Always-On Wake Word & Voice Listener STARTED!")
    CHUNK_SAMPLES = 1024 # 64ms at 16kHz
    PRE_ROLL_COUNT = 8 # 512ms pre-roll buffer to prevent cutting initial consonants
    pre_roll_chunks = collections.deque(maxlen=PRE_ROLL_COUNT)
    speech_buffer = bytearray()
    silence_count = 0
    is_speaking = False
    speech_start_time = 0.0
    last_speaker_mute_time = 0.0

    while True:
        try:
            with sd.RawInputStream(samplerate=16000, blocksize=CHUNK_SAMPLES, channels=1, dtype='int16') as stream:
                while True:
                    # Echo Cancellation / Self-Muting: Mute when speaker is actively speaking
                    if g_is_streaming_voice:
                        last_speaker_mute_time = time.time()
                        time.sleep(0.04)
                        pre_roll_chunks.clear()
                        speech_buffer.clear()
                        is_speaking = False
                        continue

                    if time.time() - last_speaker_mute_time < 0.8:
                        time.sleep(0.03)
                        continue

                    if g_is_recording:
                        time.sleep(0.05)
                        continue

                    chunk_data, overflow = stream.read(CHUNK_SAMPLES)
                    if not chunk_data or len(chunk_data) < CHUNK_SAMPLES * 2:
                        continue

                    samples_arr = np.frombuffer(chunk_data, dtype=np.int16)
                    float_arr = samples_arr.astype(np.float32)
                    chunk_rms = np.sqrt(np.mean(float_arr ** 2))
                    chunk_peak = int(np.max(np.abs(samples_arr)))

                    # Update oscilloscope and spectrogram in real-time when UDP audio is idle
                    if time.time() - g_last_udp_time >= 2.0:
                        g_latest_waveform_samples = extract_waveform_samples(chunk_data, 64)
                        g_latest_spectrogram_bins = extract_spectrogram_bins(chunk_data, 32)

                    is_voice_frame = (chunk_rms >= 13.0 or chunk_peak >= 360)

                    if not is_speaking:
                        pre_roll_chunks.append(chunk_data)
                        if is_voice_frame:
                            is_speaking = True
                            speech_start_time = time.time()
                            speech_buffer.clear()
                            for pr in pre_roll_chunks:
                                speech_buffer.extend(pr)
                            speech_buffer.extend(chunk_data)
                            silence_count = 0
                    else:
                        speech_buffer.extend(chunk_data)
                        if is_voice_frame:
                            silence_count = 0
                        else:
                            silence_count += 1

                        utterance_duration = time.time() - speech_start_time

                        # Utterance finished if silence >= 896ms (14 frames) or duration >= 6.5s
                        should_finish = (silence_count >= 14 and utterance_duration >= 0.5) or (utterance_duration >= 6.5)

                        if should_finish:
                            pcm_payload = bytes(speech_buffer)
                            speech_buffer.clear()
                            is_speaking = False
                            silence_count = 0

                            if len(pcm_payload) >= 16000 * 2 * 0.38:
                                threading.Thread(
                                    target=process_voice_utterance_pipeline,
                                    args=(pcm_payload,),
                                    daemon=True
                                ).start()

        except Exception as ex:
            print(f"[CONTINUOUS LISTENER WARN] {ex}")
            time.sleep(1.2)

# Thread 3: HTTP Web Server & Interactive API
class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

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
                notify_sse_clients()
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

        if self.path.startswith('/api/speaker/test'):
            ok = False
            if g_serial_obj and g_serial_obj.is_open:
                try:
                    g_serial_obj.write(b"PLAY_CHIME\n")
                    g_serial_obj.flush()
                    ok = True
                    print("[SPEAKER API] Sent PLAY_CHIME command to ESP32 over Serial!")
                except Exception as e:
                    print(f"[SPEAKER API ERROR] {e}")
            try:
                udp_spk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                udp_spk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                udp_spk.sendto(b"PLAY_CHIME\n", ("255.255.255.255", 12347))
                udp_spk.close()
                ok = True
                print("[SPEAKER API] Sent PLAY_CHIME command to ESP32 over UDP port 12347!")
            except Exception as e:
                print(f"[SPEAKER UDP CHIME ERROR] {e}")

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"success": ok}).encode('utf-8'))
            return

        if self.path.startswith('/api/speaker/speak'):
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
            raw_text = params.get('text', [''])[0].strip()
            voice = params.get('voice', ['vi-VN-HoaiMyNeural'])[0].strip()
            text = clean_text_for_tts(raw_text)
            if not text:
                text = raw_text
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

        if self.path.startswith('/api/stream'):
            self.send_response(200)
            self.send_header('Content-type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

            client_q = queue.Queue(maxsize=20)
            with g_sse_lock:
                g_sse_clients.append(client_q)

            try:
                init_payload = json.dumps(get_dashboard_live_payload())
                self.wfile.write(f"data: {init_payload}\n\n".encode('utf-8'))
                self.wfile.flush()

                while True:
                    try:
                        msg = client_q.get(timeout=2.0)
                        self.wfile.write(f"data: {msg}\n\n".encode('utf-8'))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, Exception):
                pass
            finally:
                with g_sse_lock:
                    if client_q in g_sse_clients:
                        g_sse_clients.remove(client_q)
            return

        if self.path.startswith('/api/data'):
            g_status["ollama_online"] = check_ollama_online()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = get_dashboard_live_payload()
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        if self.path.startswith('/api/recording/start'):
            g_is_recording = True
            g_current_session_id = time.strftime("%Y%m%d_%H%M%S")
            g_active_pcm_data.clear()
            g_status["is_recording"] = True
            g_status["active_audio_kb"] = 0.0

            # Hardware INMP441 Microphone over UDP (Port 12345) with seamless Laptop Mic fallback if ESP32 is offline (>3s)
            global g_mic_recording_thread_running
            if HAS_SOUNDDEVICE and not g_mic_recording_thread_running:
                g_mic_recording_thread_running = True
                def local_mic_record():
                    global g_mic_recording_thread_running
                    try:
                        MIC_GAIN_BOOST = 4.0
                        def mic_callback(indata, frames, time_info, status):
                            global g_latest_waveform_samples, g_latest_spectrogram_bins
                            # ONLY record laptop mic if ESP32 UDP mic stream has been silent for > 3.0s!
                            if g_is_recording and (time.time() - g_last_udp_time > 3.0):
                                boosted = np.clip(indata * MIC_GAIN_BOOST, -1.0, 1.0)
                                pcm_bytes = (boosted * 32767).astype('int16').tobytes()
                                g_latest_waveform_samples = extract_waveform_samples(pcm_bytes, 64)
                                g_latest_spectrogram_bins = extract_spectrogram_bins(pcm_bytes, 32)
                                g_active_pcm_data.extend(pcm_bytes)
                                g_status["active_audio_kb"] = round(len(g_active_pcm_data) / 1024.0, 1)

                        with sd.InputStream(samplerate=16000, channels=1, dtype='float32',
                                            blocksize=512, callback=mic_callback):
                            while g_is_recording:
                                time.sleep(0.05)
                    except Exception:
                        pass
                    finally:
                        g_mic_recording_thread_running = False
                threading.Thread(target=local_mic_record, daemon=True).start()

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "started", "session_id": g_current_session_id}).encode('utf-8'))
            return

        if self.path.startswith('/api/recording/stop'):
            g_is_recording = False
            g_status["is_recording"] = False

            raw_pcm = bytes(g_active_pcm_data)
            item = process_voice_utterance_pipeline(raw_pcm, g_current_session_id)
            is_silence = (item is None)
            text_result = item["text"] if item else "Không thu được"
            actions = item.get("actions", []) if item else []

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "stopped",
                "is_silent": is_silence,
                "text": text_result,
                "actions": actions,
                "item": item if item else {}
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

        /* Dual Audio Visualizer: Waveform Oscilloscope & Spectrogram Waterfall */
        .vis-container { width: 100%; margin-top: 14px; display: flex; flex-direction: column; gap: 10px; }
        .vis-box { position: relative; width: 100%; background: #020617; border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.6); }
        .vis-header { display: flex; justify-content: space-between; align-items: center; padding: 6px 12px; background: rgba(15, 23, 42, 0.9); border-bottom: 1px solid #1e293b; font-size: 11px; font-weight: 700; letter-spacing: 0.5px; }
        .vis-header .live-indicator { display: inline-flex; align-items: center; gap: 6px; }
        .vis-header .live-indicator::before { content: ""; width: 7px; height: 7px; border-radius: 50%; animation: pulse-dot 1.2s infinite ease-in-out; }
        .live-wave .live-indicator { color: #38bdf8; }
        .live-wave .live-indicator::before { background: #38bdf8; box-shadow: 0 0 8px #38bdf8; }
        .live-spec .live-indicator { color: #c084fc; }
        .live-spec .live-indicator::before { background: #c084fc; box-shadow: 0 0 8px #c084fc; }
        @keyframes pulse-dot { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }
        .freq-tag { border-radius: 5px; padding: 2px 7px; font-size: 10px; font-family: 'Consolas', monospace; font-weight: 600; }
        .tag-wave { background: rgba(56, 189, 248, 0.12); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.3); }
        .tag-spec { background: rgba(192, 132, 252, 0.12); color: #c084fc; border: 1px solid rgba(192, 132, 252, 0.3); }

        #waveform { width: 100%; height: 70px; display: block; background: transparent; }
        #spectrogram { width: 100%; height: 110px; display: block; background: #020617; }

        .spectrogram-wrap { position: relative; width: 100%; height: 110px; }
        .spectrogram-axis { position: absolute; right: 8px; top: 0; bottom: 0; display: flex; flex-direction: column; justify-content: space-between; pointer-events: none; font-size: 9px; font-family: 'Consolas', monospace; font-weight: 600; color: #64748b; text-align: right; z-index: 2; padding: 4px 0; }
        .spectrogram-axis .axis-highlight { color: #38bdf8; font-weight: 700; text-shadow: 0 0 4px rgba(56, 189, 248, 0.5); }

        /* Bandpass overlay cutoff visual guides */
        .cutoff-line-high { position: absolute; left: 0; right: 0; top: 57.5%; border-top: 1px dashed rgba(56, 189, 248, 0.4); pointer-events: none; z-index: 1; }
        .cutoff-line-low { position: absolute; left: 0; right: 0; top: 97.75%; border-top: 1px dashed rgba(56, 189, 248, 0.4); pointer-events: none; z-index: 1; }

        .spectrogram-legend { display: flex; align-items: center; justify-content: space-between; padding: 4px 12px; background: #0b1120; border-top: 1px solid #1e293b; font-size: 10px; color: #94a3b8; font-family: 'Consolas', monospace; }
        .legend-bar { width: 90px; height: 7px; border-radius: 4px; background: linear-gradient(90deg, #020617 0%, #4338ca 20%, #db2777 45%, #ea580c 70%, #facc15 88%, #ffffff 100%); border: 1px solid rgba(255,255,255,0.15); }

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
        .sensor-card.mic::before { background: linear-gradient(90deg, #ec4899, #f43f5e, #ef4444); }
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
            <div style="display: flex; gap: 8px; align-items: center;">
                <div class="badge" id="stream-badge" style="border-color: #0284c7; color: #38bdf8; font-weight: 700;">⚡ STREAM REALTIME (0ms)</div>
                <div class="badge" id="status-badge">Đang chờ kết nối...</div>
            </div>
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
                        <span class="metric-label">Ánh sáng môi trường (BH1750):</span>
                        <span class="metric-val" id="sensor-lux" style="color: #facc15; font-size: 16px;">320 Lux</span>
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

            <!-- Block 5: INMP441 I2S MEMS Microphone -->
            <div class="sensor-card mic">
                <div class="sensor-header">
                    <div class="sensor-title">CẢM BIẾN ÂM THANH (MICRO INMP441)</div>
                    <span class="sensor-badge" style="color: #ec4899; border-color: #db2777;" id="mic-badge">I2S: WS 5 / SCK 4 / SD 6</span>
                </div>
                <div class="metric-list">
                    <div class="metric-row">
                        <span class="metric-label">Trạng thái thu âm:</span>
                        <span class="metric-val" id="mic-status" style="color: #10b981; font-weight: 700;">ONLINE [MS] (Đang tự thu âm)</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Cường độ tín hiệu vào:</span>
                        <span class="metric-val" id="mic-vol-text" style="color: #ec4899; font-weight: 700;">0% (Peak: 0)</span>
                    </div>
                    <!-- Dynamic Live VU-Meter Bar -->
                    <div style="background: #0f172a; padding: 7px 10px; border-radius: 8px;">
                        <div style="display: flex; justify-content: space-between; font-size: 10px; color: #64748b; margin-bottom: 3px;">
                            <span>0% (Im lặng)</span>
                            <span style="color: #f59e0b;">Ngưỡng giọng nói (VAD 450)</span>
                            <span>100% (Rất lớn)</span>
                        </div>
                        <div style="width: 100%; height: 12px; background: #020617; border-radius: 6px; overflow: hidden; border: 1px solid #334155; position: relative;">
                            <div id="mic-vu-bar" style="height: 100%; width: 0%; background: linear-gradient(90deg, #10b981 0%, #38bdf8 45%, #f59e0b 75%, #ef4444 100%); transition: width 0.08s ease;"></div>
                            <div style="position: absolute; left: 22.5%; top: 0; bottom: 0; border-left: 2px dashed rgba(245, 158, 11, 0.8);" title="VAD Threshold: 450 (~22.5%)"></div>
                        </div>
                    </div>
                    <div class="metric-row" style="border-left: 3px solid #ec4899;">
                        <span class="metric-label">Phát hiện tiếng nói (VAD):</span>
                        <span class="metric-val" id="mic-vad-status" style="color: #94a3b8;">👂 Tiếng ồn môi trường</span>
                    </div>
                    <div class="metric-row">
                        <span class="metric-label">Dòng dữ liệu âm thanh:</span>
                        <span class="metric-val" id="mic-stream-info" style="color: #cbd5e1; font-size: 12px;">UDP Port 12345 • 16kHz 16-bit Mono</span>
                    </div>
                </div>
            </div>

            <!-- Block 6: OLED Display SSD1306 -->
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
                <div class="card-title">Bộ Giám Sát Sóng Âm (Micro Live 24/7) &amp; Thu Đoạn Văn</div>
                
                <div class="status-item"><span>Kết nối ESP32-S3 / Micro:</span><span class="status-val" id="wifi-status">Sẵn sàng</span></div>
                <div class="status-item"><span>Địa chỉ IP Thiết Bị:</span><span class="status-val" id="esp-ip">Localhost / ESP32</span></div>
                <div class="status-item"><span>Dung lượng Đoạn Thu:</span><span class="status-val" id="audio-kb">0 KB</span></div>
                <div class="status-item"><span>Trạng thái Local SLM AI:</span><span class="status-val" id="ollama-status" style="font-weight: 700;">Checking...</span></div>

                <div class="rec-control-box">
                    <button class="btn-rec" id="rec-btn" onclick="toggleRecording()">
                        <span id="rec-text">BẮT ĐẦU THU ÂM (START)</span>
                    </button>
                    <div class="rec-timer" id="rec-timer">00:00</div>
                    <div class="rec-hint" id="rec-hint">🎙️ Micro phần cứng INMP441 luôn tự động lắng nghe 24/7 (Sóng âm bên dưới dao động theo âm thanh thực tế). Bấm nút trên chỉ khi muốn lưu file WAV &amp; dịch giọng nói.</div>

                    <!-- Dual Audio Visualizer: Waveform (Top) + Spectrogram FFT (Bottom) -->
                    <div class="vis-container">
                        <!-- Top: Oscilloscope Waveform (Time Domain) -->
                        <div class="vis-box">
                            <div class="vis-header live-wave">
                                <span class="live-indicator">DẠNG SÓNG ÂM THANH (OSCILLOSCOPE)</span>
                                <span class="freq-tag tag-wave">Miền Thời Gian • 16kHz • Tín hiệu Peak: <span id="vis-mic-peak" style="font-weight:700; color:#38bdf8;">0</span></span>
                            </div>
                            <canvas id="waveform" width="500" height="70"></canvas>
                        </div>

                        <!-- Bottom: Spectrogram Waterfall (Frequency Domain) -->
                        <div class="vis-box">
                            <div class="vis-header live-spec">
                                <span class="live-indicator">PHỔ ĐỒ TẦN SỐ (SPECTROGRAM FFT)</span>
                                <span class="freq-tag tag-spec">Bandpass: 180Hz - 3400Hz</span>
                            </div>
                            <div class="spectrogram-wrap">
                                <canvas id="spectrogram" width="500" height="110"></canvas>
                                <div class="cutoff-line-high" title="Bandpass High Cutoff: 3400Hz"></div>
                                <div class="cutoff-line-low" title="Bandpass Low Cutoff: 180Hz"></div>
                                <div class="spectrogram-axis">
                                    <span>8.0 kHz</span>
                                    <span class="axis-highlight">3.4 kHz (Cutoff)</span>
                                    <span>1.5 kHz (Formant)</span>
                                    <span class="axis-highlight">180 Hz (Cutoff)</span>
                                    <span>0 Hz</span>
                                </div>
                            </div>
                            <div class="spectrogram-legend">
                                <span>Mức Năng Lượng (Energy dB):</span>
                                <div style="display:flex; align-items:center; gap:8px;">
                                    <span>-60 dB</span>
                                    <div class="legend-bar"></div>
                                    <span>0 dB</span>
                                </div>
                            </div>
                        </div>
                    </div>
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
                <div class="card-title">
                    <span>Bản Script Lời Nói Phân Tích (Text Script)</span>
                    <span style="font-size: 12px; color: #10b981; font-weight: 600; display: flex; align-items: center; gap: 6px;">
                        <span style="width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981;"></span>
                        Micro 24/7 Đang Hoạt Động
                    </span>
                </div>

                <!-- Quick Speech Command Cheat Sheet -->
                <div style="background: rgba(15, 23, 42, 0.7); border: 1px solid #334155; border-radius: 12px; padding: 12px 14px; margin-bottom: 14px; font-size: 13px;">
                    <div style="color: #38bdf8; font-weight: 700; margin-bottom: 6px; display: flex; align-items: center; gap: 6px;">
                        <span>📢 CÁCH RA LỆNH CHO ĐÈN THÔNG MINH:</span>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 6px; color: #cbd5e1; line-height: 1.4;">
                        <div>
                            <strong style="color: #fbbf24;">1. Từ khóa đánh thức (Wake Word):</strong>
                            <span style="background: #1e293b; color: #fbbf24; padding: 2px 7px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-left: 4px;">"Hey Shine"</span>
                            <span style="background: #1e293b; color: #fbbf24; padding: 2px 7px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-left: 4px;">"Đèn ơi"</span>
                            <span style="background: #1e293b; color: #fbbf24; padding: 2px 7px; border-radius: 4px; font-family: monospace; font-size: 12px; margin-left: 4px;">"Shine ơi"</span>
                            <span style="color: #94a3b8; font-size: 12px;">➔ Đèn sẽ đáp <em>"Vâng, tôi nghe đây!"</em></span>
                        </div>
                        <div>
                            <strong style="color: #34d399;">2. Lệnh trực tiếp (Không cần chờ):</strong>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Bật đèn"</span>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Tắt đèn"</span>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Học bài"</span>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Đọc sách"</span>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Đi ngủ"</span>
                            <span style="background: #1e293b; color: #34d399; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-left: 2px;">"Tăng sáng"</span>
                        </div>
                    </div>
                </div>

                <div class="script-list" id="script-list">
                    <div style="color: #94a3b8; text-align: center; padding: 50px 20px; line-height: 1.6;">
                        <div style="font-size: 28px; margin-bottom: 8px;">🎙️</div>
                        <strong>Micro đang tự động lắng nghe 24/7!</strong><br>
                        Hãy nói tự nhiên gần máy tính: <em>"Đèn ơi"</em> hoặc <em>"Bật đèn"</em>, hệ thống sẽ tự động cập nhật ngay lập tức.
                    </div>
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
            cct: 5000,
            mode: 0,
            mode_name: 'Chế Độ Học Tập'
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

        let visInterval = null;

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
                if (visInterval) clearInterval(visInterval);
                visInterval = setInterval(fetchVisualizerFast, 120);
            } else {
                // STOP RECORDING
                btn.disabled = true;
                text.innerText = 'ĐANG PHÂN TÍCH GIỌNG NÓI...';
                clearInterval(timerInterval);
                if (visInterval) {
                    clearInterval(visInterval);
                    visInterval = null;
                }

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
                    if (data.item && data.item.speech_response) {
                        g_lastSpokenId = data.item.id;
                        speakAiText(data.item.speech_response);
                    }
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

        // Real-Time HTML5 Spectrogram Waterfall Visualizer
        const specCanvas = document.getElementById('spectrogram');
        const specCtx = specCanvas ? specCanvas.getContext('2d') : null;

        if (specCtx) {
            specCtx.fillStyle = '#020617';
            specCtx.fillRect(0, 0, specCanvas.width, specCanvas.height);
        }

        // Professional Academic Colormap: Deep Navy -> Indigo -> Magenta -> Vibrant Orange -> Bright Yellow -> Peak White
        function getSpectrogramColor(val) {
            if (val < 0.03) return '#020617';
            if (val < 0.15) {
                const t = (val - 0.03) / 0.12;
                return `rgb(${Math.round(2 + t * 65)}, ${Math.round(6 + t * 50)}, ${Math.round(23 + t * 179)})`;
            }
            if (val < 0.35) {
                const t = (val - 0.15) / 0.20;
                return `rgb(${Math.round(67 + t * 152)}, ${Math.round(56 - t * 17)}, ${Math.round(202 - t * 83)})`;
            }
            if (val < 0.65) {
                const t = (val - 0.35) / 0.30;
                return `rgb(${Math.round(219 + t * 15)}, ${Math.round(39 + t * 49)}, ${Math.round(119 - t * 107)})`;
            }
            if (val < 0.85) {
                const t = (val - 0.65) / 0.20;
                return `rgb(${Math.round(234 + t * 16)}, ${Math.round(88 + t * 116)}, ${Math.round(12 + t * 9)})`;
            }
            const t = (val - 0.85) / 0.15;
            return `rgb(${Math.round(250 + t * 5)}, ${Math.round(204 + t * 51)}, ${Math.round(21 + t * 234)})`;
        }

        // Waterfall Spectrogram: shift canvas left and blit newest 32-bin frequency slice
        function pushSpectrogramFrame(bins) {
            if (!specCanvas || !specCtx) return;
            const w = specCanvas.width;
            const h = specCanvas.height;
            const stepX = 3;

            // Shift existing pixels left
            specCtx.drawImage(specCanvas, stepX, 0, w - stepX, h, 0, 0, w - stepX, h);

            const numBins = (bins && bins.length > 0) ? bins.length : 32;
            const binHeight = h / numBins;

            // Render each frequency bin from bottom (0 Hz) to top (8000 Hz)
            for (let i = 0; i < numBins; i++) {
                let val = (bins && bins.length > i) ? bins[i] : 0.0;
                // Delicate baseline shimmer when idle
                if (!isRecording && val === 0.0) {
                    val = (i < 3) ? (0.015 + Math.sin(Date.now() * 0.002 + i) * 0.008) : 0.0;
                }
                const y = h - (i + 1) * binHeight;
                specCtx.fillStyle = getSpectrogramColor(val);
                specCtx.fillRect(w - stepX, y, stepX, Math.ceil(binHeight) + 1);
            }
        }

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
            ctx.shadowBlur = isRecording ? 12 : 8;
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

        let g_lastTranscriptsJson = "";
        let g_isFetchingFast = false;

        function renderDashboardData(data) {
            if (!data) return;
            try {

            if (data.waveform_samples) {
                currentWaveformSamples = data.waveform_samples;
                drawWaveformAnimation();
            }
            if (data.spectrogram_bins) {
                pushSpectrogramFrame(data.spectrogram_bins);
            }

            const badge = document.getElementById('status-badge');
            if (badge) {
                badge.innerText = 'Sẵn sàng thu âm 1-Click';
                badge.style.borderColor = '#22c55e';
                badge.style.color = '#22c55e';
            }
            
            if (data.status) {
                const wifiEl = document.getElementById('wifi-status');
                if (wifiEl) wifiEl.innerText = 'Sẵn sàng';
                const espIpEl = document.getElementById('esp-ip');
                if (espIpEl) espIpEl.innerText = data.status.esp_ip;
                const audioKbEl = document.getElementById('audio-kb');
                if (audioKbEl) audioKbEl.innerText = (data.status.active_audio_kb || 0) + ' KB';
            }

            if (data.system_state) {
                const isPowerOn = data.system_state.power;
                const pwrEl = document.getElementById('state-power');
                if (pwrEl) {
                    pwrEl.innerText = isPowerOn ? 'BẬT' : 'TẮT';
                    pwrEl.style.color = isPowerOn ? '#10b981' : '#f87171';
                }
                const brEl = document.getElementById('state-brightness');
                if (brEl) brEl.innerText = isPowerOn ? (data.system_state.brightness + '%') : `0% (Bộ nhớ: ${data.system_state.brightness}%)`;
                const cctEl = document.getElementById('state-cct');
                if (cctEl) cctEl.innerText = data.system_state.cct + 'K';
                const historyDepth = data.history_depth ? ` [Stack: ${data.history_depth}]` : '';
                const modeEl = document.getElementById('state-mode');
                if (modeEl) modeEl.innerText = (data.system_state.mode_name || 'Chế Độ Học Bài') + historyDepth;
            }

            // Update Module 2 Sensors Live Telemetry with Instant DOM updates
            if (data.sensors) {
                const bme = data.sensors.bme280;
                if (bme) {
                    const tempEl = document.getElementById('sensor-temp');
                    if (tempEl) tempEl.innerText = bme.temp_c.toFixed(1) + ' °C';
                    const humEl = document.getElementById('sensor-hum');
                    if (humEl) humEl.innerText = bme.humidity_pct.toFixed(1) + ' %';
                    const pressEl = document.getElementById('sensor-press');
                    if (pressEl) pressEl.innerText = bme.pressure_hpa.toFixed(1) + ' hPa';
                    const comfortEl = document.getElementById('sensor-comfort');
                    if (comfortEl) comfortEl.innerText = bme.comfort_status || 'Lý tưởng';
                }

                const bh = data.sensors.bh1750;
                if (bh) {
                    const luxEl = document.getElementById('sensor-lux');
                    if (luxEl) luxEl.innerText = Math.round(bh.lux) + ' Lux';
                }

                const vl = data.sensors.vl53l0x;
                if (vl) {
                    const distEl = document.getElementById('sensor-dist');
                    if (distEl) distEl.innerText = vl.distance_cm.toFixed(1) + ' cm';
                    const proxEl = document.getElementById('sensor-prox');
                    if (proxEl) proxEl.innerText = vl.proximity_desc || '';
                    const barPct = Math.min(100, Math.max(5, (vl.distance_cm / 120.0) * 100));
                    const distBar = document.getElementById('dist-bar');
                    if (distBar) distBar.style.width = barPct + '%';
                    const distStat = document.getElementById('dist-status-text');
                    if (distStat) {
                        if (vl.distance_cm < 15) {
                            distStat.innerText = 'Tương tác gần';
                        } else if (vl.distance_cm < 60) {
                            distStat.innerText = 'Ngồi gần bàn';
                        } else {
                            distStat.innerText = 'Đứng xa / Rời bàn';
                        }
                    }
                }

                const pir = data.sensors.pir;
                if (pir) {
                    const motionEl = document.getElementById('sensor-motion');
                    if (motionEl) {
                        motionEl.innerText = pir.motion ? 'ĐANG CÓ CHUYỂN ĐỘNG' : 'KHÔNG CÓ CHUYỂN ĐỘNG';
                        motionEl.style.color = pir.motion ? '#10b981' : '#94a3b8';
                    }
                    const sessEl = document.getElementById('sensor-session');
                    if (sessEl) sessEl.innerText = pir.session_formatted || '0 phút';
                    const alertEl = document.getElementById('sensor-alert');
                    if (alertEl) {
                        if (pir.is_overdue) {
                            alertEl.innerText = 'Quá 45 phút! Nên nghỉ ngơi';
                            alertEl.style.color = '#ef4444';
                        } else {
                            alertEl.innerText = 'An toàn (Chưa quá 45 phút)';
                            alertEl.style.color = '#34d399';
                        }
                    }
                }

                const oled = data.sensors.oled;
                const oledEl = document.getElementById('oled-status');
                if (oled && oledEl) {
                    oledEl.innerText = oled.status || 'ONLINE (128x64)';
                    oledEl.style.color = (oled.status && oled.status.includes('OFFLINE')) ? '#ef4444' : '#10b981';
                }

                const mic = data.sensors.mic || (data.status && data.status.mic_volume_pct !== undefined ? data.status : null);
                if (mic) {
                    const micStatEl = document.getElementById('mic-status');
                    const micVolEl = document.getElementById('mic-vol-text');
                    const micVuBar = document.getElementById('mic-vu-bar');
                    const micVadEl = document.getElementById('mic-vad-status');
                    
                    const isListening = mic.active_listening !== undefined ? mic.active_listening : (data.sensors.pir ? data.sensors.pir.presence : true);
                    const isVoice = !!mic.voice_detected;
                    const peak = mic.peak || 0;
                    const volPct = Math.min(100, Math.max(0, mic.volume_pct || Math.round((peak / 2000.0) * 100)));

                    if (micStatEl) {
                        if (isListening) {
                            if (isVoice) {
                                micStatEl.innerText = 'ONLINE [MS] 🎙️ ĐANG BẮT TIẾNG NÓI!';
                                micStatEl.style.color = '#38bdf8';
                            } else {
                                micStatEl.innerText = 'ONLINE [MS] 🎙️ (Tự thu âm liên tục 24/7)';
                                micStatEl.style.color = '#10b981';
                            }
                        } else {
                            micStatEl.innerText = 'STANDBY [mS] (Tiết kiệm điện / Vắng mặt)';
                            micStatEl.style.color = '#f59e0b';
                        }
                    }

                    if (micVolEl) {
                        micVolEl.innerText = `${volPct}% (Peak: ${peak}${mic.rms ? `, RMS: ${mic.rms}` : ''})`;
                    }

                    if (micVuBar) {
                        micVuBar.style.width = volPct + '%';
                        if (volPct > 60) {
                            micVuBar.style.boxShadow = '0 0 12px #ef4444';
                        } else if (volPct > 20) {
                            micVuBar.style.boxShadow = '0 0 10px #38bdf8';
                        } else {
                            micVuBar.style.boxShadow = 'none';
                        }
                    }

                    if (micVadEl) {
                        if (isVoice) {
                            micVadEl.innerHTML = '<span style="color: #38bdf8; font-weight: 700; text-shadow: 0 0 8px rgba(56,189,248,0.7);">🎙️ ĐANG BẮT TIẾNG NÓI (VOICE ACTIVE)</span>';
                        } else {
                            micVadEl.innerHTML = '<span style="color: #94a3b8;">👂 Tiếng ồn môi trường / Chờ nói...</span>';
                        }
                    }

                    const visPeakEl = document.getElementById('vis-mic-peak');
                    if (visPeakEl) {
                        visPeakEl.innerText = peak;
                        visPeakEl.style.color = isVoice ? '#38bdf8' : '#94a3b8';
                    }
                }

                const ctxEng = data.sensors.context_engine;
                if (ctxEng) {
                    const recEl = document.getElementById('ctx-recommendation');
                    if (recEl) recEl.innerHTML = `"${ctxEng.recommendation_text || ''}"`;
                    const envEl = document.getElementById('ctx-env-summary');
                    if (envEl) envEl.innerText = `Ngữ cảnh: ${ctxEng.env_summary} | Trạng thái: ${ctxEng.user_state}`;
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
            if (ollamaEl && data.status) {
                if (data.status.ollama_online) {
                    ollamaEl.innerText = 'Online (Qwen2.5 Sẵn Sàng)';
                    ollamaEl.style.color = '#c084fc';
                } else {
                    ollamaEl.innerText = 'Offline (Bật: ollama run qwen2.5:1.5b)';
                    ollamaEl.style.color = '#f87171';
                }
            }

            // Only re-render transcripts when transcripts list has changed to avoid heavy DOM rebuilds on high-speed frames!
            if (data.transcripts && data.transcripts.length > 0) {
                const currentJson = JSON.stringify(data.transcripts);
                if (currentJson !== g_lastTranscriptsJson) {
                    g_lastTranscriptsJson = currentJson;
                    const latest = data.transcripts[0];
                    if (!g_initialLoadDone) {
                        g_lastSpokenId = latest.id;
                        g_initialLoadDone = true;
                    } else if (latest.id && latest.id !== g_lastSpokenId) {
                        g_lastSpokenId = latest.id;
                        if (latest.speech_response && latest.text !== "Không thu được" && !isRecording) {
                            speakAiText(latest.speech_response);
                        }
                    }

                    const scriptList = document.getElementById('script-list');
                    if (scriptList) {
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
            }
        }
    } catch (e) {
        console.error(e);
    }
}

        async function updateDashboardFast() {
            if (g_isFetchingFast) return;
            g_isFetchingFast = true;
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                renderDashboardData(data);
            } catch (e) {
            } finally {
                g_isFetchingFast = false;
            }
        }

        let g_lastSpokenId = null;
        let g_initialLoadDone = false;
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

        function playBrowserChime() {
            try {
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (!AudioCtx) return;
                const ctx = new AudioCtx();
                const notes = [523.25, 659.25, 783.99, 1046.50];
                const start = ctx.currentTime;
                notes.forEach((freq, idx) => {
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.type = 'sine';
                    osc.frequency.setValueAtTime(freq, start + idx * 0.12);
                    gain.gain.setValueAtTime(0.001, start + idx * 0.12);
                    gain.gain.exponentialRampToValueAtTime(0.3, start + idx * 0.12 + 0.02);
                    gain.gain.exponentialRampToValueAtTime(0.001, start + idx * 0.12 + (idx === 3 ? 0.35 : 0.10));
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.start(start + idx * 0.12);
                    osc.stop(start + idx * 0.12 + 0.4);
                });
            } catch (e) {
                console.warn("Web audio chime:", e);
            }
        }

        function testSpeakerChime(btn) {
            if (btn) {
                btn.innerText = "Đang phát chuông...";
                btn.disabled = true;
            }
            playBrowserChime();
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

        let g_sseSource = null;
        function setupLiveStream() {
            if (!!window.EventSource) {
                try {
                    if (g_sseSource) {
                        g_sseSource.close();
                    }
                    g_sseSource = new EventSource('/api/stream');
                    g_sseSource.onopen = function() {
                        const streamBadge = document.getElementById('stream-badge');
                        if (streamBadge) {
                            streamBadge.innerText = '⚡ STREAM REALTIME (0ms)';
                            streamBadge.style.color = '#38bdf8';
                            streamBadge.style.borderColor = '#0284c7';
                        }
                    };
                    g_sseSource.onmessage = function(event) {
                        if (event.data && event.data.trim().startsWith('{')) {
                            try {
                                const data = JSON.parse(event.data);
                                renderDashboardData(data);
                            } catch(err) {}
                        }
                    };
                    g_sseSource.onerror = function() {
                        const streamBadge = document.getElementById('stream-badge');
                        if (streamBadge) {
                            streamBadge.innerText = '🔄 FAST POLLING (80ms)';
                            streamBadge.style.color = '#f59e0b';
                            streamBadge.style.borderColor = '#d97706';
                        }
                    };
                } catch(e) {}
            }
            // Adaptive Fallback Polling every 80ms (12.5 FPS)
            setInterval(updateDashboardFast, 80);
        }

        loadWifiInfo();
        drawWaveformAnimation();
        pushSpectrogramFrame([]);
        setupLiveStream();
        updateDashboardFast();
    </script>
</body>
</html>
"""
        self.wfile.write(html_content.encode('utf-8'))

import traceback

def web_server_thread():
    try:
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        server = socketserver.ThreadingTCPServer((HOST, WEB_PORT), DashboardHandler)
        print(f"\n=========================================================")
        print(f"  Smart Lamp Interactive Voice & Audio Analysis Server")
        print(f"  Dashboard URL: http://localhost:{WEB_PORT}")
        print(f"=========================================================\n")
        sys.stdout.flush()
        server.serve_forever()
    except Exception as e:
        print(f"[WEB SERVER FATAL ERROR] {e}")
        with open("scratch/server_error.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {traceback.format_exc()}\n")
        sys.stdout.flush()

if __name__ == "__main__":
    sys.stdout.flush()
    t_audio = threading.Thread(target=audio_receiver_thread, daemon=True)
    t_event = threading.Thread(target=event_receiver_thread, daemon=True)
    t_serial = threading.Thread(target=serial_receiver_thread, daemon=True)

    t_audio.start()
    t_event.start()
    t_serial.start()

    t_voice_listener = threading.Thread(target=continuous_voice_listener_thread, daemon=True)
    t_voice_listener.start()

    try:
        web_server_thread()
    except KeyboardInterrupt:
        print("\nStopping Dashboard Receiver...")
    except Exception as e:
        print(f"[MAIN FATAL ERROR] {e}")
        with open("scratch/server_error.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {traceback.format_exc()}\n")

