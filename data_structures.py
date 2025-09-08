from dataclasses import dataclass, asdict
from typing import List, Optional

@dataclass
class DiagnosisResult:
    """
    定義單一部位（寸、關、尺）的標準化診斷結果格式。
    """
    position: str               # 部位名稱, e.g., "寸部 (左)"
    heart_rate_bpm: float       # 心率
    avg_amplitude_pa: float     # 振幅
    avg_pressure_pa: float      # 平均壓力
    pulse_name: str             # 初步脈象, e.g., "浮-實脈"
    general_meaning: str        # 概要意義, e.g., "實證"
    specific_symptom: str       # 具體症狀, e.g., "熱邪壅肺"
    recommended_herbs: List[str]# 建議藥材列表
    
    def to_dict(self) -> dict:
        """將此物件轉換為字典，方便後續處理。"""
        return asdict(self)