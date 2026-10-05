import sys
import os
import urllib.request
import urllib.parse

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import edge_tts
import miniaudio
import asyncio

async def test_edge(v, t):
    try:
        comm = edge_tts.Communicate(t, v)
        buf = bytearray()
        async for c in comm.stream():
            if c['type'] == 'audio':
                buf.extend(c['data'])
        print(f"[EDGE OK] Voice '{v}' generated {len(buf)} bytes!")
        return buf
    except Exception as e:
        print(f"[EDGE FAIL] Voice '{v}' error: {e}")
        return None

# Test multiple Vietnamese Edge TTS voice identifiers
asyncio.run(test_edge("vi-VN-HoaiMyNeural", "Xin chào bạn"))
asyncio.run(test_edge("vi-VN-NamMinhNeural", "Xin chào bạn"))

# Test Google Translate TTS fallback
try:
    text = "Vâng! Đã chuyển sang Chế Độ Thư Giãn cho bạn!"
    encoded = urllib.parse.quote(text)
    req_url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q={encoded}"
    req = urllib.request.Request(req_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=3.0) as resp:
        gdata = resp.read()
        print(f"[GOOGLE TTS OK] Generated {len(gdata)} bytes MP3!")
        decoded = miniaudio.decode(gdata, nchannels=1, sample_rate=16000)
        print(f"[GOOGLE TTS DECODE OK] Decoded {len(decoded.samples)} PCM samples!")
except Exception as e:
    print(f"[GOOGLE TTS FAIL] {e}")
