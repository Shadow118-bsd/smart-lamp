# WORKSPACE AGENTS GUIDELINES & LOCK RULES

## 🔒 IMMUTABLE RULE: NO GIT PUSH WITHOUT EXPLICIT PERMISSION
> **CRITICAL INSTRUCTION FOR ALL AI ASSISTANTS / DEVELOPERS:**
> **DO NOT** run `git push` to any remote branch/repository under ANY circumstances unless the user explicitly and directly commands to push code (e.g. "push code", "đẩy code", "push lên").
> All code edits, builds, tests, or local commits may be done locally, but the remote repository MUST NOT be touched until explicitly instructed.

## 🔒 IMMUTABLE HARDWARE LOCK: SENSOR TELEMETRY SUBSYSTEM (MODULE 2)

> **CRITICAL INSTRUCTION FOR ALL AI ASSISTANTS / DEVELOPERS:**
> The following sensor hardware, pins, timings, calibration parameters, and filtering logic have been fully integrated, bench-tested, and locked.
> **DO NOT ALTER, REFACTOR, OR OVERWRITE THESE CONFIGURATIONS UNDER ANY CIRCUMSTANCES UNLESS THE USER EXPLICITLY REQUESTS TO CHANGE SENSORS.**

---

### 1. Verified Hardware Pin Assignments (DO NOT CHANGE)
| Subsystem | Component | Pins | Protocol / Notes |
| :--- | :--- | :--- | :--- |
| **I2C Bus** | BME280 + VL53L0X + OLED SSD1306 | **SDA = GPIO 8**, **SCL = GPIO 9** | Shared I2C Bus, **100kHz Clock Speed**, 100ms Timeout |
| **PIR Motion** | HC-SR501 / Mini PIR | **OUT = GPIO 7** | Digital Input |
| **Audio I2S** | MAX98357A 3W Mono Amp | **BCLK = GPIO 10**, **LRC = GPIO 11**, **DIN = GPIO 12** | **GAIN tied to GND (12dB)**, 16kHz 16-bit Mono |
| **OLED Display** | 0.96" SSD1306 (128x64) | **SDA = GPIO 8**, **SCL = GPIO 9** | I2C Addr `0x3C` (or `0x3D`) |

---

### 2. Time-of-Flight (VL53L0X) Calibrated Configuration & Anti-Spiking Rules
* **I2C Address:** `0x29`
* **Profile:** **Standard Desk Profile** (Balanced for $3.5\text{cm} \rightarrow 120\text{cm}$).
  * `tof.setMeasurementTimingBudget(50000);` (50ms budget)
  * `tof.startContinuous(50);`
  * **DO NOT** enable Long-Range mode (`setSignalRateLimit(0.1)` or `VcselPeriodPreRange = 18`), as this saturates close-range targets ($< 20\text{cm}$) and produces false error `8190`.
* **Anti-Spiking Hysteresis Filter:**
  * When `dist_mm < 35` or `0`: Hand touching / very close $\rightarrow$ clamp to `1.0cm` (`[CUI SAT!]`).
  * When `dist_mm >= 8190` (Phase error / Blind zone / Angle dropout):
    * If previous distance was $< 20.0\text{cm}$: Hand is inside $< 3.5\text{cm}$ blind zone $\rightarrow$ clamp to `1.0cm`.
    * If previous distance was $< 60.0\text{cm}$ and error persists $< 20$ frames ($< 1.2\text{s}$): **HOLD previous distance**. DO NOT ramp up to 120cm!
    * If error persists $\ge 20$ frames ($> 1.2\text{s}$): User truly left desk $\rightarrow$ gently drift to `120.0cm` (`[ROI BAN]`).
  * When `dist_mm` is valid ($35\text{mm} \dots 1200\text{mm}$):
    * `g_distance_cm = (0.60f * raw_cm) + (0.40f * g_distance_cm);`

---

### 3. Environmental Sensor (BME280) Configuration
* **I2C Address:** Auto-detect `0x76` or `0x77`.
* **Sampling:** Temp x1, Hum x1, Pressure x1, Filter x2, Normal mode.
* **Polling Rate:** Every 1000ms.

---

### 4. Motion & Presence (PIR) Configuration
* **Polling Rate:** Every 60ms.
* **Session Logic:** Continuous motion refreshes `g_last_motion_ms`. Absence of motion for $> 20$ seconds resets presence. Study timer tracks cumulative active session up to 45 minutes with progress bar on OLED.

---

### 5. Encapsulation & Code Integrity
* All sensor operations are encapsulated in `src/sensors_manager.h` and `main/smart_lamp_module2_sensors/sensors_manager.h`.
* When implementing future features (e.g., Rotary Encoder, Voice recognition commands, LED PWM dimming/CCT, Web UI themes):
  * **Access sensors exclusively through `sensors_manager` public getters** (`sensors.getDistanceCm()`, `sensors.getTemperature()`, etc.).
  * **NEVER modify or delete `sensors_manager.h`**.
