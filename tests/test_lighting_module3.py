"""
======================================================================================
  SMART DESK LAMP - MODULE 3 (MẢNG 1: OFFLINE) LOGIC & UNIT VERIFICATION SUITE
  Automated tests for:
   1. Gamma 2.2 Photometric Curve & Dual-Channel Kelvin Mixing
   2. Ben Buxton Full-Step Rotary FSM Transitions (CW, CCW, Debounce rejection)
   3. Single-Click Power ON/OFF Toggle with NVS State Restoration (Cách A)
   4. Long-Press 1.0s OLED Menu Navigation & NVS Commit
   5. 6 Clinical / Ergonomic Lighting Presets
======================================================================================
"""

import math
import unittest

PWM_MAX_DUTY = 1023
PWM_MIN_DUTY = 46

def calculate_pwm(brightness: int, cct: int):
    """Exact replica of LightingManager::applyPwm() logic."""
    if brightness == 0:
        return 0, 0
    normalized_b = float(brightness) / 100.0
    gamma_val = math.pow(normalized_b, 2.2)
    cool_ratio = (float(cct) - 2700.0) / (6500.0 - 2700.0)
    cool_ratio = max(0.0, min(1.0, cool_ratio))
    warm_ratio = 1.0 - cool_ratio

    target_warm = warm_ratio * float(PWM_MAX_DUTY)
    target_cool = cool_ratio * float(PWM_MAX_DUTY)

    duty_warm = 0
    duty_cool = 0

    if target_warm >= float(PWM_MIN_DUTY):
        duty_warm = round(float(PWM_MIN_DUTY) + gamma_val * (target_warm - float(PWM_MIN_DUTY)))
        duty_warm = min(PWM_MAX_DUTY, duty_warm)

    if target_cool >= float(PWM_MIN_DUTY):
        duty_cool = round(float(PWM_MIN_DUTY) + gamma_val * (target_cool - float(PWM_MIN_DUTY)))
        duty_cool = min(PWM_MAX_DUTY, duty_cool)

    return duty_warm, duty_cool


class RotaryFsm:
    """Exact replica of Ben Buxton Full-Step FSM in LightingManager."""
    DIR_NONE = 0x0
    DIR_CW = 0x10
    DIR_CCW = 0x20

    R_START = 0x0
    R_CW_FINAL = 0x1
    R_CW_BEGIN = 0x2
    R_CW_NEXT = 0x3
    R_CCW_BEGIN = 0x4
    R_CCW_FINAL = 0x5
    R_CCW_NEXT = 0x6

    TABLE = [
        [R_START, R_CW_BEGIN, R_CCW_BEGIN, R_START],
        [R_CW_NEXT, R_START, R_CW_FINAL, R_START | DIR_CW],
        [R_CW_NEXT, R_CW_BEGIN, R_START, R_START],
        [R_CW_NEXT, R_CW_BEGIN, R_CW_FINAL, R_START],
        [R_CCW_NEXT, R_START, R_CCW_BEGIN, R_START],
        [R_CCW_NEXT, R_CCW_FINAL, R_START, R_START | DIR_CCW],
        [R_CCW_NEXT, R_CCW_FINAL, R_CCW_BEGIN, R_START],
    ]

    def __init__(self):
        self.state = self.R_START

    def step(self, clk: int, dt: int) -> int:
        pinstate = (clk << 1) | dt
        self.state = self.TABLE[self.state & 0x0F][pinstate]
        res = self.state & 0x30
        if res == self.DIR_CW:
            return 1
        elif res == self.DIR_CCW:
            return -1
        return 0


class LightingManagerSim:
    """Exact replica of state machine & NVS handling in LightingManager."""
    PRESETS = [
        {"name": "Hoc Tap",  "brightness": 80, "cct": 5000},
        {"name": "Doc Sach", "brightness": 70, "cct": 4000},
        {"name": "May Tinh", "brightness": 40, "cct": 4000},
        {"name": "Thu Gian", "brightness": 35, "cct": 3000},
        {"name": "Ban Dem",  "brightness": 10, "cct": 2700},
        {"name": "Thu Cong", "brightness": 80, "cct": 4000},
    ]

    def __init__(self):
        self.brightness = 0
        self.saved_brightness = 80
        self.cct = 4000
        self.mode = 0
        self.menu_active = False
        self.menu_cursor = 0
        self.is_quick_adjust = False
        self.nvs = {"mode": 0, "cct": 4000, "brightness": 80}

    def load_from_nvs(self):
        self.mode = self.nvs.get("mode", 0)
        self.cct = self.nvs.get("cct", 4000)
        self.saved_brightness = self.nvs.get("brightness", 80)

    def save_to_nvs(self):
        self.nvs["mode"] = self.mode
        self.nvs["cct"] = self.cct
        if self.brightness > 0:
            self.nvs["brightness"] = self.brightness
        elif self.saved_brightness > 0:
            self.nvs["brightness"] = self.saved_brightness

    def toggle_power(self):
        if self.brightness > 0:
            # Turn OFF
            self.saved_brightness = self.brightness
            self.brightness = 0
            self.save_to_nvs()
            self.is_quick_adjust = False
        else:
            # Turn ON
            self.brightness = self.saved_brightness if self.saved_brightness > 0 else 80
            self.is_quick_adjust = False

    def on_button_click(self):
        if not self.menu_active:
            self.toggle_power()
        else:
            self.menu_cursor = (self.menu_cursor + 1) % 3

    def on_button_long_press(self):
        if not self.menu_active:
            self.menu_active = True
            self.menu_cursor = 0
        else:
            self.menu_active = False
            self.is_quick_adjust = False
            self.save_to_nvs()

    def on_rotary_turn(self, direction: int):
        if not self.menu_active:
            if self.brightness == 0 and direction > 0:
                self.brightness = 5
            else:
                self.brightness = max(0, min(100, self.brightness + direction * 5))
            if self.brightness > 0:
                self.saved_brightness = self.brightness
            self.mode = 5 # Thu Cong
            self.is_quick_adjust = True
            return

        self.is_quick_adjust = False
        if self.menu_cursor == 0:
            self.brightness = max(0, min(100, self.brightness + direction * 5))
            if self.brightness > 0:
                self.saved_brightness = self.brightness
            self.mode = 5
        elif self.menu_cursor == 1:
            self.cct = max(2700, min(6500, self.cct + direction * 200))
            self.mode = 5
        elif self.menu_cursor == 2:
            m = (self.mode + direction) % len(self.PRESETS)
            self.mode = m
            if self.mode != 5:
                self.brightness = self.PRESETS[self.mode]["brightness"]
                self.cct = self.PRESETS[self.mode]["cct"]
                self.saved_brightness = self.brightness


class TestLightingModule3(unittest.TestCase):

    def test_gamma_2_2_pwm_calculations(self):
        """Test optical Gamma 2.2 curve and dual-channel duty cycles."""
        # 0% Brightness: both channels must be 0
        w, c = calculate_pwm(0, 4000)
        self.assertEqual((w, c), (0, 0))

        # 100% Warm (2700K) at 100% Brightness
        w, c = calculate_pwm(100, 2700)
        self.assertEqual(w, 1023)
        self.assertEqual(c, 0)

        # 100% Cool (6500K) at 100% Brightness
        w, c = calculate_pwm(100, 6500)
        self.assertEqual(w, 0)
        self.assertEqual(c, 1023)

        # Midpoint 4600K at 100% Brightness: 50% split
        w, c = calculate_pwm(100, 4600)
        self.assertAlmostEqual(w, 512, delta=2)
        self.assertAlmostEqual(c, 512, delta=2)
        self.assertAlmostEqual(w + c, 1023, delta=2)

        # Gamma 2.2 perceptual effect with hardware deadzone compensation at 50% brightness:
        w_50, c_50 = calculate_pwm(50, 4600)
        total_duty_50 = w_50 + c_50
        # Target for each channel is 511.5; duty = 46 + (0.5)^2.2 * (511.5 - 46) = 147; total = 294
        target_half = 0.5 * float(PWM_MAX_DUTY)
        expected_channel_50 = round(float(PWM_MIN_DUTY) + math.pow(0.5, 2.2) * (target_half - float(PWM_MIN_DUTY)))
        self.assertAlmostEqual(w_50, expected_channel_50, delta=2)
        self.assertAlmostEqual(c_50, expected_channel_50, delta=2)
        self.assertTrue(total_duty_50 < 350, "Gamma 2.2 must give smooth low-luminance scaling")

        # Hardware Deadzone Verification: 5% brightness must be >= PWM_MIN_DUTY (46) so LEDs turn on
        w_5, c_5 = calculate_pwm(5, 2700)
        self.assertTrue(w_5 >= PWM_MIN_DUTY, "5% brightness must overcome hardware turn-on threshold")
        self.assertEqual(c_5, 0)

        # Clamping at out-of-range CCT
        w_low, c_low = calculate_pwm(100, 2000) # Below 2700K
        self.assertEqual(w_low, 1023)
        self.assertEqual(c_low, 0)

        w_high, c_high = calculate_pwm(100, 8000) # Above 6500K
        self.assertEqual(w_high, 0)
        self.assertEqual(c_high, 1023)

    def test_ben_buxton_rotary_fsm(self):
        """Test Ben Buxton 4-phase quadrature transitions for CW and CCW."""
        fsm = RotaryFsm()
        
        # CW sequence: 11 -> 01 -> 00 -> 10 -> 11
        d1 = fsm.step(0, 1) # CW_BEGIN
        d2 = fsm.step(0, 0) # CW_NEXT
        d3 = fsm.step(1, 0) # CW_FINAL
        d4 = fsm.step(1, 1) # R_START | DIR_CW
        self.assertEqual((d1, d2, d3, d4), (0, 0, 0, 1), "CW full sequence must emit +1 at detent")

        # CCW sequence: 11 -> 10 -> 00 -> 01 -> 11
        d1 = fsm.step(1, 0) # CCW_BEGIN
        d2 = fsm.step(0, 0) # CCW_NEXT
        d3 = fsm.step(0, 1) # CCW_FINAL
        d4 = fsm.step(1, 1) # R_START | DIR_CCW
        self.assertEqual((d1, d2, d3, d4), (0, 0, 0, -1), "CCW full sequence must emit -1 at detent")

        # Incomplete / Glitch sequence: 11 -> 01 -> 11 (bounced back without completing cycle)
        fsm.step(0, 1)
        glitch = fsm.step(1, 1)
        self.assertEqual(glitch, 0, "Bounced half-step must be ignored without emitting delta")

    def test_power_toggle_and_state_persistence(self):
        """Test Cách A: Single-click toggles power ON/OFF and preserves last state in NVS."""
        lamp = LightingManagerSim()
        self.assertEqual(lamp.brightness, 0, "Default boot state must be 0% Standby OFF")

        # 1. Single click to Turn ON
        lamp.on_button_click()
        self.assertEqual(lamp.brightness, 80, "First ON must restore default 80%")
        self.assertFalse(lamp.menu_active, "Home single click must not open menu")

        # 2. Adjust brightness to 45% via knob
        lamp.brightness = 45
        lamp.saved_brightness = 45

        # 3. Single click to Turn OFF
        lamp.on_button_click()
        self.assertEqual(lamp.brightness, 0, "Lamp should be turned OFF (0%)")
        self.assertEqual(lamp.saved_brightness, 45, "saved_brightness must remember 45%")
        self.assertEqual(lamp.nvs["brightness"], 45, "NVS must persist 45%")

        # 4. Single click to Turn ON again
        lamp.on_button_click()
        self.assertEqual(lamp.brightness, 45, "Lamp must turn ON back to exactly 45%")

    def test_menu_navigation_and_nvs_commit(self):
        """Test long-press 1.0s opens menu, click cycles cursor, long-press saves and exits."""
        lamp = LightingManagerSim()
        lamp.brightness = 50

        # Long-press at Home -> Opens Menu
        lamp.on_button_long_press()
        self.assertTrue(lamp.menu_active, "Long press must open menu")
        self.assertEqual(lamp.menu_cursor, 0, "Menu starts at cursor 0 (Do sang)")

        # Single clicks inside Menu -> Cycle cursor
        lamp.on_button_click()
        self.assertEqual(lamp.menu_cursor, 1, "Cursor advances to 1 (Nhiet mau)")
        lamp.on_button_click()
        self.assertEqual(lamp.menu_cursor, 2, "Cursor advances to 2 (Che do)")
        lamp.on_button_click()
        self.assertEqual(lamp.menu_cursor, 0, "Cursor wraps around to 0 (Do sang)")
        self.assertTrue(lamp.menu_active, "Single click inside menu does not close menu")

        # Adjust CCT inside Menu cursor 1
        lamp.menu_cursor = 1
        lamp.on_rotary_turn(1) # +200K
        self.assertEqual(lamp.cct, 4200)

        # Long-press inside Menu -> Commits NVS and exits Menu
        lamp.on_button_long_press()
        self.assertFalse(lamp.menu_active, "Long press inside menu must exit menu")
        self.assertEqual(lamp.nvs["cct"], 4200, "CCT must be committed to NVS")

    def test_presets(self):
        """Verify the 6 ergonomic presets match project standards."""
        lamp = LightingManagerSim()
        lamp.menu_active = True
        lamp.menu_cursor = 2

        # Cycle through all presets
        presets = lamp.PRESETS
        self.assertEqual(len(presets), 6)
        self.assertEqual(presets[0]["name"], "Hoc Tap")
        self.assertEqual((presets[0]["brightness"], presets[0]["cct"]), (80, 5000))

        self.assertEqual(presets[1]["name"], "Doc Sach")
        self.assertEqual((presets[1]["brightness"], presets[1]["cct"]), (70, 4000))

        self.assertEqual(presets[2]["name"], "May Tinh")
        self.assertEqual((presets[2]["brightness"], presets[2]["cct"]), (40, 4000))

        self.assertEqual(presets[3]["name"], "Thu Gian")
        self.assertEqual((presets[3]["brightness"], presets[3]["cct"]), (35, 3000))

        self.assertEqual(presets[4]["name"], "Ban Dem")
        self.assertEqual((presets[4]["brightness"], presets[4]["cct"]), (10, 2700))

        self.assertEqual(presets[5]["name"], "Thu Cong")
        self.assertEqual((presets[5]["brightness"], presets[5]["cct"]), (80, 4000))


if __name__ == "__main__":
    unittest.main(verbosity=2)
