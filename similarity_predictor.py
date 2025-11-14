# 檔案: similarity_predictor.py
# 描述: 修正了濾波器截止頻率，並使用更穩健的 find_peaks 參數來正確計算心率。

import json
import os
import traceback

import joblib
import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt, find_peaks
from scipy.spatial.distance import euclidean
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm


def extract_features(waveform_data):
    """
    從波形數據中提取特徵向量。
    【已修正】:
    1. 降低高頻截止頻率至 20Hz，以更好地去除雜訊。
    2. 使用 prominence 和 distance 參數來穩健地偵測波峰。
    """

    y_values_original = waveform_data[:, 1]
    timestamps_ms = waveform_data[:, 0]

    fs = 100.0  # 假設一個預設值

    try:
        if len(timestamps_ms) > 10:
            avg_interval_ms = np.mean(np.diff(timestamps_ms))
            if avg_interval_ms > 0:
                fs = 1000.0 / avg_interval_ms  # 計算真實取樣率 (Hz)

        # --- 【修正 1：修改濾波器參數】 ---
        f_low = 0.5  # 高通：濾掉基線漂移

        # 20 Hz 對於脈搏訊號已經非常足夠
        f_high_requested = 20.0  # 低通：從 300 Hz 降至 20 Hz

        nyquist_freq = fs / 2.0
        f_high = min(f_high_requested, nyquist_freq * 0.99)

        if f_high > f_low:
            b, a = butter(N=4, Wn=[f_low, f_high], btype="bandpass", fs=fs)
            y_values = filtfilt(b, a, y_values_original)
        else:
            y_values = y_values_original

    except Exception as filter_error:
        print(f"警告：濾波器在執行時發生錯誤: {filter_error}。將使用原始數據進行分析。")
        y_values = y_values_original

    # --- 【特徵提取】 ---

    mean_y = np.mean(y_values)
    std_y = np.std(y_values)
    max_y = np.max(y_values)
    min_y = np.min(y_values)

    diff_y = np.diff(y_values, n=1)
    mean_diff = np.mean(diff_y) if diff_y.size > 0 else 0
    std_diff = np.std(diff_y) if diff_y.size > 0 else 0

    # --- 【修正 2：修改 find_peaks 邏輯】 ---

    # 1. 最小距離 (distance):
    #    假設最快心率 180 BPM (3 Hz)，則波峰間最小間隔為 fs / 3.0
    #    假設最慢心率 40 BPM (0.66 Hz)
    min_dist_samples = fs / 3.0  # (例如 fs=100Hz, 則間隔 33 點)

    # 2. 突起程度 (prominence):
    #    波峰必須至少比周圍高出 0.35 倍的訊號標準差 (這是一個經驗值)
    min_prominence = std_y * 0.35

    # 3. 高度 (height):
    #    波峰至少要高於平均值 (保留)
    min_height = mean_y

    try:
        peaks, _ = find_peaks(
            y_values,
            height=min_height,
            prominence=min_prominence,
            distance=min_dist_samples,
        )
    except Exception:
        # 如果新版 find_peaks 出錯，回退到舊版 (雖然不準，但程式不會崩潰)
        peaks, _ = find_peaks(y_values, height=mean_y)

    num_peaks = len(peaks)

    feature_vector = np.array(
        [mean_y, std_y, max_y, min_y, mean_diff, std_diff, num_peaks]
    )
    return feature_vector


# ... (create_all_databases, load_specific_database, find_most_similar 函式保持不變) ...


def create_all_databases(base_folder):
    pressure_levels = ["沉", "中", "浮"]
    for level in pressure_levels:
        folder_path = os.path.join(base_folder, level)
        print(f"\n--- 正在處理壓力級別: '{level}' --- ")

        if not os.path.exists(folder_path):
            print(f"警告：找不到資料夾 '{folder_path}'，已跳過。")
            continue

        files = [f for f in os.listdir(folder_path) if f.endswith(".csv")]
        if not files:
            print(f"警告：在 '{folder_path}' 中找不到任何 CSV 檔案，已跳過。")
            continue

        reference_features = []
        reference_labels = []

        for file_name in tqdm(files, desc=f"建立 '{level}' 級數據庫"):
            label = os.path.splitext(file_name)[0]
            file_path = os.path.join(folder_path, file_name)

            try:
                df = (
                    pd.read_csv(file_path, header=None, usecols=[0, 1])
                    .apply(pd.to_numeric, errors="coerce")
                    .dropna()
                )
                if df.empty:
                    continue

                # 【重要】: 這裡會呼叫上面修改過的 extract_features
                features = extract_features(df.to_numpy())
                reference_features.append(features)
                reference_labels.append(label)

            except Exception as e:
                print(f"\n處理檔案 {file_name} 時出錯: {e}")
                continue

        if not reference_features:
            print(f"警告: 在 '{level}' 資料夾中沒有成功處理任何檔案。")
            continue

        reference_features = np.array(reference_features)
        scaler = StandardScaler()
        scaled_reference_features = scaler.fit_transform(reference_features)

        np.save(f"ref_features_{level}.npy", scaled_reference_features)
        joblib.dump(scaler, f"ref_scaler_{level}.pkl")
        with open(f"ref_labels_{level}.json", "w", encoding="utf-8") as f:
            json.dump(reference_labels, f, ensure_ascii=False)

        print(f"'{level}' 級指紋數據庫已成功建立並儲存！")


def load_specific_database(pressure_level):
    try:
        features = np.load(f"ref_features_{pressure_level}.npy")
        scaler = joblib.load(f"ref_scaler_{pressure_level}.pkl")
        with open(f"ref_labels_{pressure_level}.json", "r", encoding="utf-8") as f:
            labels = json.load(f)
        print(f"'{pressure_level}' 級指紋數據庫載入成功！")
        return features, labels, scaler
    except FileNotFoundError:
        print(f"錯誤：找不到 '{pressure_level}' 級的數據庫檔案。")
        return None, None, None


def find_most_similar(new_csv_path, reference_features, reference_labels, scaler):
    """比對新的CSV檔案，找出最相似的標準樣本。"""
    try:
        df_new = (
            pd.read_csv(new_csv_path, header=None, usecols=[0, 1])
            .apply(pd.to_numeric, errors="coerce")
            .dropna()
        )
        if df_new.empty:
            return "錯誤：新的CSV檔案為空或無法解析。"

        # 【重要】: 這裡也會自動使用帶有濾波器和修正版 find_peaks 的新版 extract_features
        new_features = extract_features(df_new.to_numpy())
        scaled_new_features = scaler.transform(new_features.reshape(1, -1))

        distances = [
            euclidean(scaled_new_features[0], ref_feature)
            for ref_feature in reference_features
        ]
        closest_index = np.argmin(distances)

        raw_label = reference_labels[closest_index]
        clean_name = raw_label.split('_')[0]  # 例如 "數脈_aug_002" -> "數脈"
        # --------------------------------

        result = {
            "檔案名稱": os.path.basename(new_csv_path),
            "最相似的標準樣本": clean_name,    # 使用乾淨的名稱回傳給 GUI
            "原始樣本編號": raw_label,         # 保留原始編號供參考
            "相似度(距離)": f"{distances[closest_index]:.4f}",
        }
        return result
        

    except Exception as e:
        tb_str = traceback.format_exc()
        error_message = f"預測過程中發生錯誤: {e}\n詳細追蹤:\n{tb_str}"
        return error_message
