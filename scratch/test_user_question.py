import sys
import os
import re

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.append(os.path.abspath("scratch"))
from udp_receiver import parse_multi_intent_speech

def test():
    query = "đèn bàn nên để ánh sáng gì khi tôi đọc sách ban đêm"
    print(f"👉 Input Query: \"{query}\"")
    actions = parse_multi_intent_speech(query)
    print(f"Actions Parsed ({len(actions)} items):")
    for act in actions:
        print(f"Engine: {act.get('engine')}")
        print(f"Intent: {act.get('intent_name')} (ID: {act.get('cmd')} | Mode: {act.get('mode')})")
        print(f"AI Speech Response: '{act.get('speech_response', '')}'")

if __name__ == "__main__":
    test()
