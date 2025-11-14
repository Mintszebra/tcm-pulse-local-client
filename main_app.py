# 檔案: main_app.py
# 描述: 最終修正版，恢復了手動設定測量壓力層級的功能，並修復了UI邏輯錯誤。

import sys
import csv
import pandas as pd
import numpy as np
import markdown
from collections import deque
from typing import Optional, List, Any
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QGroupBox, QListWidget, QTextEdit, QMessageBox,
    QFileDialog, QLabel, QLineEdit, QCheckBox, QFormLayout, QComboBox
)
from PyQt6.QtCore import pyqtSignal, pyqtSlot, QThread, QObject
from PyQt6.QtGui import QCloseEvent, QIntValidator
import pyqtgraph as pg

# --- 專案檔案匯入 ---
from pulse_monitor_interface import PulseDiagnosisInterface, DeviceStatus, SensorDataPoint
from fake_pulse_monitor import FakePulseMonitor
from real_pulse_monitor import RealPulseMonitor
from analysis_sender import push_analysis_from_text
from similarity_predictor import load_specific_database, find_most_similar, extract_features
from rag_example import RAGApplication

MAX_PLOT_POINTS = 500

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

class PulseMonitorGUI(QMainWindow):
    status_updated_signal = pyqtSignal(DeviceStatus)
    devices_found_signal = pyqtSignal(list)
    data_received_signal = pyqtSignal(list)
    log_message_signal = pyqtSignal(str)
    start_rag_query = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("智慧中醫脈診輔助系統")
        self.setGeometry(100, 100, 1200, 800)
        
        self.monitor: PulseDiagnosisInterface = RealPulseMonitor()
        
        self.is_measuring = False
        self.current_status: Optional[DeviceStatus] = None
        self.discovered_devices: List[dict] = []

        self.plot_time_data = deque(maxlen=MAX_PLOT_POINTS)
        self.plot_cun_data = deque(maxlen=MAX_PLOT_POINTS)
        self.plot_guan_data = deque(maxlen=MAX_PLOT_POINTS)
        self.plot_chi_data = deque(maxlen=MAX_PLOT_POINTS)
        self.full_measurement_data: List[SensorDataPoint] = []
        self.measurement_start_time: Optional[int] = None
        
        self.current_analysis_results = {}

        self._setup_ui()
        self._connect_signals()

        self.rag_app = None
        try:
            print("正在初始化 RAG 知識庫...")
            self.rag_app = RAGApplication(data_dir="data"); self.rag_app.build_index()
            print("RAG 知識庫準備就緒！")
            
            self.rag_thread = QThread()
            self.rag_worker = RAGWorker(self.rag_app)
            self.rag_worker.moveToThread(self.rag_thread)
            self.start_rag_query.connect(self.rag_worker.run_query)
            self.rag_worker.query_finished.connect(self._on_rag_query_finished)
            self.rag_thread.start()
            print("RAG 背景查詢執行緒已啟動。")
        except Exception as e:
            print(f"❌ RAG 知識庫初始化失敗: {e}"); QMessageBox.warning(self, "警告", f"RAG 知識庫初始化失敗。\n錯誤: {e}")

        self._on_fake_monitor_toggled(self.use_fake_monitor_checkbox.isChecked())
        self.log_message_signal.emit("程式已啟動。")

    def _setup_ui(self):
        main_widget = QWidget(); self.setCentralWidget(main_widget); main_layout = QHBoxLayout(main_widget)
        left_panel = QWidget(); left_layout = QVBoxLayout(left_panel); left_panel.setFixedWidth(320)
        conn_group = QGroupBox("連接控制"); conn_layout = QVBoxLayout(conn_group)
        self.use_fake_monitor_checkbox = QCheckBox("使用虛擬設備"); conn_layout.addWidget(self.use_fake_monitor_checkbox)
        self.scan_button = QPushButton("掃描附近設備"); conn_layout.addWidget(self.scan_button)
        self.device_list_widget = QListWidget(); conn_layout.addWidget(self.device_list_widget)
        self.connect_button = QPushButton("連接選定設備"); conn_layout.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("斷開連接"); conn_layout.addWidget(self.disconnect_button)
        self.reset_button = QPushButton("設備重置 (Reset)"); conn_layout.addWidget(self.reset_button)
        
        measure_group = QGroupBox("測量設定"); measure_layout = QVBoxLayout(measure_group)
        settings_layout = QFormLayout()
        self.duration_input = QLineEdit("20")
        self.duration_input.setValidator(QIntValidator(5, 60))
        settings_layout.addRow("測量時長 (秒):", self.duration_input)
        
        self.measure_pressure_combo = QComboBox()
        self.measure_pressure_combo.addItem("浮", PulseDiagnosisInterface.PressureLevel.FLOATING)
        self.measure_pressure_combo.addItem("中", PulseDiagnosisInterface.PressureLevel.MIDDLE)
        self.measure_pressure_combo.addItem("沉", PulseDiagnosisInterface.PressureLevel.SINKING)
        self.measure_pressure_combo.setCurrentIndex(1) # 預設選中「中」
        settings_layout.addRow("測量壓力層級:", self.measure_pressure_combo)

        measure_layout.addLayout(settings_layout)
        
        self.sim_settings_group = QGroupBox("虛擬設備模擬設定"); sim_layout = QFormLayout(self.sim_settings_group)
        self.simulation_profile_combo = QComboBox()
        for profile in PulseDiagnosisInterface.MeasurementProfile:
            self.simulation_profile_combo.addItem(profile.name, profile)
        sim_layout.addRow("模擬脈象:", self.simulation_profile_combo)
        self.sim_settings_group.setVisible(False)
        measure_layout.addWidget(self.sim_settings_group)
        
        self.start_button = QPushButton("開始測量"); self.stop_button = QPushButton("緊急停止")
        self.save_button = QPushButton("儲存原始數據為 CSV")
        measure_layout.addWidget(self.start_button); measure_layout.addWidget(self.stop_button); measure_layout.addWidget(self.save_button)
        
        patient_group = QGroupBox("患者資訊"); patient_layout = QFormLayout(patient_group)
        self.weight_input = QLineEdit(); self.weight_input.setPlaceholderText("例如: 65")
        self.weight_input.setValidator(QIntValidator(0, 300))
        self.is_pregnant_checkbox = QCheckBox(); patient_layout.addRow("體重 (kg):", self.weight_input)
        patient_layout.addRow("是否為孕婦:", self.is_pregnant_checkbox)
        
        left_layout.addWidget(conn_group); left_layout.addWidget(measure_group)
        left_layout.addWidget(patient_group); left_layout.addStretch()
        
        center_panel = QWidget(); center_layout = QVBoxLayout(center_panel); plot_group = QGroupBox("即時脈搏波形")
        plot_layout = QVBoxLayout(plot_group); self.plot_widget = pg.GraphicsLayoutWidget(); plot_layout.addWidget(self.plot_widget)
        self.plot_widget.setBackground('w'); self.cun_plot = self.plot_widget.addPlot(row=0, col=0, title="寸部")
        self.guan_plot = self.plot_widget.addPlot(row=1, col=0, title="關部"); self.chi_plot = self.plot_widget.addPlot(row=2, col=0, title="尺部")
        self.chi_plot.setLabel('bottom', '經過時間 (ms)'); self.cun_curve = self.cun_plot.plot(pen=pg.mkPen('r', width=2))
        self.guan_curve = self.guan_plot.plot(pen=pg.mkPen('g', width=2)); self.chi_curve = self.chi_plot.plot(pen=pg.mkPen('b', width=2))
        for plot_item in [self.cun_plot, self.guan_plot, self.chi_plot]: plot_item.showGrid(x=True, y=True, alpha=0.3)
        center_layout.addWidget(plot_group)
        
        right_panel = QWidget(); right_layout = QVBoxLayout(right_panel); right_panel.setFixedWidth(450)
        analysis_group = QGroupBox("分析報告"); analysis_layout = QVBoxLayout(analysis_group)
        self.analysis_result_text = QTextEdit(); self.analysis_result_text.setReadOnly(True)
        self.analysis_result_text.setPlaceholderText("測量完成後，此處將顯示完整分析報告..."); analysis_layout.addWidget(self.analysis_result_text)
        self.save_report_button = QPushButton("儲存分析報告為 TXT"); self.save_report_button.setEnabled(False)
        analysis_layout.addWidget(self.save_report_button)
        log_group = QGroupBox("狀態與日誌"); log_layout = QVBoxLayout(log_group); self.log_text = QTextEdit()
        self.log_text.setReadOnly(True); self.log_text.setFixedHeight(200); log_layout.addWidget(self.log_text)
        right_layout.addWidget(analysis_group); right_layout.addWidget(log_group)
        main_layout.addWidget(left_panel); main_layout.addWidget(center_panel, 1); main_layout.addWidget(right_panel)

    def _connect_signals(self):
        self.scan_button.clicked.connect(self._handle_scan); self.connect_button.clicked.connect(self._handle_connect)
        self.disconnect_button.clicked.connect(self._handle_disconnect); self.start_button.clicked.connect(self._handle_start_measurement)
        self.stop_button.clicked.connect(self._handle_stop); self.save_button.clicked.connect(self._handle_save_data)
        self.reset_button.clicked.connect(self._handle_reset); self.use_fake_monitor_checkbox.toggled.connect(self._on_fake_monitor_toggled)
        self.save_report_button.clicked.connect(self._handle_save_report); self.status_updated_signal.connect(self._update_status_display)
        self.devices_found_signal.connect(self._update_device_list); self.data_received_signal.connect(self._update_plot_and_data)
        self.log_message_signal.connect(self._append_log_message)
        self.device_list_widget.currentItemChanged.connect(lambda: self._update_status_display(self.current_status))


    @pyqtSlot(bool)
    def _on_fake_monitor_toggled(self, checked: bool):
        if self.monitor.is_connected():
            QMessageBox.warning(self, "模式切換失敗", "請先斷開當前設備連接。")
            # --- 【修正 2】在用程式碼改變 Checkbox 狀態前，先阻斷信號，完成後再恢復，避免無限循環 ---
            self.use_fake_monitor_checkbox.blockSignals(True)
            try:
                self.use_fake_monitor_checkbox.setChecked(not checked)
            finally:
                self.use_fake_monitor_checkbox.blockSignals(False)
            return
        
        self.sim_settings_group.setVisible(checked)
        self.monitor.shutdown()
        if checked: self.log_message_signal.emit("[系統] 已切換至虛擬設備模式。"); self.monitor = FakePulseMonitor()
        else: self.log_message_signal.emit("[系統] 已切換至真實硬體模式。"); self.monitor = RealPulseMonitor()
        self.monitor.register_event_callback(self._event_handler); self._update_status_display(DeviceStatus.DISCONNECTED)

    def _handle_start_measurement(self):
        self.full_measurement_data.clear(); self.plot_time_data.clear(); self.plot_cun_data.clear()
        self.plot_guan_data.clear(); self.plot_chi_data.clear(); self.analysis_result_text.clear()
        self.measurement_start_time = None; self.save_report_button.setEnabled(False); self.current_analysis_results = {}
        try: duration = int(self.duration_input.text())
        except (ValueError, TypeError): duration = 20
        
        if self.use_fake_monitor_checkbox.isChecked():
            selected_profile = self.simulation_profile_combo.currentData()
            self.log_message_signal.emit(f"開始模擬 '{selected_profile.name}'，時長 {duration} 秒...")
            self.monitor.start_measurement_by_profile(selected_profile, duration_s=duration)
        else:
            selected_level_text = self.measure_pressure_combo.currentText()
            selected_level_enum = self.measure_pressure_combo.currentData()
            
            self.log_message_signal.emit(f"開始 '{selected_level_text}' 級壓力測量，時長 {duration} 秒...")
            self.monitor.start_measurement_by_level([selected_level_enum] * 3, duration_s=duration)

    @pyqtSlot(DeviceStatus)
    def _update_status_display(self, status: DeviceStatus):
        previous_status = self.current_status; self.current_status = status
        # 即使傳入的 status 是 None (例如程式剛啟動時觸發currentItemChanged)，也要保護起來
        if status is not None:
            self.log_message_signal.emit(f"設備狀態: {status.name}")
        
        is_connected = self.monitor.is_connected()
        self.is_measuring = status == DeviceStatus.MEASURING if status else False
        is_fake_mode = self.use_fake_monitor_checkbox.isChecked()

        self.scan_button.setEnabled(not is_connected and not is_fake_mode)
        self.connect_button.setEnabled(not is_connected and (is_fake_mode or self.device_list_widget.currentItem() is not None))
        self.disconnect_button.setEnabled(is_connected); self.reset_button.setEnabled(is_connected)
        self.start_button.setEnabled(is_connected and not self.is_measuring); self.stop_button.setEnabled(is_connected and self.is_measuring)
        self.save_button.setEnabled(not self.is_measuring and len(self.full_measurement_data) > 0)
        
        if (previous_status in [DeviceStatus.MEASURING, DeviceStatus.STOPPING]) and \
           (status == DeviceStatus.CONNECTED_IDLE) and self.full_measurement_data:
            self.log_message_signal.emit("操作結束，正在進行整合分析..."); self._run_integrated_analysis()

    def _save_distance_report(self):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S"); filename = f"pulse_distance_report_{timestamp}.txt"
            report_content = f"脈搏波形相似度比對詳細報告\n時間: {timestamp}\n" + "=" * 40 + "\n\n"
            display_order = [key for key in sorted(self.current_analysis_results.keys(), key=lambda x: ['寸', '關', '尺'].index(x[0]))]
            for pos_key in display_order:
                result = self.current_analysis_results[pos_key]; report_content += f"--- {pos_key} 比對結果 ---\n"
                sim_data = result.get('sim_results')
                if isinstance(sim_data, dict):
                     report_content += f"  - {sim_data.get('最相似的標準樣本', ''):<5s} (距離: {sim_data.get('相似度(距離)', 0)})\n"
                else: report_content += f"  - 比對失敗或無結果: {sim_data}\n"
                report_content += "\n"
            with open(filename, 'w', encoding='utf-8') as f: f.write(report_content)
            self.log_message_signal.emit(f"詳細距離報告已自動儲存至: {filename}")
        except Exception as e: self.log_message_signal.emit(f"錯誤：自動儲存距離報告失敗 - {e}")


    def _describe_features_from_vector(self, features: np.ndarray) -> str:
        if features is None or len(features) < 7: return "特徵數據不足"
        desc = (f"波形平均值 {features[0]:.1f}，標準差 {features[1]:.1f} (反映穩定性)，振幅範圍 {features[3]:.1f} - {features[2]:.1f}，"
                f"變化率均值 {features[4]:.1f}，變化率標準差 {features[5]:.1f} (反映銳利度)，共偵測到 {int(features[6])} 個主要波峰。")
        return desc

    def _run_integrated_analysis(self):
        if not self.full_measurement_data: return
        
        pressure_level = self.measure_pressure_combo.currentText()
        self.log_message_signal.emit(f"使用手動設定的壓力級別 '{pressure_level}' 進行比對...")
        
        ref_features, ref_labels, ref_scaler = load_specific_database(pressure_level)
        if ref_features is None: self.analysis_result_text.setText(f"錯誤：無法載入 '{pressure_level}' 級數據庫。"); return

        positions_to_analyze = ['寸', '關', '尺']; start_time = self.full_measurement_data[0].timestamp_ms
        pos_map = {'寸': 'pressure_pa_cun', '關': 'pressure_pa_guan', '尺': 'pressure_pa_chi'}
        
        self.analysis_result_text.setHtml("<html><body><h4>正在進行初步比對與深度分析...</h4></body></html>")

        for position in positions_to_analyze:
            timestamps = [dp.timestamp_ms - start_time for dp in self.full_measurement_data]
            pressures = [getattr(dp, pos_map[position]) for dp in self.full_measurement_data]
            temp_df = pd.DataFrame({'timestamp': timestamps, 'pressure': pressures})
            temp_csv_path = f"temp_waveform_{position}.csv"
            temp_df.to_csv(temp_csv_path, index=False, header=False)
            sim_results = find_most_similar(temp_csv_path, ref_features, ref_labels, ref_scaler)
            if isinstance(sim_results, dict): preliminary_pulse_name = sim_results.get('最相似的標準樣本', '比對失敗')
            else: preliminary_pulse_name = "比對失敗"
            waveform_data = temp_df.to_numpy(); current_features_vec = extract_features(waveform_data)
            feature_description = self._describe_features_from_vector(current_features_vec)
            patient_context = f"一位病人的基本情況是：體重約為 {self.weight_input.text() or '未提供'} 公斤。"
            if self.is_pregnant_checkbox.isChecked(): patient_context += " **目前處於懷孕狀態**。"
            query_text = (
                "你是一位專業的中醫師。請嚴格依下列『制式化輸出合約』與『標準輸出模板』作答，只根據提供的病人與脈象資訊，不得加入任何多餘說明或提問。\n【制式化輸出合約】\n- 僅輸出模板內容；禁止額外對話/開場白/結語/解說。\n- 標題與順序必須完全一致，包含首行 。\n- 劑量欄位僅填數字（可含一位小數），不得附加 g 或括號備註；準備/先煎等寫在「煎服方法」。\n- 若暫不開藥：表格僅保留表頭；「煎服方法：不需煎服」；休息/保暖/觀察等寫在「用藥禁忌與注意事項」。\n- 「暫不建議使用中藥」等同義語句全文最多一次，且僅能出現在「用藥禁忌與注意事項」。\n- 僅允許在「用藥禁忌與注意事項」用項目符號（- ），不得用破折/連字號作分隔線。\n- 不得新增/刪減/改動任何標題文字；不得使用除模板外的 ` 或**或 ####。\n- 資訊不足時仍須依現有資料完成判斷，不得向使用者追問。\n\n### 病人資訊\n{patient_context}\n\n### 脈象資訊\n- 位置: {hand}手{position}部 ({pressure_level}脈)\n- 初步比對脈象: **{pulse_name}**\n- 特徵描述: '{feature_description}'\n\n請直接輸出下列『標準輸出模板』，以填入內容的方式給出最終答案：\n####脈象判斷\n[此處填寫您對脈象的判斷]\n\n####證候診斷 (總結)\n[此處填寫您對具體病症的診斷]\n\n####個人化用藥建議 (含劑量)\n| 藥材 | 劑量 (g/日) | 作用 |\n| :--- | :--- | :--- |\n| [藥材1] | [劑量1] | [作用1] |\n| [藥材2] | [劑量2] | [作用2] |\n\n**煎服方法**：[此處填寫煎服方法]\n\n#### 用藥禁忌與注意事項\n- [注意事項1]\n- [注意事項2]\n"
            ).format(patient_context=patient_context, hand='左', position=position, pressure_level=pressure_level, pulse_name=preliminary_pulse_name, feature_description=feature_description)
            full_position_name = f"{position}部 ({pressure_level}脈)"
            self.current_analysis_results[full_position_name] = {'sim_results': sim_results, 'features_str': feature_description, 'rag_response': '<h4>正在等候 RAG 系統回覆...</h4>', 'feature_vector': current_features_vec}
            self.start_rag_query.emit(full_position_name, query_text)

    @pyqtSlot(str, str)
    def _on_rag_query_finished(self, position_name: str, rag_response: str):
        if position_name in self.current_analysis_results:
            self.current_analysis_results[position_name]['rag_response'] = rag_response
        if len(self.current_analysis_results) == 3 and all('rag_response' in v and '回覆' not in v['rag_response'] for v in self.current_analysis_results.values()):
            self._update_display_from_results()

    @pyqtSlot()
    def _update_display_from_results(self):
        html_output = "<html><body>"
        display_order = [key for key in sorted(self.current_analysis_results.keys(), key=lambda x: ['寸', '關', '尺'].index(x[0]))]

        for pos_key in display_order:
            result = self.current_analysis_results[pos_key]
            
            sim_results_data = result.get('sim_results')
            if isinstance(sim_results_data, dict):
                preliminary_pulse_name = sim_results_data.get('最相似的標準樣本', '比對失敗')
            else:
                preliminary_pulse_name = "比對失敗"

            html_output += f'<p align="center" style="font-size: 16px;"><b>--- {pos_key} 分析 ---</b></p>'
            
            
            # 建立一個新的文字區塊來存放所有量化數據
            quantitative_block = f"<b>量化特徵:</b> {result['features_str']}\n<b>初步比對:</b> {preliminary_pulse_name}"
            
            # 獲取我們在步驟一中儲存的特徵向量
            feature_vector = result.get('feature_vector')
            
            # 檢查特徵向量是否存在且有效
            if feature_vector is not None and len(feature_vector) >= 7:
                try:
                    # 根據 similarity_predictor.py 的定義來解析特徵
                    # feature_vector = [mean_y, std_y, max_y, min_y, mean_diff, std_diff, num_peaks]
                    avg_pressure = feature_vector[0]
                    amplitude = feature_vector[2] - feature_vector[3]
                    peak_count = feature_vector[6]
                    
                    # 從GUI讀取測量時長以計算心率
                    duration = int(self.duration_input.text())
                    heart_rate = (peak_count / duration) * 60 if duration > 0 else 0
                    
                    # 將計算出的數值加入到文字區塊中
                    quantitative_block += f"\n心率 (bpm): {heart_rate:.0f}"
                    quantitative_block += f"\n振幅 (Pa): {amplitude:.1f}"
                    quantitative_block += f"\n平均壓力 (Pa): {avg_pressure:.1f}"

                except (ValueError, TypeError, IndexError) as e:
                    print(f"計算量化指標時出錯: {e}")

            # 使用 <pre> 標籤來保留換行格式並顯示
            html_output += f"<pre>{quantitative_block}</pre>"
            

            rag_html = markdown.markdown(result['rag_response'], extensions=['fenced_code', 'tables'])
            html_output += f"<div>{rag_html}</div><hr>"
            
        html_output += "</body></html>"
        self.analysis_result_text.setHtml(html_output)
        self.save_report_button.setEnabled(True)
        self.log_message_signal.emit("整合分析報告已完成！")
        
        try:
            full_text_report = self.analysis_result_text.toPlainText()
            self.log_message_signal.emit("正在將分析報告傳送到遠端伺服器...")
            push_analysis_from_text(full_text_report)
            self.log_message_signal.emit("報告已成功傳送！")
            
        except Exception as e:
            error_msg = f"錯誤：傳送報告到伺服器失敗 - {e}"
            print(error_msg)
            self.log_message_signal.emit(error_msg)
        
        self._save_distance_report()

    def _handle_save_report(self):
        report_text = self.analysis_result_text.toPlainText();
        if not report_text: QMessageBox.warning(self, "儲存失敗", "沒有可儲存的報告內容。"); return
        path, _ = QFileDialog.getSaveFileName(self, "儲存分析報告", "", "Text Files (*.txt)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f: f.write(report_text)
                QMessageBox.information(self, "成功", f"分析報告已成功儲存至:\n{path}")
            except IOError as e: QMessageBox.critical(self, "失敗", f"儲存檔案時出錯:\n{e}")
    def _event_handler(self, event):
        if isinstance(event, DeviceStatus): self.status_updated_signal.emit(event)
        elif isinstance(event, list) and event and isinstance(event[0], dict): self.devices_found_signal.emit(event)
        elif isinstance(event, list) and event and isinstance(event[0], SensorDataPoint): self.data_received_signal.emit(event)
        elif isinstance(event, list) and not event: self.devices_found_signal.emit([])
    @pyqtSlot(list)
    def _update_plot_and_data(self, data_points: List[SensorDataPoint]):
        self.full_measurement_data.extend(data_points)
        for dp in data_points:
            if self.measurement_start_time is None: self.measurement_start_time = dp.timestamp_ms
            relative_time_ms = dp.timestamp_ms - self.measurement_start_time
            self.plot_time_data.append(relative_time_ms); self.plot_cun_data.append(dp.pressure_pa_cun)
            self.plot_guan_data.append(dp.pressure_pa_guan); self.plot_chi_data.append(dp.pressure_pa_chi)
        self.cun_curve.setData(list(self.plot_time_data), list(self.plot_cun_data)); self.guan_curve.setData(list(self.plot_time_data), list(self.plot_guan_data)); self.chi_curve.setData(list(self.plot_time_data), list(self.plot_chi_data))
    def _handle_save_data(self):
        if not self.full_measurement_data: return
        path, _ = QFileDialog.getSaveFileName(self, "儲存原始數據", "", "CSV Files (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f); writer.writerow(['timestamp_ms', 'pressure_pa_cun', 'pressure_pa_guan', 'pressure_pa_chi'])
                    for dp in self.full_measurement_data: writer.writerow([dp.timestamp_ms, dp.pressure_pa_cun, dp.pressure_pa_guan, dp.pressure_pa_chi])
                QMessageBox.information(self, "成功", f"數據已儲存至:\n{path}")
            except IOError as e: QMessageBox.critical(self, "失敗", f"儲存檔案時出錯:\n{e}")
    @pyqtSlot(str)
    def _append_log_message(self, message):
        self.log_text.append(message); self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
    @pyqtSlot(list)
    def _update_device_list(self, devices):
        self.device_list_widget.clear(); self.discovered_devices = devices
        if not devices: self.device_list_widget.addItem("未發現任何設備")
        else:
            for d in devices: self.device_list_widget.addItem(f"{d.get('name', 'N/A')} ({d.get('address', 'N/A')})")
    def _handle_scan(self): self.log_message_signal.emit("正在掃描設備..."); self.monitor.scan_for_devices()
    def _handle_connect(self):
        if self.use_fake_monitor_checkbox.isChecked(): self.monitor.connect("00:11:22:33:44:55"); return
        selected_item = self.device_list_widget.currentItem()
        if not selected_item: return
        address = selected_item.text().split('(')[1][:-1]; self.monitor.connect(address)
    def _handle_disconnect(self): self.log_message_signal.emit("正在請求斷開連接..."); self.monitor.disconnect()
    def _handle_stop(self): self.log_message_signal.emit("正在發送緊急停止指令..."); self.monitor.stop_measurement()
    def _handle_reset(self): self.log_message_signal.emit("正在發送設備重置指令..."); self.monitor.reset()
    def closeEvent(self, event: Optional[QCloseEvent]):
        if QMessageBox.question(self, '確認', "您確定要退出程式嗎？") == QMessageBox.StandardButton.Yes:
            if hasattr(self, 'rag_thread') and self.rag_thread.isRunning(): self.rag_thread.quit(); self.rag_thread.wait()
            self.monitor.shutdown(); event.accept()
        else: event.ignore()

if __name__ == "__main__":
    pg.setConfigOptions(antialias=True)
    app = QApplication(sys.argv)
    window = PulseMonitorGUI()
    window.show()
    sys.exit(app.exec())