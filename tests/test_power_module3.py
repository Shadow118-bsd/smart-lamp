import unittest

def calculate_soc(v: float) -> int:
    """
    Python reference model for PowerManager::calculateSoC(float v)
    8-segment piecewise linear approximation for 1S Li-ion cell.
    """
    if v >= 4.18:
        return 100
    if v >= 4.05:
        return int(90.0 + (v - 4.05) / (4.18 - 4.05) * 10.0)
    if v >= 3.95:
        return int(80.0 + (v - 3.95) / (4.05 - 3.95) * 10.0)
    if v >= 3.85:
        return int(65.0 + (v - 3.85) / (3.95 - 3.85) * 15.0)
    if v >= 3.75:
        return int(50.0 + (v - 3.75) / (3.85 - 3.75) * 15.0)
    if v >= 3.68:
        return int(35.0 + (v - 3.68) / (3.75 - 3.68) * 15.0)
    if v >= 3.55:
        return int(15.0 + (v - 3.55) / (3.68 - 3.55) * 20.0)
    if v >= 3.30:
        return int(5.0 + (v - 3.30) / (3.55 - 3.30) * 10.0)
    if v > 3.10:
        return int((v - 3.10) / (3.30 - 3.10) * 5.0)
    return 0


def determine_power_source(current_ma: float) -> str:
    """
    Classifies power source from net current:
    > +30mA: Charging (Mains USB-C)
    < -30mA: Discharging (Battery UPS)
    else: Idle / Mains Full
    """
    if current_ma > 30.0:
        return "MAINS_CHARGING"
    elif current_ma < -30.0:
        return "BATTERY_UPS"
    else:
        return "MAINS_IDLE"


def check_protection_levels(voltage_v: float, percent: int, on_battery: bool):
    """
    Evaluates UVLO levels:
    - Critical (< 3.10V or <= 2%): Standby cutoff
    - Low Battery (< 3.35V or <= 10%): Cap brightness <= 20%
    """
    if not on_battery:
        return {"low_battery": False, "critical": False, "capped_brightness": None}
    
    is_critical = (voltage_v <= 3.10 or percent <= 2)
    is_low = (voltage_v <= 3.35 or percent <= 10)
    
    capped_br = 0 if is_critical else (20 if is_low else None)
    return {
        "low_battery": is_low,
        "critical": is_critical,
        "capped_brightness": capped_br
    }


class TestPowerManagerModule3(unittest.TestCase):

    def test_battery_soc_estimation(self):
        """Kiểm tra ước lượng SoC ở các dải điện áp Li-ion 1S"""
        # Đầy pin
        self.assertEqual(calculate_soc(4.20), 100)
        self.assertEqual(calculate_soc(4.18), 100)
        
        # Vùng điện áp danh định (3.7V - 4.0V)
        self.assertTrue(80 <= calculate_soc(4.00) <= 90)
        self.assertTrue(50 <= calculate_soc(3.80) <= 65)
        self.assertTrue(35 <= calculate_soc(3.70) <= 50)
        
        # Vùng xả sâu
        self.assertTrue(15 <= calculate_soc(3.60) <= 35)
        self.assertTrue(5 <= calculate_soc(3.40) <= 15)
        
        # Vùng cạn kiệt
        self.assertTrue(calculate_soc(3.20) < 5)
        self.assertEqual(calculate_soc(3.05), 0)

    def test_power_path_detection(self):
        """Kiểm tra nhận diện chiều dòng sạc / xả UPS"""
        # Cắm sạc USB-C dòng dương
        self.assertEqual(determine_power_source(500.0), "MAINS_CHARGING")
        self.assertEqual(determine_power_source(120.0), "MAINS_CHARGING")
        
        # Xả pin dòng âm (chạy UPS khi mất điện)
        self.assertEqual(determine_power_source(-250.0), "BATTERY_UPS")
        self.assertEqual(determine_power_source(-800.0), "BATTERY_UPS")
        
        # Dòng cân bằng / Idle
        self.assertEqual(determine_power_source(5.0), "MAINS_IDLE")
        self.assertEqual(determine_power_source(-10.0), "MAINS_IDLE")

    def test_uvlo_hardware_protection(self):
        """Kiểm tra các cấp độ bảo vệ Under-Voltage Lockout (UVLO)"""
        # Khi cắm sạc điện thoại USB-C, không bao giờ kích hoạt UVLO
        res_mains = check_protection_levels(3.25, 3, on_battery=False)
        self.assertFalse(res_mains["low_battery"])
        self.assertFalse(res_mains["critical"])
        
        # Khi chạy bằng pin và pin bình thường (3.8V, 60%)
        res_normal = check_protection_levels(3.80, 60, on_battery=True)
        self.assertFalse(res_normal["low_battery"])
        self.assertFalse(res_normal["critical"])
        self.assertIsNone(res_normal["capped_brightness"])
        
        # Khi pin yếu (3.30V, 8%) -> Cần hạ sáng <= 20%
        res_low = check_protection_levels(3.30, 8, on_battery=True)
        self.assertTrue(res_low["low_battery"])
        self.assertFalse(res_low["critical"])
        self.assertEqual(res_low["capped_brightness"], 20)
        
        # Khi pin cạn kiệt khẩn cấp (3.05V, 0%) -> Cắt tải về 0% Standby
        res_crit = check_protection_levels(3.05, 0, on_battery=True)
        self.assertTrue(res_crit["critical"])
        self.assertEqual(res_crit["capped_brightness"], 0)


if __name__ == '__main__':
    unittest.main()
