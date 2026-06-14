import cv2
import csv
import io
import napari
import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QComboBox, QSpinBox, QPushButton, QCheckBox, QFileDialog,
    QSizePolicy
)
from qtpy.QtCore import QTimer, Qt
from napari.qt.threading import thread_worker
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from datetime import datetime

try:
    import rawpy
except ImportError:
    rawpy = None

class LocateTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        self.live_timer = QTimer()
        self.live_timer.timeout.connect(self._fetch_live_frame)
        self.live_layer = None
        self.live_active = False

        layout = QVBoxLayout()
        self.capture_prefix_input = QLineEdit()
        self.capture_prefix_input.setReadOnly(True)
        
        self.acc_input = QSpinBox(); self.acc_input.setRange(1, 64)
        self.combine_sel = QComboBox(); self.combine_sel.addItems(["stack", "mean", "sum"])
        
        self.live_btn = QPushButton("Start Live View"); self.live_btn.clicked.connect(self.toggle_live)
        snap_j = QPushButton("Snap JPEG"); snap_j.clicked.connect(self.snap_jpeg)
        snap_d = QPushButton("Capture DNG"); snap_d.clicked.connect(self.fetch_dng_capture)
        self.raw_btn = QPushButton("Capture RAW"); self.raw_btn.clicked.connect(self.fetch_raw_capture)
        self.dng_raw_btn = QPushButton("Capture RAW DNG"); self.dng_raw_btn.clicked.connect(self.fetch_dng_raw_capture)

        layout.addWidget(QLabel("Filename:")); layout.addWidget(self.capture_prefix_input)
        layout.addWidget(QLabel("Accumulations:")); layout.addWidget(self.acc_input)
        layout.addWidget(QLabel("Combine:")); layout.addWidget(self.combine_sel)
        layout.addWidget(self.live_btn); layout.addWidget(snap_j); layout.addWidget(snap_d); layout.addWidget(self.raw_btn); layout.addWidget(self.dng_raw_btn)
        
        self.split_channels_checkbox = QCheckBox("Split DNG/RAW channels"); self.split_channels_checkbox.setChecked(True)
        layout.addWidget(self.split_channels_checkbox)

        # --- NEW: Checkbox to toggle Grid Lines ---
        self.grid_spacing_input = QSpinBox()
        self.grid_spacing_input.setRange(10, 1000)
        self.grid_spacing_input.setValue(100) # Default 100-pixel grid spacing
        self.grid_spacing_input.setSuffix(" px")
        self.grid_spacing_input.valueChanged.connect(self._redraw_grid_if_active)
        
        self.show_grid_cb = QCheckBox("Show Grid Lines")
        self.show_grid_cb.stateChanged.connect(self._toggle_grid)
        
        grid_row = QHBoxLayout()
        grid_row.addWidget(self.show_grid_cb)
        grid_row.addWidget(QLabel("Spacing:"))
        grid_row.addWidget(self.grid_spacing_input)
        grid_row.addStretch()
        layout.addLayout(grid_row)
        
        layout.addStretch()
        self.setLayout(layout)

    def toggle_live(self):
        if self.live_timer.isActive():
            self.live_timer.stop(); self.live_active = False; self.live_btn.setText("Start Live View")
            if self.live_layer and self.live_layer in self.main.viewer.layers: self.main.viewer.layers.remove(self.live_layer)
            self.live_layer = None
        else:
            self.live_active = True; self.live_timer.start(250); self.live_btn.setText("Stop Live View")

    
    def _fetch_live_frame(self):
        @thread_worker
        def worker(): 
            # --- FIX: Grab the currently selected Photo Camera Index ---
            cam_idx = self.main.photo_settings_tab.cam_sel.currentData()
            return self.main.handler.fetch_snapshot(camera_index=cam_idx)
            # -----------------------------------------------------------
            
        def on_done(content):
            if not self.live_active: return
            img = cv2.cvtColor(cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
            if self.live_layer is None or self.live_layer not in self.main.viewer.layers:
                self.live_layer = self.main.viewer.add_image(img, name="Live View")
            else: self.live_layer.data = img
            
        wk = worker()
        wk.returned.connect(on_done)
        wk.start()

    def snap_jpeg(self):
        if not self.live_layer: return
        now = datetime.now()
        name = self.main._build_capture_filename(timestamp=now)
        new_layer = self.main.viewer.add_image(self.live_layer.data.copy(), name=name)
        if self.main.cam_store.use_numeric_suffix: self.main.cam_store.suffix_next += 1
        self.main._refresh_capture_name_preview()
        self.main.viewer.layers.selection = {self.live_layer}
        self.main.status_label.setText(f"Snapshot saved: {name}")

    def _display_raw_capture(self, raw_data, pattern, suffix):
        if raw_data is None: return
        name = f"{self.main._build_capture_filename()} ({suffix})"
        if self.split_channels_checkbox.isChecked():
            stack, cmaps, names = self.main.processor.build_channel_stack(raw_data, pattern)
            self.main.viewer.add_image(stack, name=name, channel_axis=1, colormap=cmaps, metadata={"channel_names": names})
        else:
            self.main.viewer.add_image(raw_data, name=name, colormap="gray")
        self.main.status_label.setText(f"{suffix} captured.")

    # def fetch_raw_capture(self):
    #     self.raw_btn.setEnabled(False); self.main.progress_bar.show(); self.main.status_label.setText("Capturing RAW...")
    #     params = {
    #         "camera_index": self.main.photo_settings_tab.cam_sel.currentData(), # <--- EXPLICITLY PASS CAMERA
    #         "exposure": self.main.photo_settings_tab.exp_input.value(),
    #         "exposure": self.main.photo_settings_tab.exp_input.value(),
    #         "gain": self.main.photo_settings_tab.gain_input.value(),
    #         "mode": self.main._get_raw_mode_str(),
    #         "accumulations": self.acc_input.value(),
    #         "combine": self.combine_sel.currentText(),
    #     }


    def fetch_raw_capture(self):
        self.raw_btn.setEnabled(False); self.main.progress_bar.show(); self.main.status_label.setText("Capturing RAW...")
        params = {
            "camera_index": self.main.photo_settings_tab.cam_sel.currentData(), # <-- ADD THIS LINE
            "exposure": self.main.photo_settings_tab.exp_input.value(),
            "gain": self.main.photo_settings_tab.gain_input.value(),
            "mode": self.main._get_raw_mode_str(),
            "accumulations": self.acc_input.value(),
            "combine": self.combine_sel.currentText(),
        }
            
        @thread_worker
        def worker():
            payload, headers = self.main.handler.fetch_raw(params)
            raw, meta = self.main.processor.decode_rawframe(payload, headers, params)
            return raw, meta.get("pattern") if raw is not None else None
        def on_done(res):
            self.raw_btn.setEnabled(True); self.main.progress_bar.hide()
            self._display_raw_capture(res[0], res[1], "RAW")
        wk = worker(); wk.returned.connect(on_done); wk.start()

    def fetch_dng_capture(self):

        self.main.progress_bar.show(); self.main.status_label.setText("Fetching DNG...")
        cam_idx = self.main.photo_settings_tab.cam_sel.currentData() # <-- Get Index

        @thread_worker
        def worker(): return self.main.handler.fetch_dng(camera_index=cam_idx) # <-- Pass Index
        def on_done(payload):
            self.main.progress_bar.hide()
            if rawpy:
                with rawpy.imread(io.BytesIO(payload)) as raw:
                    self.main.viewer.add_image(raw.postprocess(use_camera_wb=True, output_bps=16), name="DNG", rgb=True)
                self.main.status_label.setText("DNG Loaded.")
        wk = worker(); wk.returned.connect(on_done); wk.start()
    def fetch_dng_raw_capture(self):
        if not rawpy: return
        self.dng_raw_btn.setEnabled(False); self.main.progress_bar.show(); self.main.status_label.setText("Fetching RAW DNG...")
        cam_idx = self.main.photo_settings_tab.cam_sel.currentData() # <-- Get Index
        @thread_worker
        def worker():
            payload = self.main.handler.fetch_dng(camera_index=cam_idx) # <-- Pass Index
            with rawpy.imread(io.BytesIO(payload)) as raw:
                plane = np.array(raw.raw_image_visible, copy=True)
                desc = raw.color_desc.decode() if isinstance(raw.color_desc, (bytes, bytearray)) else raw.color_desc
                c_map = {idx: desc[idx].upper() for idx in range(len(desc))}
                pat = "".join([c_map[idx] for row in raw.raw_pattern for idx in row])
            return plane, pat
        def on_done(res):
            self.dng_raw_btn.setEnabled(True); self.main.progress_bar.hide()
            self._display_raw_capture(res[0], res[1], "DNG RAW")
        wk = worker(); wk.returned.connect(on_done); wk.start()

    def _redraw_grid_if_active(self):
        """Redraws the grid if the spacing is changed while the grid is active."""
        if self.show_grid_cb.isChecked():
            self._toggle_grid(Qt.Checked)

    def _toggle_grid(self, state):
        """Generates a physical grid of lines and overlays it as a Shapes layer."""
        grid_layer_name = "Grid Overlay"
        
        # If unchecking, remove the layer and exit
        if state != Qt.Checked:
            if grid_layer_name in self.main.viewer.layers:
                self.main.viewer.layers.remove(grid_layer_name)
            return

        # Find the dimensions of the largest currently loaded image
        max_h, max_w = 3040, 4056  # Default fallback dimensions
        for layer in self.main.viewer.layers:
            if isinstance(layer, napari.layers.Image):
                shape = layer.data.shape
                if len(shape) >= 2:
                    max_h = max(max_h, shape[-2])
                    max_w = max(max_w, shape[-1])

        grid_spacing = self.grid_spacing_input.value()
        lines = []
        
        # Generate Vertical lines
        for x in range(0, max_w, grid_spacing):
            lines.append([[0, x], [max_h, x]])
            
        # Generate Horizontal lines
        for y in range(0, max_h, grid_spacing):
            lines.append([[y, 0], [y, max_w]])

        # Remove the old grid if it exists
        if grid_layer_name in self.main.viewer.layers:
            self.main.viewer.layers.remove(grid_layer_name)

        # Draw the new grid
        self.main.viewer.add_shapes(
            lines,
            shape_type='line',
            edge_color='cyan',
            edge_width=2,
            opacity=0.4,
            name=grid_layer_name
        )
class VideoTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout()
        self.video_name = QLineEdit("recording_1")
        self.rec_btn = QPushButton("Start Recording")
        self.rec_btn.clicked.connect(self.toggle_recording)
        layout.addWidget(QLabel("Prefix:")); layout.addWidget(self.video_name); layout.addWidget(self.rec_btn)
        layout.addStretch(); self.setLayout(layout)

    def toggle_recording(self):
        start = self.rec_btn.text() == "Start Recording"
        try:
            self.main.handler.post_video(start, self.video_name.text())
            self.rec_btn.setText("Stop Recording" if start else "Start Recording")
        except Exception as e:
            self.main.status_label.setText(f"Video Error: {e}")


class SpectroscopyTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout()
        
        self.spec_fig = Figure(figsize=(5,3)) 
        self.spec_canvas = FigureCanvas(self.spec_fig)
        self.spec_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding) 
        self.spec_axes = self.spec_fig.add_subplot(111)
        self.toolbar = NavigationToolbar(self.spec_canvas, self)
        
        self.sep_ch_box = QCheckBox("Separate layers")
        self.sep_ch_box.setChecked(True)
        self.use_nm_box = QCheckBox("X-Axis in nm")
        self.use_nm_box.toggled.connect(self._toggle_nm)
        
        self.mode_sel = QComboBox(); self.mode_sel.addItems(["Intensity", "Intensity-BG", "T", "A"])
        
        self.export_name = QLineEdit("spectra.csv")
        export_btn = QPushButton("Export CSV"); export_btn.clicked.connect(self._export_csv)

        btn_row = QHBoxLayout()
        self.live_spec_btn = QPushButton("Live")
        self.snap_spec_btn = QPushButton("Snap"); self.snap_spec_btn.clicked.connect(lambda: self._execute_spectral_snap("Snap"))
        self.save_spec_btn = QPushButton("Save")
        btn_row.addWidget(self.live_spec_btn); btn_row.addWidget(self.snap_spec_btn); btn_row.addWidget(self.save_spec_btn)

        layout.addWidget(self.toolbar); layout.addWidget(self.spec_canvas); layout.addLayout(btn_row)
        
        m_row = QHBoxLayout(); m_row.addWidget(QLabel("Mode:")); m_row.addWidget(self.mode_sel); layout.addLayout(m_row)
        c_row = QHBoxLayout(); c_row.addWidget(self.sep_ch_box); c_row.addWidget(self.use_nm_box); c_row.addStretch(); layout.addLayout(c_row)
        
        layout.addWidget(QLabel("Export:")); layout.addWidget(self.export_name); layout.addWidget(export_btn)
        
        self.setLayout(layout)

    def update_mode_selection(self):
        has_bg = self.main.spec_data.bg_profile is not None
        has_ref = self.main.spec_data.ref_profile is not None
        model = self.mode_sel.model()
        for i in range(self.mode_sel.count()):
            text = self.mode_sel.itemText(i); item = model.item(i)
            if not item: continue
            en = True
            if text == "Intensity-BG" and not has_bg: en = False
            elif text in ["T", "A"] and not (has_bg and has_ref): en = False
            item.setFlags(item.flags() | Qt.ItemIsEnabled if en else item.flags() & ~Qt.ItemIsEnabled)
        
        curr = model.item(self.mode_sel.currentIndex())
        if curr and not (curr.flags() & Qt.ItemIsEnabled): self.mode_sel.setCurrentIndex(0)

    def _toggle_nm(self, checked):
        self.main.spec_settings.use_wavelengths = checked
        self._redraw_current_plot()

    def _redraw_current_plot(self):
        if not self.main.spec_data.last_profiles: return
        mode = self.mode_sel.currentText()
        profs = self.main.processor.apply_spectral_math(self.main.spec_data.last_profiles, self.main.spec_data.bg_profile, self.main.spec_data.ref_profile, mode)
        use_nm = self.use_nm_box.isChecked() and bool(self.main.spec_settings.wavelength_coeffs)
        
        self.spec_axes.clear()
        for lbl, x, y in profs:
            if use_nm: x = self.main.processor.apply_wavelength_calibration(x, self.main.spec_settings.wavelength_coeffs, self.main.spec_settings.calib_model_type)
            self.spec_axes.plot(x, y, label=lbl)
            
        self.spec_axes.set_xlabel("Wavelength (nm)" if use_nm else "Pixel Index")
        self.spec_axes.set_ylabel("Intensity" if mode == "Intensity" else mode)
        self.spec_axes.legend(fontsize=8); self.spec_canvas.draw()

    def _execute_spectral_snap(self, capture_type="Snap"):
        self.main.status_label.setText(f"Fetching {capture_type}...")
        self.snap_spec_btn.setEnabled(False)
        cfg = self.main.spectral_settings_tab.get_config()
        
        @thread_worker
        def worker():
            if cfg["readout_setup"] == "Full frame":
                payload, headers = self.main.handler.fetch_raw({"exposure": cfg["exposure"], "gain": cfg["gain"], "mode": cfg["mode"], "accumulations": cfg["accumulations"], "combine": cfg["combine"]})
                raw, meta = self.main.processor.decode_rawframe(payload, headers, {"mode": cfg["mode"]})
                if raw is not None:
                    stack, cmaps, names = self.main.processor.build_channel_stack(raw, meta.get("pattern", "RGGB"))
                    return "image", stack, cmaps, names
                return "error", None, None, None
            else:
                return "spectra", self.main.handler.fetch_spectral_capture(cfg), None, None

        def on_done(res):
            self.snap_spec_btn.setEnabled(True)
            res_type, data, cmaps, names = res
            if res_type == "error": self.main.status_label.setText("Failed."); return
            
            # --- FIX: Read the checkbox right on this active tab instead of the hidden one ---
            split_ch = self.sep_ch_box.isChecked() 
            # --------------------------------------------------------------------------------
            
            if res_type == "image":
                name = f"Spec_{capture_type}"
                if split_ch: 
                    self.main.viewer.add_image(data, name=name, channel_axis=1, colormap=cmaps, metadata={"channel_names": names})
                else: 
                    self.main.viewer.add_image(np.squeeze(np.mean(data, axis=1)), name=name, colormap="gray")
                self.main.status_label.setText(f"Loaded {capture_type}.")
                
            elif res_type == "spectra":
                raw_profs = self.main.processor.parse_spectral_json(data, split_ch, name=capture_type)
                
                if capture_type in ["BG", "Ref"]:
                    if capture_type == "BG": self.main.spec_data.bg_profile = raw_profs
                    else: self.main.spec_data.ref_profile = raw_profs
                    self.update_mode_selection()
                    
                    self.main.config_axes.clear()
                    use_nm = self.use_nm_box.isChecked() and bool(self.main.spec_settings.wavelength_coeffs)
                    if self.main.spec_data.bg_profile:
                        for l, x, y in self.main.spec_data.bg_profile:
                            if use_nm: x = self.main.processor.apply_wavelength_calibration(x, self.main.spec_settings.wavelength_coeffs, self.main.spec_settings.calib_model_type)
                            self.main.config_axes.plot(x, y, label=l, linestyle="--")
                    if self.main.spec_data.ref_profile:
                        for l, x, y in self.main.spec_data.ref_profile:
                            if use_nm: x = self.main.processor.apply_wavelength_calibration(x, self.main.spec_settings.wavelength_coeffs, self.main.spec_settings.calib_model_type)
                            self.main.config_axes.plot(x, y, label=l, linestyle="-")
                    self.main.config_axes.set_title("Calibration Caches", fontsize=10); self.main.config_axes.legend(fontsize=8); self.main.config_canvas.draw()
                    self.main.status_label.setText(f"{capture_type} cached.")
                else:
                    self.main.spec_data.last_profiles = raw_profs
                    self._redraw_current_plot()
                    self.main.status_label.setText("Plotted.")

        wk = worker(); wk.returned.connect(on_done); wk.start()

    # def _export_csv(self):
    #     profs = self.main.spec_data.last_profiles
    #     if not profs: return
    #     path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(self.main.cam_store.save_directory / (self.export_name.text() or "spectra.csv")), "CSV (*.csv)")
    #     if not path: return
    #     with open(path, "w", newline="") as fh:
    #         w = csv.writer(fh)
    #         use_nm = self.use_nm_box.isChecked() and bool(self.main.spec_settings.wavelength_coeffs)
    #         w.writerow(["Layer", "Wavelength (nm)" if use_nm else "Pixel X", "Y"])
    #         for l, xs, ys in profs:
    #             if use_nm: xs = self.main.processor.apply_wavelength_calibration(xs, self.main.spec_settings.wavelength_coeffs, self.main.spec_settings.calib_model_type)
    #             for x, y in zip(xs, ys): w.writerow([l, float(x), float(y)])
    #     self.main.status_label.setText(f"Exported to {path}")
    def _export_csv(self):
        raw_profs = self.main.spec_data.last_profiles
        if not raw_profs: return
        
        # --- NEW: Apply the selected math mode before exporting ---
        mode = self.mode_sel.currentText()
        profs = self.main.processor.apply_spectral_math(
            raw_profs, 
            self.main.spec_data.bg_profile, 
            self.main.spec_data.ref_profile, 
            mode
        )
        # ----------------------------------------------------------
        
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", str(self.main.cam_store.save_directory / (self.export_name.text() or "spectra.csv")), "CSV (*.csv)")
        if not path: return
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            use_nm = self.use_nm_box.isChecked() and bool(self.main.spec_settings.wavelength_coeffs)
            
            # Update the column header to reflect the current mode
            y_label = "Intensity" if mode == "Intensity" else mode
            w.writerow(["Layer", "Wavelength (nm)" if use_nm else "Pixel X", y_label])
            
            for l, xs, ys in profs:
                if use_nm: xs = self.main.processor.apply_wavelength_calibration(xs, self.main.spec_settings.wavelength_coeffs, self.main.spec_settings.calib_model_type)
                for x, y in zip(xs, ys): w.writerow([l, float(x), float(y)])
                
        self.main.status_label.setText(f"Exported {mode} to {path}")