import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.append(os.path.abspath("scratch"))
from udp_receiver import parse_multi_intent_speech

def test():
    queries = [
        "Hôm nay trời hơi âm u thì nên bật đèn bàn màu gì vậy",
        "bây giờ trời hơi sáng thì nên chỉnh độ sáng như thế nào và sang chế độ gì thì hợp lý"
    ]
    for q in queries:
        print(f"👉 Input Query: \"{q}\"")
        actions = parse_multi_intent_speech(q)
        for act in actions:
            print(f"   Engine: {act.get('engine')}")
            print(f"   Intent: {act.get('intent_name')} (ID: {act.get('cmd')} | Mode: {act.get('mode')})")
            print(f"   AI Speech Response: '{act.get('speech_response', '')}'")
        print("-" * 65)

if __name__ == "__main__":
    test()
