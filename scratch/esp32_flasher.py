import esptool.bin_image as bin_image
import subprocess
import sys
import os
import re
import time
import socket
from pathlib import Path

# Fix Windows console UTF-8 output encoding for Vietnamese paths
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
APP_FLASH_BIN = BASE_DIR / "scratch" / "app_flash.bin"
PATCHED_BIN = BASE_DIR / "scratch" / "patched_app.bin"
VOICE_CONFIG_H = BASE_DIR / "components" / "voice" / "include" / "voice_config.h"
MAIN_CPP = BASE_DIR / "src" / "main.cpp"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_available_com_ports():
    try:
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        return ports
    except Exception:
        return []

def get_current_wifi_ssid():
    try:
        res = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            if "SSID" in line and "BSSID" not in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    return parts[1].strip()
    except Exception:
        pass
    return ""

def update_voice_config_header(new_ssid, new_pass, new_ip):
    if not VOICE_CONFIG_H.exists():
        return
    content = VOICE_CONFIG_H.read_text(encoding='utf-8')
    content = re.sub(r'#define\s+CONFIG_VOICE_WIFI_SSID\s+".*?"', f'#define CONFIG_VOICE_WIFI_SSID           "{new_ssid}"', content)
    content = re.sub(r'#define\s+CONFIG_VOICE_WIFI_PASS\s+".*?"', f'#define CONFIG_VOICE_WIFI_PASS           "{new_pass}"', content)
    content = re.sub(r'#define\s+CONFIG_VOICE_UDP_DEST_IP\s+".*?"', f'#define CONFIG_VOICE_UDP_DEST_IP         "{new_ip}"', content)
    VOICE_CONFIG_H.write_text(content, encoding='utf-8')
    print(f"[+] Updated {VOICE_CONFIG_H.name} with new Wi-Fi credentials")

def update_main_cpp_wifi(new_ssid, new_pass):
    if not MAIN_CPP.exists():
        return
    content = MAIN_CPP.read_text(encoding='utf-8')
    content = re.sub(r'const char\*\s+WIFI_SSID\s*=\s*".*?";', f'const char* WIFI_SSID = "{new_ssid}";', content)
    content = re.sub(r'const char\*\s+WIFI_PASS\s*=\s*".*?";', f'const char* WIFI_PASS = "{new_pass}";', content)
    MAIN_CPP.write_text(content, encoding='utf-8')
    print(f"[+] Updated {MAIN_CPP.name} with new Wi-Fi credentials")

def patch_and_flash_wifi(port="COM3", new_ssid="", new_pass="", new_ip=None):
    if not new_ssid or not new_ssid.strip():
        raise ValueError("Tên Wi-Fi (SSID) không được để trống!")
        
    new_ssid = new_ssid.strip()
    new_pass = new_pass.strip()
    if not new_ip:
        new_ip = get_local_ip()

    # Update source code files
    update_voice_config_header(new_ssid, new_pass, new_ip)
    update_main_cpp_wifi(new_ssid, new_pass)

    if APP_FLASH_BIN.exists():
        print(f"[*] Patching firmware for SSID='{new_ssid}', Pass='***', IP='{new_ip}'...")
        with open(APP_FLASH_BIN, "rb") as f:
            image = bin_image.ESP32S3FirmwareImage(f)
            
        encoded_ssid = new_ssid.encode('utf-8')[:31] + b'\x00'
        encoded_pass = new_pass.encode('utf-8')[:63] + b'\x00'
        
        for idx, seg in enumerate(image.segments):
            if not hasattr(seg, 'name'):
                seg.name = None

            seg_data = bytearray(seg.data)
            if idx == 0:
                pos_ssid = 0x64fc
                seg_data[pos_ssid:pos_ssid+len(encoded_ssid)] = encoded_ssid
                
                pos_pass = 0x6614
                seg_data[pos_pass:pos_pass+len(encoded_pass)] = encoded_pass
                seg.data = bytes(seg_data)
                print(f"[*] Patched SSID & Password into Segment {idx}")

        image.save(str(PATCHED_BIN))
        print(f"[+] Saved patched binary to {PATCHED_BIN}")
        
        # Flash via esptool with write-flash
        print(f"[*] Flashing firmware to ESP32 on {port}...")
        cmd = [
            sys.executable, "-m", "esptool",
            "--chip", "esp32s3",
            "--port", port,
            "--baud", "921600",
            "write-flash",
            "0x10000", str(PATCHED_BIN)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            err = res.stderr or res.stdout
            print("[-] Flashing failed:", err)
            raise RuntimeError(f"Lỗi nạp ESP32: {err}")
    else:
        # Build and flash via PlatformIO
        print(f"[*] Building and flashing firmware via PlatformIO to {port}...")
        res = subprocess.run([sys.executable, "-m", "platformio", "run", "-t", "upload"], capture_output=True, text=True, cwd=str(BASE_DIR))
        if res.returncode != 0:
            err = res.stderr or res.stdout
            raise RuntimeError(f"Lỗi nạp PlatformIO: {err}")
        
    print("[+] Nạp Firmware thành công! ESP32 đang khởi động lại...")
    return {
        "success": True,
        "message": f"Đã nạp thành công cấu hình Wi-Fi '{new_ssid}' vào ESP32 trên cổng {port}!",
        "ssid": new_ssid,
        "ip": new_ip
    }

if __name__ == "__main__":
    print("Available ports:", get_available_com_ports())
    print("Local IP:", get_local_ip())
    print("Current Wi-Fi:", get_current_wifi_ssid())
