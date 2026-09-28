import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.append(os.path.abspath("scratch"))
import udp_receiver

def test_multi_clause_revert():
    print("===================================================================")
    print("   TESTING MULTI-CLAUSE WITH REVERT (chuyển sang chế độ cũ Bật Đèn Tắt Đèn)  ")
    print("===================================================================\n")

    udp_receiver.g_system_state = {'power': True, 'brightness': 80, 'cct': 4000, 'mode': 2, 'mode_name': 'Chế Độ Học Bài'}
    udp_receiver.g_previous_state = {'power': True, 'brightness': 50, 'cct': 3000, 'mode': 1, 'mode_name': 'Chế Độ Thư Giãn'}

    phrase = "chuyển sang chế độ cũ Bật Đèn Tắt Đèn"
    actions = udp_receiver.parse_multi_intent_speech(phrase)
    print(f"Phrase: '{phrase}'")
    print(f"Parsed {len(actions)} actions:")
    for i, a in enumerate(actions, 1):
        print(f"  [{i}] Clause='{a['clause']}', CMD={a['cmd']}, Intent='{a['intent_name']}'")

    speech = udp_receiver.update_system_state(actions)
    print(f"\nSpeech Response: '{speech}'")
    print(f"Updated actions:")
    for i, a in enumerate(actions, 1):
        print(f"  [{i}] Clause='{a['clause']}', CMD={a['cmd']}, Intent='{a['intent_name']}'")

    print(f"\nFinal State: Mode={udp_receiver.g_system_state.get('mode_name')}, Power={udp_receiver.g_system_state.get('power')}")

if __name__ == "__main__":
    test_multi_clause_revert()
