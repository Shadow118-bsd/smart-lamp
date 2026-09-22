import sys
import os

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.append(os.path.abspath("scratch"))
from udp_receiver import parse_multi_intent_speech, update_system_state, g_system_state, g_previous_state

def print_states():
    print(f"   ➔ Current State:  Mode={g_system_state.get('mode_name')} (ID: {g_system_state.get('mode')}), Brightness={g_system_state.get('brightness')}%, Power={g_system_state.get('power')}")
    print(f"   ➔ Previous State: Mode={g_previous_state.get('mode_name')} (ID: {g_previous_state.get('mode')}), Brightness={g_previous_state.get('brightness')}%, Power={g_previous_state.get('power')}\n")

def test():
    print("=== STEP 1: Execute 'bật chế độ học bài' (Mode 2) ===")
    actions1 = parse_multi_intent_speech("bật chế độ học bài")
    update_system_state(actions1)
    print_states()

    print("=== STEP 2: Execute 'chuyển sang chế độ ban đêm' (Mode 4) ===")
    actions2 = parse_multi_intent_speech("chuyển sang chế độ ban đêm")
    update_system_state(actions2)
    print_states()

    print("=== STEP 3: Execute 'chuyển về chế độ học bài' (Mode 2) ===")
    actions3 = parse_multi_intent_speech("chuyển về chế độ học bài")
    update_system_state(actions3)
    print_states()

    print("=== STEP 4: REVERT 1: Execute 'chuyển lại chế độ cũ' ===")
    actions_rev1 = parse_multi_intent_speech("chuyển lại chế độ cũ")
    update_system_state(actions_rev1)
    print_states()

    print("=== STEP 5: REVERT 2 (TOGGLE BACK): Execute 'chuyển lại chế độ cũ' ===")
    actions_rev2 = parse_multi_intent_speech("chuyển lại chế độ cũ")
    update_system_state(actions_rev2)
    print_states()

    print("=== STEP 6: REVERT 3 (TOGGLE AGAIN): Execute 'chuyển lại chế độ cũ' ===")
    actions_rev3 = parse_multi_intent_speech("chuyển lại chế độ cũ")
    update_system_state(actions_rev3)
    print_states()

if __name__ == "__main__":
    test()
