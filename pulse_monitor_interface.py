# 檔案: pulse_monitor_interface.py
# 描述: 定義與「桌上型中醫脈診模擬與數據採集系統」互動的抽象介面 (API Contract)。

from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum, auto
from dataclasses import dataclass
from typing import List, Dict, Callable, Any

# ==============================================================================
# 1. 資料結構定義 (Data Structures)
# ==============================================================================

class DeviceStatus(Enum):
    DISCONNECTED = auto()
    SCANNING = auto()
    CONNECTING = auto()
    CONNECTED_IDLE = auto()
    INFLATING = auto()       # 在真實硬體中，對應 eSettingPressure
    MEASURING = auto()       # 在真實硬體中，對應 eSampling
    STOPPING = auto()
    ERROR = auto()

@dataclass
class SensorDataPoint:
    timestamp_ms: int
    pressure_pa_cun: float
    pressure_pa_guan: float
    pressure_pa_chi: float

# ==============================================================================
# 2. 回呼函數型別定義 (Callback Type Definitions)
# ==============================================================================

EventCallback = Callable[[Any], None]

# ==============================================================================
# 3. 抽象介面定義 (Abstract Base Class - The API Contract)
# ==============================================================================

class PulseDiagnosisInterface(ABC):
    class PressureLevel(Enum):
        FLOATING = auto()
        MIDDLE = auto()
        SINKING = auto()

    class MeasurementProfile(Enum):
        ALL_FLOATING = auto()
        ALL_MIDDLE = auto()
        ALL_SINKING = auto()
        LIVER_FIRE_SIM = auto()
        KIDNEY_DEFICIENCY_SIM = auto()

    @abstractmethod
    def scan_for_devices(self, timeout: int = 5) -> None:
        pass

    @abstractmethod
    def connect(self, device_address: str, timeout: int = 10) -> None:
        pass

    @abstractmethod
    def disconnect(self) -> None:
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        pass
        
    @abstractmethod
    def set_pressure_levels_pa(self, cun_pa: float, guan_pa: float, chi_pa: float) -> None:
        """
        【低階API】非阻塞地直接設定三個位置的目標壓力值（單位：Pa）。
        
        硬體會進入 INFLATING (eSettingPressure) 狀態，直到壓力穩定後回到 CONNECTED_IDLE。
        
        :param cun_pa: 寸部目標壓力 (Pa)
        :param guan_pa: 關部目標壓力 (Pa)
        :param chi_pa: 尺部目標壓力 (Pa)
        """
        pass

    @abstractmethod
    def start_measurement_by_level(self, levels: List[PulseDiagnosisInterface.PressureLevel], duration_s: float = 20.0) -> None:
        """
        【低階API】以指定的壓力級別組合開始一次固定時長的測量。

        :param levels: 一個包含三個壓力級別的列表，順序為 [寸, 關, 尺]。
        :param duration_s: 測量持續時間（秒）。
        """
        pass
    
    @abstractmethod
    def start_measurement_by_profile(self, profile: MeasurementProfile, duration_s: float = 20.0):
        """
        【高階API】以預設的測量模式開始一次固定時長的測量。
        :param duration_s: 測量持續時間（秒）。
        """
        pass

    @abstractmethod
    def stop_measurement(self) -> None:
        pass

    @abstractmethod
    def reset(self) -> None:
        """
        【低階API】發送重設指令，強制設備停止當前所有操作並返回 Idle 狀態。
        壓力會被釋放歸零。
        """
        pass

    @abstractmethod
    def register_event_callback(self, callback: EventCallback) -> None:
        pass

    @abstractmethod
    def shutdown(self) -> None:
        pass