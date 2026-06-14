import numpy as np
from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, 
    QTabWidget, QTableWidget, QTableWidgetItem, QAbstractItemView, QFileDialog,
    QSizePolicy
)
from qtpy.QtCore import Qt
from pathlib import Path

# Matplotlib imports for the preview graph
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# class PhotoSettingsTab(QWidget):
#     def __init__(self, main_app):
#         super().__init__()
#         self.main = main_app
#         layout = QVBoxLayout()
        
#         self.url_input = QLineEdit(self.main.cam_store.url)
#         self.cam_sel = QComboBox()
#         self.cam_sel.currentIndexChanged.connect(self.main._on_camera_selected)
        
#         self.res_sel = QComboBox()
#         self.exp_input = QSpinBox(); self.exp_input.setRange(1, 10**7); self.exp_input.setValue(50000)
#         self.gain_input = QDoubleSpinBox(); self.gain_input.setRange(1.0, 16.0); self.gain_input.setValue(4.0)
        
#         layout.addWidget(QLabel("URL:")); layout.addWidget(self.url_input)
#         layout.addWidget(QLabel("Camera:")); layout.addWidget(self.cam_sel)
#         layout.addWidget(QLabel("Res:")); layout.addWidget(self.res_sel)
#         layout.addWidget(QLabel("Exp (µs):")); layout.addWidget(self.exp_input)
#         layout.addWidget(QLabel("Gain:")); layout.addWidget(self.gain_input)
#         layout.addStretch()
#         self.setLayout(layout)
class PhotoSettingsTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout()
        
        # URL - Read from core, write to core on change
        self.url_input = QLineEdit(self.main.cam_store.url)
        self.url_input.textChanged.connect(lambda text: setattr(self.main.cam_store, 'url', text))
        
        # Camera Selection
        self.cam_sel = QComboBox()
        self.cam_sel.currentIndexChanged.connect(self.main._on_camera_selected)
        
        # Resolution - Write to core on change
        self.res_sel = QComboBox()
        self.res_sel.currentTextChanged.connect(lambda text: setattr(self.main.cam_store, 'resolution', text))
        
        # Exposure - Read from core, write to core on change
        self.exp_input = QSpinBox()
        self.exp_input.setRange(1, 10**7)
        self.exp_input.setValue(self.main.cam_store.exposure)
        self.exp_input.valueChanged.connect(lambda val: setattr(self.main.cam_store, 'exposure', val))
        
        # Gain - Read from core, write to core on change
        self.gain_input = QDoubleSpinBox()
        self.gain_input.setRange(1.0, 16.0)
        self.gain_input.setValue(self.main.cam_store.gain)
        self.gain_input.valueChanged.connect(lambda val: setattr(self.main.cam_store, 'gain', val))
        
        layout.addWidget(QLabel("URL:")); layout.addWidget(self.url_input)
        layout.addWidget(QLabel("Camera:")); layout.addWidget(self.cam_sel)
        layout.addWidget(QLabel("Res:")); layout.addWidget(self.res_sel)
        layout.addWidget(QLabel("Exp (µs):")); layout.addWidget(self.exp_input)
        layout.addWidget(QLabel("Gain:")); layout.addWidget(self.gain_input)
        layout.addStretch()
        self.setLayout(layout)

class SpectralSettingsTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout() 
        
        cam_row = QHBoxLayout() 
        self.spectral_cam_sel = QComboBox() 
        self.spectral_cam_sel.currentIndexChanged.connect(self.main._on_spectral_camera_selected)
        self.spectral_res_sel = QComboBox() 
        self.spectral_res_sel.addItems(["1", "2", "4", "6", "8", "10"])
        cam_row.addWidget(QLabel("Spectral Camera:")); cam_row.addWidget(self.spectral_cam_sel) 
        layout.addLayout(cam_row) 
        
        res_row = QHBoxLayout() 
        res_row.addWidget(QLabel("Spectral Binning:")); res_row.addWidget(self.spectral_res_sel) 
        self.spectral_use_sep_ch_box = QCheckBox("Use separate channels"); self.spectral_use_sep_ch_box.setChecked(True)
        res_row.addWidget(self.spectral_use_sep_ch_box)
        layout.addLayout(res_row) 

        accum_row = QHBoxLayout() 
        self.spectral_accum_input = QSpinBox(); self.spectral_accum_input.setRange(1, 1000); self.spectral_accum_input.setValue(1)
        self.spectral_accum_mode = QComboBox(); self.spectral_accum_mode.addItems(["Average", "Sum"])
        accum_row.addWidget(QLabel("Num. Accum.")); accum_row.addWidget(self.spectral_accum_input)
        accum_row.addWidget(QLabel("Accum Mode")); accum_row.addWidget(self.spectral_accum_mode)
        layout.addLayout(accum_row)        
        
        exp_gain_row = QHBoxLayout()
        self.spectral_exp_input = QSpinBox(); self.spectral_exp_input.setRange(1, 10**7); self.spectral_exp_input.setValue(50000)
        self.spectral_gain_input = QDoubleSpinBox(); self.spectral_gain_input.setRange(1.0, 16.0); self.spectral_gain_input.setValue(4.0)
        exp_gain_row.addWidget(QLabel("Exposure (µs):")); exp_gain_row.addWidget(self.spectral_exp_input)
        exp_gain_row.addWidget(QLabel("Gain:")); exp_gain_row.addWidget(self.spectral_gain_input)
        layout.addLayout(exp_gain_row)

        readout_row = QHBoxLayout()
        readout_row.addWidget(QLabel("Readout setup:"))
        self.readout_sel = QComboBox()
        self.readout_sel.addItems(["Full frame", "Single ROI", "Multiple ROI", "Multiple ROI Custom"])
        readout_row.addWidget(self.readout_sel)
        layout.addLayout(readout_row)

        # ROI Tabs Setup
        self.readout_tabs = QTabWidget()

        # Fullframe
        fullframe_tab = QWidget(); fullframe_layout = QVBoxLayout()
        self.fullframe_resolution_label = QLabel("Resolution: 2048 x 2048")
        self.fullframe_resolution_label.setAlignment(Qt.AlignCenter)
        fullframe_layout.addWidget(self.fullframe_resolution_label)
        binning_layout = QHBoxLayout()
        self.horizontal_binning = QComboBox(); self.horizontal_binning.addItems(["1", "2", "4", "8"])
        self.vertical_binning = QComboBox(); self.vertical_binning.addItems(["1", "2", "4", "8"])
        binning_layout.addWidget(QLabel("Horizontal Binning:")); binning_layout.addWidget(self.horizontal_binning)
        binning_layout.addWidget(QLabel("Vertical Binning:")); binning_layout.addWidget(self.vertical_binning)
        fullframe_layout.addLayout(binning_layout)
        fullframe_tab.setLayout(fullframe_layout)
        self.readout_tabs.addTab(fullframe_tab, "Fullframe")

        # Single ROI
        single_roi_tab = QWidget(); single_roi_layout = QVBoxLayout()
        x_coord_layout = QHBoxLayout()
        self.x_left_input = QSpinBox(); self.x_left_input.setRange(0, 2048)
        self.x_width_input = QSpinBox(); self.x_width_input.setRange(1, 2048)
        x_coord_layout.addWidget(QLabel("X-Coordinate (Left):")); x_coord_layout.addWidget(self.x_left_input)
        x_coord_layout.addWidget(QLabel("Width:")); x_coord_layout.addWidget(self.x_width_input)
        single_roi_layout.addLayout(x_coord_layout)
        y_coord_layout = QHBoxLayout()
        self.y_top_input = QSpinBox(); self.y_top_input.setRange(0, 2048)
        self.y_height_input = QSpinBox(); self.y_height_input.setRange(1, 2048)
        y_coord_layout.addWidget(QLabel("Y-Coordinate (Top):")); y_coord_layout.addWidget(self.y_top_input)
        y_coord_layout.addWidget(QLabel("Height:")); y_coord_layout.addWidget(self.y_height_input)
        single_roi_layout.addLayout(y_coord_layout)
        roi_binning_layout = QHBoxLayout()
        self.roi_horizontal_binning = QComboBox(); self.roi_horizontal_binning.addItems(["1", "2", "4", "8"])
        roi_binning_layout.addWidget(QLabel("Horizontal Binning:")); roi_binning_layout.addWidget(self.roi_horizontal_binning)
        single_roi_layout.addLayout(roi_binning_layout)
        single_roi_tab.setLayout(single_roi_layout)
        self.readout_tabs.addTab(single_roi_tab, "Single ROI")

        # Multiple ROI
        multiple_roi_tab = QWidget(); multiple_roi_layout = QVBoxLayout()
        h_bounds_layout = QHBoxLayout()
        self.multi_x_left_input = QSpinBox(); self.multi_x_left_input.setRange(0, 2048)
        self.multi_x_width_input = QSpinBox(); self.multi_x_width_input.setRange(1, 2048)
        h_bounds_layout.addWidget(QLabel("Horizontal Bounds (Left):")); h_bounds_layout.addWidget(self.multi_x_left_input)
        h_bounds_layout.addWidget(QLabel("Width:")); h_bounds_layout.addWidget(self.multi_x_width_input)
        multiple_roi_layout.addLayout(h_bounds_layout)
        first_roi_layout = QHBoxLayout()
        self.multi_y_top_input = QSpinBox(); self.multi_y_top_input.setRange(0, 2048)
        first_roi_layout.addWidget(QLabel("First ROI Start (Top):")); first_roi_layout.addWidget(self.multi_y_top_input)
        multiple_roi_layout.addLayout(first_roi_layout)
        roi_dim_layout = QHBoxLayout()
        self.multi_roi_height_input = QSpinBox(); self.multi_roi_height_input.setRange(1, 2048)
        self.multi_roi_count_input = QSpinBox(); self.multi_roi_count_input.setRange(1, 100)
        roi_dim_layout.addWidget(QLabel("ROI Height:")); roi_dim_layout.addWidget(self.multi_roi_height_input)
        roi_dim_layout.addWidget(QLabel("Number of ROIs:")); roi_dim_layout.addWidget(self.multi_roi_count_input)
        multiple_roi_layout.addLayout(roi_dim_layout)
        gap_layout = QHBoxLayout()
        self.multi_gap_input = QSpinBox(); self.multi_gap_input.setRange(0, 2048)
        gap_layout.addWidget(QLabel("Vertical Gap:")); gap_layout.addWidget(self.multi_gap_input)
        multiple_roi_layout.addLayout(gap_layout)
        self.multi_preview_label = QLabel("Total Vertical Span: 0"); self.multi_preview_label.setAlignment(Qt.AlignCenter)
        multiple_roi_layout.addWidget(self.multi_preview_label)
        multiple_roi_tab.setLayout(multiple_roi_layout)
        self.readout_tabs.addTab(multiple_roi_tab, "Multiple ROI")

        # Custom ROI
        custom_roi_tab = QWidget(); custom_roi_layout = QVBoxLayout()
        c_width_layout = QHBoxLayout()
        self.custom_x_left_input = QSpinBox(); self.custom_x_left_input.setRange(0, 2048)
        self.custom_x_width_input = QSpinBox(); self.custom_x_width_input.setRange(1, 2048)
        c_width_layout.addWidget(QLabel("Common Bounds (Left):")); c_width_layout.addWidget(self.custom_x_left_input)
        c_width_layout.addWidget(QLabel("Width:")); c_width_layout.addWidget(self.custom_x_width_input)
        custom_roi_layout.addLayout(c_width_layout)
        self.roi_table = QTableWidget(0, 4)
        self.roi_table.setHorizontalHeaderLabels(["Index", "Top (Y)", "Height (H)", "Delete"])
        custom_roi_layout.addWidget(self.roi_table)
        c_btn_row = QHBoxLayout()
        add_roi_btn = QPushButton("+ Add New ROI"); add_roi_btn.clicked.connect(self._add_new_roi)
        clear_all_btn = QPushButton("Clear All"); clear_all_btn.clicked.connect(self._clear_all_rois)
        c_btn_row.addWidget(add_roi_btn); c_btn_row.addWidget(clear_all_btn)
        custom_roi_layout.addLayout(c_btn_row)
        custom_roi_tab.setLayout(custom_roi_layout)
        self.readout_tabs.addTab(custom_roi_tab, "Custom ROI")

        layout.addWidget(self.readout_tabs)
        
        # --- Matplotlib Config Graph & Buttons ---
        self.config_fig = Figure(figsize=(5, 3))
        self.config_canvas = FigureCanvas(self.config_fig)
        self.config_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.config_axes = self.config_fig.add_subplot(111)
        layout.addWidget(self.config_canvas)
        
        layout.addStretch() 
        btn_row = QHBoxLayout() 
        self.bg_btn = QPushButton("Background Capture") 
        self.bg_btn.setStyleSheet("color:red") 
        self.ref_btn = QPushButton("Reference Capture") 
        self.ref_btn.setStyleSheet("color:red") 
        self.reset_btn = QPushButton("Reset Settings") 

        btn_row.addWidget(self.bg_btn)
        btn_row.addWidget(self.ref_btn)
        btn_row.addWidget(self.reset_btn) 
        layout.addLayout(btn_row) 
        
        self.setLayout(layout)

    def _add_new_roi(self):
        row = self.roi_table.rowCount()
        self.roi_table.insertRow(row)
        idx_item = QTableWidgetItem(str(row + 1)); idx_item.setFlags(Qt.ItemIsEnabled)
        self.roi_table.setItem(row, 0, idx_item)
        self.roi_table.setItem(row, 1, QTableWidgetItem("0")) 
        self.roi_table.setItem(row, 2, QTableWidgetItem("0")) 
        del_btn = QPushButton("Delete"); del_btn.clicked.connect(lambda: self.roi_table.removeRow(row))
        self.roi_table.setCellWidget(row, 3, del_btn)

    def _clear_all_rois(self):
        self.roi_table.setRowCount(0)

    def get_config(self):
        """Builds the JSON payload for the server from the UI inputs."""
        cfg = {}
        cfg["camera_index"] = self.spectral_cam_sel.currentData()
        cfg["camera_label"] = self.spectral_cam_sel.currentText()
        cfg["mode"] = self.main._resolve_raw_mode_from_camera()
        cfg["binning"] = self.spectral_res_sel.currentText()
        cfg["exposure"] = self.spectral_exp_input.value()
        cfg["gain"] = self.spectral_gain_input.value()
        cfg["accumulations"] = self.spectral_accum_input.value()
        cfg["combine"] = "sum" if self.spectral_accum_mode.currentText() == "Sum" else "mean"
        cfg["readout_setup"] = self.readout_sel.currentText()
        
        cfg["fullframe"] = {
            "horizontal_binning": self.horizontal_binning.currentText(),
            "vertical_binning": self.vertical_binning.currentText(),
        }
        cfg["single_roi"] = {
            "x_left": self.x_left_input.value(), "width": self.x_width_input.value(),
            "y_top": self.y_top_input.value(), "height": self.y_height_input.value(),
            "horizontal_binning": self.roi_horizontal_binning.currentText(),
        }
        cfg["multiple_roi"] = {
            "x_left": self.multi_x_left_input.value(), "width": self.multi_x_width_input.value(),
            "y_top": self.multi_y_top_input.value(), "roi_height": self.multi_roi_height_input.value(),
            "roi_count": self.multi_roi_count_input.value(), "gap": self.multi_gap_input.value(),
        }
        
        rows = []
        for r in range(self.roi_table.rowCount()):
            try: rows.append({"top": int(self.roi_table.item(r, 1).text()), "height": int(self.roi_table.item(r, 2).text())})
            except: pass
        cfg["custom_roi"] = {
            "x_left": self.custom_x_left_input.value(), "width": self.custom_x_width_input.value(), "rows": rows
        }
        cfg["split_channels"] = self.spectral_use_sep_ch_box.isChecked()
        return cfg

    def set_config(self, cfg):
        """Restores the UI inputs from a loaded JSON config."""
        if not cfg: return
        def set_combo(c, text):
            idx = c.findText(str(text), Qt.MatchExactly)
            if idx >= 0: c.setCurrentIndex(idx)

        set_combo(self.spectral_cam_sel, cfg.get("camera_label"))
        set_combo(self.spectral_res_sel, cfg.get("binning"))
        self.spectral_exp_input.setValue(cfg.get("exposure", 50000))
        self.spectral_gain_input.setValue(cfg.get("gain", 4.0))
        self.spectral_accum_input.setValue(cfg.get("accumulations", 1))
        set_combo(self.spectral_accum_mode, "Sum" if cfg.get("combine", "mean") == "sum" else "Average")
        set_combo(self.readout_sel, cfg.get("readout_setup"))
        
        ff = cfg.get("fullframe", {})
        set_combo(self.horizontal_binning, ff.get("horizontal_binning"))
        set_combo(self.vertical_binning, ff.get("vertical_binning"))
        
        s = cfg.get("single_roi", {})
        self.x_left_input.setValue(s.get("x_left", 0)); self.x_width_input.setValue(s.get("width", 0))
        self.y_top_input.setValue(s.get("y_top", 0)); self.y_height_input.setValue(s.get("height", 0))
        set_combo(self.roi_horizontal_binning, s.get("horizontal_binning"))
        
        m = cfg.get("multiple_roi", {})
        self.multi_x_left_input.setValue(m.get("x_left", 0)); self.multi_x_width_input.setValue(m.get("width", 0))
        self.multi_y_top_input.setValue(m.get("y_top", 0)); self.multi_roi_height_input.setValue(m.get("roi_height", 0))
        self.multi_roi_count_input.setValue(m.get("roi_count", 0)); self.multi_gap_input.setValue(m.get("gap", 0))
        
        c = cfg.get("custom_roi", {})
        self.custom_x_left_input.setValue(c.get("x_left", 0)); self.custom_x_width_input.setValue(c.get("width", 0))
        self.roi_table.setRowCount(0)
        for row in c.get("rows", []):
            self._add_new_roi()
            idx = self.roi_table.rowCount() - 1
            self.roi_table.item(idx, 1).setText(str(row.get("top", 0)))
            self.roi_table.item(idx, 2).setText(str(row.get("height", 0)))
            
        self.spectral_use_sep_ch_box.setChecked(cfg.get("split_channels", True))


class CalibrationSettingsTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout()

        layout.addWidget(QLabel("<b>Wavelength Calibration (Pixel to nm)</b>"))
        
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Fitting Model:"))
        self.calib_model_sel = QComboBox()
        self.calib_model_sel.addItems([
            "Linear (1st Degree)", "Quadratic (2nd Degree)", "Cubic (3rd Degree)",
            "Prism (Hartmann)", "Grating (Physical)", "Cauchy (Empirical)"
        ])
        self.calib_model_sel.setCurrentIndex(1)
        self.calib_model_sel.currentIndexChanged.connect(self._toggle_calib_guesses)
        model_row.addWidget(self.calib_model_sel)
        model_row.addStretch()
        layout.addLayout(model_row)

        self.calib_guess_widget = QWidget(); guess_layout = QHBoxLayout()
        guess_layout.setContentsMargins(0, 0, 0, 0)
        guess_layout.addWidget(QLabel("Initial Guesses (p0):"))
        self.calib_guess_input = QLineEdit("300.0, 10000.0, -100.0")
        guess_layout.addWidget(self.calib_guess_input)
        self.calib_guess_widget.setLayout(guess_layout); self.calib_guess_widget.hide()
        layout.addWidget(self.calib_guess_widget)

        self.calib_table = QTableWidget(3, 2)
        self.calib_table.setHorizontalHeaderLabels(["Pixel Index", "Wavelength (nm)"])
        self.calib_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.calib_table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Point"); add_btn.clicked.connect(lambda: self.calib_table.insertRow(self.calib_table.rowCount()))
        
        rm_btn = QPushButton("- Remove Point"); rm_btn.clicked.connect(self._remove_point)
        
        calc_btn = QPushButton("Calculate && Apply Calibration"); calc_btn.clicked.connect(self._compute_spectral_calibration)
        
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addWidget(calc_btn)
        layout.addLayout(btn_row)

        self.calib_info_label = QLabel("Current Calibration: None")
        layout.addWidget(self.calib_info_label)
        
        self.calib_fig = Figure(figsize=(5, 3))
        self.calib_canvas = FigureCanvas(self.calib_fig)
        self.calib_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.calib_axes = self.calib_fig.add_subplot(111)
        layout.addWidget(self.calib_canvas)

        layout.addStretch()
        self.setLayout(layout)

    def _remove_point(self):
        """Removes the currently selected row, or the last row if none is selected."""
        current_row = self.calib_table.currentRow()
        if current_row >= 0:
            self.calib_table.removeRow(current_row)
        elif self.calib_table.rowCount() > 0:
            self.calib_table.removeRow(self.calib_table.rowCount() - 1)

    def _toggle_calib_guesses(self):
        text = self.calib_model_sel.currentText()
        if "Prism" in text or "Grating" in text or "Cauchy" in text:
            self.calib_guess_widget.show()
            if "Prism" in text:
                self.calib_guess_input.setText("300.0, 10000.0, -100.0")
            elif "Grating" in text:
                self.calib_guess_input.setText("1000.0, 30000.0, 0.78, 2000.0")
            else: # Cauchy
                self.calib_guess_input.setText("300.0, 10000.0, 1000.0, -100.0")
        else:
            self.calib_guess_widget.hide()

    def _compute_spectral_calibration(self):
        import numpy as np
        
        pixels, wavelengths = [], []
        for row in range(self.calib_table.rowCount()):
            px = self.calib_table.item(row, 0); nm = self.calib_table.item(row, 1)
            if px and nm and px.text() and nm.text():
                try:
                    pixels.append(float(px.text()))
                    wavelengths.append(float(nm.text()))
                except ValueError: pass
                    
        model_map = {
            "Linear": "poly1", "Quadratic": "poly2", "Cubic": "poly3", 
            "Prism": "prism", "Grating": "grating", "Cauchy": "cauchy"
        }
        model_text = self.calib_model_sel.currentText()
        model_id = next((v for k, v in model_map.items() if k in model_text), "poly2")
        
        p0 = None
        if model_id in ["prism", "grating", "cauchy"]:
            try: p0 = [float(x.strip()) for x in self.calib_guess_input.text().split(",")]
            except ValueError:
                self.main.status_label.setText("Error: Guesses must be comma-separated numbers.")
                return
        
        try:
            coeffs = self.main.processor.calculate_calibration_coeffs(pixels, wavelengths, model_type=model_id, p0=p0)
            self.main.spec_settings.wavelength_coeffs = coeffs
            self.main.spec_settings.calib_model_type = model_id
            
            if hasattr(self.main.spectroscopy_tab, 'use_nm_box'):
                self.main.spectroscopy_tab.use_nm_box.setChecked(True)
                
            coeff_str = ", ".join([f"{c:.2e}" for c in coeffs])
            self.calib_info_label.setText(f"Active: {model_text}\nCoeffs: [{coeff_str}]")
            
            # --- Plotting the Points and the Fitted Model ---
            self.calib_axes.clear()
            self.calib_axes.scatter(pixels, wavelengths, color='red', label='Data Points', zorder=5)
            
            if len(pixels) > 1:
                p_min, p_max = min(pixels), max(pixels)
                # Expand the line slightly beyond the max/min points for better visual context
                padding = (p_max - p_min) * 0.1 if p_max > p_min else 100
                dense_pixels = np.linspace(max(0, p_min - padding), p_max + padding, 200)
                
                dense_wavelengths = self.main.processor.apply_wavelength_calibration(
                    dense_pixels, coeffs, model_id
                )
                self.calib_axes.plot(dense_pixels, dense_wavelengths, color='blue', label=f'Fit ({model_text})')
            
            self.calib_axes.set_xlabel("Pixel Index")
            self.calib_axes.set_ylabel("Wavelength (nm)")
            self.calib_axes.set_title("Wavelength Calibration Fit")
            self.calib_axes.legend(fontsize=8)
            self.calib_axes.grid(True, linestyle='--', alpha=0.6)
            
            self.calib_fig.tight_layout()
            self.calib_canvas.draw()
            
            # Update the main graph with the new X-Axis calibration
            if hasattr(self.main, 'spectroscopy_tab'):
                self.main.spectroscopy_tab._redraw_current_plot()
            
            # Auto-save the calibration config immediately
            self.main._save_config_to_json()
            
            self.main.status_label.setText("Calibration calculated, applied, and saved!")
            
        except Exception as e:
            self.main.status_label.setText(f"Plotting/Calibration failed: {e}")
            print(f"Calibration Exception: {e}")

class SavingSettingsTab(QWidget):
    def __init__(self, main_app):
        super().__init__()
        self.main = main_app
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Default capture file name:"))
        self.prefix_input = QLineEdit(self.main.cam_store.file_root)
        self.prefix_input.editingFinished.connect(lambda: self._set_file_root(self.prefix_input.text()))
        layout.addWidget(self.prefix_input)

        self.add_date_cb = QCheckBox("Append date (YYMMDD)"); self.add_date_cb.setChecked(bool(self.main.cam_store.append_date))
        self.add_date_cb.stateChanged.connect(lambda state: self._update_flag("append_date", state))
        layout.addWidget(self.add_date_cb)

        self.add_time_cb = QCheckBox("Append time (HHMMSS)"); self.add_time_cb.setChecked(bool(self.main.cam_store.append_time))
        self.add_time_cb.stateChanged.connect(lambda state: self._update_flag("append_time", state))
        layout.addWidget(self.add_time_cb)

        delim_row = QHBoxLayout(); delim_row.addWidget(QLabel("Delimiter (_ or -):"))
        self.delim_sel = QComboBox(); self.delim_sel.addItems(["_", "-"]); self.delim_sel.setCurrentText(self.main.cam_store.name_delimiter)
        self.delim_sel.currentTextChanged.connect(self._set_delim)
        delim_row.addWidget(self.delim_sel); layout.addLayout(delim_row)

        self.num_suf_cb = QCheckBox("Add numeric suffix"); self.num_suf_cb.setChecked(bool(self.main.cam_store.use_numeric_suffix))
        self.num_suf_cb.stateChanged.connect(self._toggle_numeric)
        layout.addWidget(self.num_suf_cb)

        suf_row = QHBoxLayout(); suf_row.addWidget(QLabel("Starting number:"))
        self.suf_start_in = QSpinBox(); self.suf_start_in.setRange(0, 1000000); self.suf_start_in.setValue(self.main.cam_store.suffix_start)
        self.suf_start_in.valueChanged.connect(self._set_suf_start); suf_row.addWidget(self.suf_start_in)
        self.reset_btn = QPushButton("Reset Counter"); self.reset_btn.clicked.connect(self._reset_counter)
        suf_row.addWidget(self.reset_btn); layout.addLayout(suf_row)

        dir_row = QHBoxLayout()
        self.dir_input = QLineEdit(str(self.main.cam_store.save_directory))
        self.dir_input.editingFinished.connect(lambda: self._update_dir(self.dir_input.text()))
        browse_btn = QPushButton("Browse…"); browse_btn.clicked.connect(self._browse)
        layout.addWidget(QLabel("Snapshot Directory:")); dir_row.addWidget(self.dir_input); dir_row.addWidget(browse_btn)
        layout.addLayout(dir_row)

        self.auto_dng_cb = QCheckBox("Auto-save DNG captures to disk"); self.auto_dng_cb.setChecked(bool(self.main.cam_store.auto_save_dng))
        self.auto_dng_cb.stateChanged.connect(lambda state: setattr(self.main.cam_store, "auto_save_dng", state == Qt.Checked))
        layout.addWidget(self.auto_dng_cb)

        layout.addStretch(); self.setLayout(layout); self._sync_controls()

    def _set_file_root(self, text): self.main.cam_store.file_root = text.strip() or "capture"; self.main._refresh_capture_name_preview()
    def _update_flag(self, attr, state): setattr(self.main.cam_store, attr, state == Qt.Checked); self.main._refresh_capture_name_preview()
    def _set_delim(self, text): self.main.cam_store.name_delimiter = text; self.main._refresh_capture_name_preview()
    def _toggle_numeric(self, state): self.main.cam_store.use_numeric_suffix = (state == Qt.Checked); self._sync_controls(); self.main._refresh_capture_name_preview()
    def _set_suf_start(self, val): self.main.cam_store.suffix_start = val; self.main._refresh_capture_name_preview()
    def _reset_counter(self): self.main.cam_store.suffix_next = self.main.cam_store.suffix_start; self.main._refresh_capture_name_preview()
    def _update_dir(self, text): self.main.cam_store.save_directory = Path(text).expanduser().resolve() if text else Path.cwd()
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Save Directory", str(self.main.cam_store.save_directory))
        if d: self._update_dir(d); self.dir_input.setText(str(self.main.cam_store.save_directory))
    def _sync_controls(self):
        en = self.main.cam_store.use_numeric_suffix
        self.suf_start_in.setEnabled(en); self.reset_btn.setEnabled(en)