
# 描述: 主要的前端 GUI 應用程式，新增了數據記錄與匯出功能。

import tkinter as tk
from tkinter import ttk, messagebox, filedialog # 新增 filedialog
import queue
import csv # 新增 csv 模組
from typing import List, Dict, Any

# 匯入 matplotlib 相關函式庫
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from collections import deque

# 匯入我們定義的介面和假實現
from pulse_monitor_interface import (
    PulseDiagnosisInterface, DeviceStatus, SensorDataPoint
)
from fake_pulse_monitor import FakePulseMonitor

# --- 中文字體設定 ---
try:
    plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei UI', 'SimSun']
    plt.rcParams['axes.unicode_minus'] = False
except Exception as e:
    print(f"中文字體設定失敗，圖表標籤可能顯示異常: {e}")


class PulseMonitorApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Pulse Diagnosis Monitor")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.geometry("850x700")

        self.device: PulseDiagnosisInterface = FakePulseMonitor()
        self.event_queue = queue.Queue()
        
        # --- 數據儲存修改 ---
        # 用於即時繪圖的滾動數據 (只保留部分數據)
        self.plot_data = [{'time': deque(maxlen=300), 'pressure': deque(maxlen=300)} for _ in range(3)]
        # 用於完整記錄所有數據的列表
        self.recorded_data: List[SensorDataPoint] = []
        
        self._current_status = DeviceStatus.DISCONNECTED

        self._create_widgets()
        self.device.register_event_callback(self.event_queue.put)
        self.update_gui()

    def _create_widgets(self):
        
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        top_frame = ttk.Frame(main_frame)
        top_frame.pack(side=tk.TOP, fill=tk.X, pady=5)

        conn_frame = ttk.LabelFrame(top_frame, text="Connection", padding=10)
        conn_frame.pack(side=tk.LEFT, padx=(0, 10), fill=tk.Y)
        self.device_combobox = ttk.Combobox(conn_frame, state="readonly", width=35)
        self.device_combobox.pack(pady=2)
        scan_conn_frame = ttk.Frame(conn_frame)
        scan_conn_frame.pack(pady=2)
        self.scan_button = ttk.Button(scan_conn_frame, text="Scan", command=self.device.scan_for_devices)
        self.scan_button.pack(side=tk.LEFT, padx=5)
        self.connect_button = ttk.Button(scan_conn_frame, text="Connect", command=self.connect_device, state=tk.DISABLED)
        self.connect_button.pack(side=tk.LEFT, padx=5)
        self.disconnect_button = ttk.Button(conn_frame, text="Disconnect", command=self.device.disconnect, state=tk.DISABLED)
        self.disconnect_button.pack(pady=2, fill=tk.X)

        measure_frame = ttk.LabelFrame(top_frame, text="Measurement Settings", padding=10)
        measure_frame.pack(side=tk.LEFT, padx=10, fill=tk.Y)
        self.pressure_vars = []
        labels = ["Cun (寸):", "Guan (關):", "Chi (尺):"]
        pressure_levels = [level.name for level in PulseDiagnosisInterface.PressureLevel]
        for i, label_text in enumerate(labels):
            var = tk.StringVar(value=pressure_levels[1])
            self.pressure_vars.append(var)
            row_frame = ttk.Frame(measure_frame)
            row_frame.pack(anchor=tk.W, pady=2)
            ttk.Label(row_frame, text=label_text, width=10).pack(side=tk.LEFT)
            ttk.Combobox(row_frame, textvariable=var, values=pressure_levels, state="readonly", width=12).pack(side=tk.LEFT)

        action_frame = ttk.LabelFrame(top_frame, text="Actions", padding=10)
        action_frame.pack(side=tk.LEFT, padx=10, fill=tk.Y)
        self.start_button = ttk.Button(action_frame, text="Start Measurement", command=self.start_custom_measurement, state=tk.DISABLED)
        self.start_button.pack(fill=tk.BOTH, expand=True)
        self.stop_button = ttk.Button(action_frame, text="Stop", command=self.device.stop_measurement, state=tk.DISABLED)
        self.stop_button.pack(fill=tk.BOTH, expand=True, pady=(5,0))

        
        # --- *** 新增：數據記錄與匯出框架 *** ---
       
        record_frame = ttk.LabelFrame(main_frame, text="Data Recording", padding=10)
        record_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 0))

        self.record_count_label = ttk.Label(record_frame, text="Recorded Points: 0")
        self.record_count_label.pack(side=tk.LEFT, padx=5)

        self.export_button = ttk.Button(record_frame, text="Export to CSV", command=self.export_to_csv, state=tk.DISABLED)
        self.export_button.pack(side=tk.RIGHT, padx=5)
        
        self.clear_button = ttk.Button(record_frame, text="Clear Recorded Data", command=self.clear_recorded_data, state=tk.DISABLED)
        self.clear_button.pack(side=tk.RIGHT, padx=5)
        
        # --- 繪圖框架 ---
        plot_frame = ttk.Frame(main_frame)
        plot_frame.pack(fill=tk.BOTH, expand=True, pady=5, padx=10)
        self.fig = Figure(figsize=(8, 5), dpi=100); self.ax_pressure = self.fig.add_subplot(1, 1, 1)
        # ... (繪圖相關設定與之前相同) ...
        self.plot_lines = []
        colors = ['#E63946', '#457B9D', '#2A9D8F']
        for i, label_text in enumerate(labels):
            line, = self.ax_pressure.plot([], [], color=colors[i], label=label_text)
            self.plot_lines.append(line)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.status_label = ttk.Label(self.root, text="Status: DISCONNECTED", padding=5, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)


    # --- *** 新增與修改的函數 *** ---
    

    def start_custom_measurement(self):
        """讀取下拉選單的設定，並在啟動測量前清除舊數據"""
        # 詢問用戶是否要清除舊數據
        if self.recorded_data:
            if messagebox.askyesno("Confirm", "A new measurement will clear previous recorded data. Continue?"):
                self.clear_recorded_data()
            else:
                return # User cancelled

        try:
            selected_levels_str = [var.get() for var in self.pressure_vars]
            selected_levels_enum = [PulseDiagnosisInterface.PressureLevel[s] for s in selected_levels_str]
            self.device.start_measurement_by_level(selected_levels_enum)
        except KeyError:
            messagebox.showerror("Error", "Invalid pressure level selected.")

    def handle_data_update(self, data_batch: List[SensorDataPoint]):
        """處理數據更新，同時更新繪圖佇列和完整記錄列表"""
        needs_redraw = True
        
        # 將新數據加入完整記錄列表
        self.recorded_data.extend(data_batch)
        self.record_count_label.config(text=f"Recorded Points: {len(self.recorded_data)}")
        
        # 更新用於繪圖的滾動數據
        for point in data_batch:
            self.plot_data[0]['time'].append(point.timestamp_ms / 1000.0)
            self.plot_data[0]['pressure'].append(point.pressure_pa_cun)
            self.plot_data[1]['time'].append(point.timestamp_ms / 1000.0)
            self.plot_data[1]['pressure'].append(point.pressure_pa_guan)
            self.plot_data[2]['time'].append(point.timestamp_ms / 1000.0)
            self.plot_data[2]['pressure'].append(point.pressure_pa_chi)
        
        if needs_redraw:
            # ... (繪圖更新程式碼與之前相同) ...
            self.ax_pressure.set_title("Pressure Waveforms") # 重設標題
            self.ax_pressure.set_xlabel("Time (s)")
            self.ax_pressure.set_ylabel("Pressure (Pa)")
            for i in range(3):
                self.plot_lines[i].set_data(list(self.plot_data[i]['time']), list(self.plot_data[i]['pressure']))
            self.ax_pressure.relim()
            self.ax_pressure.autoscale_view(True, True, True)
            self.ax_pressure.grid(True)
            self.ax_pressure.legend()
            self.canvas.draw()
            
    def clear_recorded_data(self):
        """清除所有已記錄的數據和圖表"""
        print("Clearing all recorded data.")
        self.recorded_data.clear()
        for i in range(3):
            self.plot_data[i]['time'].clear()
            self.plot_data[i]['pressure'].clear()
            self.plot_lines[i].set_data([], [])
        
        # 清空圖表並重繪
        self.ax_pressure.relim()
        self.ax_pressure.autoscale_view(True, True, True)
        self.canvas.draw()
        
        self.record_count_label.config(text="Recorded Points: 0")
        self.update_button_states(self._current_status)

    def export_to_csv(self):
        """將已記錄的數據匯出為 CSV 檔案"""
        if not self.recorded_data:
            messagebox.showwarning("Warning", "No data to export.")
            return

        # 彈出檔案儲存對話框
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Save Measurement Data"
        )

        if not filepath:
            print("Export cancelled by user.")
            return

        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.writer(csvfile)
                # 寫入表頭
                writer.writerow(['timestamp_ms', 'pressure_pa_cun', 'pressure_pa_guan', 'pressure_pa_chi'])
                # 寫入數據
                for point in self.recorded_data:
                    writer.writerow([
                        point.timestamp_ms,
                        point.pressure_pa_cun,
                        point.pressure_pa_guan,
                        point.pressure_pa_chi
                    ])
            messagebox.showinfo("Success", f"Data successfully exported to:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export data: {e}")

    def update_button_states(self, status: DeviceStatus):
       
        self._current_status = status
        is_connected = status not in [DeviceStatus.DISCONNECTED, DeviceStatus.SCANNING, DeviceStatus.CONNECTING]
        is_measuring = status in [DeviceStatus.INFLATING, DeviceStatus.MEASURING]
        is_scanning = status == DeviceStatus.SCANNING

        self.scan_button.config(state=tk.DISABLED if is_connected or is_scanning else tk.NORMAL)
        self.connect_button.config(state=tk.NORMAL if not is_connected and self.device_combobox.get() else tk.DISABLED)
        self.disconnect_button.config(state=tk.NORMAL if is_connected else tk.DISABLED)
        self.start_button.config(state=tk.NORMAL if is_connected and not is_measuring else tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL if is_measuring else tk.DISABLED)
        
        # 只有在沒有測量且有數據時，才啟用匯出和清除按鈕
        can_manage_data = not is_measuring and len(self.recorded_data) > 0
        self.export_button.config(state=tk.NORMAL if can_manage_data else tk.DISABLED)
        self.clear_button.config(state=tk.NORMAL if can_manage_data else tk.DISABLED)


   
    def connect_device(self):
        selected = self.device_combobox.get()
        if not selected: return messagebox.showerror("Error", "Please select a device.")
        address = selected.split('(')[1].replace(')', '')
        self.device.connect(address)

    def update_gui(self):
        try:
            while not self.event_queue.empty():
                event = self.event_queue.get_nowait()
                if isinstance(event, DeviceStatus): self.handle_status_update(event)
                elif isinstance(event, list) and len(event) > 0 and isinstance(event[0], SensorDataPoint): self.handle_data_update(event)
                elif isinstance(event, list): self.handle_device_list_update(event)
        except queue.Empty: pass
        finally: self.root.after(50, self.update_gui)

    def handle_status_update(self, status: DeviceStatus): self.status_label.config(text=f"Status: {status.name}"); self.update_button_states(status)
    def handle_device_list_update(self, devices: List[Dict[str,str]]):
        device_names = [f"{d['name']} ({d['address']})" for d in devices]
        self.device_combobox['values'] = device_names
        if device_names: self.device_combobox.current(0)
        self.update_button_states(self._current_status)

    def on_closing(self):
        print("Closing application..."); self.device.shutdown(); self.root.destroy()

if __name__ == "__main__":
    main_window = tk.Tk()
    app = PulseMonitorApp(main_window)
    main_window.mainloop()