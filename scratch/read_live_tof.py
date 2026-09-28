import urllib.request
import json
import time
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

for i in range(10):
    try:
        resp = urllib.request.urlopen('http://localhost:8088/api/data', timeout=1.0)
        data = json.loads(resp.read().decode())
        vl = data['sensors']['vl53l0x']
        print(f"Sample {i+1}: dist = {vl['distance_cm']:.1f} cm | is_user_near = {vl['is_user_near']} | is_hand_near = {vl['is_hand_near']}")
    except Exception as e:
        print(f"Sample {i+1}: Error: {e}")
    time.sleep(0.4)
