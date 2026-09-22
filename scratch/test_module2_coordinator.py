import sys
import os
import json
import re

# Set UTF-8 encoding on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

print("==================================================================")
print("  Module 2 Verification Test Suite — System Coordinator & NLU     ")
print("==================================================================")

# -------------------------------------------------------------------------
# 1. Phonetic Wake Word & Aliases Pattern Matcher
# -------------------------------------------------------------------------
WAKE_WORD_PATTERNS = [
    r"\bhey\s+shine\b",
    r"\bshine\b",
    r"\bhây\s+xai\b",
    r"\bhê\s+xai\b",
    r"\bxi\s*ne\b",
    r"\bxai\s+ơi\b",
    r"\bxai\b"
]

def check_wake_word(speech_text):
    """
    Checks if speech_text contains any registered phonetic wake word variant.
    Returns (has_wake_word, remaining_command_text, wake_matched)
    """
    text_lower = speech_text.lower().strip()
    for pat in WAKE_WORD_PATTERNS:
        match = re.search(pat, text_lower)
        if match:
            wake_matched = match.group(0)
            # Remove wake word from sentence to get remaining command
            remaining = text_lower[match.end():].strip()
            # Remove leading punctuation/conjunctions
            remaining = re.sub(r"^[,\.\s\-\?\!]+", "", remaining).strip()
            return True, remaining, wake_matched

    return False, text_lower, None


# -------------------------------------------------------------------------
# 2. Negation Guard Boundary Checker
# -------------------------------------------------------------------------
NEGATION_PREFIXES = ["đừng", "không", "chớ", "không được", "đừng có", "chớ có"]

def is_clause_negated(clause_text, verb_keyword):
    """
    Checks if verb_keyword in clause_text is preceded by any negation prefix.
    """
    text_lower = clause_text.lower().strip()
    pos = text_lower.find(verb_keyword.lower())
    if pos == -1:
        return False

    prefix_window = text_lower[max(0, pos - 20):pos].strip()
    for neg in NEGATION_PREFIXES:
        if neg in prefix_window:
            return True
    return False


# -------------------------------------------------------------------------
# 3. Action Verb Boundary Clause Splitter & Fast Path Parser
# -------------------------------------------------------------------------
ACTION_VERBS = ["bật", "mở", "tắt", "tăng", "giảm", "chuyển", "đổi", "chỉnh", "đặt"]

def split_into_clauses(speech_text):
    """
    Splits continuous multi-action speech into individual action clauses.
    Example: "bật đèn tăng độ sáng 20% chuyển chế độ học bài" -> ["bật đèn", "tăng độ sáng 20%", "chuyển chế độ học bài"]
    """
    text = speech_text.lower().strip()
    words = text.split()

    boundaries = []
    for idx, word in enumerate(words):
        clean_word = re.sub(r'[^\w\s]', '', word)
        if clean_word in ACTION_VERBS:
            # Avoid splitting if preceded by negation like "đừng"
            if idx > 0 and words[idx - 1] in NEGATION_PREFIXES:
                continue
            boundaries.append(idx)

    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    clauses = []
    for i in range(len(boundaries)):
        start_idx = boundaries[i]
        end_idx = boundaries[i + 1] if i + 1 < len(boundaries) else len(words)
        clause_str = " ".join(words[start_idx:end_idx]).strip()
        if clause_str:
            clauses.append(clause_str)

    return clauses


def parse_action_clause(clause):
    """
    Parses a single action clause into structured action object with ParamType & Negation flags.
    """
    clause_lower = clause.lower().strip()

    # Negation Check
    if any(neg in clause_lower for neg in NEGATION_PREFIXES):
        return {
            "clause": clause,
            "cmd": 0,
            "intent": "CMD_NONE",
            "is_negated": True,
            "ignore": True,
            "reason": "Negated by prefix"
        }

    # Extract numbers
    numbers = re.findall(r'\d+', clause_lower)
    val = int(numbers[0]) if numbers else None

    # 1. Revert / Alt-Tab Toggle (CMD 10)
    if "cũ" in clause_lower or "trở về" in clause_lower or "quay lại" in clause_lower or "ban đầu" in clause_lower:
        return {"clause": clause, "cmd": 10, "intent": "CMD_REVERT_MODE", "val": 0, "mode": 0, "param_type": "ABSOLUTE"}

    # 2. Brightness (Check first before power to avoid matching 'sáng' in 'độ sáng')
    if "tăng" in clause_lower and ("sáng" in clause_lower or val is not None):
        step = val if val is not None else 10
        return {"clause": clause, "cmd": 3, "intent": "CMD_SET_BRIGHTNESS", "val": step, "param_type": "RELATIVE"}

    if "giảm" in clause_lower and ("sáng" in clause_lower or val is not None):
        step = -val if val is not None else -10
        return {"clause": clause, "cmd": 3, "intent": "CMD_SET_BRIGHTNESS", "val": step, "param_type": "RELATIVE"}

    if ("đặt" in clause_lower or "chỉnh" in clause_lower) and "sáng" in clause_lower and val is not None:
        return {"clause": clause, "cmd": 3, "intent": "CMD_SET_BRIGHTNESS", "val": val, "param_type": "ABSOLUTE"}

    # 3. Power On / Off
    if "bật" in clause_lower or "mở" in clause_lower:
        if "chế độ" not in clause_lower:
            return {"clause": clause, "cmd": 1, "intent": "CMD_POWER_ON", "val": 0, "mode": 0, "param_type": "ABSOLUTE"}

    if "tắt" in clause_lower:
        return {"clause": clause, "cmd": 2, "intent": "CMD_POWER_OFF", "val": 0, "mode": 0, "param_type": "ABSOLUTE"}

    # 4. Color Temp (CCT)
    if "ấm" in clause_lower or "vàng" in clause_lower:
        step = val if val is not None else 500
        return {"clause": clause, "cmd": 7, "intent": "CMD_CCT_WARMER", "val": step, "param_type": "RELATIVE"}

    if "lạnh" in clause_lower or "trắng" in clause_lower:
        step = val if val is not None else 500
        return {"clause": clause, "cmd": 8, "intent": "CMD_CCT_COOLER", "val": step, "param_type": "RELATIVE"}

    if ("đặt" in clause_lower or "chỉnh" in clause_lower) and ("kelvin" in clause_lower or "k" in clause_lower) and val is not None:
        k_val = val if val >= 2400 else val * 100
        return {"clause": clause, "cmd": 11, "intent": "CMD_SET_CCT_EXACT", "val": k_val, "param_type": "ABSOLUTE"}

    # 5. Modes
    if "học" in clause_lower or "làm việc" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 2, "mode_name": "Chế Độ Học Bài", "val": 0, "param_type": "ABSOLUTE"}
    if "đọc" in clause_lower or "sách" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 3, "mode_name": "Chế Độ Đọc Sách", "val": 0, "param_type": "ABSOLUTE"}
    if "ngủ" in clause_lower or "ban đêm" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 4, "mode_name": "Chế Độ Ban Đêm", "val": 0, "param_type": "ABSOLUTE"}
    if "thư giãn" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 1, "mode_name": "Chế Độ Thư Giãn", "val": 0, "param_type": "ABSOLUTE"}
    if "máy tính" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 5, "mode_name": "Chế Độ Dùng Máy Tính", "val": 0, "param_type": "ABSOLUTE"}
    if "thiết kế" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 6, "mode_name": "Chế Độ Thiết Kế / High-CRI", "val": 0, "param_type": "ABSOLUTE"}
    if "hoàng hôn" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 7, "mode_name": "Chế Độ Hoàng Hôn", "val": 0, "param_type": "ABSOLUTE"}
    if "tối đa" in clause_lower or "100%" in clause_lower:
        return {"clause": clause, "cmd": 9, "intent": "CMD_SET_MODE", "mode": 8, "mode_name": "Chế Độ Tối Đa 100%", "val": 0, "param_type": "ABSOLUTE"}

    return {"clause": clause, "cmd": 0, "intent": "CMD_UNKNOWN", "val": 0, "mode": 0, "param_type": "ABSOLUTE"}


# -------------------------------------------------------------------------
# 4. State Coalescing & Aggregation Engine
# -------------------------------------------------------------------------
def coalesce_action_batch(actions, current_state, previous_state):
    """
    Evaluates action batch against current_state atomically.
    Computes final_target_state and returns (new_current_state, new_previous_state, unified_response_speech).
    """
    if not actions:
        return dict(current_state), dict(previous_state), "Chưa nhận diện được hành động nào."

    # Copy state to draft
    draft_state = dict(current_state)
    draft_prev = dict(previous_state)

    # Check for Alt-Tab Revert Command (cmd == 10)
    has_revert = any(act.get("cmd") == 10 for act in actions if not act.get("is_negated"))

    if has_revert:
        # Swap current <-> previous
        new_current = dict(draft_prev)
        new_prev = dict(draft_state)
        prev_name = new_current.get("mode_name", "Trạng Thái Trước")
        speech = f"Tôi đã khôi phục lại {prev_name} (Độ sáng {new_current.get('brightness')}%, {new_current.get('cct')}K) cho bạn!"
        return new_current, new_prev, speech

    # Save state snapshot to previous_state before mutating
    is_distinct = (
        draft_prev.get("mode") != draft_state.get("mode") or
        draft_prev.get("power") != draft_state.get("power") or
        abs(draft_prev.get("brightness", 0) - draft_state.get("brightness", 0)) >= 5
    )
    if is_distinct:
        draft_prev = dict(draft_state)

    applied_descriptions = []

    for act in actions:
        if act.get("is_negated") or act.get("ignore"):
            continue

        cmd = act.get("cmd", 0)
        val = act.get("val", 0)
        param_type = act.get("param_type", "ABSOLUTE")
        mode = act.get("mode", 0)

        if cmd == 1:
            draft_state["power"] = True
            applied_descriptions.append("bật đèn")
        elif cmd == 2:
            draft_state["power"] = False
            applied_descriptions.append("tắt đèn")
        elif cmd == 3:
            if param_type == "ABSOLUTE":
                draft_state["brightness"] = max(0, min(100, val))
                applied_descriptions.append(f"đặt độ sáng {draft_state['brightness']}%")
            else: # RELATIVE
                draft_state["brightness"] = max(0, min(100, draft_state["brightness"] + val))
                applied_descriptions.append(f"{'tăng' if val > 0 else 'giảm'} độ sáng {abs(val)}%")
        elif cmd == 7:
            draft_state["cct"] = max(2400, min(6500, draft_state["cct"] - val))
            applied_descriptions.append(f"chỉnh ánh sáng ấm hơn")
        elif cmd == 8:
            draft_state["cct"] = max(2400, min(6500, draft_state["cct"] + val))
            applied_descriptions.append(f"chỉnh ánh sáng trắng hơn")
        elif cmd == 11:
            draft_state["cct"] = max(2400, min(6500, val))
            applied_descriptions.append(f"đặt nhiệt màu {draft_state['cct']}K")
        elif cmd == 9:
            draft_state["mode"] = mode
            if mode == 1:
                draft_state["mode_name"] = "Chế Độ Thư Giãn"
                draft_state["cct"], draft_state["brightness"] = 3000, 50
            elif mode == 2:
                draft_state["mode_name"] = "Chế Độ Học Bài"
                draft_state["cct"], draft_state["brightness"] = 4000, 80
            elif mode == 3:
                draft_state["mode_name"] = "Chế Độ Đọc Sách"
                draft_state["cct"], draft_state["brightness"] = 3000, 70
            elif mode == 4:
                draft_state["mode_name"] = "Chế Độ Ban Đêm"
                draft_state["cct"], draft_state["brightness"] = 2700, 15
            elif mode == 5:
                draft_state["mode_name"] = "Chế Độ Dùng Máy Tính"
                draft_state["cct"], draft_state["brightness"] = 3500, 60
            elif mode == 6:
                draft_state["mode_name"] = "Chế Độ Thiết Kế / High-CRI"
                draft_state["cct"], draft_state["brightness"] = 5000, 90
            elif mode == 7:
                draft_state["mode_name"] = "Chế Độ Hoàng Hôn"
                draft_state["cct"], draft_state["brightness"] = 2400, 35
            elif mode == 8:
                draft_state["mode_name"] = "Chế Độ Tối Đa 100%"
                draft_state["cct"], draft_state["brightness"] = 5500, 100

            applied_descriptions.append(f"chuyển sang {draft_state['mode_name']}")

    # Build ONE Unified Response Speech
    if not applied_descriptions:
        speech = "Đã nhận câu lệnh của bạn nhưng không có thay đổi nào được thực thi."
    elif len(applied_descriptions) == 1:
        speech = f"Đã {applied_descriptions[0]} cho bạn!"
    else:
        desc_summary = ", ".join(applied_descriptions[:-1]) + " và " + applied_descriptions[-1]
        speech = f"Đã {desc_summary} cho bạn! (Trạng thái hiện tại: {draft_state.get('mode_name', 'Tùy chỉnh')}, Độ sáng {draft_state['brightness']}%, {draft_state['cct']}K)."

    return draft_state, draft_prev, speech


# -------------------------------------------------------------------------
# Test Cases & Verification Suite
# -------------------------------------------------------------------------
def run_tests():
    print("\n--- TEST CASE 1: Phonetic Wake Word Alias Matching ---")
    test_phrases = [
        "Hey Shine bật đèn học bài",
        "Hây Xai tăng độ sáng 20%",
        "Xi ne chuyển chế độ đọc sách",
        "Xai ơi tắt đèn",
        "bật đèn ban đêm" # No wake word
    ]

    for phrase in test_phrases:
        has_wake, cmd_text, matched = check_wake_word(phrase)
        print(f"Phrase: '{phrase}' -> HasWake: {has_wake} (Matched: '{matched}') | Remaining: '{cmd_text}'")
        assert (has_wake and matched is not None) if "bật đèn ban đêm" not in phrase else (not has_wake)


    print("\n--- TEST CASE 2: Multi-Action Batching & State Coalescing ---")
    initial_state = {"power": False, "brightness": 50, "cct": 3000, "mode": 1, "mode_name": "Chế Độ Thư Giãn"}
    initial_prev = {"power": True, "brightness": 50, "cct": 3000, "mode": 1, "mode_name": "Chế Độ Thư Giãn"}

    input_speech = "Bật đèn tăng độ sáng 20% chuyển chế độ học bài"
    clauses = split_into_clauses(input_speech)
    print(f"Input Speech: '{input_speech}'")
    print(f"Clauses Extracted ({len(clauses)}): {clauses}")
    assert len(clauses) == 3

    actions = [parse_action_clause(c) for c in clauses]
    print(f"Parsed Action Batch: {json.dumps(actions, ensure_ascii=False)}")

    final_state, final_prev, response_speech = coalesce_action_batch(actions, initial_state, initial_prev)
    print(f"Final Coalesced State: {json.dumps(final_state, ensure_ascii=False)}")
    print(f"Unified Response Speech: 🔊 \"{response_speech}\"")

    assert final_state["power"] == True
    assert final_state["mode"] == 2
    assert final_state["brightness"] == 80 # Mode 2 (Học bài) sets brightness to 80
    assert "chuyển sang Chế Độ Học Bài" in response_speech


    print("\n--- TEST CASE 3: Negation Guard Boundary Checker ---")
    negation_speech = "Đừng tắt đèn, tăng độ sáng 10%"
    neg_clauses = split_into_clauses(negation_speech)
    print(f"Negation Input: '{negation_speech}'")
    print(f"Extracted Clauses: {neg_clauses}")

    neg_actions = [parse_action_clause(c) for c in neg_clauses]
    print(f"Parsed Negated Batch: {json.dumps(neg_actions, ensure_ascii=False)}")

    state_after_neg, _, neg_speech = coalesce_action_batch(neg_actions, final_state, final_prev)
    print(f"State After Negation Test: Power={state_after_neg['power']} (Should stay True!)")
    assert state_after_neg["power"] == True # Power MUST stay True because "tắt đèn" was negated by "Đừng"


    print("\n--- TEST CASE 4: Absolute vs Relative Brightness Parameter ---")
    abs_clause = "Đặt độ sáng 30%"
    rel_clause = "Tăng độ sáng 20%"
    
    act_abs = parse_action_clause(abs_clause)
    act_rel = parse_action_clause(rel_clause)

    print(f"Abs Action: {act_abs}")
    print(f"Rel Action: {act_rel}")

    assert act_abs["param_type"] == "ABSOLUTE" and act_abs["val"] == 30
    assert act_rel["param_type"] == "RELATIVE" and act_rel["val"] == 20


    print("\n--- TEST CASE 5: Alt-Tab State Memory Swap (CMD 10) ---")
    revert_actions = [parse_action_clause("chế độ cũ")]
    reverted_state, reverted_prev, revert_speech = coalesce_action_batch(revert_actions, final_state, final_prev)
    print(f"Reverted State: {json.dumps(reverted_state, ensure_ascii=False)}")
    print(f"Revert Speech: 🔊 \"{revert_speech}\"")
    assert reverted_state["mode"] == initial_state["mode"]

    print("\n==================================================================")
    print("  ✅ ALL MODULE 2 SYSTEM COORDINATOR TESTS PASSED SUCCESSFULLY!   ")
    print("==================================================================")

if __name__ == "__main__":
    run_tests()
