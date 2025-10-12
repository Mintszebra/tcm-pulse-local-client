# 檔案: similarity_predictor.py
# 描述: 增加了詳細的錯誤追蹤功能以進行除錯

import os
import pandas as pd
import numpy as np
import joblib
import json
from scipy.signal import find_peaks
from scipy.spatial.distance import euclidean
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
import traceback # <--- 新增匯入

# extract_features, create_all_databases, load_specific_database 這幾個函式維持不變
def extract_features(waveform_data):
    y_values = waveform_data[:, 1]
    mean_y = np.mean(y_values); std_y = np.std(y_values)
    max_y = np.max(y_values); min_y = np.min(y_values)
    diff_y = np.diff(y_values, n=1)
    mean_diff = np.mean(diff_y) if diff_y.size > 0 else 0
    std_diff = np.std(diff_y) if diff_y.size > 0 else 0
    peaks, _ = find_peaks(y_values, height=mean_y)
    num_peaks = len(peaks)
    feature_vector = np.array([mean_y, std_y, max_y, min_y, mean_diff, std_diff, num_peaks])
    return feature_vector

def create_all_databases(base_folder):
    # ... (此函式不變)
    pressure_levels = ['沉', '中', '浮'];
    for level in pressure_levels:
        folder_path = os.path.join(base_folder, level); print(f"\n--- 正在處理壓力級別: '{level}' --- ")
        if not os.path.exists(folder_path): print(f"警告：找不到資料夾 '{folder_path}'，已跳過。"); continue
        files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
        if not files: print(f"警告：在 '{folder_path}' 中找不到任何 CSV 檔案，已跳過。"); continue
        reference_features = []; reference_labels = []
        for file_name in tqdm(files, desc=f"建立 '{level}' 級數據庫"):
            label = os.path.splitext(file_name)[0]; file_path = os.path.join(folder_path, file_name)
            try:
                df = pd.read_csv(file_path, header=None, usecols=[0, 1]).apply(pd.to_numeric, errors='coerce').dropna()
                if df.empty: continue
                features = extract_features(df.to_numpy()); reference_features.append(features); reference_labels.append(label)
            except Exception: continue
        if not reference_features: print(f"警告: 在 '{level}' 資料夾中沒有成功處理任何檔案。"); continue
        reference_features = np.array(reference_features); scaler = StandardScaler()
        scaled_reference_features = scaler.fit_transform(reference_features)
        np.save(f'ref_features_{level}.npy', scaled_reference_features); joblib.dump(scaler, f'ref_scaler_{level}.pkl')
        with open(f'ref_labels_{level}.json', 'w', encoding='utf-8') as f: json.dump(reference_labels, f, ensure_ascii=False)
        print(f"'{level}' 級指紋數據庫已成功建立並儲存！")

def load_specific_database(pressure_level):
    # ... (此函式不變)
    try:
        features = np.load(f'ref_features_{pressure_level}.npy'); scaler = joblib.load(f'ref_scaler_{pressure_level}.pkl')
        with open(f'ref_labels_{pressure_level}.json', 'r', encoding='utf-8') as f: labels = json.load(f)
        print(f"'{pressure_level}' 級指紋數據庫載入成功！"); return features, labels, scaler
    except FileNotFoundError:
        print(f"錯誤：找不到 '{pressure_level}' 級的數據庫檔案。"); return None, None, None

# --- 【修改 find_most_similar 函式的錯誤處理】 ---
def find_most_similar(new_csv_path, reference_features, reference_labels, scaler):
    """比對新的CSV檔案，找出最相似的標準樣本。"""
    try:
        df_new = pd.read_csv(new_csv_path, header=None, usecols=[0, 1]).apply(pd.to_numeric, errors='coerce').dropna()
        if df_new.empty: 
            return "錯誤：新的CSV檔案為空或無法解析。"
        
        new_features = extract_features(df_new.to_numpy())
        scaled_new_features = scaler.transform(new_features.reshape(1, -1))
        
        distances = [euclidean(scaled_new_features[0], ref_feature) for ref_feature in reference_features]
        closest_index = np.argmin(distances)
        
        result = {
            '檔案名稱': os.path.basename(new_csv_path),
            '最相似的標準樣本': reference_labels[closest_index],
            '相似度(距離)': f"{distances[closest_index]:.4f}"
        }
        return result
        
    except Exception as e:
        # --- 【關鍵修改】 ---
        # 當錯誤發生時，回傳更詳細的追蹤訊息
        tb_str = traceback.format_exc()
        error_message = f"預測過程中發生錯誤: {e}\n\n詳細追蹤:\n{tb_str}"
        return error_message