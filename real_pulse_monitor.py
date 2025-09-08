# 檔案: real_pulse_monitor.py
# 描述: 實現 PulseDiagnosisInterface，使用 Bleak 與真實的 BPS 硬體進行 BLE 通訊。
# (版本 2.3：恢復客戶端計時邏輯以確保自動停止)

import asyncio
import threading
import time
import struct
from typing import List, Optional, Callable, Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from pulse_monitor_interface import (
    PulseDiagnosisInterface, DeviceStatus, SensorDataPoint, EventCallback
)

# --- (UUIDs 和常數保持不變) ---
BPS_SERVICE_UUID = "652C47C0-C653-41BC-8828-30200EF3350A"
COMMAND_CHAR_UUID = "652C47C1-C653-41BC-8828-30200EF3350A"
MACHINE_STATUS_CHAR_UUID = "652C47C2-C653-41BC-8828-30200EF3350A"
PULSE_VALUE_CHAR_UUID = "652C47C3-C653-41BC-8828-30200EF3350A"

class CommandType:
    NULL = 0x00; STOP_SAMPLING = 0x01; START_SAMPLING = 0x02
    SET_PRESSURE = 0x03; RESET = 0x04

class MachineStatus:
    NULL = 0x00; IDLE = 0x01; SAMPLING = 0x02; SETTING_PRESSURE = 0x03

STATUS_MAP = {
    MachineStatus.IDLE: DeviceStatus.CONNECTED_IDLE,
    MachineStatus.SAMPLING: DeviceStatus.MEASURING,
    MachineStatus.SETTING_PRESSURE: DeviceStatus.INFLATING,
}
MAX_PRESSURE_PA = 90000.0


class RealPulseMonitor(PulseDiagnosisInterface):
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._event_callback: Optional[EventCallback] = None
        self._client: Optional[BleakClient] = None
        self._command_char = None
        
        # --- 核心修改：重新引入客戶端計時任務 ---
        self._timed_measure_task: Optional[asyncio.Task] = None
        
        self.pressure_level_map = {
            self.PressureLevel.FLOATING: 10665.0,
            self.PressureLevel.MIDDLE:   15998.0,
            self.PressureLevel.SINKING:  21331.0,
        }
        self._start_background_loop()

    # ... (背景迴圈, _submit_coro, _fire_event, _machine_status_handler, _pulse_value_handler, _write_command, scan, connect, disconnect 等函式保持不變) ...
    def _start_background_loop(self):
        def loop_runner():
            self._loop = asyncio.new_event_loop(); asyncio.set_event_loop(self._loop); self._loop.run_forever()
        self._thread = threading.Thread(target=loop_runner, daemon=True); self._thread.start()
        while self._loop is None or not self._loop.is_running(): time.sleep(0.01)

    def _submit_coro(self, coro):
        if self._loop and self._loop.is_running(): return asyncio.run_coroutine_threadsafe(coro, self._loop)
        return None

    def _fire_event(self, event_data: Any):
        if self._event_callback: self._event_callback(event_data)
            
    def _machine_status_handler(self, sender: int, data: bytearray):
        status_code = int.from_bytes(data, 'little')
        device_status = STATUS_MAP.get(status_code, DeviceStatus.ERROR)
        self._fire_event(device_status)

    def _pulse_value_handler(self, sender: int, data: bytearray):
        try:
            timestamp_us, cun, guan, chi = struct.unpack('<Qfff', data)
            timestamp_ms = timestamp_us // 1000
            dp = SensorDataPoint(timestamp_ms=timestamp_ms, pressure_pa_cun=cun, pressure_pa_guan=guan, pressure_pa_chi=chi)
            self._fire_event([dp])
        except struct.error as e: print(f"Error unpacking pulse data: {e}. Received {len(data)} bytes.")

    async def _write_command(self, data: bytes):
        if self._client and self._client.is_connected and self._command_char:
            try: await self._client.write_gatt_char(self._command_char, data, response=True)
            except BleakError as e: print(f"Failed to write command: {e}"); await self._handle_disconnect_logic()
        else: print("Cannot write command: not connected or command characteristic not found.")

    def scan_for_devices(self, timeout: int = 5):
        async def _scan():
            self._fire_event(DeviceStatus.SCANNING)
            found_devices = []
            try:
                discovered = await BleakScanner.discover(timeout=timeout, service_uuids=[BPS_SERVICE_UUID])
                for d in discovered: found_devices.append({'address': d.address, 'name': d.name})
            except BleakError as e: print(f"Scan failed: {e}"); self._fire_event(DeviceStatus.ERROR)
            self._fire_event(found_devices); self._fire_event(DeviceStatus.DISCONNECTED)
        self._submit_coro(_scan())

    def connect(self, device_address: str, timeout: int = 10):
        async def _connect():
            self._fire_event(DeviceStatus.CONNECTING)
            try:
                self._client = BleakClient(device_address, timeout=timeout)
                await self._client.connect()
                if self._client.is_connected:
                    await self._client.start_notify(MACHINE_STATUS_CHAR_UUID, self._machine_status_handler)
                    self._command_char = self._client.services.get_characteristic(COMMAND_CHAR_UUID)
                    await asyncio.sleep(0.5)
                    await self._write_command(bytes([CommandType.RESET]))
                    self._fire_event(DeviceStatus.CONNECTED_IDLE)
                else: await self._handle_disconnect_logic()
            except Exception as e: print(f"Connection failed: {e}"); await self._handle_disconnect_logic()
        self._submit_coro(_connect())
    
    async def _handle_disconnect_logic(self):
        if self._timed_measure_task and not self._timed_measure_task.done(): self._timed_measure_task.cancel()
        if self._client and self._client.is_connected:
            try: await self._client.disconnect()
            except Exception: pass
        self._client = None; self._command_char = None
        self._fire_event(DeviceStatus.DISCONNECTED)

    def disconnect(self): self._submit_coro(self._handle_disconnect_logic())
    def is_connected(self) -> bool: return self._client is not None and self._client.is_connected

    def set_pressure_levels_pa(self, cun_pa: float, guan_pa: float, chi_pa: float) -> None:
        if not self.is_connected(): return
        for p in [cun_pa, guan_pa, chi_pa]:
            if not (0 <= p <= MAX_PRESSURE_PA):
                print(f"Error: Pressure {p} Pa is out of range (0-{MAX_PRESSURE_PA} Pa)."); self._fire_event(DeviceStatus.ERROR); return
        payload = struct.pack('<Bfff', CommandType.SET_PRESSURE, cun_pa, guan_pa, chi_pa)
        self._submit_coro(self._write_command(payload))

    def start_measurement_by_level(self, levels: List[PulseDiagnosisInterface.PressureLevel], duration_s: float = 20.0):
        if not self.is_connected() or (self._timed_measure_task and not self._timed_measure_task.done()): return
        pressures_pa = [self.pressure_level_map[level] for level in levels]
        self._timed_measure_task = self._submit_coro(self._run_measurement_flow(pressures_pa, duration_s))

    def start_measurement_by_profile(self, profile: PulseDiagnosisInterface.MeasurementProfile, duration_s: float = 20.0):
        if profile == self.MeasurementProfile.ALL_FLOATING: levels = [self.PressureLevel.FLOATING] * 3
        elif profile == self.MeasurementProfile.ALL_SINKING: levels = [self.PressureLevel.SINKING] * 3
        else: levels = [self.PressureLevel.MIDDLE] * 3
        pressures_pa = [self.pressure_level_map[level] for level in levels]
        self._timed_measure_task = self._submit_coro(self._run_measurement_flow(pressures_pa, duration_s))
        
    # --- 核心修改：恢復客戶端計時的測量流程 ---
    async def _run_measurement_flow(self, pressures_pa: List[float], duration_s: float):
        try:
            # 步驟 1: 設定壓力
            cun_pa, guan_pa, chi_pa = pressures_pa
            set_pressure_payload = struct.pack('<Bfff', CommandType.SET_PRESSURE, cun_pa, guan_pa, chi_pa)
            await self._write_command(set_pressure_payload)

            # 步驟 2: 等待壓力設定完成
            await asyncio.sleep(4.0)

            # 步驟 3: 訂閱脈搏數據通知
            await self._client.start_notify(PULSE_VALUE_CHAR_UUID, self._pulse_value_handler)
            
            # 步驟 4: 發送 "Start Sampling" 指令
            start_sampling_payload = struct.pack('<B', CommandType.START_SAMPLING)
            await self._write_command(start_sampling_payload)

            # 步驟 5: 客戶端開始計時
            await asyncio.sleep(duration_s)

            # 步驟 6: 計時結束，客戶端發送 "Stop Sampling" 指令
            # 注意：_stop_sampling_flow 現在是測量流程的一部分，而非最終清理步驟
            await self._stop_sampling_flow()

        except asyncio.CancelledError:
            print("Measurement flow was cancelled by user.")
            # 確保被取消時也執行停止流程
            await self._stop_sampling_flow()
        except Exception as e:
            print(f"Error during measurement flow: {e}")
            await self._stop_sampling_flow() # 出錯時也要停止
        finally:
            # --- 新增：安全機制 ---
            # 無論測量流程如何結束，最後都發送 reset 指令來釋放壓力
            print("[Safety] Measurement flow finished. Sending Reset to release pressure.")
            await self._write_command(bytes([CommandType.RESET]))
            self._timed_measure_task = None

    async def _stop_sampling_flow(self):
        """統一的停止流程，包含發送指令和取消訂閱"""
        if self._client and self._client.is_connected:
            stop_payload = struct.pack('<B', CommandType.STOP_SAMPLING)
            await self._write_command(stop_payload)
            await self._client.stop_notify(PULSE_VALUE_CHAR_UUID)

    def stop_measurement(self) -> None:
        self._fire_event(DeviceStatus.STOPPING)
        if self._timed_measure_task and not self._timed_measure_task.done():
            # 如果定時任務正在運行，取消它，取消處理程序會呼叫 _stop_sampling_flow
            self._timed_measure_task.cancel()
        else:
            # 如果沒有定時任務（例如設備卡在某個狀態），直接發送停止指令
            self._submit_coro(self._stop_sampling_flow())

    def reset(self) -> None: self._submit_coro(self._write_command(bytes([CommandType.RESET])))
    def register_event_callback(self, callback: EventCallback) -> None: self._event_callback = callback
    def shutdown(self) -> None:
        if self._loop and self._loop.is_running():
            if self.is_connected():
                future = self._submit_coro(self._handle_disconnect_logic())
                if future:
                    try: future.result(timeout=2)
                    except Exception as e: print(f"Error during shutdown disconnect: {e}")
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread: self._thread.join()
        print("RealPulseMonitor background thread and event loop shut down.")