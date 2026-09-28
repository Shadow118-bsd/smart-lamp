import miniaudio
import numpy as np

with open('scratch/test_voice.mp3', 'rb') as f:
    mp3_bytes = f.read()

decoded = miniaudio.decode(mp3_bytes, nchannels=1, sample_rate=16000)
raw_samples = np.frombuffer(decoded.samples, dtype=np.int16).astype(np.float32)

# Attenuate to 28% volume to match udp_receiver.py and eliminate MAX98357A Class-D clipping
clean_samples = raw_samples * 0.28

# Add 10ms (160 samples) smooth fade-in and fade-out to prevent clicks
fade_len = min(160, len(clean_samples))
fade_in = np.linspace(0.0, 1.0, fade_len)
fade_out = np.linspace(1.0, 0.0, fade_len)
clean_samples[:fade_len] *= fade_in
clean_samples[-fade_len:] *= fade_out

samples = clean_samples.astype(np.int16)
num_samples = len(samples)
print(f"Decoded & attenuated {num_samples} samples (duration = {num_samples/16000.0:.2f}s, max amp = {np.max(np.abs(samples))})")

header_lines = [
    "#pragma once",
    "#include <Arduino.h>",
    "",
    "// Pre-rendered 16kHz 16-bit Mono PCM audio for boot voice prompt",
    "// Speech: 'Chao ban, Toi la tro ly den thong minh'",
    f"#define BOOT_VOICE_SAMPLE_RATE 16000",
    f"#define BOOT_VOICE_SAMPLE_COUNT {num_samples}",
    "",
    "const int16_t boot_voice_pcm[BOOT_VOICE_SAMPLE_COUNT] PROGMEM = {"
]

chunk_size = 16
for i in range(0, num_samples, chunk_size):
    chunk = samples[i:i+chunk_size]
    line = "    " + ", ".join(str(int(val)) for val in chunk)
    if i + chunk_size < num_samples:
        line += ","
    header_lines.append(line)

header_lines.append("};")
header_lines.append("")

full_code = "\n".join(header_lines)

with open('src/boot_voice_data.h', 'w', encoding='utf-8') as f:
    f.write(full_code)

with open('main/smart_lamp_module2_sensors/boot_voice_data.h', 'w', encoding='utf-8') as f:
    f.write(full_code)

print("Generated src/boot_voice_data.h and main/smart_lamp_module2_sensors/boot_voice_data.h successfully!")
