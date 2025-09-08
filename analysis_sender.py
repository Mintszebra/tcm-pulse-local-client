import requests
from pulse_parser import parse_pulse_report

API_URL = "https://mapleproject.pythonanywhere.com/api/push-analysis"

def push_analysis_from_text(raw_text: str):
    data_list = parse_pulse_report(raw_text)
    if not data_list:
        print("❌ 解析失敗，無資料")
        return
    print(f"📤 送出 {len(data_list)} 筆 → {API_URL}")
    res = requests.post(API_URL, json=data_list)
    if res.status_code == 200:
        print("✅ 成功推送")
    else:
        print(f"❌ 錯誤 {res.status_code}: {res.text}")
