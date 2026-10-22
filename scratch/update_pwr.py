with open('src/power_manager.h', 'r', encoding='utf-8') as f:
    c = f.read()

# 1. Update Threshold Constants
t1 = """    // Ngưỡng điện áp phân biệt nguồn ngoài (USB cắm sạc) vs Pin qua Boost
    // Nguồn USB ngoài: 4.95V - 5.25V
    // Nguồn qua mạch boost khi xả pin: sụt áp dưới tải
    static constexpr float V_CHARGING_THRESHOLD = 4.94f;"""

r1 = """    // Ngưỡng điện áp phân biệt 3 trạng thái thực nghiệm chuẩn xác:
    // 1. Sạc qua ESP32: V >= 4.90V (hoặc có tín hiệu USB CDC)
    // 2. Sạc qua Mạch sạc nhỏ: V <= 4.73V (dòng sạc ghì áp xuống 4.61V - 4.69V)
    // 3. Chạy Pin độc lập: 4.74V <= V <= 4.88V (thực tế 4.76V - 4.87V)
    static constexpr float V_THRESH_ESP32_CHARGE = 4.90f;
    static constexpr float V_THRESH_MODULE_CHARGE = 4.73f;"""

# 2. Update evaluateState
t2 = """        // 1. Nhận diện Nguồn sạc ngoài (Cắm sạc USB vào ESP32 hoặc cắm vào module sạc)
        bool is_plugged_mains = HWCDC::isPlugged() || (m_state.bus_voltage_v >= V_CHARGING_THRESHOLD);"""

r2 = """        // 1. Nhận diện Nguồn sạc ngoài dựa trên bản đồ điện áp thực nghiệm:
        bool is_charging_esp32 = (m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE) || HWCDC::isPlugged();

        // Nhận diện sạc qua Mạch Sạc Nhỏ (điện áp bị ghì sụt xuống <= 4.73V)
        // Bộ lọc debounce 2 mẫu liên tiếp (1 giây) chống gai sụt áp tức thời khi xoay núm LED
        if (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.00f) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        bool is_plugged_mains = is_charging_esp32 || is_charging_module;"""

# 3. Add m_low_v_charge_count
t3 = """    unsigned long m_charging_start_ms = 0;"""
r3 = """    unsigned long m_charging_start_ms = 0;
    uint8_t m_low_v_charge_count = 0;"""

if t1 in c and t2 in c and t3 in c:
    c = c.replace(t1, r1).replace(t2, r2).replace(t3, r3)
    with open('src/power_manager.h', 'w', encoding='utf-8') as f:
        f.write(c)
    print("UPDATED_POWER_MANAGER_SUCCESS")
else:
    print("TARGETS_NOT_FOUND")
    if t1 not in c: print("t1 missing")
    if t2 not in c: print("t2 missing")
    if t3 not in c: print("t3 missing")
