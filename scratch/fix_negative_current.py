with open('src/power_manager.h', 'r', encoding='utf-8') as f:
    pwr = f.read()

# 1. Update evaluateState in src/power_manager.h
old_eval = '''        // 1. Nhận diện Nguồn sạc ngoài dựa trên bản đồ điện áp & dòng điện thực nghiệm:
        // Cắm sạc qua cổng ESP32 USB: V >= 4.90V hoặc có tín hiệu USB CDC
        bool is_charging_esp32 = (m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE) || HWCDC::isPlugged();

        // Nhận diện sạc qua Mạch Sạc Nhỏ:
        // Chỉ nhận diện là sạc qua mạch nhỏ khi dòng tải THẤP (I < 25mA, tức là đèn không bật hoặc pin ngắt)
        // VÀ điện áp rơi vào dải 4.50V - 4.75V đặc trưng của mạch sạc nhỏ.
        // NẾU đèn đang bật (I >= 25mA) mà áp tụt <= 4.75V, đó là HIỆN TƯỢNG SỤT ÁP DO XẢ PIN, TUYỆT ĐỐI KHÔNG COI LÀ SẠC!
        bool is_small_charger_candidate = (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.50f && m_i_filtered < 25.0f);
        if (is_small_charger_candidate) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        bool is_plugged_mains = is_charging_esp32 || is_charging_module;

        if (is_plugged_mains) {
            // --- ĐANG CẮM SẠC NGOÀI (MAINS / CHARGING) ---
            if (m_charging_start_ms == 0) {
                m_charging_start_ms = now; // Ghi nhận thời điểm bắt đầu cắm sạc
            }

            // Tự động nạp bù lượng điện tích theo tốc độ sạc chuẩn
            float charge_added_mah = (VIRTUAL_CHARGE_RATE_MA * delta_sec) / 3600.0f;
            m_rem_mah += charge_added_mah;

            // Kiểm tra bão hòa đầy 100% (Saturation Lock)
            bool time_saturated = (now - m_charging_start_ms >= OVERNIGHT_SATURATION_TIME_MS);
            if (m_rem_mah >= BATTERY_NOMINAL_MAH || time_saturated) {
                m_rem_mah = BATTERY_NOMINAL_MAH;
                m_state.battery_percent = 100;
                m_state.source = POWER_SRC_MAINS_IDLE;
            } else {
                m_state.source = POWER_SRC_MAINS;
            }
            m_state.is_charging = true; // Luôn hiển thị tia sét khi có nguồn sạc cắm vào
        } else {
            // --- ĐANG CHẠY BẰNG PIN (BATTERY / DISCHARGING) ---
            m_charging_start_ms = 0;
            m_state.source = POWER_SRC_BATTERY;
            m_state.is_charging = false;

            // Dòng tải thực tế của hệ thống (tối thiểu 6mA dòng tĩnh nuôi mạch)
            float load_current = (m_i_filtered > 5.0f) ? m_i_filtered : 6.0f;

            // Dòng xả thực rút từ cell pin 3.7V qua mạch Boost
            float battery_draw_ma = load_current * BOOST_CURRENT_RATIO;

            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;

            // CHỈ ĐẾM THỜI GIAN VÀ DUNG LƯỢNG TEST KHI ĐÈN THỰC SỰ BẬT SÁNG (I >= 25mA)
            // Khi tắt đèn (chỉ ăn dòng tĩnh 7-10mA) hoặc khi cắm sạc: Bộ đếm ĐÓNG BĂNG đứng yên!
            if (m_i_filtered >= 25.0f) {
                m_used_mah += discharge_mah;
                m_discharge_sec += delta_sec;
            }

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }
        }'''

new_eval = '''        // 1. Nhận diện Nguồn sạc ngoài dựa trên bản đồ điện áp & dòng điện thực nghiệm:
        // Cắm sạc qua cổng ESP32 USB: V >= 4.90V (Bỏ HWCDC::isPlugged để không bị kẹt khi rút dây)
        bool is_charging_esp32 = (m_state.bus_voltage_v >= V_THRESH_ESP32_CHARGE);

        // Lấy độ lớn dòng điện tuyệt đối |I| (chống dòng ngược âm khi chạy nguồn pin)
        float current_mag = fabsf(m_i_filtered);

        // Nhận diện sạc qua Mạch Sạc Nhỏ:
        // Chỉ nhận diện là sạc qua mạch nhỏ khi dòng tải THẤP (|I| < 25mA, tức là đèn không bật hoặc pin ngắt)
        // VÀ điện áp rơi vào dải 4.50V - 4.75V đặc trưng của mạch sạc nhỏ.
        // NẾU đèn đang bật (|I| >= 25mA) mà áp tụt <= 4.75V, đó là HIỆN TƯỢNG SỤT ÁP DO XẢ PIN, TUYỆT ĐỐI KHÔNG COI LÀ SẠC!
        bool is_small_charger_candidate = (m_state.bus_voltage_v <= V_THRESH_MODULE_CHARGE && m_state.bus_voltage_v >= 4.50f && current_mag < 25.0f);
        if (is_small_charger_candidate) {
            if (m_low_v_charge_count < 3) m_low_v_charge_count++;
        } else {
            if (m_low_v_charge_count > 0) m_low_v_charge_count--;
        }
        bool is_charging_module = (m_low_v_charge_count >= 2);

        bool is_plugged_mains = is_charging_esp32 || is_charging_module;

        if (is_plugged_mains) {
            // --- ĐANG CẮM SẠC NGOÀI (MAINS / CHARGING) ---
            if (m_charging_start_ms == 0) {
                m_charging_start_ms = now; // Ghi nhận thời điểm bắt đầu cắm sạc
            }

            // Tự động nạp bù lượng điện tích theo tốc độ sạc chuẩn
            float charge_added_mah = (VIRTUAL_CHARGE_RATE_MA * delta_sec) / 3600.0f;
            m_rem_mah += charge_added_mah;

            // Kiểm tra bão hòa đầy 100% (Saturation Lock)
            bool time_saturated = (now - m_charging_start_ms >= OVERNIGHT_SATURATION_TIME_MS);
            if (m_rem_mah >= BATTERY_NOMINAL_MAH || time_saturated) {
                m_rem_mah = BATTERY_NOMINAL_MAH;
                m_state.battery_percent = 100;
                m_state.source = POWER_SRC_MAINS_IDLE;
            } else {
                m_state.source = POWER_SRC_MAINS;
            }
            m_state.is_charging = true; // Luôn hiển thị tia sét khi có nguồn sạc cắm vào
        } else {
            // --- ĐANG CHẠY BẰNG PIN (BATTERY / DISCHARGING) ---
            m_charging_start_ms = 0;
            m_state.source = POWER_SRC_BATTERY;
            m_state.is_charging = false;

            // Dòng tải thực tế của hệ thống: Lấy độ lớn tuyệt đối |I| (chống dòng ngược âm)
            float load_current = (current_mag > 5.0f) ? current_mag : 6.0f;

            // Dòng xả thực rút từ cell pin 3.7V qua mạch Boost
            float battery_draw_ma = load_current * BOOST_CURRENT_RATIO;

            // Tích phân trừ dung lượng Coulomb
            float discharge_mah = (battery_draw_ma * delta_sec) / 3600.0f;
            m_rem_mah -= discharge_mah;

            // CHỈ ĐẾM THỜI GIAN VÀ DUNG LƯỢNG TEST KHI ĐÈN THỰC SỰ BẬT SÁNG (|I| >= 20mA)
            // Khi tắt đèn (chỉ ăn dòng tĩnh 7-10mA) hoặc khi cắm sạc: Bộ đếm ĐÓNG BĂNG đứng yên!
            if (current_mag >= 20.0f) {
                m_used_mah += discharge_mah;
                m_discharge_sec += delta_sec;
            }

            if (m_rem_mah < 0.0f) {
                m_rem_mah = 0.0f;
            }
        }'''

if old_eval in pwr:
    pwr = pwr.replace(old_eval, new_eval)
    with open('src/power_manager.h', 'w', encoding='utf-8') as f:
        f.write(pwr)
    print("SUCCESS: src/power_manager.h updated!")
else:
    print("ERROR: old_eval not found in src/power_manager.h")

# 2. Update src/main.cpp to display positive current
with open('src/main.cpp', 'r', encoding='utf-8') as f:
    main_code = f.read()

old_oled_line1 = 'display.printf("V:%.2fV | I:%4.0fmA", power.getVoltage(), power.getCurrentMa());'
new_oled_line1 = 'display.printf("V:%.2fV | I:%4.0fmA", power.getVoltage(), fabsf(power.getCurrentMa()));'

if old_oled_line1 in main_code:
    main_code = main_code.replace(old_oled_line1, new_oled_line1)
    with open('src/main.cpp', 'w', encoding='utf-8') as f:
        f.write(main_code)
    print("SUCCESS: src/main.cpp updated!")
else:
    print("ERROR: old_oled_line1 not found in src/main.cpp")
