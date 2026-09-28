import serial
import time
import sys

port = "COM3"
baud = 115200

print(f"Opening {port} at {baud} baud...")
try:
    ser = serial.Serial(port, baud, timeout=1)
    # Toggle DTR/RTS or send reset if possible, or just listen
    print("Listening to Serial output for 5 seconds...")
    start_time = time.time()
    lines = []
    while time.time() - start_time < 5:
        line = ser.readline()
        if line:
            try:
                decoded = line.decode('utf-8', errors='replace').strip()
                if decoded:
                    print(f"[SERIAL] {decoded}")
                    lines.append(decoded)
            except Exception as e:
                print(f"[RAW] {line}")
    ser.close()
    if not lines:
        print("No serial output received during the 5 second window.")
    else:
        print(f"Total lines received: {len(lines)}")
except Exception as e:
    print(f"Error opening serial port {port}: {e}")
