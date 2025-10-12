# 檔案: tcm_analyzer.py
# 描述: AI分析器，使用固定的 DiagnosisResult 格式回傳結果。

import pandas as pd
import numpy as np
from scipy.signal import find_peaks
from typing import Dict, Any, Optional, List
import joblib

# 匯入我們新定義的數據結構
from data_structures import DiagnosisResult

class TCMAnalyzer:
    def __init__(self, pulse_db_path: str, medicine_db_path: str, model_path: str = 'pulse_model.pkl'):
        self.pulse_db = self._load_pulse_database(pulse_db_path) 
        self.medicine_db = self._load_medicine_database(medicine_db_path)
        self.sample_rate = 120

        try:
            self.model = joblib.load(model_path)
            print(f"成功從 {model_path} 載入 AI 診斷模型。")
        except FileNotFoundError:
            print(f"警告：找不到 AI 模型檔案 {model_path}，將無法使用 AI 診斷功能。")
            self.model = None

    def _load_pulse_database(self, filepath: str) -> Optional[pd.DataFrame]:
        try:
            df = pd.read_csv(filepath)
            df.columns = df.columns.str.strip()
            df['脈象名稱 (中文)'] = df['脈象名稱 (中文)'].str.strip()
            df.set_index('脈象名稱 (中文)', inplace=True)
            print(f"成功從 {filepath} 載入詳細脈象資料庫。")
            return df
        except Exception as e:
            print(f"錯誤：載入詳細脈象資料庫 {filepath} 失敗: {e}")
            return None
            
    def _load_medicine_database(self, filepath: str) -> Optional[pd.DataFrame]:
        try:
            df = pd.read_csv(filepath)
            df.columns = df.columns.str.strip()
            print(f"成功從 {filepath} 載入藥材資料庫。")
            return df
        except Exception as e:
            print(f"錯誤：載入藥材資料庫 {filepath} 失敗: {e}")
            return None

    def _extract_features(self, time_s: np.ndarray, pressure_pa: np.ndarray) -> Dict[str, Any]:
        if len(pressure_pa) < self.sample_rate: return {"error": "數據點過少"}
        avg_pressure_pa = np.mean(pressure_pa)
        pulse_wave = pressure_pa - avg_pressure_pa
        peaks, _ = find_peaks(pulse_wave, height=np.std(pulse_wave) * 0.8, distance=self.sample_rate * 0.4)
        if len(peaks) < 2: return {"error": "無法檢測到足夠的波峰"}
        avg_peak_interval_s = np.mean(np.diff(time_s[peaks]))
        heart_rate_bpm = 60.0 / avg_peak_interval_s if avg_peak_interval_s > 0 else 0
        avg_amplitude_pa = np.mean(pulse_wave[peaks])
        return {
            "avg_pressure_pa": round(avg_pressure_pa, 2),
            "heart_rate_bpm": round(heart_rate_bpm, 2),
            "avg_amplitude_pa": round(avg_amplitude_pa, 2),
            "peak_count": len(peaks)
        }

    def _classify_pulse(self, features: Dict[str, Any], position_name: str, hand: str) -> DiagnosisResult:
        full_position_name = f"{position_name} ({'左' if hand == 'left' else '右'})"
        
        if "error" in features or self.model is None:
            return DiagnosisResult(
                position=full_position_name,
                heart_rate_bpm=0, avg_amplitude_pa=0, avg_pressure_pa=0,
                pulse_name="分析失敗",
                general_meaning=features.get("error", "AI模型未載入"),
                specific_symptom="N/A",
                recommended_herbs=[]
            )

        try:
            feature_values = [[
                features["avg_pressure_pa"],
                features["heart_rate_bpm"],
                features["avg_amplitude_pa"]
            ]]
            specific_symptom = self.model.predict(feature_values)[0]
        except Exception as e:
            print(f"❌ AI 模型預測失敗: {e}")
            specific_symptom = "AI預測出錯"

        recommended_herbs = []
        if self.medicine_db is not None and specific_symptom not in ["N/A", "AI預測出錯"]:
            matched_herbs = self.medicine_db[self.medicine_db['症狀'] == specific_symptom]
            if not matched_herbs.empty:
                recommended_herbs = matched_herbs['建議中藥材'].tolist()

        pulse_name = "AI診斷"
        general_meaning = "依模型分析"

        return DiagnosisResult(
            position=full_position_name,
            heart_rate_bpm=features.get("heart_rate_bpm", 0),
            avg_amplitude_pa=features.get("avg_amplitude_pa", 0),
            avg_pressure_pa=features.get("avg_pressure_pa", 0),
            pulse_name=pulse_name,
            general_meaning=general_meaning,
            specific_symptom=specific_symptom,
            recommended_herbs=recommended_herbs
        )

    def analyze_pulse_data(self, data: Dict[str, np.ndarray], hand: str) -> Dict[str, DiagnosisResult]:
        results = {}
        for pos, name in [('cun', '寸'), ('guan', '關'), ('chi', '尺')]:
            if pos not in data or 'time_s' not in data:
                full_pos_name = f"{name} ({'左' if hand == 'left' else '右'})"
                results[pos] = DiagnosisResult(full_pos_name, 0, 0, 0, "缺少數據", "", "N/A", [])
                continue
            
            features = self._extract_features(data['time_s'], data[pos])
            results[pos] = self._classify_pulse(features, name, hand)
        return results