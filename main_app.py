# 檔案: main_app.py (版本 3 - 已整合訊號濾波)
import sys, csv
from collections import deque
from typing import Optional, Dict, List, Any
# --- 修改：匯入濾波器相關函式 ---
from scipy.signal import find_peaks, butter, filtfilt
from dataclasses import fields
import markdown

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QGroupBox, QComboBox, QListWidget, QTextEdit, QMessageBox,
    QFileDialog, QRadioButton, QDialog, QLabel, QDialogButtonBox, QLineEdit,
    QCheckBox, QFormLayout
)
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QThread, QObject
from PyQt6.QtGui import QCloseEvent, QIntValidator
from pyqtgraph import PlotWidget, mkPen
import numpy as np

from real_pulse_monitor import RealPulseMonitor
from pulse_monitor_interface import PulseDiagnosisInterface, DeviceStatus, SensorDataPoint
from rag_example import RAGApplication
from analysis_sender import push_analysis_from_text
from pulse_parser import parse_pulse_report


MAX_PLOT_POINTS = 500

# ... (RAGWorker, CustomLevelDialog 類別保持不變)
class RAGWorker(QObject):
    query_finished = pyqtSignal(str, str)
    def __init__(self, rag_app_instance: RAGApplication):
        super().__init__()
        self.rag_app = rag_app_instance
    @pyqtSlot(str, str)
    def run_query(self, position_name: str, query_text: str):
        if self.rag_app:
            try:
                response = self.rag_app.query(query_text)
                self.query_finished.emit(position_name, response)
            except Exception as e:
                self.query_finished.emit(position_name, f"RAG 查詢出錯: {e}")
        else:
            self.query_finished.emit(position_name, "RAG 應用未初始化。")

class CustomLevelDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自訂各部壓力"); layout = QVBoxLayout(self); self.selectors = {}
        positions = ['寸', '關', '尺']; levels = list(PulseDiagnosisInterface.PressureLevel)
        for pos in positions:
            row_layout = QHBoxLayout(); label = QLabel(f"{pos}部壓力:"); combo = QComboBox()
            for level in levels: combo.addItem(level.name, level)
            self.selectors[pos] = combo; row_layout.addWidget(label); row_layout.addWidget(combo); layout.addLayout(row_layout)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept); button_box.rejected.connect(self.reject); layout.addWidget(button_box)
    def get_selected_levels(self) -> list[PulseDiagnosisInterface.PressureLevel]:
        return [self.selectors['寸'].currentData(), self.selectors['關'].currentData(), self.selectors['尺'].currentData()]

class PulseMonitorGUI(QMainWindow):
    start_rag_query = pyqtSignal(str, str)
    status_updated_signal = pyqtSignal(DeviceStatus); devices_found_signal = pyqtSignal(list)
    data_received_signal = pyqtSignal(list); log_message_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧中醫脈診輔助系統 (RAG版)"); self.setGeometry(100, 100, 1600, 800)
        
        self.monitor: PulseDiagnosisInterface = RealPulseMonitor()
        self.monitor.register_event_callback(self._event_handler)
        
        self.discovered_devices = []; self.is_measuring = False; self.current_status: Optional[DeviceStatus] = None
        self.selected_hand_for_measurement: Optional[str] = None
        self.time_data = deque(maxlen=MAX_PLOT_POINTS); self.cun_data = deque(maxlen=MAX_PLOT_POINTS)
        self.guan_data = deque(maxlen=MAX_PLOT_POINTS); self.chi_data = deque(maxlen=MAX_PLOT_POINTS)
        
        # --- 新增：定義濾波器參數 ---
        self.sample_rate = 120  # 設備的取樣率 (Hz)
        self.filter_lowcut = 0.5 # 高通濾波截止頻率 (Hz)，濾除基線漂移
        self.filter_highcut = 15.0 # 低通濾波截止頻率 (Hz)，濾除高頻雜訊
        self.filter_order = 3 # 濾波器階數

        # 只有超過此振幅的波峰才被認為是有效脈搏
        self.MIN_PULSE_AMPLITUDE_PA = 30.0
        
        self.current_analysis_results = {}
        self.combined_analysis_to_send = {}
        
        self._setup_ui(); self._connect_signals()
        
        # ... (RAG 初始化部分保持不變)
        self.rag_app = None
        try:
            print("正在初始化 RAG 知識庫...")
            self.rag_app = RAGApplication(data_dir="data")
            self.rag_app.build_index()
            print("RAG 知識庫準備就緒！")
            self.rag_thread = QThread(); self.rag_worker = RAGWorker(self.rag_app)
            self.rag_worker.moveToThread(self.rag_thread)
            self.start_rag_query.connect(self.rag_worker.run_query)
            self.rag_worker.query_finished.connect(self._on_rag_query_finished)
            self.rag_thread.finished.connect(self.rag_worker.deleteLater)
            self.rag_thread.start()
            print("RAG 背景查詢執行緒已啟動。")
        except Exception as e:
            print(f"❌ RAG 知識庫初始化失敗: {e}")
            QMessageBox.warning(self, "警告", f"RAG 知識庫初始化失敗，詳細問答功能將不可用。\n錯誤: {e}")
        self.log_message_signal.emit("程式已啟動，請掃描設備。")

    # --- 新增：訊號濾波函式 ---
    def _filter_signal(self, data: np.ndarray) -> np.ndarray:
        """
        對輸入的訊號應用一個帶通濾波器。
        """
        nyquist = 0.5 * self.sample_rate
        low = self.filter_lowcut / nyquist
        high = self.filter_highcut / nyquist
        
        # 獲取濾波器係數
        b, a = butter(self.filter_order, [low, high], btype='band')
        
        # 應用零相位濾波
        filtered_data = filtfilt(b, a, data)
        return filtered_data

    # ... (_get_pulse_name_from_features, _update_display_from_results 保持不變)
    def _get_pulse_name_from_features(self, features: dict) -> str:
        if "error" in features: return "分析失敗"
        avg_p, hr, amp = features["avg_pressure_pa"], features["heart_rate_bpm"], features["avg_amplitude_pa"]
        pulse_characteristics: List[str] = []
        if avg_p < 13000: pulse_characteristics.append("浮")
        elif avg_p > 18000: pulse_characteristics.append("沉")
        if hr < 60: pulse_characteristics.append("遲")
        elif hr > 90: pulse_characteristics.append("數")
        if amp < 150: pulse_characteristics.append("虛")
        elif amp > 250: pulse_characteristics.append("實")
        return "-".join(pulse_characteristics) + "脈" if pulse_characteristics else "平脈"
        
    def _update_display_from_results(self):
        html_output = "<html><body style='font-family: Arial, sans-serif;'>"
        for pos_key in sorted(self.current_analysis_results.keys()):
            result = self.current_analysis_results[pos_key]
            html_output += f'<p align="center" style="font-size: 16px;"><b>--- {result["full_name"]} 分析 ---</b></p>'
            html_output += f"<pre><b>量化特徵:</b> {result['features_str']}\n"
            html_output += f"<b>傳統脈象:</b> {result['pulse_name']}</pre>"
            rag_html = markdown.markdown(result['rag_response'], extensions=['fenced_code', 'tables'])
            html_output += rag_html
            html_output += "<hr>"
        html_output += "</body></html>"
        self.analysis_result_text.setHtml(html_output)

    # --- 修改：在特徵提取前加入濾波步驟 ---
    def _extract_features(self, time_s: np.ndarray, pressure_pa: np.ndarray) -> Dict[str, Any]:
        if len(pressure_pa) < self.sample_rate * 2:
            return {"error": "數據點過少，無法進行有效分析"}

        filtered_pressure = self._filter_signal(pressure_pa)
        
        avg_pressure_pa = np.mean(pressure_pa)
        pulse_wave = filtered_pressure - np.mean(filtered_pressure)

        # --- 核心修改：設定一個更 robust 的波峰高度閾值 ---
        # 閾值為「相對標準差」和「絕對最小振幅」中的較大值
        # 這樣既能捕捉到低振幅的真實脈搏，又能過濾掉空測時的雜訊
        peak_height_threshold = max(np.std(pulse_wave) * 0.7, self.MIN_PULSE_AMPLITUDE_PA)

        peaks, _ = find_peaks(
            pulse_wave, 
            height=peak_height_threshold, 
            distance=self.sample_rate * 0.4
        )
        
        if len(peaks) < 3:
            return {"error": f"清晰波峰數量不足 (僅找到 {len(peaks)} 個)"}
            
        avg_peak_interval_s = np.mean(np.diff(time_s[peaks]))
        heart_rate_bpm = 60.0 / avg_peak_interval_s if avg_peak_interval_s > 0 else 0
        avg_amplitude_pa = np.mean(pulse_wave[peaks])
        
        return {
            "avg_pressure_pa": round(avg_pressure_pa, 2), 
            "heart_rate_bpm": round(heart_rate_bpm, 2),
            "avg_amplitude_pa": round(avg_amplitude_pa, 2), 
            "peak_count": len(peaks)
        }

    # ... (_describe_features_to_text, _setup_ui, _connect_signals, 等UI和事件處理函式保持不變)
    def _describe_features_to_text(self, features: dict) -> str:
        p_desc, a_desc, h_desc = "正常範圍", "正常範圍", "正常範圍"
        if features['avg_pressure_pa'] < 13000: p_desc = "顯著偏低"
        elif features['avg_pressure_pa'] > 18000: p_desc = "顯著偏高"
        if features['avg_amplitude_pa'] < 150: a_desc = "微弱"
        elif features['avg_amplitude_pa'] > 250: a_desc = "強勁有力"
        if features['heart_rate_bpm'] < 60: h_desc = "過緩"
        elif features['heart_rate_bpm'] > 90: h_desc = "過快"
        return f"平均壓力{p_desc}、脈搏振幅{a_desc}、且心率{h_desc}"

    def _setup_ui(self):
        main_widget = QWidget(); self.setCentralWidget(main_widget); main_layout = QHBoxLayout(main_widget)
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel); left_panel.setFixedWidth(320)
        conn_group = QGroupBox("連接控制"); conn_layout = QVBoxLayout(conn_group)
        self.scan_button = QPushButton("掃描附近設備")
        self.device_list_widget = QListWidget()
        self.connect_button = QPushButton("連接選定設備")
        self.disconnect_button = QPushButton("斷開連接")
        self.reset_button = QPushButton("設備重置 (Reset)")
        conn_layout.addWidget(self.scan_button); conn_layout.addWidget(self.device_list_widget)
        conn_layout.addWidget(self.connect_button); conn_layout.addWidget(self.disconnect_button)
        conn_layout.addWidget(self.reset_button)
        measure_group = QGroupBox("測量控制"); measure_layout = QVBoxLayout(measure_group)
        hand_layout = QHBoxLayout(); hand_label = QLabel("測量手："); self.hand_selector = QComboBox()
        self.hand_selector.addItem("左手", "left"); self.hand_selector.addItem("右手", "right")
        hand_layout.addWidget(hand_label); hand_layout.addWidget(self.hand_selector); measure_layout.addLayout(hand_layout)
        duration_layout = QHBoxLayout()
        duration_layout.addWidget(QLabel("測量時長 (5-60秒):"))
        self.duration_input = QLineEdit("20")
        self.duration_input.setValidator(QIntValidator(5, 60))
        self.duration_input.setFixedWidth(50)
        duration_layout.addWidget(self.duration_input)
        duration_layout.addStretch()
        measure_layout.addLayout(duration_layout)
        self.profile_radio = QRadioButton("使用便捷模式"); self.profile_combo = QComboBox()
        self.profile_radio.setChecked(True)
        for profile in PulseDiagnosisInterface.MeasurementProfile: self.profile_combo.addItem(profile.name, profile)
        self.custom_radio = QRadioButton("自訂各部壓力")
        measure_layout.addWidget(self.profile_radio); measure_layout.addWidget(self.profile_combo)
        measure_layout.addWidget(self.custom_radio)
        self.profile_radio.toggled.connect(self.profile_combo.setEnabled)
        self.start_button = QPushButton("開始測量")
        self.stop_button = QPushButton("緊急停止")
        self.set_pressure_button = QPushButton("手動設壓 (不採樣)")
        self.save_button = QPushButton("儲存數據為 CSV")
        measure_layout.addWidget(self.start_button); measure_layout.addWidget(self.stop_button)
        measure_layout.addWidget(self.set_pressure_button)
        measure_layout.addWidget(self.save_button)
        patient_group = QGroupBox("患者資訊 (影響劑量建議)"); patient_layout = QFormLayout(patient_group)
        self.weight_input = QLineEdit(); self.weight_input.setPlaceholderText("例如: 65"); self.weight_input.setValidator(QIntValidator(0, 300))
        self.is_pregnant_checkbox = QCheckBox()
        patient_layout.addRow("體重 (kg):", self.weight_input); patient_layout.addRow("是否為孕婦:", self.is_pregnant_checkbox)
        left_layout.addWidget(conn_group); left_layout.addWidget(measure_group); left_layout.addWidget(patient_group); left_layout.addStretch()
        center_panel = QWidget(); center_layout = QVBoxLayout(center_panel)
        plot_group = QGroupBox("即時脈搏波形"); plot_layout = QVBoxLayout(plot_group)
        self.plot_widget = PlotWidget(); plot_layout.addWidget(self.plot_widget); self.plot_widget.setBackground('w')
        self.plot_widget.setLabel('left', '壓力 (Pa)'); self.plot_widget.setLabel('bottom', '時間戳 (ms)')
        self.plot_widget.showGrid(x=True, y=True); self.plot_widget.addLegend()
        self.cun_curve = self.plot_widget.plot(pen=mkPen('r', width=2), name="寸部"); self.guan_curve = self.plot_widget.plot(pen=mkPen('g', width=2), name="關部")
        self.chi_curve = self.plot_widget.plot(pen=mkPen('b', width=2), name="尺部")
        center_layout.addWidget(plot_group)
        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel); right_panel.setFixedWidth(450)
        analysis_group = QGroupBox("初步分析結果"); analysis_layout = QVBoxLayout(analysis_group)
        self.analysis_result_text = QTextEdit(); self.analysis_result_text.setReadOnly(True); self.analysis_result_text.setPlaceholderText("測量完成後，此處將顯示分析結果...")
        analysis_layout.addWidget(self.analysis_result_text)
        log_group = QGroupBox("狀態與日誌"); log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit(); self.log_text.setReadOnly(True); log_layout.addWidget(self.log_text)
        right_layout.addWidget(analysis_group, 1); right_layout.addWidget(log_group, 0); log_group.setFixedHeight(200)
        main_layout.addWidget(left_panel, 0); main_layout.addWidget(center_panel, 1); main_layout.addWidget(right_panel, 0)

    def _connect_signals(self):
        self.scan_button.clicked.connect(self._handle_scan)
        self.connect_button.clicked.connect(self._handle_connect)
        self.disconnect_button.clicked.connect(self._handle_disconnect)
        self.start_button.clicked.connect(self._handle_start_measurement)
        self.stop_button.clicked.connect(self._handle_stop)
        self.save_button.clicked.connect(self._handle_save_data)
        self.reset_button.clicked.connect(self._handle_reset)
        self.set_pressure_button.clicked.connect(self._handle_set_pressure)
        self.status_updated_signal.connect(self._update_status_display)
        self.devices_found_signal.connect(self._update_device_list)
        self.data_received_signal.connect(self._update_plot_and_data)
        self.log_message_signal.connect(self._append_log_message)

    def _event_handler(self, event):
        if isinstance(event, DeviceStatus): self.status_updated_signal.emit(event)
        elif isinstance(event, list) and event and isinstance(event[0], dict): self.devices_found_signal.emit(event)
        elif isinstance(event, list) and event and isinstance(event[0], SensorDataPoint): self.data_received_signal.emit(event)
        elif isinstance(event, list) and not event: self.devices_found_signal.emit(event)
        else: self.log_message_signal.emit(f"[未知事件] 收到無法識別的事件: {type(event)}")

    @pyqtSlot(str)
    def _append_log_message(self, message): self.log_text.append(message); s = self.log_text.verticalScrollBar(); s.setValue(s.maximum())

    @pyqtSlot(DeviceStatus)
    def _update_status_display(self, status: DeviceStatus):
        previous_status = self.current_status; self.current_status = status
        self.log_message_signal.emit(f"[狀態更新] >> 設備狀態變更為: {status.name}")
        is_connected = self.monitor.is_connected()
        self.is_measuring = status == DeviceStatus.MEASURING
        self.scan_button.setEnabled(not is_connected)
        self.connect_button.setEnabled(not is_connected and self.device_list_widget.count() > 0)
        self.disconnect_button.setEnabled(is_connected)
        self.reset_button.setEnabled(is_connected)
        self.start_button.setEnabled(is_connected and not self.is_measuring)
        self.set_pressure_button.setEnabled(is_connected and not self.is_measuring)
        self.stop_button.setEnabled(is_connected and self.is_measuring)
        self.save_button.setEnabled(not self.is_measuring and len(self.time_data) > 0)
        if (previous_status == DeviceStatus.MEASURING or previous_status == DeviceStatus.STOPPING) and \
            status == DeviceStatus.CONNECTED_IDLE and self.time_data:
            self.log_message_signal.emit("[提示] >> 操作結束，正在進行數據分析...")
            if self.selected_hand_for_measurement: self._run_and_display_analysis(self.selected_hand_for_measurement)

    @pyqtSlot(list)
    def _update_device_list(self, devices):
        self.log_message_signal.emit(f"[掃描結果] >> 發現 {len(devices)} 個設備。"); self.device_list_widget.clear()
        self.discovered_devices = devices
        if not devices: self.device_list_widget.addItem("未發現任何設備")
        else:
            for device in devices: self.device_list_widget.addItem(f"{device.get('name', 'N/A')} ({device.get('address', 'N/A')})")
        self._update_status_display(DeviceStatus.DISCONNECTED)
    
    def _run_and_display_analysis(self, selected_hand: str):
        if not self.time_data: self.analysis_result_text.setText("分析失敗：沒有數據。"); return
        if not self.rag_app: self.analysis_result_text.setText("分析失敗：RAG知識庫未載入。"); return
        
        self.current_analysis_results = {}
        self.combined_analysis_to_send = {}
        
        weight_str = self.weight_input.text().strip(); is_pregnant = self.is_pregnant_checkbox.isChecked()
        patient_context = f"一位病人的基本情況是：體重約為 {weight_str if weight_str else '未提供'} 公斤。"
        if is_pregnant: patient_context += " **目前處於懷孕狀態**。"
        else: patient_context += " 非懷孕狀態。"
        
        all_data_to_analyze = {'time_s': np.array(list(self.time_data))/1000.0, 'cun': np.array(list(self.cun_data)), 'guan': np.array(list(self.guan_data)), 'chi': np.array(list(self.chi_data))}
        hand_text = '左' if selected_hand == 'left' else '右'

        for pos, name in [('cun', '寸'), ('guan', '關'), ('chi', '尺')]:
            features = self._extract_features(all_data_to_analyze['time_s'], all_data_to_analyze[pos])
            full_position_name = f"{name}部 ({hand_text})"
            
            # --- 核心修改：檢查特徵提取是否成功 ---
            if "error" in features:
                # 如果提取失敗，顯示錯誤訊息並跳過 RAG 查詢
                self.current_analysis_results[full_position_name] = {
                    'full_name': full_position_name,
                    'features_str': f"分析失敗: {features['error']}",
                    'pulse_name': "無法判斷",
                    'rag_response': f"<h4>分析錯誤</h4><p>由於無法從訊號中提取有效的量化特徵 ({features['error']})，因此無法進行後續的 RAG 知識庫查詢。</p>"
                }
                # 直接進入下一個循環
                continue
            
            # --- 如果提取成功，則執行原有邏輯 ---
            self.current_analysis_results[full_position_name] = {
                'full_name': full_position_name,
                'features_str': f"心率: {features.get('heart_rate_bpm', 'N/A')} | 振幅: {features.get('avg_amplitude_pa', 'N/A')} | 平均壓力: {features.get('avg_pressure_pa', 'N/A')}",
                'pulse_name': self._get_pulse_name_from_features(features),
                'rag_response': '<h4>RAG 知識庫診斷詳解</h4><p>正在查詢中，請稍候...</p>'
            }
            
            feature_description = self._describe_features_to_text(features)
            query_text = (
                "你是一位專業的中醫師。請嚴格依下列『制式化輸出合約』與『標準輸出模板』作答，"
                "只根據提供的病人與脈象資訊，不得加入任何多餘說明或提問。\n"
                "【制式化輸出合約】\n"
                "- 僅輸出模板內容；禁止額外對話/開場白/結語/解說。\n"
                "- 標題與順序必須完全一致，包含首行 。\n"
                "- 劑量欄位僅填數字（可含一位小數），不得附加 g 或括號備註；準備/先煎等寫在「煎服方法」。\n"
                "- 若暫不開藥：表格僅保留表頭；「煎服方法：不需煎服」；休息/保暖/觀察等寫在「用藥禁忌與注意事項」。\n"
                "- 「暫不建議使用中藥」等同義語句全文最多一次，且僅能出現在「用藥禁忌與注意事項」。\n"
                "- 僅允許在「用藥禁忌與注意事項」用項目符號（- ），不得用破折/連字號作分隔線。\n"
                "- 不得新增/刪減/改動任何標題文字；不得使用除模板外的  或**或 ####。\n"
                "- 資訊不足時仍須依現有資料完成判斷，不得向使用者追問。\n\n"
                "### 病人資訊\n{patient_context}\n\n"
                "### 脈象資訊\n- 位置: {hand_text}手{name}部\n- 特徵描述: '{feature_description}'\n\n"
                "請直接輸出下列『標準輸出模板』，以填入內容的方式給出最終答案：\n"
                "####脈象判斷\n"
                "[此處填寫您對脈象的判斷]\n\n"
                "####證候診斷 (總結)\n"
                "[此處填寫您對具體病症的診斷]\n\n"
                "####個人化用藥建議 (含劑量)\n"
                "| 藥材 | 劑量 (g/日) | 作用 |\n"
                "| :--- | :--- | :--- |\n"
                "| [藥材1] | [劑量1] | [作用1] |\n"
                "| [藥材2] | [劑量2] | [作用2] |\n\n"
                "**煎服方法**：[此處填寫煎服方法]\n\n"
                "#### 用藥禁忌與注意事項\n"
                "- [注意事項1]\n"
                "- [注意事項2]\n"
            ).format(patient_context=patient_context, hand_text=hand_text, name=name, feature_description=feature_description)
            self.start_rag_query.emit(full_position_name, query_text)
        self._update_display_from_results()
        
    @pyqtSlot(str, str)
    def _on_rag_query_finished(self, position_name: str, rag_response: str):
        if position_name in self.current_analysis_results:
            self.current_analysis_results[position_name]['rag_response'] = rag_response
            self.combined_analysis_to_send[position_name] = rag_response
            hand_text = '左' if self.selected_hand_for_measurement == 'left' else '右'
            required_positions = {f'寸部 ({hand_text})', f'關部 ({hand_text})', f'尺部 ({hand_text})'}
            if set(self.combined_analysis_to_send.keys()) == required_positions:
                self._update_display_from_results()
                full_text_report = self.analysis_result_text.toPlainText()
                try:
                    push_analysis_from_text(full_text_report)
                except Exception as e:
                    print(f"[錯誤] 無法將完整的分析文本推送到後端: {e}")
                self.combined_analysis_to_send = {}
            self._update_display_from_results()

    @pyqtSlot(list)
    def _update_plot_and_data(self, data_points):
        for dp in data_points:
            self.time_data.append(dp.timestamp_ms); self.cun_data.append(dp.pressure_pa_cun)
            self.guan_data.append(dp.pressure_pa_guan); self.chi_data.append(dp.pressure_pa_chi)
        self.cun_curve.setData(list(self.time_data), list(self.cun_data)); self.guan_curve.setData(list(self.time_data), list(self.guan_data))
        self.chi_curve.setData(list(self.time_data), list(self.chi_data))

    def _handle_scan(self): self.log_message_signal.emit("正在請求掃描設備 (持續5秒)..."); self.scan_button.setEnabled(False); self.monitor.scan_for_devices(timeout=5)
    def _handle_connect(self):
        selected_item = self.device_list_widget.currentItem()
        if not selected_item: QMessageBox.warning(self, "連接錯誤", "請先在列表中選擇一個設備。"); return
        selected_index = self.device_list_widget.currentRow()
        if 0 <= selected_index < len(self.discovered_devices):
            address = self.discovered_devices[selected_index].get('address')
            if address: self.log_message_signal.emit(f"正在嘗試連接到 {address}..."); self.monitor.connect(address)
            else: QMessageBox.critical(self, "連接錯誤", "選擇的設備沒有有效的位址。")
        else: QMessageBox.warning(self, "連接錯誤", "請選擇一個有效的設備進行連接。")
    def _handle_disconnect(self): self.log_message_signal.emit("正在請求斷開連接..."); self.monitor.disconnect()
    def _handle_stop(self): self.log_message_signal.emit("正在發送緊急停止指令..."); self.monitor.stop_measurement()

    @pyqtSlot()
    def _handle_reset(self):
        self.log_message_signal.emit("正在發送設備重置指令..."); 
        self.monitor.reset()

    @pyqtSlot()
    def _handle_set_pressure(self):
        dialog = CustomLevelDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_levels = dialog.get_selected_levels()
            try:
                pressures_pa = [self.monitor.pressure_level_map[level] for level in selected_levels]
                cun_pa, guan_pa, chi_pa = pressures_pa
                self.log_message_signal.emit(f"正在手動設定壓力 -> 寸:{cun_pa:.0f}Pa, 關:{guan_pa:.0f}Pa, 尺:{chi_pa:.0f}Pa...")
                self.monitor.set_pressure_levels_pa(cun_pa, guan_pa, chi_pa)
            except (KeyError, AttributeError) as e:
                self.log_message_signal.emit(f"[錯誤] 無法轉換壓力級別: {e}")
                QMessageBox.warning(self, "錯誤", "無法從當前監控器獲取壓力映射，請確保已連接到真實硬體。")
        else:
            self.log_message_signal.emit("手動設壓操作已取消。")

    def _handle_start_measurement(self):
        self.time_data.clear(); self.cun_data.clear(); self.guan_data.clear(); self.chi_data.clear()
        self.analysis_result_text.clear()
        self.selected_hand_for_measurement = self.hand_selector.currentData()
        hand_text = self.hand_selector.currentText()
        try:
            duration = int(self.duration_input.text())
        except (ValueError, TypeError):
            duration = 20
            self.duration_input.setText("20")
        self.log_message_signal.emit(f"[提示] >> 已選擇測量 {hand_text}，時長 {duration} 秒。準備開始新測量...")
        if self.profile_radio.isChecked():
            selected_profile = self.profile_combo.currentData()
            self.log_message_signal.emit(f"正在使用模式 '{selected_profile.name}' 開始測量..."); 
            self.monitor.start_measurement_by_profile(selected_profile, duration_s=duration)
        elif self.custom_radio.isChecked():
            dialog = CustomLevelDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_levels = dialog.get_selected_levels()
                level_names = [level.name for level in selected_levels]
                self.log_message_signal.emit(f"正在以自訂壓力 [寸:{level_names[0]}, 關:{level_names[1]}, 尺:{level_names[2]}] 開始測量...")
                self.monitor.start_measurement_by_level(selected_levels, duration_s=duration)
            else: 
                self.log_message_signal.emit("自訂測量已取消。")

    def _handle_save_data(self):
        if not self.time_data: QMessageBox.warning(self, "儲存錯誤", "沒有測量數據可供儲存。"); return
        path, _ = QFileDialog.getSaveFileName(self, "儲存數據", "", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f); header = [field.name for field in fields(SensorDataPoint)]; writer.writerow(header)
                    all_data = zip(list(self.time_data), list(self.cun_data), list(self.guan_data), list(self.chi_data))
                    for row in all_data: writer.writerow(row)
                self.log_message_signal.emit(f"數據已成功儲存到 {path}"); QMessageBox.information(self, "儲存成功", f"數據已成功儲存到\n{path}")
            except IOError as e: self.log_message_signal.emit(f"[錯誤] 儲存檔案時出錯: {e}"); QMessageBox.critical(self, "儲存失敗", f"儲存檔案時出錯:\n{e}")

    def closeEvent(self, event: Optional[QCloseEvent]):
        reply = QMessageBox.question(self, '確認退出', "您確定要退出程式嗎？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.log_message_signal.emit("正在執行關機程序..."); 
            if hasattr(self, 'rag_thread') and self.rag_thread.isRunning():
                self.rag_thread.quit(); self.rag_thread.wait()
            self.monitor.shutdown()
            if event: event.accept()
        else:
            if event: event.ignore()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PulseMonitorGUI()
    window.show()
    sys.exit(app.exec())