import sys
sys.stdout.reconfigure(encoding='utf-8')
from udp_receiver import parse_multi_intent_speech, parse_vietnamese_command, update_system_state, g_system_state, g_previous_state

test_cases = [
    "chuyển sang chế độ thư giãn tăng độ sáng lên 100%",
    "chuyển sang chế độ thư giãn tăng sáng",
    "chuyển sang chế độ học bài giảm độ sáng xuống 50%",
    "chuyển sang chế độ đọc sách độ sáng 100%",
    "chế độ ban đêm giảm độ sáng còn 10%",
    "chế độ thư giãn và tăng độ sáng thêm 20%",
    "bật đèn chuyển sang chế độ thư giãn tăng độ sáng lên 90%",
    "chuyển sang chế độ tối đa",
    "tắt đèn chuyển sang chế độ thư giãn tăng độ sáng lên 100%",
    "chuyển sang chế độ cũ Bật Đèn Tắt Đèn"
]

print("================================================================")
print("      TESTING LIVE UDP_RECEIVER MULTI-CLAUSE & BRIGHTNESS      ")
print("================================================================")

for tc in test_cases:
    print("=" * 60)
    print("USER SAID:", tc)
    acts = parse_multi_intent_speech(tc)
    for i, a in enumerate(acts, 1):
        print(f"  [{i}] Clause='{a['clause']}', CMD={a['cmd']}, Val={a['val']}, Mode={a['mode']}, Type={a['param_type']}, Intent='{a['intent_name']}'")
    speech = update_system_state(acts)
    print("  AI SPEECH:", speech)
    print(f"  FINAL STATE: Mode='{g_system_state['mode_name']}' (ID={g_system_state['mode']}), Power={g_system_state['power']}, Brightness={g_system_state['brightness']}%, CCT={g_system_state['cct']}K")
