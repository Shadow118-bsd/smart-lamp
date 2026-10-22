with open('src/main.cpp', 'r', encoding='utf-8') as f:
    c = f.read()

target = """            if (lux >= 1000.0f) {
                display.printf("T:%.1fC H:%.0f%% Lux:%.1fk", sensors.getTemperature(), sensors.getHumidity(), lux / 1000.0f);
            } else {
                display.printf("T:%.1fC H:%.0f%% Lux:%.0f", sensors.getTemperature(), sensors.getHumidity(), lux);
            }"""

replacement = """            display.printf("T:%.0fC H:%.0f%% V:%.2fV", sensors.getTemperature(), sensors.getHumidity(), power.getVoltage());"""

if target in c:
    c = c.replace(target, replacement)
    with open('src/main.cpp', 'w', encoding='utf-8') as f:
        f.write(c)
    print("REPLACED_SUCCESS")
else:
    print("TARGET_NOT_FOUND")
