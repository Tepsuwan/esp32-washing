# --- File: main.py ---
# --- Version: 2.1 (ETH01 - Universal, Auto-restore, Production Stable) ---

import machine
import time
import ujson as json
import network
import ubinascii
import os
import requests
from umqtt.simple import MQTTClient

# --- Identity ---
MQTT_BROKER = "141.98.19.212"
CLIENT_ID = ubinascii.hexlify(machine.unique_id()).decode('utf-8').upper()
COMMAND_TOPIC          = b"washing_machine/" + CLIENT_ID.encode() + b"/commands"
COMMAND_RESPONSE_TOPIC = b"washing_machine/" + CLIENT_ID.encode() + b"/command_response"

# GitHub base URL
GITHUB_RAW = "https://raw.githubusercontent.com/Tepsuwan/esp32-washing/main"

# --- ปิด WiFi ก่อนเริ่ม LAN ---
try:
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
    time.sleep(1)
except:
    pass

client = None
lan    = None

# --- Utility ---
def check_file_exists(filename):
    try:
        os.stat(filename)
        return True
    except OSError:
        return False

def file_copy(source, dest):
    try:
        with open(source, 'rb') as fs:
            with open(dest, 'wb') as fd:
                while True:
                    chunk = fs.read(512)
                    if not chunk:
                        break
                    fd.write(chunk)
        print(f"Copied {source} -> {dest}")
        return True
    except Exception as e:
        print(f"Copy error {source}->{dest}: {e}")
        try:
            os.remove(dest)
        except:
            pass
        return False

def backup_file(filename, backup_name):
    if check_file_exists(filename):
        return file_copy(filename, backup_name)
    return False

def ensure_initial_backups():
    for f in ['main.py', 'wash.py', 'boot.py']:
        bak = f.split('.')[0] + '.bak'
        if not check_file_exists(bak):
            print(f"Creating backup: {bak}")
            file_copy(f, bak)

def get_ip():
    try:
        return lan.ifconfig()[0]
    except:
        return "0.0.0.0"

def download_file(url, dest):
    """ดาวน์โหลดไฟล์จาก URL และบันทึกลงบอร์ด"""
    r = requests.get(url)
    if r.status_code == 200:
        with open(dest, "w") as f:
            f.write(r.text)
        r.close()
        return True
    r.close()
    return False

# --- Auto-restore wash.py ถ้าพัง ---
try:
    import wash
    wash.get_machine_status  # ทดสอบว่ามีฟังก์ชัน
    MACHINE_APP = wash.MACHINE_TYPE  # "wash" หรือ "dryer"
    print(f"wash.py OK — type: {MACHINE_APP}")
except Exception as e:
    print(f"wash.py error: {e}")
    if check_file_exists("wash.bak"):
        print("Restoring wash.py from backup...")
        file_copy("wash.bak", "wash.py")
        time.sleep(1)
        machine.reset()
    else:
        print("No wash.bak found! Cannot restore.")
        machine.reset()

# wash.py ของแต่ละประเภทใน GitHub
# เครื่องซัก → wash_washing.py, เครื่องอบ → wash_dryer.py
WASH_PY_URL = f"{GITHUB_RAW}/wash_washing.py" if MACHINE_APP == "wash" else f"{GITHUB_RAW}/wash_dryer.py"

STATUS_TOPIC = b"washing_machine/" + CLIENT_ID.encode() + b"/status"

# --- Ethernet ---
def connect_eth():
    eth = network.LAN(mdc=machine.Pin(23), mdio=machine.Pin(18),
                      phy_type=network.PHY_LAN8720, phy_addr=1,
                      power=machine.Pin(16, machine.Pin.OUT))
    eth.active(True)
    print(f"[{CLIENT_ID}] Connecting to Ethernet...")
    timeout = 0
    while not eth.isconnected():
        time.sleep(1)
        timeout += 1
        print(".", end="")
        if timeout > 15:
            print(f"\n[{CLIENT_ID}] Ethernet timeout, resetting...")
            machine.reset()
    print(f"\n[{CLIENT_ID}] LAN Connected! IP: {eth.ifconfig()[0]}")
    return eth

# --- MQTT Callback ---
def on_command(topic, msg):
    global client
    print(f"[{CLIENT_ID}] CMD: {msg}")
    response_data = {"status": "error", "version": 5.0, "message": "Unknown error"}
    try:
        data = json.loads(msg.decode())
        if "command" not in data:
            response_data = {"status": "error", "message": "Missing 'command' key"}
            return

        cmd = data["command"]
        key = cmd.get("key", "")

        if key == "start":
            result = json.loads(wash.start_operation())
            response_data = {"status": "success", "version": 5.0, "message": "Start sent.", "modbus_response": result}

        elif key == "stop":
            result = json.loads(wash.stop_operation())
            response_data = {"status": "success", "version": 5.0, "message": "Stop sent.", "modbus_response": result}

        elif key == "reset_error":
            result = json.loads(wash.reset_error())
            response_data = {"status": "success", "version": 5.0, "message": "Error reset.", "modbus_response": result}
            client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
            time.sleep(1)
            machine.reset()

        elif key == "menu" and "value" in cmd:
            result = json.loads(wash.select_program(int(cmd["value"])))
            response_data = {"status": "success", "version": 5.0, "message": f"Program {cmd['value']} selected.", "modbus_response": result}

        elif key == "coins" and "value" in cmd:
            result = json.loads(wash.add_coins(int(cmd["value"])))
            response_data = {"status": "success", "version": 5.0, "message": f"Added {cmd['value']} coins.", "modbus_response": result}

        elif key == "command" and "address" in cmd and "value" in cmd:
            result = json.loads(wash.sendcommand(int(cmd["address"]), cmd["value"]))
            response_data = {"status": "success", "version": 5.0, "message": "Read done.", "modbus_response": result}

        elif key == "register" and "address" in cmd and "value" in cmd:
            result = json.loads(wash.send_command(int(cmd["address"]), cmd["value"]))
            response_data = {"status": "success", "version": 5.0, "message": "Write done.", "modbus_response": result}

        elif key == "get_status":
            wash_status = json.loads(wash.get_machine_status())
            payload = {"version": 5.0, "cmd": "get_status", "ip": get_ip(), "client_id": CLIENT_ID, "status": wash_status}
            client.publish(STATUS_TOPIC, json.dumps(payload).encode())
            response_data = {"status": "success", "version": 5.0, "message": "Status published."}

        elif key == "reboot":
            response_data = {"status": "success", "version": 5.0, "message": "Rebooting."}
            client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
            time.sleep(3)
            machine.reset()

        elif key == "reset_wifi":
            response_data = {"status": "success", "version": 5.0, "message": "ETH device — no WiFi. Rebooting."}
            client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
            time.sleep(3)
            machine.reset()

        # --- Restore จาก backup ---
        elif key == "restore_wash":
            if check_file_exists("wash.bak"):
                file_copy("wash.bak", "wash.py")
                response_data = {"status": "success", "version": 5.0, "message": "wash.py restored. Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(3)
                machine.reset()
            else:
                response_data = {"status": "error", "message": "No wash.bak found."}

        elif key == "restore_main":
            if check_file_exists("main.bak"):
                file_copy("main.bak", "main.py")
                response_data = {"status": "success", "version": 5.0, "message": "main.py restored. Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(3)
                machine.reset()
            else:
                response_data = {"status": "error", "message": "No main.bak found."}

        elif key == "restore_boot":
            if check_file_exists("boot.bak"):
                file_copy("boot.bak", "boot.py")
                response_data = {"status": "success", "version": 5.0, "message": "boot.py restored. Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(3)
                machine.reset()
            else:
                response_data = {"status": "error", "message": "No boot.bak found."}

        # --- OTA Update ---
        elif key == "update_code" and "url" in cmd and "file_name" in cmd:
            fname = cmd["file_name"]
            backup_file(fname, fname.split(".")[0] + ".bak")
            if download_file(cmd["url"], fname):
                response_data = {"status": "success", "message": f"Updated {fname}. Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(5)
                machine.reset()
            else:
                response_data = {"status": "error", "message": f"Download failed."}

        elif key == "update_wash":
            # ดึง wash.py ให้ถูกประเภท (wash หรือ dryer) อัตโนมัติ
            url = cmd.get("value", WASH_PY_URL)
            backup_file("wash.py", "wash.bak")
            if download_file(url, "wash.py"):
                response_data = {"status": "success", "message": f"Updated wash.py ({MACHINE_APP}). Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(5)
                machine.reset()
            else:
                response_data = {"status": "error", "message": "Download wash.py failed."}

        elif key == "update_main":
            url = cmd.get("value", f"{GITHUB_RAW}/main.py")
            backup_file("main.py", "main.bak")
            if download_file(url, "main.py"):
                response_data = {"status": "success", "message": "Updated main.py. Rebooting..."}
                client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
                time.sleep(5)
                machine.reset()
            else:
                response_data = {"status": "error", "message": "Download main.py failed."}

        elif key == "update_version":
            # อัปเดตทุกไฟล์ — wash.py ดึงให้ถูกประเภทอัตโนมัติ
            updates = [
                ("boot.py", f"{GITHUB_RAW}/boot.py"),
                ("main.py", f"{GITHUB_RAW}/main.py"),
                ("wash.py", WASH_PY_URL),  # ถูกประเภทอัตโนมัติ
            ]
            failed = []
            for fname, url in updates:
                backup_file(fname, fname.split(".")[0] + ".bak")
                if download_file(url, fname):
                    print(f"Updated {fname}")
                else:
                    failed.append(fname)
                    print(f"Failed {fname}")

            if failed:
                response_data = {"status": "error", "version": 5.0, "message": f"Failed: {failed}. Rebooting..."}
            else:
                response_data = {"status": "success", "version": 5.0, "message": "OTA done. Rebooting..."}
            client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
            time.sleep(5)
            machine.reset()

        else:
            response_data = {"status": "error", "version": 5.0, "message": f"Unknown command: {key}"}

    except Exception as e:
        print(f"[{CLIENT_ID}] CMD Error: {e}")
        response_data = {"status": "error", "version": 5.0, "message": f"Error: {e}"}

    finally:
        try:
            client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(response_data).encode())
            print(f"[{CLIENT_ID}] Response: {response_data.get('message','')}")
        except Exception as e:
            print(f"[{CLIENT_ID}] Publish response failed: {e}")

# --- Connect MQTT ---
def connect_mqtt():
    global client
    try:
        if client:
            client.disconnect()
    except:
        pass
    c = MQTTClient(CLIENT_ID, MQTT_BROKER, keepalive=60)
    c.set_callback(on_command)
    c.connect()
    c.subscribe(COMMAND_TOPIC)
    print(f"[{CLIENT_ID}] MQTT Connected & Subscribed")
    client = c
    return c

# ================== BOOT ==================
lan = connect_eth()

for attempt in range(5):
    try:
        connect_mqtt()
        break
    except Exception as e:
        print(f"MQTT attempt {attempt+1} failed: {e}")
        time.sleep(3)
        if attempt == 4:
            print("Max retries. Rebooting...")
            machine.reset()

try:
    ensure_initial_backups()
except Exception as e:
    print(f"Backup check error: {e}")

try:
    online_payload = {
        "version": 5.0, "app": MACHINE_APP, "device_type": MACHINE_APP,
        "ip": get_ip(), "client_id": CLIENT_ID,
        "status": "success", "online": True, "temp": "NA", "message": "online"
    }
    client.publish(COMMAND_RESPONSE_TOPIC, json.dumps(online_payload).encode())
except Exception as e:
    print(f"Online notify error: {e}")

last_status = None
ping_counter = 0
print(f"[{CLIENT_ID}] System Running (V2.1) — type: {MACHINE_APP}")

# ================== MAIN LOOP ==================
while True:
    try:
        client.check_msg()

        status_str = wash.get_machine_status()
        wash_status = json.loads(status_str)

        payload = {
            "version": 5.0, "app": MACHINE_APP, "device_type": MACHINE_APP,
            "error_status": False, "ip": get_ip(),
            "client_id": CLIENT_ID, "online": True, "temp": "NA",
            "status": wash_status
        }
        client.publish(STATUS_TOPIC, json.dumps(payload).encode())

        if status_str != last_status:
            print(f"[{CLIENT_ID}] Status changed & published")
            last_status = status_str

        ping_counter += 1
        if ping_counter >= 12:
            client.ping()
            ping_counter = 0

    except OSError as e:
        print(f"[{CLIENT_ID}] Network error: {e}. Reconnecting...")
        time.sleep(5)
        try:
            connect_mqtt()
        except Exception as re_e:
            print(f"Reconnect failed: {re_e}. Rebooting...")
            time.sleep(3)
            machine.reset()

    except Exception as e:
        print(f"[{CLIENT_ID}] Loop error: {e}")

    time.sleep(5)
