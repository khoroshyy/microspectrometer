"""
Qt/Napari widgets for interacting with the Pi camera HTTP control server.

This module acts as the "Main Controller". It instantiates the logic from `core.py`,
builds the UI using the separated files `tabs_control.py` and `tabs_settings.py`, 
and handles the global save/load functionality.
"""

import json
from datetime import datetime
from pathlib import Path

import napari
#from qtpy.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QHBoxLayout, QPushButton, QProgressBar, QLabel
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QHBoxLayout, 
    QPushButton, QProgressBar, QLabel, QTableWidgetItem # <-- Added QTableWidgetItem here
)
from napari.qt.threading import thread_worker

# Import our headless core classes
from core import CameraSettingsStore, SpectralSettingsStore, SpectralDataStore, CameraHandler, DataHandler

# Import our separated UI Tabs
from tabs_control import LocateTab, VideoTab, SpectroscopyTab
from tabs_settings import PhotoSettingsTab, SpectralSettingsTab, SavingSettingsTab, CalibrationSettingsTab

RAW_MODE_DEFAULTS = {"imx477": "4056:3040:12:U", "imx296": "1456:1088:10:U"}

class PiCamPlugin(QWidget):
    """The Main PyQt5 Widget that docks into Napari."""
    
    def __init__(self, viewer: napari.Viewer):
        super().__init__()
        self.viewer = viewer
        
        # 1. Initialize our central "Brain" (Stores and Handlers)
        self.cam_store = CameraSettingsStore()
        self.spec_settings = SpectralSettingsStore()
        self.spec_data = SpectralDataStore()
        self.handler = CameraHandler(getattr(self.cam_store, "url", "http://192.168.0.197:8080"))
        self.processor = DataHandler()
        
        # 2. Instantiate all the separated UI tabs (Passing `self` so they can access the Brain)
        self.locate_tab = LocateTab(self)
        self.video_tab = VideoTab(self)
        self.spectroscopy_tab = SpectroscopyTab(self)
        
        self.photo_settings_tab = PhotoSettingsTab(self)
        self.spectral_settings_tab = SpectralSettingsTab(self)
        self.saving_settings_tab = SavingSettingsTab(self)
        self.calibration_settings_tab = CalibrationSettingsTab(self)

        # 3. Assemble the General Settings nested layout
        self.general_settings_widget = QWidget()
        gs_layout = QVBoxLayout()
        inner_tabs = QTabWidget()
        inner_tabs.addTab(self.photo_settings_tab, "Photo Settings")
        inner_tabs.addTab(self.spectral_settings_tab, "Spectral Settings")
        inner_tabs.addTab(self.saving_settings_tab, "Saving Settings")
        inner_tabs.addTab(self.calibration_settings_tab, "Calibration")
        gs_layout.addWidget(inner_tabs)
        
        # 4. Add the Global Apply/Save/Load buttons
        btns = QHBoxLayout()
        apply_btn = QPushButton("Apply Hardware Settings"); apply_btn.clicked.connect(self.apply_hardware_settings)
        save_btn = QPushButton("Save Config"); save_btn.clicked.connect(self._save_config_to_json)
        load_btn = QPushButton("Load Config"); load_btn.clicked.connect(self._load_config_from_json)
        btns.addWidget(apply_btn); btns.addWidget(save_btn); btns.addWidget(load_btn)
        gs_layout.addLayout(btns)
        self.general_settings_widget.setLayout(gs_layout)

        # 5. Assemble the Main Tabs
        self.tabs = QTabWidget()
        self.tabs.addTab(self.locate_tab, "Locate")
        self.tabs.addTab(self.spectroscopy_tab, "Spectroscopy")
        self.tabs.addTab(self.video_tab, "Video")
        self.tabs.addTab(self.general_settings_widget, "General Settings")

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
        self.setLayout(layout)
        
        # 6. Initialize small Shared components (Calibration Plot & BG/Ref Buttons)
        # Because these visually live in the Spectral Settings tab but trigger the Snap function 
        # in the Spectroscopy tab, we route them in the master controller.
        self.config_axes = self.spectral_settings_tab.config_axes
        self.config_canvas = self.spectral_settings_tab.config_canvas
        self.spectral_settings_tab.bg_btn.clicked.connect(lambda: self.spectroscopy_tab._execute_spectral_snap("BG"))
        self.spectral_settings_tab.ref_btn.clicked.connect(lambda: self.spectroscopy_tab._execute_spectral_snap("Ref"))
        self.spectral_settings_tab.reset_btn.clicked.connect(self._reset_spectral_caches)

        # 7. Start up
        self.refresh_camera_list()
        self._load_config_from_json()

    def _reset_spectral_caches(self):
        """Clears caches and asks Spectroscopy tab to reset plots."""
        self.spec_data.bg_profile = None
        self.spec_data.ref_profile = None
        self.spec_data.last_profiles = []
        if hasattr(self.spectroscopy_tab, "spec_axes"):
            self.spectroscopy_tab.spec_axes.clear()
            self.spectroscopy_tab.spec_canvas.draw()
        if hasattr(self, "config_axes"):
            self.config_axes.clear()
            self.config_canvas.draw()
        self.spectroscopy_tab.update_mode_selection()
        self.status_label.setText("Spectral reference caches cleared.")

    def refresh_camera_list(self):
        """Fetches available cameras from the server and updates combo boxes."""
        @thread_worker
        def worker(): return self.handler.fetch_cameras()
        def on_done(data):
            self.cam_store.available_cameras = data if isinstance(data, list) else data.get("cameras", [])
            for combo in [self.photo_settings_tab.cam_sel, self.spectral_settings_tab.spectral_cam_sel]:
                combo.blockSignals(True); combo.clear()
                for c in self.cam_store.available_cameras:
                    combo.addItem(f"{c.get('index')}: {c.get('Model')}", c.get('index'))
                combo.blockSignals(False)
            if self.spectral_settings_tab.spectral_cam_sel.count() > 0: self._on_spectral_camera_selected()
            if self.photo_settings_tab.cam_sel.count() > 0: self._on_camera_selected(0)
        wk = worker(); wk.returned.connect(on_done); wk.start()

    def _on_camera_selected(self, index):
        cam_idx = self.photo_settings_tab.cam_sel.itemData(index)
        
        # 1. Remember state and safely pause
        was_live = self.locate_tab.live_active
        if was_live:
            self.locate_tab.toggle_live()

        @thread_worker
        def switch_task():
            return self.handler.send_switch_camera(cam_idx)

        def on_switch_done(response):
            cam_data = next((c for c in self.cam_store.available_cameras if c.get('index') == cam_idx), {})
            self.cam_store.update_modes(cam_data.get("parsed_modes", []))
            
            self.photo_settings_tab.res_sel.blockSignals(True)
            self.photo_settings_tab.res_sel.clear()
            self.photo_settings_tab.res_sel.addItems(self.cam_store.available_resolutions)
            self.photo_settings_tab.res_sel.blockSignals(False)
            
            self.status_label.setText(f"Switched to Camera {cam_idx}")
            
            # 2. ONLY restart Live View if it was already running
            if was_live:
                self.locate_tab.toggle_live()

        wk = switch_task()
        wk.returned.connect(on_switch_done)
        wk.start()

    def _on_spectral_camera_selected(self, index=None):
        if self.spectral_settings_tab.spectral_cam_sel.count() == 0: return
        cam_idx = self.spectral_settings_tab.spectral_cam_sel.currentData()
        cam_data = next((c for c in self.cam_store.available_cameras if c.get('index') == cam_idx), {})
        mode_str = self._resolve_raw_mode_from_camera(cam_data)
        try: parts = mode_str.split(':'); raw_width, raw_height = int(parts[0]), int(parts[1])
        except (IndexError, ValueError): raw_width, raw_height = 4056, 3040
        eff_width, eff_height = raw_width // 2, raw_height // 2
        self.spectral_settings_tab.fullframe_resolution_label.setText(f"Resolution: {eff_width} x {eff_height}")
        # Update bounds dynamically based on sensor
        for x_box in [self.spectral_settings_tab.x_left_input, self.spectral_settings_tab.multi_x_left_input, self.spectral_settings_tab.custom_x_left_input]: x_box.setRange(0, eff_width)
        for w_box in [self.spectral_settings_tab.x_width_input, self.spectral_settings_tab.multi_x_width_input, self.spectral_settings_tab.custom_x_width_input]: w_box.setRange(1, eff_width)
        for y_box in [self.spectral_settings_tab.y_top_input, self.spectral_settings_tab.multi_y_top_input, self.spectral_settings_tab.multi_gap_input]: y_box.setRange(0, eff_height)
        for h_box in [self.spectral_settings_tab.y_height_input, self.spectral_settings_tab.multi_roi_height_input]: h_box.setRange(1, eff_height)

    def _resolve_raw_mode_from_camera(self, cam_data=None):
        data = cam_data
        if data is None:
            cam_idx = self.photo_settings_tab.cam_sel.currentData()
            data = next((c for c in self.cam_store.available_cameras if c.get('index') == cam_idx), {})
        model_lower = (data or {}).get("Model", "").lower()
        for key, mode in RAW_MODE_DEFAULTS.items():
            if key in model_lower: return mode
        return getattr(self.cam_store, "current_mode", "4056:3040:12:U")

    def _get_raw_mode_str(self):
        return self._resolve_raw_mode_from_camera()

    # def apply_hardware_settings(self):
    #     self.handler.base_url = self.photo_settings_tab.url_input.text().rstrip('/')
    #     hw_payload = {"exposure_time": self.photo_settings_tab.exp_input.value(), "gain_value": self.photo_settings_tab.gain_input.value(), "resolution": self.photo_settings_tab.res_sel.currentText()}
    #     spec_payload = self.spectral_settings_tab.get_config()
    #     self.status_label.setText("Pushing all settings to server...")
    #     @thread_worker
    #     def apply_task(): return self.handler.send_apply(hw_payload), self.handler.send_spectral_settings(spec_payload)
    #     def on_done(responses):
    #         hw_resp, spec_resp = responses
    #         if hw_resp.status_code == 200 and spec_resp.status_code == 200: self.status_label.setText("Settings Successfully Applied!")
    #         else: self.status_label.setText(f"Warning: HW({hw_resp.status_code}), Spec({spec_resp.status_code})")
    #     wk = apply_task(); wk.returned.connect(on_done); wk.start()

    def apply_hardware_settings(self):
        # 1. Remember if Live View was active, and stop it safely if it was
        was_live = self.locate_tab.live_active
        if was_live:
            self.locate_tab.toggle_live()

        # Update handler URL from the core
        self.handler.base_url = self.cam_store.url.rstrip('/')
        
        # Build hardware payload directly from the core
        hw_payload = {
            "exposure_time": self.cam_store.exposure, 
            "gain_value": self.cam_store.gain, 
            "resolution": self.cam_store.resolution
        }
        
        spec_payload = self.spectral_settings_tab.get_config()
        self.status_label.setText("Pushing all settings to server...")
        
        @thread_worker
        def apply_task(): 
            return self.handler.send_apply(hw_payload), self.handler.send_spectral_settings(spec_payload)
            
        def on_done(responses):
            hw_resp, spec_resp = responses
            if hw_resp.status_code == 200 and spec_resp.status_code == 200: 
                self.status_label.setText("Settings Successfully Applied!")
            else: 
                self.status_label.setText(f"Warning: HW({hw_resp.status_code}), Spec({spec_resp.status_code})")
            
            # 2. ONLY restart Live View if it was running before we clicked apply
            if was_live:
                self.locate_tab.toggle_live()
                
        wk = apply_task()
        wk.returned.connect(on_done)
        wk.start()
    def _build_capture_filename(self, timestamp=None, suffix_value=None):
        ts = timestamp or datetime.now()
        parts = [self.cam_store.file_root]
        if getattr(self.cam_store, "append_date", False): parts.append(ts.strftime("%y%m%d"))
        if getattr(self.cam_store, "append_time", False): parts.append(ts.strftime("%H%M%S"))
        if getattr(self.cam_store, "use_numeric_suffix", False): parts.append(f"{int(suffix_value or self.cam_store.suffix_next):04d}")
        return self.cam_store.name_delimiter.join(parts)

    def _refresh_capture_name_preview(self):
        preview = self._build_capture_filename()
        self.cam_store.current_filename = preview
        self.locate_tab.capture_prefix_input.setText(preview)

    def _save_config_to_json(self):
        calib_pts = []
        for row in range(self.calibration_settings_tab.calib_table.rowCount()):
            px = self.calibration_settings_tab.calib_table.item(row, 0)
            nm = self.calibration_settings_tab.calib_table.item(row, 1)
            if px and nm and px.text() and nm.text(): calib_pts.append({"px": px.text(), "nm": nm.text()})
        cfg = {
            "camera": {"url": self.photo_settings_tab.url_input.text(), "exposure": self.photo_settings_tab.exp_input.value(), "gain": self.photo_settings_tab.gain_input.value(), "resolution": self.photo_settings_tab.res_sel.currentText()},
            "spectral": {"split_channels": self.spectroscopy_tab.sep_ch_box.isChecked(), "mode": self.spectroscopy_tab.mode_sel.currentText(), "file": self.spectroscopy_tab.export_name.text(), "ui_settings": self.spectral_settings_tab.get_config()},
            "saving": {"file_root": self.cam_store.file_root, "directory": str(self.cam_store.save_directory), "auto_save_dng": self.cam_store.auto_save_dng, "append_date": self.cam_store.append_date, "append_time": self.cam_store.append_time, "name_delimiter": self.cam_store.name_delimiter, "use_numeric_suffix": self.cam_store.use_numeric_suffix, "suffix_start": self.cam_store.suffix_start, "suffix_next": self.cam_store.suffix_next},
            "calibration": {"wavelength_coeffs": self.spec_settings.wavelength_coeffs, "model_type": self.spec_settings.calib_model_type, "points": calib_pts}
        }
        with open("cam_config.json", 'w') as f: json.dump(cfg, f, indent=4)
        self.status_label.setText("Config saved.")

    # def _load_config_from_json(self):
    #     if not Path("cam_config.json").exists(): return
    #     with open("cam_config.json", 'r') as f: cfg = json.load(f)
    #     c, s, saving, calib = cfg.get("camera", {}), cfg.get("spectral", {}), cfg.get("saving", {}), cfg.get("calibration", {})
        
    #     self.photo_settings_tab.url_input.setText(c.get("url", "")); self.photo_settings_tab.exp_input.setValue(c.get("exposure", 50000)); self.photo_settings_tab.gain_input.setValue(c.get("gain", 4.0))
    #     idx = self.photo_settings_tab.res_sel.findText(c.get("resolution", ""))
    #     if idx >= 0: self.photo_settings_tab.res_sel.setCurrentIndex(idx)
        
    #     self.spectroscopy_tab.sep_ch_box.setChecked(s.get("split_channels", True))
    #     self.spectroscopy_tab.export_name.setText(s.get("file", "spectra.csv"))
        
    #     if "model_type" in calib:
    #         self.spec_settings.calib_model_type = calib["model_type"]
    #         idx = self.calibration_settings_tab.calib_model_sel.findText({"poly1":"Linear (1st Degree)","poly2":"Quadratic (2nd Degree)","poly3":"Cubic (3rd Degree)","prism":"Prism (Hartmann)","grating":"Grating (Physical)"}.get(self.spec_settings.calib_model_type, ""))
    #         if idx >= 0: self.calibration_settings_tab.calib_model_sel.setCurrentIndex(idx)
    #     if "wavelength_coeffs" in calib: self.spec_settings.wavelength_coeffs = calib["wavelength_coeffs"]
    #     if "points" in calib:
    #         self.calibration_settings_tab.calib_table.setRowCount(max(len(calib["points"]), 3))
    #         for i, pt in enumerate(calib["points"]):
    #             self.calibration_settings_tab.calib_table.setItem(i, 0, QTableWidgetItem(str(pt.get("px", "")))); self.calibration_settings_tab.calib_table.setItem(i, 1, QTableWidgetItem(str(pt.get("nm", ""))))
    #         if self.spec_settings.wavelength_coeffs: self.calibration_settings_tab.calib_info_label.setText(f"Active: {self.calibration_settings_tab.calib_model_sel.currentText()}")
        
    #     self.spectroscopy_tab.update_mode_selection()
    #     idx = self.spectroscopy_tab.mode_sel.findText(s.get("mode", "Intensity"))
    #     if idx >= 0: self.spectroscopy_tab.mode_sel.setCurrentIndex(idx)
            
    #     self.saving_settings_tab.prefix_input.setText(saving.get("file_root", "capture")); self.cam_store.file_root = saving.get("file_root", "capture")
    #     self.saving_settings_tab._update_dir(saving.get("directory", ""))
    #     self.saving_settings_tab.auto_dng_cb.setChecked(saving.get("auto_save_dng", False))
    #     self.saving_settings_tab.add_date_cb.setChecked(saving.get("append_date", False))
    #     self.saving_settings_tab.add_time_cb.setChecked(saving.get("append_time", False))
    #     self.saving_settings_tab.delim_sel.setCurrentText(saving.get("name_delimiter", "_"))
    #     self.saving_settings_tab.suf_start_in.setValue(saving.get("suffix_start", 1))
    #     self.cam_store.suffix_next = saving.get("suffix_next", 1)
    #     self.saving_settings_tab.num_suf_cb.setChecked(saving.get("use_numeric_suffix", False))
        
    #     self.spectral_settings_tab.set_config(s.get("ui_settings"))
    #     self._refresh_capture_name_preview()
    def _load_config_from_json(self):
        if not Path("cam_config.json").exists(): 
            return
            
        with open("cam_config.json", 'r') as f: 
            cfg = json.load(f)
            
        c = cfg.get("camera", {})
        s = cfg.get("spectral", {})
        saving = cfg.get("saving", {})
        calib = cfg.get("calibration", {})
        
        # 1. Update Core (Camera Settings)
        self.cam_store.url = c.get("url", "http://192.168.0.197:8080")
        self.cam_store.exposure = c.get("exposure", 50000)
        self.cam_store.gain = c.get("gain", 4.0)
        self.cam_store.resolution = c.get("resolution", "")

        # 2. Update UI to reflect Core (Camera Settings)
        self.photo_settings_tab.url_input.setText(self.cam_store.url)
        self.photo_settings_tab.exp_input.setValue(self.cam_store.exposure)
        self.photo_settings_tab.gain_input.setValue(self.cam_store.gain)
        
        idx = self.photo_settings_tab.res_sel.findText(self.cam_store.resolution)
        if idx >= 0: 
            self.photo_settings_tab.res_sel.setCurrentIndex(idx)
        
        # --- SPECTRAL & SPECTROSCOPY TAB SETTINGS ---
        self.spectroscopy_tab.sep_ch_box.setChecked(s.get("split_channels", True))
        self.spectroscopy_tab.export_name.setText(s.get("file", "spectra.csv"))
        
        self.spectroscopy_tab.update_mode_selection()
        idx = self.spectroscopy_tab.mode_sel.findText(s.get("mode", "Intensity"))
        if idx >= 0: 
            self.spectroscopy_tab.mode_sel.setCurrentIndex(idx)
            
        self.spectral_settings_tab.set_config(s.get("ui_settings"))

        # --- CALIBRATION SETTINGS ---
        if "model_type" in calib:
            self.spec_settings.calib_model_type = calib["model_type"]
            idx = self.calibration_settings_tab.calib_model_sel.findText(
                {"poly1":"Linear (1st Degree)", "poly2":"Quadratic (2nd Degree)", 
                 "poly3":"Cubic (3rd Degree)", "prism":"Prism (Hartmann)", 
                 "grating":"Grating (Physical)"}.get(self.spec_settings.calib_model_type, "")
            )
            if idx >= 0: 
                self.calibration_settings_tab.calib_model_sel.setCurrentIndex(idx)
                
        if "wavelength_coeffs" in calib: 
            self.spec_settings.wavelength_coeffs = calib["wavelength_coeffs"]
            
        if "points" in calib:
            self.calibration_settings_tab.calib_table.setRowCount(max(len(calib["points"]), 3))
            for i, pt in enumerate(calib["points"]):
                self.calibration_settings_tab.calib_table.setItem(i, 0, QTableWidgetItem(str(pt.get("px", ""))))
                self.calibration_settings_tab.calib_table.setItem(i, 1, QTableWidgetItem(str(pt.get("nm", ""))))
            if self.spec_settings.wavelength_coeffs: 
                self.calibration_settings_tab.calib_info_label.setText(f"Active: {self.calibration_settings_tab.calib_model_sel.currentText()}")

        # --- SAVING SETTINGS ---
        self.cam_store.file_root = saving.get("file_root", "capture")
        self.saving_settings_tab.prefix_input.setText(self.cam_store.file_root)
        self.saving_settings_tab._update_dir(saving.get("directory", ""))
        self.saving_settings_tab.auto_dng_cb.setChecked(saving.get("auto_save_dng", False))
        self.saving_settings_tab.add_date_cb.setChecked(saving.get("append_date", False))
        self.saving_settings_tab.add_time_cb.setChecked(saving.get("append_time", False))
        self.saving_settings_tab.delim_sel.setCurrentText(saving.get("name_delimiter", "_"))
        self.saving_settings_tab.suf_start_in.setValue(saving.get("suffix_start", 1))
        self.cam_store.suffix_next = saving.get("suffix_next", 1)
        self.saving_settings_tab.num_suf_cb.setChecked(saving.get("use_numeric_suffix", False))
        
        self._refresh_capture_name_preview()
        
        # 3. Automatically apply hardware settings so the camera matches the file
        self.apply_hardware_settings()
if __name__ == "__main__":
    v = napari.Viewer()
    v.window.add_dock_widget(PiCamPlugin(v), name="Pi Control")
    napari.run()