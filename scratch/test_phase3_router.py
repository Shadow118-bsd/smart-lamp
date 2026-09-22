import sys
import os
import json

# Add scratch dir to path
sys.path.append(os.path.abspath("scratch"))

from udp_receiver import parse_multi_intent_speech, query_local_slm_intent

def test_phase3_offloading():
    print("=========================================================")
    print("   TESTING PHASE 3: EDGE-FIRST SLM OFFLOADING ROUTER   ")
    print("=========================================================\n")

    test_queries = [
        # Direct commands (Should hit Local Fast Path ~1ms)
        "Bật đèn rồi chuyển chế độ đọc sách và tăng sáng 20%",
        "Tắt đèn",
        
        # Conversational / Open Context queries (Should attempt Local SLM Offload)
        "Tôi chuẩn bị học bài đêm thì nên chỉnh đèn thế nào cho tốt mắt?",
        "Tạo không khí ấm cúng để nghỉ ngơi"
    ]

    for q in test_queries:
        print(f"👉 Input Query: \"{q}\"")
        actions = parse_multi_intent_speech(q)
        print(f"   Actions Parsed ({len(actions)} items):")
        for idx, act in enumerate(actions, 1):
            print(f"   - Lệnh {idx}: Clause='{act['clause']}', CMD={act['cmd']}, Val={act['val']}, Mode={act['mode']}, Intent='{act['intent_name']}', Score={act['score']}%")
        print("-" * 65)

if __name__ == "__main__":
    test_phase3_offloading()
