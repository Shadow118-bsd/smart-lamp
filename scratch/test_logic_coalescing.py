import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.append(os.path.abspath("scratch"))
import udp_receiver

def test():
    test_cases = [
        "tắt đèn chuyển sang chế độ thư giãn",
        "chuyển sang chế độ thư giãn rồi tắt đèn",
        "bật đèn tắt đèn",
        "tắt đèn bật đèn",
        "chuyển sang chế độ cũ Bật Đèn Tắt Đèn",
        "chuyển sang chế độ cũ Tắt Đèn Bật Đèn"
    ]

    for phrase in test_cases:
        print(f"\n==================================================")
        print(f"Phrase: '{phrase}'")
        udp_receiver.g_system_state = {'power': True, 'brightness': 70, 'cct': 4000, 'mode': 2, 'mode_name': 'Chế Độ Học Bài'}
        udp_receiver.g_previous_state = {'power': True, 'brightness': 50, 'cct': 3000, 'mode': 1, 'mode_name': 'Chế Độ Thư Giãn'}

        actions = udp_receiver.parse_multi_intent_speech(phrase)
        for i, a in enumerate(actions, 1):
            print(f"  [{i}] '{a['clause']}' -> CMD: {a['cmd']}")

        speech = udp_receiver.update_system_state(actions)
        print(f"Speech: '{speech}'")
        print(f"Final State: Mode={udp_receiver.g_system_state.get('mode_name')}, Power={udp_receiver.g_system_state.get('power')}, Brightness={udp_receiver.g_system_state.get('brightness')}%, CCT={udp_receiver.g_system_state.get('cct')}K")

if __name__ == "__main__":
    test()
