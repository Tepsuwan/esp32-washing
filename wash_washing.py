# --- File: wash.py ---
# --- Version: 1.1 (ETH01 - Stability fixes) ---

import machine
import time
import ujson

RS485_TX_PIN = 17
RS485_RX_PIN = 15  # ขา 5 หลบ LAN8720

MODBUS_BAUDRATE = 9600
MODBUS_DATA_BITS = 8
MODBUS_STOP_BITS = 1
MODBUS_PARITY = None
MODBUS_SLAVE_ADDRESS = 1
MACHINE_TYPE = "wash"

def calculate_crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')

class ModbusRTUClient:
    def __init__(self, uart_id=1, tx_pin=RS485_TX_PIN, rx_pin=RS485_RX_PIN):
        self.uart = machine.UART(uart_id, baudrate=MODBUS_BAUDRATE, tx=tx_pin, rx=rx_pin,
                                  bits=MODBUS_DATA_BITS, stop=MODBUS_STOP_BITS, parity=MODBUS_PARITY)
        self.slave_address = MODBUS_SLAVE_ADDRESS
        time.sleep_ms(100)

    def _flush_uart(self):
        # เคลียร์ buffer ที่ค้างอยู่ก่อนส่งคำสั่งใหม่
        deadline = time.ticks_add(time.ticks_ms(), 50)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self.uart.any():
                self.uart.read()
            else:
                break

    def _send_modbus_request(self, slave_address, function_code, start_address, quantity_or_value):
        self._flush_uart()
        pdu = bytearray([function_code])
        pdu.extend(start_address.to_bytes(2, 'big'))
        if function_code == 0x03:
            pdu.extend(quantity_or_value.to_bytes(2, 'big'))
        elif function_code == 0x10:
            pdu.extend(quantity_or_value.to_bytes(2, 'big'))
        else:
            raise ValueError("Unsupported function code")

        adu = bytearray([slave_address])
        adu.extend(pdu)
        adu.extend(calculate_crc16(adu))
        self.uart.write(adu)
        time.sleep_ms(100)

    def _read_modbus_response(self):
        response = bytearray()
        start_time = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start_time) < 500:
            if self.uart.any():
                response.extend(self.uart.read())
            if len(response) >= 5:
                if len(response) >= 3 and response[1] == 0x03 and len(response) >= response[2] + 5:
                    received_crc = int.from_bytes(response[-2:], 'little')
                    calculated_crc = int.from_bytes(calculate_crc16(response[:-2]), 'little')
                    if received_crc == calculated_crc:
                        return response
                elif len(response) == 8 and response[1] == 0x10:
                    received_crc = int.from_bytes(response[-2:], 'little')
                    calculated_crc = int.from_bytes(calculate_crc16(response[:-2]), 'little')
                    if received_crc == calculated_crc:
                        return response
                elif len(response) > 2 and (response[1] & 0x80):
                    if len(response) == 5:
                        received_crc = int.from_bytes(response[-2:], 'little')
                        calculated_crc = int.from_bytes(calculate_crc16(response[:-2]), 'little')
                        if received_crc == calculated_crc:
                            return None
        return None

    def read_holding_registers(self, start_address, quantity):
        self._send_modbus_request(self.slave_address, 0x03, start_address, quantity)
        response = self._read_modbus_response()
        if response and response[1] == 0x03:
            data_bytes = response[3:-2]
            registers = []
            for i in range(0, len(data_bytes), 2):
                registers.append(int.from_bytes(data_bytes[i:i+2], 'big'))
            return registers
        return None

    def write_multiple_registers(self, start_address, values):
        byte_count = len(values) * 2
        pdu = bytearray([0x10])
        pdu.extend(start_address.to_bytes(2, 'big'))
        pdu.extend(len(values).to_bytes(2, 'big'))
        pdu.extend(byte_count.to_bytes(1, 'big'))
        for value in values:
            pdu.extend(value.to_bytes(2, 'big'))

        adu = bytearray([self.slave_address])
        adu.extend(pdu)
        adu.extend(calculate_crc16(adu))
        self.uart.write(adu)

        response = self._read_modbus_response()
        if response and response[1] == 0x10 and len(response) == 8:
            response_start_addr = int.from_bytes(response[2:4], 'big')
            response_num_regs = int.from_bytes(response[4:6], 'big')
            if response_start_addr == start_address and response_num_regs == len(values):
                return True
        return False

modbus_client = ModbusRTUClient()

def _read_register_safe(address, count=1):
    """อ่าน register เดี่ยวอย่างปลอดภัย ไม่ query ซ้ำสอง"""
    result = modbus_client.read_holding_registers(address, count)
    if result and len(result) >= count:
        return result[0] if count == 1 else result
    return 0 if count == 1 else None

def get_machine_status():
    status_data = modbus_client.read_holding_registers(20, 40)
    if status_data and len(status_data) >= 18:
        run_status_map  = {0: "Power on", 1: "Standby", 2: "NA", 3: "Autorun", 4: "Manual", 5: "Idle"}
        door_status_map = {0: "normal", 1: "opened", 2: "closed", 3: "locked", 4: "error", 5: "locking"}
        error_status_map = {0: "normal", 1: "error"}

        # FIX: อ่าน machine_size, screen_size, program_count ครั้งเดียว ไม่ query ซ้ำ 2 รอบ
        prog_time     = modbus_client.read_holding_registers(240, 5)
        prog_data     = modbus_client.read_holding_registers(274, 5)
        machine_size  = _read_register_safe(100)
        screen_size   = _read_register_safe(140)
        program_count = _read_register_safe(115)

        response = {
            "app": "wash", "version": 5.0,
            "SLAVE": MODBUS_SLAVE_ADDRESS, "RX_PIN": RS485_RX_PIN, "TX_PIN": RS485_TX_PIN,
            "device_type": "wash",
            "prog_data": prog_data, "machine_size": machine_size,
            "screen_size": screen_size, "program_count": program_count, "prog_time": prog_time,
            "run_status":  run_status_map.get(status_data[0],  f"Unknown ({status_data[0]})"),
            "door_status": door_status_map.get(status_data[1], f"Unknown ({status_data[1]})"),
            "error_status": error_status_map.get(status_data[2], f"Unknown ({status_data[2]})"),
            "auto_time_hour": status_data[3], "auto_time_min": status_data[4], "auto_time_sec": status_data[5],
            "current_inlet_temperature":  status_data[6],
            "current_outlet_temperature": status_data[7],
            "currently_running_program_number": status_data[8],
            "currently_running_step_number":    status_data[9],
            "coins_required_of_currently_selecting_program": status_data[10],
            "current_coins":           status_data[11],
            "total_coins_recorded":    status_data[12],
            "coins_recorded_in_cash_box": status_data[13],
            "matchine_menu":    status_data[14],
            "coin_inserted":    status_data[15],
            "must_insert_coin": status_data[16],
            "coin_insert":      status_data[17],
            "raw_data": status_data,
            "message": "success", "error": False
        }
        return ujson.dumps(response)

    # fallback error
    status_error = modbus_client.read_holding_registers(60, 9)
    base_error = {
        "app": "wash", "version": 5.0,
        "SLAVE": MODBUS_SLAVE_ADDRESS, "RX_PIN": RS485_RX_PIN, "TX_PIN": RS485_TX_PIN,
        "device_type": "wash", "door_status": 4, "error_status": 1,
        "auto_time_hour": 0, "auto_time_min": 0, "auto_time_sec": 0,
        "current_inlet_temperature": 0, "current_outlet_temperature": 0,
        "currently_running_program_number": 0, "currently_running_step_number": 0,
        "coins_required_of_currently_selecting_program": 0, "current_coins": 0,
        "total_coins_recorded": 0, "coins_recorded_in_cash_box": 0,
        "matchine_menu": 0, "must_insert_coin": 0, "coin_inserted": 0, "coin_insert": 0,
        "raw_data": status_data, "raw_erro": status_error,
    }
    if status_error:
        base_error.update({"run_status": "N/A", "message": "error", "error": "Wash Error"})
    else:
        base_error.update({"run_status": "error", "error": "Modbus Connect Error", "message": "เชื่อมต่อเครื่องซักไม่สำเร็จ"})
    return ujson.dumps(base_error)

def select_program(program_number):
    if not 0 <= program_number <= 30:
        return ujson.dumps({"status": "error", "message": "Invalid program number."})
    if modbus_client.write_multiple_registers(5, [program_number]):
        return ujson.dumps({"status": "success", "message": f"Selected program {program_number}."})
    return ujson.dumps({"status": "error", "message": "Failed to select program."})

def start_operation():
    if modbus_client.write_multiple_registers(1, [1]):
        return ujson.dumps({"status": "success", "message": "Start command sent."})
    return ujson.dumps({"status": "error", "message": "Failed to send start command."})

def stop_operation():
    if modbus_client.write_multiple_registers(3, [1]):
        return ujson.dumps({"status": "success", "message": "Stop command sent."})
    return ujson.dumps({"status": "error", "message": "Failed to send stop command."})

def add_coins(amount):
    if not -10 <= amount <= 65535:
        return ujson.dumps({"status": "error", "message": "Invalid coin amount."})
    if modbus_client.write_multiple_registers(4, [amount]):
        return ujson.dumps({"status": "success", "message": f"Added {amount} coins."})
    return ujson.dumps({"status": "error", "message": "Failed to add coins."})

def reset_error():
    if modbus_client.write_multiple_registers(0, [1]):
        return ujson.dumps({"status": "success", "message": "Error reset command sent."})
    return ujson.dumps({"status": "error", "message": "Failed to send error reset command."})

def sendcommand(address, value):
    result = modbus_client.read_holding_registers(address, value)
    return ujson.dumps({"status": "success", "message": result})

def send_command(address, value):
    result = modbus_client.write_multiple_registers(address, value)
    return ujson.dumps({"status": "success", "message": result})
