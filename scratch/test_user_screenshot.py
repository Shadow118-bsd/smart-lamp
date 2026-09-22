import re
import difflib
import sys

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

COMMAND_DICTIONARY = [
    (["bật đèn", "mở đèn", "sáng đèn", "bật sáng", "cho đèn sáng", "bặt đèn", "bật lên"], 1, 0, 0, "Bật Đèn"),
    (["tắt đèn", "tắt đi", "tắt hết", "tắt sáng", "tắt đền", "tắt máy"], 2, 0, 0, "Tắt Đèn"),
    (["học", "chế độ học", "học bài", "làm việc", "chế độ làm việc"], 9, 0, 2, "Chế Độ Học Bài"),
    (["đọc", "chế độ đọc", "đọc sách", "chế độ đọc sách"], 9, 0, 3, "Chế Độ Đọc Sách"),
    (["ngủ", "chế độ ngủ", "ban đêm", "đèn ngủ", "chế độ ban đêm"], 9, 0, 4, "Chế Độ Ban Đêm"),
    (["tăng sáng", "sáng hơn", "tăng độ sáng", "sáng thêm", "tăng 10", "tăng 20"], 3, 10, 0, "Tăng Độ Sáng"),
    (["giảm sáng", "tối hơn", "giảm độ sáng", "tối bớt", "giảm 10", "giảm 20"], 3, -10, 0, "Giảm Độ Sáng"),
    (["ấm hơn", "vàng hơn", "tăng màu ấm", "ấm lên"], 7, 10, 0, "Tăng Màu Ấm"),
    (["lạnh hơn", "trắng hơn", "tăng màu trắng", "trắng lên"], 8, 10, 0, "Tăng Màu Trắng"),
]

def parse_vietnamese_command(speech_text):
    text = speech_text.lower().strip()
    if not text:
        return 0, 0, 0, "Chưa có lời nói", 0.0

    numbers = re.findall(r'\d+', text)
    custom_val = int(numbers[0]) if numbers else None

    best_match_ratio = 0.0
    best_result = (0, 0, 0, "Lời nói thử nghiệm", 0.0)

    for phrases, cmd, val, mode, name in COMMAND_DICTIONARY:
        for phrase in phrases:
            ratio = difflib.SequenceMatcher(None, phrase, text).ratio()
            if phrase in text or text in phrase:
                ratio = max(ratio, 0.85)

            if ratio > best_match_ratio:
                best_match_ratio = ratio
                final_val = custom_val if (custom_val is not None and cmd == 3) else val
                if cmd == 3 and ("giảm" in text or "tối" in text) and final_val > 0:
                    final_val = -final_val
                best_result = (cmd, final_val, mode, name, round(ratio * 100.0, 1))

    return best_result

def test_user_screenshot_input():
    text = "bật đèn Tăng độ sáng lên 5% rồi chuyển sang chế độ học bài Giảm độ sáng xuống 10% Chuyển sang chế định chế độ ban đêm tắt đèn".lower().strip()
    
    # Split by conjunctions OR before Action Verbs
    pattern = r'[,;]|\b(?:rồi|sau đó|tiếp theo|và|kèm|đồng thời)\b|(?=\b(?:bật|mở|tắt|tăng|giảm|chuyển|đổi|chỉnh|ấm|lạnh|trắng|vàng)\b)'
    raw_clauses = re.split(pattern, text)
    clauses = [c.strip() for c in raw_clauses if c.strip() and len(c.strip()) > 1]

    print("=== RAW CLAUSES DETECTED ===")
    for idx, c in enumerate(clauses, 1):
        cmd, val, mode, name, score = parse_vietnamese_command(c)
        print(f"Lệnh {idx}: Clause='{c}' -> {name} (Cmd: {cmd}, Val: {val}, Mode: {mode})")

if __name__ == "__main__":
    test_user_screenshot_input()
