"""
Headless core for the Pi camera Napari plugin.

This module is the "Engine". It intentionally contains NO user interface (Qt) 
or Napari imports. This ensures the data handling, network requests, and math 
can run independently, making the code much easier to test and maintain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import numpy as np
import requests
from scipy.optimize import curve_fit


# ==========================================
# STORES (DATA & CONFIGURATION BUCKETS)
# ==========================================

@dataclass
class CameraSettingsStore:
    """
    Stores basic hardware state and available sensor capabilities.
    Acts as the single source of truth for the camera's physical settings.
    """
    url: str = "http://192.168.0.197:8080"
    exposure: int = 50000
    gain: float = 4.0
    resolution: str = "640x480"
    camera_index: int = 0
    accumulations: int = 1
    combine: str = "stack"
    prefix: str = "capture"
    save_directory: Path = field(default_factory=Path.cwd)
    auto_save_dng: bool = False
    
    # --- UI & Saving State ---
    file_root: str = "capture"
    current_filename: str = "capture"
    append_date: bool = False
    append_time: bool = False
    name_delimiter: str = "_"
    use_numeric_suffix: bool = False
    suffix_start: int = 1
    suffix_next: int = 1
    current_mode: str = "4056:3040:12:U"

    available_cameras: List[Dict[str, Any]] = field(default_factory=list)
    available_resolutions: List[str] = field(default_factory=list)
    raw_mode_map: Dict[str, str] = field(default_factory=dict)

    def update_modes(self, modes: List[Any]) -> None:
        """Parses the available sensor modes from the camera and stores them."""
        self.available_resolutions = []
        self.raw_mode_map = {}
        for mode in modes:
            if isinstance(mode, dict):
                res = str(mode.get("resolution", "")).strip()
                raw = str(mode.get("raw_mode", ""))
                if res:
                    self.available_resolutions.append(res)
                    if ":" in raw:
                        self.raw_mode_map[res] = raw
            else:
                res = str(mode).strip()
                if res:
                    self.available_resolutions.append(res)

@dataclass
class SpectralSettingsStore:
    """
    Stores processing preferences for the Spectroscopy tab.
    Contains the massive 'ui_settings' dictionary that gets pushed to the Pi.
    """
    split_channels: bool = True
    roi_mode: str = "Intensity"
    export_filename: str = "spectra_export.csv"
    ui_settings: Dict[str, Any] = field(default_factory=dict)
    
    # --- CALIBRATION STORAGE ---
    wavelength_coeffs: List[float] = field(default_factory=list) # Holds the polynomial/model params
    use_wavelengths: bool = False                                # Checkbox state
    calib_model_type: str = "poly2"                              # 'poly1', 'poly2', 'poly3', 'prism', 'grating', 'cauchy'

@dataclass
class SpectralDataStore:
    """
    Stores the actual acquired 1D spectral data results.
    This acts as a cache so you don't have to re-take pictures when 
    switching math modes (e.g., switching from Intensity to Absorbance).
    """
    last_profiles: List[Any] = field(default_factory=list)  # The most recent Snap
    bg_profile: Optional[List[Any]] = None                  # Cached Dark/Background 
    ref_profile: Optional[List[Any]] = None                 # Cached Light/Reference 


# ==========================================
# LOGIC & NETWORK HANDLERS
# ==========================================

class CameraHandler:
    """
    The exclusive interface for all network requests.
    Every time the script needs to talk to the Raspberry Pi over HTTP, 
    it must pass through this class.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    #def fetch_snapshot(self) -> bytes:
    #    """Requests a lightweight JPEG preview image (used for Live View)."""
    #    resp = requests.get(f"{self.base_url}/snapshot.jpg", timeout=(1, 5.0))
    #    resp.raise_for_status()
    #    return resp.content
    def fetch_snapshot(self, camera_index: int = None) -> bytes:
        """Requests a lightweight JPEG preview image (used for Live View)."""
        params = {}
        if camera_index is not None:
            params["camera_index"] = camera_index
            
        # Passing params dict to attach ?camera_index=X to the URL
        resp = requests.get(f"{self.base_url}/snapshot.jpg", params=params, timeout=(1.0, 5.0))
        resp.raise_for_status()
        return resp.content

    def fetch_raw(self, params: Dict[str, Any]) -> Tuple[bytes, Dict[str, Any]]:
        """
        Requests a heavy, uncompressed RAW binary image array.
        Used for the 'Full frame' mode where we need spatial 2D image data.
        """
        exposure_s = max(0.0, float(params.get("exposure", 0)) / 1_000_000.0)
        timeout = max(15.0, (exposure_s * params.get("accumulations", 1)) + 5.0)
        resp = requests.get(f"{self.base_url}/rawframe", params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.content, dict(resp.headers)

    def fetch_spectral_capture(self, payload: Dict[str, Any]) -> Any:
        """
        POSTs the exact ROI settings to the Pi, and asks the Pi to calculate the 1D spectra.
        Returns a lightweight JSON dictionary instead of a heavy image.
        """
        resp = requests.post(
            f"{self.base_url}/spectral_capture", json=payload, timeout=60.0
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_cameras(self) -> Any:
        """Gets the list of available cameras attached to the Pi."""
        resp = requests.get(f"{self.base_url}/available_cameras", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def send_apply(self, payload: Dict[str, Any]) -> requests.Response:
        """Applies hardware exposure/gain without taking a picture."""
        return requests.post(
            f"{self.base_url}/apply_recording_settings", json=payload, timeout=10.0
        )
        
    def send_spectral_settings(self, payload: Dict[str, Any]) -> requests.Response:
        """Pushes the spectral ROI configuration to the Pi server."""
        return requests.post(
            f"{self.base_url}/settings/spectral_acquisition", json=payload, timeout=10.0
        )
        
    def fetch_dng(self, camera_index: int = None) -> bytes:
        """Downloads a DNG raw file."""
        params = {}
        if camera_index is not None:
            params["camera_index"] = camera_index
            
        # Add params=params to the request
        resp = requests.get(f"{self.base_url}/capture_dng", params=params, timeout=15.0)
        resp.raise_for_status()
        return resp.content

    def send_switch_camera(self, index: int) -> requests.Response:
        """Tells the Pi to switch active sensor (if multiple exist)."""
        return requests.get(f"{self.base_url}/switch_camera/{index}", timeout=10.0)

    def post_video(self, start: bool, prefix: str) -> requests.Response:
        """Starts or stops hardware H264 recording on the Pi."""
        endpoint = "start_recording" if start else "stop_recording"
        payload = {"base": prefix} if start else {}
        return requests.post(f"{self.base_url}/{endpoint}", json=payload, timeout=10.0)


class DataHandler:
    """
    Handles all Math, Array shaping, Formatting logic, and Physics.
    Decoupled from network and UI so it can just crunch numbers.
    """

    # @staticmethod
    # def decode_rawframe(payload: bytes, headers: Dict[str, Any], params: Dict[str, Any]):
    #     dtype_name = headers.get("X-Raw-DType", "uint16")
    #     raw_dtype = np.dtype(dtype_name)
    #     raw_data = np.frombuffer(payload, dtype=raw_dtype)

    #     mode_parts = (params.get("mode") or "").split(":")
    #     target_w = target_h = target_bd = None
    #     if len(mode_parts) >= 2:
    #         target_w, target_h = int(mode_parts[0]), int(mode_parts[1])
    #     if len(mode_parts) >= 3:
    #         target_bd = int(mode_parts[2])

    #     if raw_dtype.itemsize == 1 and target_bd and target_bd > 8 and len(payload) % 2 == 0:
    #         raw_data = np.frombuffer(payload, dtype=np.dtype("<u2"))

    #     raw_plane = None
    #     shape_header = headers.get("X-Raw-Shape")
    #     if shape_header:
    #         try:
    #             dims = tuple(int(d) for d in str(shape_header).lower().replace("x", " ").split())
    #             if raw_data.size == np.prod(dims):
    #                 raw_plane = raw_data.reshape(dims)
    #         except ValueError:
    #             pass

    #     if raw_plane is None and target_h and target_h > 0:
    #         acc = int(headers.get("X-Raw-Accumulations", 1))
    #         divisor = target_h * acc
    #         if divisor and raw_data.size % divisor == 0:
    #             inferred_w = raw_data.size // divisor
    #             shape = (acc, target_h, inferred_w) if acc > 1 else (target_h, inferred_w)
    #             raw_plane = raw_data.reshape(shape)

    #     if raw_plane is None:
    #         return None, {}

    #     if raw_plane.dtype.itemsize < 2:
    #         raw_plane = raw_plane.astype(np.uint16, copy=False)

    #     return raw_plane, {
    #         "pattern": str(headers.get("X-Raw-CFA", "RGGB")).upper(),
    #         "bit_depth_reported": target_bd or (raw_plane.dtype.itemsize * 8),
    #         "accumulations": int(headers.get("X-Raw-Accumulations", 1)),
    #         "combine": headers.get("X-Raw-Combine"),
    #     }
    @staticmethod
    def decode_rawframe(payload: bytes, headers: Dict[str, Any], params: Dict[str, Any]):
        # --- NEW: Force all headers to lowercase for safe, case-insensitive lookup ---
        h = {str(k).lower(): v for k, v in headers.items()}
        
        dtype_name = h.get("x-raw-dtype", "uint16")
        raw_dtype = np.dtype(dtype_name)
        raw_data = np.frombuffer(payload, dtype=raw_dtype)

        mode_parts = (params.get("mode") or "").split(":")
        target_w = target_h = target_bd = None
        if len(mode_parts) >= 2:
            target_w, target_h = int(mode_parts[0]), int(mode_parts[1])
        if len(mode_parts) >= 3:
            target_bd = int(mode_parts[2])

        if raw_dtype.itemsize == 1 and target_bd and target_bd > 8 and len(payload) % 2 == 0:
            raw_data = np.frombuffer(payload, dtype=np.dtype("<u2"))

        raw_plane = None
        shape_header = h.get("x-raw-shape")
        if shape_header:
            try:
                dims = tuple(int(d) for d in str(shape_header).lower().replace("x", " ").split())
                if raw_data.size == np.prod(dims):
                    # --- FIX START: Swap Width and Height for Numpy ---
                    #if len(dims) == 2:
                    #    dims = (dims[1], dims[0])
                    #elif len(dims) == 3: # Handles (Accumulations, Width, Height)
                    #    dims = (dims[0], dims[2], dims[1])
                    # --- FIX END ---
                    raw_plane = raw_data.reshape(dims)
            except ValueError:
                pass

        if raw_plane is None and target_h and target_h > 0:
            acc = int(h.get("x-raw-accumulations", 1))
            divisor = target_h * acc
            if divisor and raw_data.size % divisor == 0:
                inferred_w = raw_data.size // divisor
                shape = (acc, target_h, inferred_w) if acc > 1 else (target_h, inferred_w)
                raw_plane = raw_data.reshape(shape)

        if raw_plane is None:
            return None, {}

        if raw_plane.dtype.itemsize < 2:
            raw_plane = raw_plane.astype(np.uint16, copy=False)

        return raw_plane, {
            "pattern": str(h.get("x-raw-cfa", "RGGB")).upper(),
            "bit_depth_reported": target_bd or (raw_plane.dtype.itemsize * 8),
            "accumulations": int(h.get("x-raw-accumulations", 1)),
            "combine": h.get("x-raw-combine"),
        }

    @staticmethod
    def build_channel_stack(raw_plane: np.ndarray, pattern: str):
        stack = np.asarray(raw_plane)
        if stack.ndim == 2:
            stack = stack[np.newaxis, ...]
        
        slice_defs = {
            "RGGB": [
                (np.s_[:, 0::2, 0::2], "R", "red"),
                (np.s_[:, 0::2, 1::2], "G1", "green"),
                (np.s_[:, 1::2, 0::2], "G2", "green"),
                (np.s_[:, 1::2, 1::2], "B", "blue"),
            ],
            "GRBG": [
                (np.s_[:, 0::2, 1::2], "R", "red"),
                (np.s_[:, 0::2, 0::2], "G1", "green"),
                (np.s_[:, 1::2, 1::2], "G2", "green"),
                (np.s_[:, 1::2, 0::2], "B", "blue"),
            ],
            "GBRG": [
                (np.s_[:, 1::2, 0::2], "R", "red"),
                (np.s_[:, 0::2, 0::2], "G1", "green"),
                (np.s_[:, 1::2, 1::2], "G2", "green"),
                (np.s_[:, 0::2, 1::2], "B", "blue"),
            ],
            "BGGR": [
                (np.s_[:, 1::2, 1::2], "R", "red"),
                (np.s_[:, 0::2, 1::2], "G1", "green"),
                (np.s_[:, 1::2, 0::2], "G2", "green"),
                (np.s_[:, 0::2, 0::2], "B", "blue"),
            ],
        }
        defs = slice_defs.get(pattern, slice_defs["BGGR"])
        planes, names, cmaps = [], [], []
        
        for slc, n, c in defs:
            planes.append(stack[slc])
            names.append(n)
            cmaps.append(c)
            
        return np.stack(planes, axis=1), cmaps, names

    @staticmethod
    def calculate_profiles(layers: List[Any], split_channels: bool):
        """Legacy helper for manually calculating profiles from Napari layers."""
        profiles = []
        if not split_channels:
            all_profs = []
            for layer in layers:
                d = np.asarray(layer.data)
                p = np.mean(d, axis=tuple(range(d.ndim - 1)))
                all_profs.append(p)
            if all_profs:
                m_len = min(len(p) for p in all_profs)
                combined = np.mean([p[:m_len] for p in all_profs], axis=0)
                profiles.append(
                    (f"Combined ({len(layers)} layers)", np.arange(m_len), combined)
                )
        else:
            for layer in layers:
                if not hasattr(layer, "data"):
                    continue
                d, name = np.asarray(layer.data), layer.name
                if d.ndim == 4:
                    for i in range(d.shape[1]):
                        p = np.mean(d[:, i, :, :], axis=(0, 1))
                        profiles.append((f"{name}-Ch{i}", np.arange(d.shape[3]), p))
                elif d.ndim == 3 and d.shape[-1] in [3, 4]:
                    for i, ch in enumerate(["R", "G", "B", "A"][: d.shape[-1]]):
                        profiles.append(
                            (f"{name}-{ch}", np.arange(d.shape[1]), np.mean(d[..., i], axis=0))
                        )
                else:
                    p = np.mean(d, axis=tuple(range(d.ndim - 1)))
                    profiles.append((name, np.arange(len(p)), p))
        return profiles

    @staticmethod
    def parse_spectral_json(server_result: dict, split_channels: bool, name: str = "Snap"):
        profiles = []
        spectra_list = server_result.get("result", {}).get("spectra", [])
        
        roi_groups = {}
        for item in spectra_list:
            roi_label = item.get("label", "ROI")
            channel = item.get("channel", "Ch")
            data_matrix = item.get("spectra", [])
            if not data_matrix:
                continue
            y_data = np.mean(data_matrix, axis=0) 
            
            if roi_label not in roi_groups:
                roi_groups[roi_label] = []
            roi_groups[roi_label].append((channel, y_data))
            
        for roi_label, channels_data in roi_groups.items():
            if split_channels:
                for ch, y_data in channels_data:
                    profiles.append((f"{name}-{roi_label}-{ch}", np.arange(len(y_data)), y_data))
            else:
                all_y = [y for ch, y in channels_data]
                avg_y = np.mean(all_y, axis=0)
                profiles.append((f"{name}-{roi_label}", np.arange(len(avg_y)), avg_y))
                
        return profiles

    @staticmethod
    def apply_spectral_math(raw_profiles, bg_profiles, ref_profiles, mode: str):
        if mode == "Intensity" or not raw_profiles:
            return raw_profiles
            
        results = []
        for i, (label, x, y_raw) in enumerate(raw_profiles):
            y_out = np.copy(y_raw)
            y_bg = bg_profiles[i][2] if (bg_profiles and len(bg_profiles) > i) else np.zeros_like(y_raw)
            y_ref = ref_profiles[i][2] if (ref_profiles and len(ref_profiles) > i) else np.ones_like(y_raw)
            
            if mode == "Intensity-BG":
                y_out = y_raw - y_bg
            elif mode == "T": 
                num = y_raw - y_bg
                den = np.clip(y_ref - y_bg, 1e-6, None) 
                y_out = num / den
            elif mode == "A": 
                num = np.clip(y_raw - y_bg, 1e-6, None)
                den = np.clip(y_ref - y_bg, 1e-6, None)
                y_out = -np.log10(num / den)
                
            results.append((f"{label} ({mode})", x, y_out))
            
        return results

    # --- TRUE PHYSICAL OPTICAL MODELS ---

    @staticmethod
    def _model_prism_hartmann(x, lambda_0, c, x_0):
        """
        Hartmann dispersion formula for Prisms.
        λ = λ0 + C / (x - x0)
        """
        return lambda_0 + c / (x - x_0)

    @staticmethod
    def _model_grating_physical(x, d_spacing, f_lens, alpha_rad, x_0):
        """
        True physical model for a Grating Spectrometer on a flat sensor.
        λ = d * (sin(α) + sin(arctan((x - x0) / f)))
        
        d_spacing: Distance between grooves (nm per line)
        f_lens: Focal length of the camera lens (in pixels)
        alpha_rad: Incident angle of light on the grating (in radians)
        x_0: The pixel where the straight-through beam (0th order) would hit
        """
        beta_rad = np.arctan((x - x_0) / f_lens)
        wavelength = d_spacing * (np.sin(alpha_rad) + np.sin(beta_rad))
        return wavelength

    @staticmethod
    def _model_cauchy(x, a, b, c, x_0):
        """
        Empirical Cauchy-like dispersion formula adapted for sensor pixels.
        λ = A + B/(x - x0)^2 + C/(x - x0)^4
        """
        x_safe = np.asarray(x, dtype=float)
        diff = x_safe - x_0
        # Prevent division by zero if a pixel perfectly matches the offset
        diff[diff == 0] = 1e-9 
        return a + b / (diff**2) + c / (diff**4)

    # --- CALIBRATION MATH ---
    
    @staticmethod
    def calculate_calibration_coeffs(pixels: List[float], wavelengths: List[float], model_type: str = "poly2", p0: Optional[List[float]] = None) -> List[float]:
        """Calculates the curve mapping pixels to wavelengths using polynomials or physical models."""
        px = np.array(pixels)
        wl = np.array(wavelengths)
        
        if model_type.startswith("poly"):
            degree = int(model_type[-1])
            if len(px) < degree + 1:
                raise ValueError(f"Need at least {degree + 1} points for a degree {degree} fit.")
            coeffs = np.polyfit(px, wl, deg=degree)
            return coeffs.tolist()
            
        elif model_type == "prism":
            if len(px) < 3:
                raise ValueError("Prism (Hartmann) model requires at least 3 points.")
            guess = p0 if p0 and len(p0) == 3 else [300.0, 10000.0, -100.0]
            popt, _ = curve_fit(DataHandler._model_prism_hartmann, px, wl, p0=guess, maxfev=20000)
            return popt.tolist()
            
        elif model_type == "grating":
            if len(px) < 4:
                raise ValueError("Physical Grating model requires at least 4 points.")
            guess = p0 if p0 and len(p0) == 4 else [1000.0, 30000.0, 0.78, 2000.0]
            popt, _ = curve_fit(DataHandler._model_grating_physical, px, wl, p0=guess, maxfev=20000)
            return popt.tolist()
            
        elif model_type == "cauchy":
            if len(px) < 4:
                raise ValueError("Cauchy model requires at least 4 points.")
            guess = p0 if p0 and len(p0) == 4 else [300.0, 10000.0, 1000.0, -100.0]
            popt, _ = curve_fit(DataHandler._model_cauchy, px, wl, p0=guess, maxfev=20000)
            return popt.tolist()
            
        raise ValueError(f"Unknown calibration model: {model_type}")

    @staticmethod
    def apply_wavelength_calibration(x_pixels: np.ndarray, coeffs: List[float], model_type: str = "poly2") -> np.ndarray:
        """Converts an array of pixel indices into an array of wavelengths using the saved model."""
        if not coeffs:
            return x_pixels
            
        if model_type.startswith("poly"):
            return np.polyval(coeffs, x_pixels)
        elif model_type == "prism":
            return DataHandler._model_prism_hartmann(x_pixels, *coeffs)
        elif model_type == "grating":
            return DataHandler._model_grating_physical(x_pixels, *coeffs)
        elif model_type == "cauchy":
            return DataHandler._model_cauchy(x_pixels, *coeffs)
            
        return x_pixels


# --- SERVICE (HEADLESS CONTROLLER) ---

class PiCameraService:
    """Headless controller for the Pi Camera. No GUI dependencies."""

    def __init__(self, url: str = "http://192.168.0.197:8080", settings: Optional[CameraSettingsStore] = None):
        self.settings: CameraSettingsStore = settings or CameraSettingsStore(url=url)
        self.handler = CameraHandler(self.settings.url)
        self.is_recording = False

    def set_base_url(self, url: str) -> None:
        self.settings.url = url
        self.handler.base_url = url.rstrip("/")

    def get_raw_mode_str(self, res_text: str) -> str:
        return self.settings.raw_mode_map.get(res_text, "4056:3040:12:U")

    def list_cameras(self) -> Any:
        return self.handler.fetch_cameras()

    def switch_camera(self, index: int) -> Any:
        return self.handler.send_switch_camera(index)

    def apply_recording_settings(self, exposure_us: int, gain: float, resolution: str) -> Any:
        payload = {"exposure_time": int(exposure_us), "gain_value": float(gain), "resolution": str(resolution)}
        return self.handler.send_apply(payload)

    def fetch_snapshot_jpeg(self) -> bytes:
        return self.handler.fetch_snapshot()
    

    def fetch_dng(self) -> bytes:
        return self.handler.fetch_dng()

    def set_recording(self, start: bool, prefix: str) -> Any:
        resp = self.handler.post_video(start, prefix)
        self.is_recording = bool(start)
        return resp

    def capture_raw_to_numpy(
        self,
        exposure_us: int,
        gain: float,
        res_text: str,
        accumulations: int = 1,
        combine: str = "stack",
    ):
        params = {
            "exposure": int(exposure_us),
            "gain": float(gain),
            "mode": self.get_raw_mode_str(res_text),
            "accumulations": int(accumulations),
            "combine": str(combine),
        }
        payload, headers = self.handler.fetch_raw(params)
        raw, meta = DataHandler.decode_rawframe(payload, headers, params)
        if raw is None:
            return None
        stack, cmaps, names = DataHandler.build_channel_stack(raw, meta.get("pattern", "RGGB"))
        return stack, cmaps, names