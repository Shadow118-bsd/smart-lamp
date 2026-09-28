#ifndef SENSORS_CONFIG_H
#define SENSORS_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

/* --- HARDWARE GPIO MAPPING CHO MODULE 2 (ESP32-S3) --- */

// Bus I2C Master (Dùng chung cho BME280 + VL53L0X)
#define CONFIG_I2C_MASTER_PORT          0       // I2C_NUM_0
#define CONFIG_I2C_MASTER_SDA_GPIO      8       // SDA -> GPIO 8
#define CONFIG_I2C_MASTER_SCL_GPIO      9       // SCL -> GPIO 9
#define CONFIG_I2C_MASTER_FREQ_HZ       100000  // 100 kHz standard mode

// ToF Distance Sensors XSHUT (Hardware shutdown pins)
#define CONFIG_VL53L0X_1_XSHUT_GPIO     10      // ToF Con 1 XSHUT -> GPIO 10
#define CONFIG_VL53L0X_2_XSHUT_GPIO     11      // ToF Con 2 XSHUT -> GPIO 11

// PIR Motion / Presence Sensor
#define CONFIG_PIR_GPIO                 7       // PIR OUT -> GPIO 7

// Rotary Encoder (Knob điều khiển cơ học)
#define CONFIG_ROTARY_CLK_GPIO          1       // CLK (A) -> GPIO 1
#define CONFIG_ROTARY_DT_GPIO           2       // DT (B) -> GPIO 2
#define CONFIG_ROTARY_SW_GPIO           3       // SW Button -> GPIO 3

// I2C Device Addresses
#define BME280_I2C_ADDR_PRIMARY         0x76
#define BME280_I2C_ADDR_SECONDARY       0x77
#define VL53L0X_I2C_ADDR_DEFAULT        0x29
#define VL53L0X_I2C_ADDR_REASSIGNED     0x30

// Session Tracking Limits
#define SESSION_OVERDUE_LIMIT_SEC       (45 * 60) // 45 phút học liên tục

#ifdef __cplusplus
}
#endif

#endif // SENSORS_CONFIG_H
