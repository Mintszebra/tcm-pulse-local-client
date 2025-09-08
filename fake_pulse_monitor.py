# 檔案: fake_pulse_monitor.py (升級版)
# 描述: 能夠根據便捷模式，動態調整心率和振幅以模擬特定病症脈象的假儀器。

import asyncio
import threading
import time
import random
import math
from typing import List, Dict, Optional, Callable, Any
import concurrent.futures

from pulse_monitor_interface import (
    PulseDiagnosisInterface, DeviceStatus, SensorDataPoint, EventCallback
)

class FakePulseMonitor(PulseDiagnosisInterface):
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._event_callback: Optional[EventCallback] = None
        self._measuring_task: Optional[asyncio.Future] = None
        self._is_connected = False
        self._global_start_time = 0.0

        # 將這些參數設為可變的，以便在模擬中動態調整
        self._current_heart_rate = 75.0
        self._current_amplitude = 200.0

        self._start_background_loop()

    def _generate_realistic_pulse(self, elapsed_time_s: float) -> float:
        # 使用當前的模擬心率來計算
        beat_period_s = 60.0 / self._current_heart_rate
        time_in_cycle = (elapsed_time_s % beat_period_s) / beat_period_s
        rise_tau = 0.1; fall_tau = 0.4
        rising_edge = 1 - math.exp(-time_in_cycle / rise_tau)
        falling_edge = math.exp(-time_in_cycle / fall_tau)
        pulse = rising_edge * falling_edge * 4.5 
        return pulse

    def start_measurement_by_profile(self, profile: PulseDiagnosisInterface.MeasurementProfile):
        """
        【高階API】根據預設模式開始測量。
        此版本已升級，可以為特定模式設定不同的心率和振幅。
        """
        # 預設為正常脈象的參數
        levels = [self.PressureLevel.MIDDLE] * 3
        self._current_heart_rate = 75.0
        self._current_amplitude = 200.0

        if profile == self.MeasurementProfile.ALL_FLOATING:
            levels = [self.PressureLevel.FLOATING] * 3
        elif profile == self.MeasurementProfile.ALL_SINKING:
            levels = [self.PressureLevel.SINKING] * 3
        
        # --- 新增的病症模擬邏輯 ---
        elif profile == self.MeasurementProfile.LIVER_FIRE_SIM:
            print("[FAKE] 模擬模式：肝火旺盛 (弦實脈特徵)")
            # 模擬弦實脈：關部壓力深(沉)，整體振幅有力
            levels = [self.PressureLevel.MIDDLE, self.PressureLevel.SINKING, self.PressureLevel.MIDDLE]
            self._current_amplitude = 260.0 # 振幅 > 250, 觸發「實」
        
        elif profile == self.MeasurementProfile.KIDNEY_DEFICIENCY_SIM:
            print("[FAKE] 模擬模式：腎陽虛乏 (沉遲細弱特徵)")
            # 模擬沉遲細弱脈：尺部壓力深(沉)，心率慢，振幅弱
            levels = [self.PressureLevel.MIDDLE, self.PressureLevel.MIDDLE, self.PressureLevel.SINKING]
            self._current_heart_rate = 55.0  # 心率 < 60, 觸發「遲」
            self._current_amplitude = 140.0 # 振幅 < 150, 觸發「虛」
            
        # 呼叫底層的測量函式開始模擬
        self.start_measurement_by_level(levels)

    async def _measurement_simulator(self, levels: List['PulseDiagnosisInterface.PressureLevel']):
        try:
            pressure_map_pa = {
                self.PressureLevel.FLOATING: 80 * 133.322,   # 約 10665 Pa
                self.PressureLevel.MIDDLE:   120 * 133.322,  # 約 15998 Pa
                self.PressureLevel.SINKING:  160 * 133.322,  # 約 21331 Pa
            }
            base_pressures_pa = [pressure_map_pa.get(level, pressure_map_pa[self.PressureLevel.MIDDLE]) for level in levels]
            self._fire_event(DeviceStatus.INFLATING); await asyncio.sleep(1)
            self._fire_event(DeviceStatus.MEASURING)
            
            self._global_start_time = time.perf_counter()
            duration_s, sample_rate = 20, 120
            total_samples = sample_rate * duration_s
            
            for i in range(total_samples):
                if not self.is_connected(): raise asyncio.CancelledError("Disconnected")
                simulated_elapsed_time_s = i / sample_rate
                timestamp_ms = int(simulated_elapsed_time_s * 1000)
                
                # 使用當前的模擬振幅
                pulse_wave = self._current_amplitude * self._generate_realistic_pulse(simulated_elapsed_time_s)
                
                dp = SensorDataPoint(
                    timestamp_ms=timestamp_ms,
                    pressure_pa_cun=base_pressures_pa[0] + pulse_wave + random.uniform(-5, 5),
                    pressure_pa_guan=base_pressures_pa[1] + pulse_wave * 0.9 + random.uniform(-5, 5),
                    pressure_pa_chi=base_pressures_pa[2] + pulse_wave * 1.1 + random.uniform(-5, 5)
                )
                self._fire_event([dp]) # 改為一次只發送一個點，讓圖表更流暢

                # 模擬真實取樣延遲
                await asyncio.sleep(1.0 / sample_rate)
            
            print("[FAKE] Measurement cycle complete.")

        except asyncio.CancelledError: print("[FAKE] Measurement was cancelled.")
        finally:
            self._fire_event(DeviceStatus.CONNECTED_IDLE)
            self._measuring_task = None

    def _start_background_loop(self):
        def loop_runner():
            try:
                self._loop = asyncio.new_event_loop(); asyncio.set_event_loop(self._loop); self._loop.run_forever()
            finally:
                if self._loop: self._loop.close()
                print("[FAKE] Background event loop closed.")
        self._thread = threading.Thread(target=loop_runner, daemon=True); self._thread.start()
        while self._loop is None or not self._loop.is_running(): time.sleep(0.01)

    def _submit_coro(self, coro):
        if self._loop and self._loop.is_running() and asyncio.iscoroutine(coro):
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        return None
        
    def _fire_event(self, event_data: Any):
        if self._event_callback: self._event_callback(event_data)
        
    def scan_for_devices(self, timeout: int = 5):
        async def _scan():
            self._fire_event(DeviceStatus.SCANNING)
            devices = [{'address': '00:11:22:33:44:55', 'name': 'PulseMonitor-Sim-01'}]
            await asyncio.sleep(2); self._fire_event(devices); self._fire_event(DeviceStatus.DISCONNECTED)
        self._submit_coro(_scan())
        
    def connect(self, device_address: str, timeout: int = 10):
        async def _connect():
            self._fire_event(DeviceStatus.CONNECTING); await asyncio.sleep(1.5)
            self._is_connected = True; self._fire_event(DeviceStatus.CONNECTED_IDLE)
        self._submit_coro(_connect())
        
    def disconnect(self): self._submit_coro(self._async_disconnect())
    
    def is_connected(self) -> bool: return self._is_connected
    
    def start_measurement_by_level(self, levels: List['PulseDiagnosisInterface.PressureLevel']):
        if not self.is_connected() or (self._measuring_task and not self._measuring_task.done()): return
        self._measuring_task = self._submit_coro(self._measurement_simulator(levels))
        
    def stop_measurement(self):
        async def _stop():
            self._fire_event(DeviceStatus.STOPPING)
            if self._measuring_task and not self._measuring_task.done(): self._measuring_task.cancel()
            await asyncio.sleep(1); self._fire_event(DeviceStatus.CONNECTED_IDLE)
        self._submit_coro(_stop())
        
    def register_event_callback(self, callback: EventCallback) -> None: self._event_callback = callback
    
    def shutdown(self) -> None:
        if self._loop and self._loop.is_running():
            if self.is_connected():
                future = self._submit_coro(self._async_disconnect())
                if future:
                    try: future.result(timeout=2)
                    except Exception as e: print(f"[FAKE] Error during shutdown disconnect: {e}")
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread: self._thread.join()
        print("[FAKE] Background thread and event loop shut down.")

    async def _async_disconnect(self):
        if self._is_connected:
            if self._measuring_task and not self._measuring_task.done():
                self._measuring_task.cancel(); await asyncio.sleep(0.1)
            self._is_connected = False; self._fire_event(DeviceStatus.DISCONNECTED)
            print("[FAKE] Disconnected.")