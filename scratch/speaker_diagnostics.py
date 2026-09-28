import os
import sys

# Ensure UTF-8 output on Windows console
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

import time
import numpy as np
import serial

PORT = "COM3"
BAUD = 115200

def get_serial_conn():
    print(f"[DIAG] Kết nối tới ESP32 trên cổng {PORT}...")
    try:
        s = serial.Serial(PORT, BAUD, timeout=1.5)
        time.sleep(1.0)
        print(f"[DIAG] Kết nối thành công tới {PORT}!")
        return s
    except Exception as e:
        print(f"[DIAG LỖI] Không thể mở cổng {PORT}: {e}")
        return None

def send_pcm_to_esp32(s, pcm_bytes, desc=""):
    total_bytes = len(pcm_bytes)
    # Ensure even number of bytes for 16-bit PCM
    if total_bytes % 2 != 0:
        pcm_bytes = pcm_bytes[:-1]
        total_bytes -= 1

    print(f"\n>>> [ĐANG PHÁT] {desc} ({total_bytes} bytes PCM, ~{total_bytes/32000:.1f}s)")
    
    # 1. Clear any pending input
    s.reset_input_buffer()
    
    # 2. Send header
    header = f"[VOICE_START:16000:{total_bytes}]\n".encode('utf-8')
    s.write(header)
    s.flush()

    # 3. Wait for ACK_READY from ESP32
    t_start = time.time()
    ack_ok = False
    while time.time() - t_start < 2.0:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if "ACK_READY" in line:
            ack_ok = True
            break
            
    if not ack_ok:
        print("    [!] Cảnh báo: Không nhận được ACK_READY từ ESP32, đang thử gửi trực tiếp...")
    else:
        print("    -> Đã nhận bắt tay ACK_READY từ ESP32! Đang truyền dữ liệu chuẩn 16-bit...")

    # 4. Send binary chunks with small inter-packet pacing
    chunk_size = 512
    t0 = time.time()
    for offset in range(0, total_bytes, chunk_size):
        chunk = pcm_bytes[offset:offset+chunk_size]
        s.write(chunk)
        time.sleep(0.003) # 3ms pacing = ~150 KB/s (an toàn tuyệt đối cho buffer 16KB)
    s.flush()
    elapsed = time.time() - t0
    print(f"    Tốc độ truyền: {total_bytes/elapsed/1024:.1f} KB/s")

    # 5. Read confirmation from ESP32
    t_wait = time.time()
    while time.time() - t_wait < 1.0:
        line = s.readline().decode('utf-8', errors='ignore').strip()
        if "[AUDIO RX DONE]" in line:
            print(f"    -> ESP32 báo cáo: {line}")
            break

    # Chờ loa phát xong
    play_time = (total_bytes / 32000.0) + 0.2
    time.sleep(play_time)
    print(f"    -> Đã phát xong!")

def test_chime(s):
    print("\n>>> [ĐANG PHÁT] Bài Test 0: Nhạc chuông khởi động chuẩn (C5 -> E5 -> G5 -> C6)")
    s.write(b"PLAY_CHIME\n")
    s.flush()
    time.sleep(1.2)

def test_sine(s, freq=1000, duration_sec=1.5, volume=0.25):
    desc = f"Bài Test 1: Sóng sin thuần khiết {freq}Hz (Volume {int(volume*100)}%) qua USB"
    sample_rate = 16000
    total_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, total_samples, endpoint=False)
    
    env = np.ones(total_samples)
    fade_len = int(sample_rate * 0.05)
    env[:fade_len] = np.linspace(0, 1, fade_len)
    env[-fade_len:] = np.linspace(1, 0, fade_len)
    
    waveform = np.sin(2 * np.pi * freq * t) * env * volume * 32767.0
    pcm_bytes = waveform.astype(np.int16).tobytes()
    send_pcm_to_esp32(s, pcm_bytes, desc)

def test_tts_voice(s, text, volume_scale=0.30, desc=""):
    import asyncio, edge_tts, miniaudio
    comm = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural")
    buf = bytearray()
    async def _fetch():
        async for c in comm.stream():
            if c['type'] == 'audio': buf.extend(c['data'])
    asyncio.run(_fetch())
    
    decoded = miniaudio.decode(bytes(buf), nchannels=1, sample_rate=16000)
    raw = np.frombuffer(decoded.samples, dtype=np.int16)
    
    # Áp dụng mức volume scale
    scaled = (raw * volume_scale).astype(np.int16)
    send_pcm_to_esp32(s, scaled.tobytes(), desc)

if __name__ == "__main__":
    s = get_serial_conn()
    if not s:
        sys.exit(1)
        
    print("=" * 65)
    print("  KIỂM TRA ÂM THANH SAU KHI CẮM CHÂN GAIN -> GND (+9dB)")
    print("=" * 65)
    
    try:
        # 1. Nhạc chuông
        test_chime(s)
        time.sleep(1.0)
        
        # 2. Sóng sin 1000Hz
        test_sine(s, freq=1000, duration_sec=1.5, volume=0.25)
        time.sleep(1.0)
        
        # 3. Giọng nói câu chào (Volume 30% - Mức chuẩn cho Gain 9dB)
        test_tts_voice(
            s, 
            text="Xin chào bạn, tôi là đèn học thông minh. Chân ghen đã nối đất chín đề-xi-ben rất êm ái.",
            volume_scale=0.30,
            desc="Bài Test 2: Giọng nói AI chào hỏi (Volume 30% - Chuẩn Gain 9dB)"
        )
        time.sleep(1.2)
        
        # 4. Giọng nói phản hồi lệnh thực tế của hệ thống
        test_tts_voice(
            s,
            text="Đã bật đèn học, độ sáng tám mươi phần trăm. Nhiệt độ phòng hai mươi tám độ C.",
            volume_scale=0.30,
            desc="Bài Test 3: Giọng nói phản hồi lệnh thực tế (Volume 30%)"
        )
        
        print("\n" + "=" * 65)
        print("  HOÀN THÀNH TẤT CẢ CÁC BÀI TEST ÂM THANH!")
        print("=" * 65)
    finally:
        s.close()
        print("[DIAG] Đã giải phóng cổng COM3.")
