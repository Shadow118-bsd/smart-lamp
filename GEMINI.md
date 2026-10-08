# GEMINI SYSTEM INSTRUCTIONS - HARDWARE & GIT LOCK

Include and adhere to the guidelines specified in [AGENTS.md](file:///d:/H%E1%BB%8Dc/TuongTacNguoiMay/Project/smart-lamp/AGENTS.md).
1. Under NO circumstances should the sensor subsystem (BME280, VL53L0X, PIR, MAX98357A I2S, and SSD1306 OLED) or their calibrated pins, clock rates, or anti-spiking hysteresis logic be modified without explicit instruction from the user.
2. **STRICT GIT PUSH LOCK:** DO NOT run `git push` to any remote repository under ANY circumstances unless the user explicitly requests/commands to push code (e.g. "push code", "đẩy code", "push lên git"). All changes must remain local until explicit permission is given.
