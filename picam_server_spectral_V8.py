"""HTTP server exposing camera capture, preview, and configuration endpoints."""

from flask import Flask, Response, request, send_file, jsonify, render_template_string, after_this_request
import io
import os
import time
import cv2
import threading
from datetime import datetime
from picamera2 import Picamera2, libcamera # CameraInfo not imported for wider compatibility
from picamera2.sensor_format import SensorFormat # For manipulating raw sensor formats
from picamera2.encoders import H264Encoder
from picamera2.outputs import FileOutput
import json
import numpy as np # Implicitly used by Picamera2 for array operations and required for raw capture
import traceback # Ensure traceback is always imported for clean error reporting
import zipfile # For creating ZIP archives with multiple snapshot files
from typing import Any, Dict, List

BAYER_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
SPECTRAL_SETTINGS_PATH = os.environ.get("SPECTRAL_SETTINGS_PATH", "/tmp/spectral_acquisition_settings.json")
DEFAULT_SPECTRAL_ACQUISITION = {
    "camera_index": 0,
    "binning": "1",
    "exposure": 2000000,
    "gain": 5.0,
    "accumulations": 1,
    "combine": "mean",
    "mode": "4056:3040:12:U",
    "readout_setup": "Full frame",
    "fullframe": {"horizontal_binning": "1", "vertical_binning": "1"},
    "single_roi": {"x_left": 0, "width": 4056, "y_top": 0, "height": 3040, "horizontal_binning": "1", "vertical_binning": "1"},
    "multiple_roi": {"x_left": 0, "width": 4056, "y_top": 0, "roi_height": 16, "roi_count": 1, "gap": 0},
    "custom_roi": {"x_left": 0, "width": 4056, "rows": []},
}
CURRENT_SPECTRAL_ACQUISITION = None


def infer_cfa_pattern(*format_candidates):
    """Return a 4-character Bayer pattern guess based on available format strings."""
    for candidate in format_candidates:
        fmt = str(candidate or "").upper()
        if not fmt:
            continue
        for pattern in BAYER_PATTERNS:
            if pattern in fmt:
                return pattern
        if fmt.startswith("S") and len(fmt) >= 5:
            possible = fmt[1:5]
            if possible in BAYER_PATTERNS:
                return possible
    return "BGGR"



def _deep_copy_jsonable(value):
    return json.loads(json.dumps(value))


def _coerce_int(value, default=0, minimum=None, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _coerce_float(value, default=0.0, minimum=None, maximum=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def normalize_spectral_acquisition_settings(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    merged = _deep_copy_jsonable(DEFAULT_SPECTRAL_ACQUISITION)
    if payload:
        for key, value in payload.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value

    merged["camera_index"] = _coerce_int(merged.get("camera_index", 0), default=0, minimum=0)
    merged["binning"] = str(merged.get("binning", "1") or "1")
    merged["exposure"] = _coerce_int(merged.get("exposure", 2000000), default=2000000, minimum=1)
    merged["gain"] = _coerce_float(merged.get("gain", 5.0), default=5.0, minimum=0.0)
    merged["accumulations"] = _coerce_int(merged.get("accumulations", 1), default=1, minimum=1, maximum=64)
    combine = str(merged.get("combine", "mean") or "mean").strip().lower()
    merged["combine"] = combine if combine in {"mean", "avg", "average", "sum", "total"} else "mean"
    merged["mode"] = str(merged.get("mode", DEFAULT_SPECTRAL_ACQUISITION["mode"]) or DEFAULT_SPECTRAL_ACQUISITION["mode"])
    merged["readout_setup"] = str(merged.get("readout_setup", "Full frame") or "Full frame")

    fullframe = merged.setdefault("fullframe", {})
    fullframe["horizontal_binning"] = str(fullframe.get("horizontal_binning", "1") or "1")
    fullframe["vertical_binning"] = str(fullframe.get("vertical_binning", "1") or "1")

    single = merged.setdefault("single_roi", {})
    single["x_left"] = _coerce_int(single.get("x_left", 0), 0, 0)
    single["width"] = _coerce_int(single.get("width", 0), 0, 1)
    single["y_top"] = _coerce_int(single.get("y_top", 0), 0, 0)
    single["height"] = _coerce_int(single.get("height", 0), 0, 1)
    single["horizontal_binning"] = str(single.get("horizontal_binning", "1") or "1")
    single["vertical_binning"] = str(single.get("vertical_binning", "1") or "1")

    multiple = merged.setdefault("multiple_roi", {})
    multiple["x_left"] = _coerce_int(multiple.get("x_left", 0), 0, 0)
    multiple["width"] = _coerce_int(multiple.get("width", 0), 0, 1)
    multiple["y_top"] = _coerce_int(multiple.get("y_top", 0), 0, 0)
    multiple["roi_height"] = _coerce_int(multiple.get("roi_height", 16), 16, 1)
    multiple["roi_count"] = _coerce_int(multiple.get("roi_count", 1), 1, 1)
    multiple["gap"] = _coerce_int(multiple.get("gap", 0), 0, 0)

    custom = merged.setdefault("custom_roi", {})
    custom["x_left"] = _coerce_int(custom.get("x_left", 0), 0, 0)
    custom["width"] = _coerce_int(custom.get("width", 0), 0, 1)
    rows: List[Dict[str, int]] = []
    for row in custom.get("rows", []) or []:
        rows.append({
            "top": _coerce_int((row or {}).get("top", 0), 0, 0),
            "height": _coerce_int((row or {}).get("height", 1), 1, 1),
        })
    custom["rows"] = rows
    return merged


def save_spectral_acquisition_settings(settings: Dict[str, Any]) -> None:
    normalized = normalize_spectral_acquisition_settings(settings)
    os.makedirs(os.path.dirname(SPECTRAL_SETTINGS_PATH), exist_ok=True)
    with open(SPECTRAL_SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(normalized, fh, indent=2)


def load_spectral_acquisition_settings() -> Dict[str, Any]:
    global CURRENT_SPECTRAL_ACQUISITION
    if CURRENT_SPECTRAL_ACQUISITION is not None:
        return _deep_copy_jsonable(CURRENT_SPECTRAL_ACQUISITION)
    if os.path.exists(SPECTRAL_SETTINGS_PATH):
        try:
            with open(SPECTRAL_SETTINGS_PATH, "r", encoding="utf-8") as fh:
                CURRENT_SPECTRAL_ACQUISITION = normalize_spectral_acquisition_settings(json.load(fh))
                return _deep_copy_jsonable(CURRENT_SPECTRAL_ACQUISITION)
        except Exception as exc:
            print(f"Warning: failed to load spectral acquisition settings: {exc}")
    CURRENT_SPECTRAL_ACQUISITION = normalize_spectral_acquisition_settings(DEFAULT_SPECTRAL_ACQUISITION)
    return _deep_copy_jsonable(CURRENT_SPECTRAL_ACQUISITION)


def update_spectral_acquisition_settings(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    global CURRENT_SPECTRAL_ACQUISITION
    base = load_spectral_acquisition_settings()
    merged = normalize_spectral_acquisition_settings({**base, **(payload or {})})
    for key in ("fullframe", "single_roi", "multiple_roi", "custom_roi"):
        if key in (payload or {}):
            merged[key] = normalize_spectral_acquisition_settings({key: (payload or {}).get(key)})[key]
    CURRENT_SPECTRAL_ACQUISITION = merged
    save_spectral_acquisition_settings(CURRENT_SPECTRAL_ACQUISITION)
    return _deep_copy_jsonable(CURRENT_SPECTRAL_ACQUISITION)


def _mode_dimensions(mode_str: str):
    mode_parts = str(mode_str or DEFAULT_SPECTRAL_ACQUISITION["mode"]).split(':')
    if len(mode_parts) != 4:
        raise ValueError("Invalid mode format. Expected W:H:B:P_or_U")
    return int(mode_parts[0]), int(mode_parts[1]), int(mode_parts[2]), mode_parts[3].upper()


def _normalize_roi_bounds(x_left: int, width: int, y_top: int, height: int, frame_width: int, frame_height: int):
    x_left = max(0, min(x_left, max(0, frame_width - 1)))
    y_top = max(0, min(y_top, max(0, frame_height - 1)))
    width = max(1, min(width, frame_width - x_left))
    height = max(1, min(height, frame_height - y_top))
    return x_left, width, y_top, height


def _build_spectral_roi_definitions(settings: Dict[str, Any], frame_shape) -> List[Dict[str, Any]]:
    frame_height, frame_width = int(frame_shape[-2]), int(frame_shape[-1])
    readout = str(settings.get("readout_setup", "Full frame"))
    rois: List[Dict[str, Any]] = []

    if readout == "Single ROI":
        roi = settings.get("single_roi", {})
        x_left, width, y_top, height = _normalize_roi_bounds(
            int(roi.get("x_left", 0)), int(roi.get("width", frame_width)),
            int(roi.get("y_top", 0)), int(roi.get("height", frame_height)),
            frame_width, frame_height,
        )
        rois.append({"label": "single_roi", "x_left": x_left, "width": width, "y_top": y_top, "height": height})
    elif readout == "Multiple ROI":
        roi = settings.get("multiple_roi", {})
        x_left = int(roi.get("x_left", 0))
        width = int(roi.get("width", frame_width))
        start_top = int(roi.get("y_top", 0))
        roi_height = max(1, int(roi.get("roi_height", 1)))
        roi_count = max(1, int(roi.get("roi_count", 1)))
        gap = max(0, int(roi.get("gap", 0)))
        for idx in range(roi_count):
            y_top = start_top + idx * (roi_height + gap)
            if y_top >= frame_height:
                break
            _, _, y_top, height = _normalize_roi_bounds(0, 1, y_top, roi_height, 1, frame_height)
            x_left_n, width_n, _, _ = _normalize_roi_bounds(x_left, width, 0, 1, frame_width, 1)
            rois.append({"label": f"roi_{idx+1}", "x_left": x_left_n, "width": width_n, "y_top": y_top, "height": height})
    elif readout == "Multiple ROI Custom":
        roi = settings.get("custom_roi", {})
        x_left = int(roi.get("x_left", 0))
        width = int(roi.get("width", frame_width))
        x_left_n, width_n, _, _ = _normalize_roi_bounds(x_left, width, 0, 1, frame_width, 1)
        for idx, row in enumerate(roi.get("rows", []) or []):
            y_top = int((row or {}).get("top", 0))
            row_height = max(1, int((row or {}).get("height", 1)))
            _, _, y_top, height = _normalize_roi_bounds(0, 1, y_top, row_height, 1, frame_height)
            rois.append({"label": f"custom_{idx+1}", "x_left": x_left_n, "width": width_n, "y_top": y_top, "height": height})
    else:
        x_left, width, y_top, height = _normalize_roi_bounds(0, frame_width, 0, frame_height, frame_width, frame_height)
        rois.append({"label": "full_frame", "x_left": x_left, "width": width, "y_top": y_top, "height": height})

    return rois or [{"label": "full_frame", "x_left": 0, "width": frame_width, "y_top": 0, "height": frame_height}]


def _vertical_binning_factor(settings: Dict[str, Any]) -> int:
    readout = str(settings.get("readout_setup", "Full frame"))
    if readout == "Full frame":
        return _coerce_int(settings.get("fullframe", {}).get("vertical_binning", "1"), default=1, minimum=1)
    if readout == "Single ROI":
        return _coerce_int(settings.get("single_roi", {}).get("vertical_binning", "1"), default=1, minimum=1)
    return 1


def _normalize_raw_capture_like_core(raw_data: np.ndarray, mode_str: str, accumulations: int) -> np.ndarray:
    arr = np.asarray(raw_data)
    mode_parts = str(mode_str or DEFAULT_SPECTRAL_ACQUISITION["mode"]).split(":")
    target_h = target_bd = None
    if len(mode_parts) >= 2:
        try:
            target_h = int(mode_parts[1])
        except ValueError:
            target_h = None
    if len(mode_parts) >= 3:
        try:
            target_bd = int(mode_parts[2])
        except ValueError:
            target_bd = None

    # Mirror core.py::decode_rawframe: when reported dtype is uint8 for >8-bit modes,
    # reinterpret the byte buffer as little-endian uint16 *before* reshaping/splitting.
    if arr.dtype.itemsize == 1 and target_bd and target_bd > 8:
        if arr.ndim == 2:
            h, w = arr.shape
            if w % 2 != 0:
                raise ValueError(f"Cannot reinterpret uint8 raw row width {w} as uint16")
            arr = np.frombuffer(np.ascontiguousarray(arr).tobytes(), dtype=np.dtype("<u2")).reshape(h, w // 2)
        elif arr.ndim == 3:
            n, h, w = arr.shape
            if w % 2 != 0:
                raise ValueError(f"Cannot reinterpret uint8 raw row width {w} as uint16")
            arr = np.frombuffer(np.ascontiguousarray(arr).tobytes(), dtype=np.dtype("<u2")).reshape(n, h, w // 2)
        elif arr.ndim == 1 and arr.size % 2 == 0:
            arr = np.frombuffer(np.ascontiguousarray(arr).tobytes(), dtype=np.dtype("<u2"))

    if arr.ndim in (2, 3):
        return arr
    if arr.ndim == 1 and target_h and target_h > 0:
        divisor = target_h * max(1, int(accumulations))
        if divisor and arr.size % divisor == 0:
            inferred_w = arr.size // divisor
            shape = (accumulations, target_h, inferred_w) if accumulations > 1 else (target_h, inferred_w)
            return arr.reshape(shape)
    raise ValueError(f"Unsupported raw capture shape {arr.shape}; cannot normalize like core.py")


def _split_bayer_stack_like_core(raw_plane: np.ndarray, pattern: str):
    stack = np.asarray(raw_plane)
    if stack.ndim == 2:
        stack = stack[np.newaxis, ...]
    if stack.ndim != 3:
        raise ValueError(f"Expected 2D or 3D raw plane, got {stack.shape}")
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
    defs = slice_defs.get(str(pattern or "BGGR").upper(), slice_defs["BGGR"])
    channel_arrays = [stack[s].copy() for s, _, _ in defs]
    channel_names = [name for _, name, _ in defs]
    return channel_arrays, channel_names


def _bin_rows(arr2d: np.ndarray, factor: int) -> np.ndarray:
    factor = max(1, int(factor))
    arr = np.asarray(arr2d, dtype=np.float64)
    if factor == 1:
        return arr
    usable = (arr.shape[0] // factor) * factor
    if usable <= 0:
        return arr.mean(axis=0, keepdims=True)
    arr = arr[:usable, :]
    return arr.reshape(usable // factor, factor, arr.shape[1]).mean(axis=1)


def _extract_spectra_from_frame(raw_frame: np.ndarray, settings: Dict[str, Any], cfa_pattern: str):
    arr = np.asarray(raw_frame)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D combined raw frame, got {arr.shape}")
    channel_arrays, channel_names = _split_bayer_stack_like_core(arr, cfa_pattern)
    readout = str(settings.get("readout_setup", "Full frame"))
    result_items = []
    for channel_array, channel_name in zip(channel_arrays, channel_names):
        # _split_bayer_stack_like_core returns a 3D stack with length-1 frame axis.
        channel2d = np.asarray(channel_array[0], dtype=np.float64)
        rois = _build_spectral_roi_definitions(settings, channel2d.shape)
        for roi in rois:
            x_left = int(roi["x_left"])
            width = int(roi["width"])
            y_top = int(roi["y_top"])
            height = int(roi["height"])
            sub = channel2d[y_top:y_top + height, x_left:x_left + width]
            if sub.size == 0:
                continue
            if readout == "Full frame":
                vb = _vertical_binning_factor(settings)
                binned = _bin_rows(sub, vb)
                spectra = binned.tolist()
            else:
                spectra = [sub.mean(axis=0).tolist()]
            result_items.append({
                "label": roi["label"],
                "channel": channel_name,
                "row_count": len(spectra),
                "spectrum_length": len(spectra[0]) if spectra else 0,
                "roi": {
                    "x_left": x_left,
                    "width": width,
                    "y_top": y_top,
                    "height": height,
                    "label": roi["label"],
                },
                "spectra": spectra,
            })
    return {
        "readout_setup": readout,
        "cfa": str(cfa_pattern or "BGGR").upper(),
        "spectra": result_items,
    }


def _capture_raw_data_from_settings(settings: Dict[str, Any]):
    global picam2, current_camera_index, preview_config
    now = datetime.now().strftime("%y%m%d_%H%M%S")
    temp_picam2 = None
    picam2_was_managed = False

    exposure_time = _coerce_int(settings.get("exposure", 2000000), default=2000000, minimum=1)
    gain = _coerce_float(settings.get("gain", 5.0), default=5.0, minimum=0.0)
    accumulations = _coerce_int(settings.get("accumulations", 1), default=1, minimum=1, maximum=64)
    combine_mode = str(settings.get("combine", "mean") or "mean").strip().lower()
    mode_str = str(settings.get("mode", DEFAULT_SPECTRAL_ACQUISITION["mode"]))
    requested_camera_index = _coerce_int(settings.get("camera_index", current_camera_index), default=current_camera_index, minimum=0)
    mode_width, mode_height, mode_bit_depth, mode_packing_type = _mode_dimensions(mode_str)
    capture_duration_ms = exposure_time // 1000 + 1000

    try:
        if requested_camera_index != current_camera_index:
            _init_picamera_unlocked(requested_camera_index)
        if picam2 is not None:
            if picam2.started:
                picam2.stop()
            picam2.close()
            picam2 = None
            picam2_was_managed = True

        temp_picam2 = Picamera2(camera_num=current_camera_index)
        chosen_sensor_mode = None
        for smode in temp_picam2.sensor_modes:
            if smode.get('size') == (mode_width, mode_height) and smode.get('bit_depth') == mode_bit_depth:
                chosen_sensor_mode = smode
                break
        if chosen_sensor_mode is None:
            raise RuntimeError(f"No matching sensor mode found for {mode_width}x{mode_height} at {mode_bit_depth}-bit")

        cfa_pattern = infer_cfa_pattern(
            chosen_sensor_mode.get('format'),
            chosen_sensor_mode.get('unpacked'),
            chosen_sensor_mode.get('colour_filter_array'),
            chosen_sensor_mode.get('cfa'),
        )
        raw_format_for_config = chosen_sensor_mode['unpacked'] if mode_packing_type == 'U' else chosen_sensor_mode['format']
        raw_capture_config = temp_picam2.create_still_configuration(
            raw={'format': raw_format_for_config, 'size': (mode_width, mode_height)},
            sensor={'output_size': (mode_width, mode_height), 'bit_depth': mode_bit_depth},
            display=None,
            buffer_count=2
        )
        temp_picam2.configure(raw_capture_config)

        # Match /rawframe CFA refinement after configure().
        try:
            stream_cfg = temp_picam2.stream_configuration('raw')
            fmt_candidate = None
            cfa_candidates = []
            if isinstance(stream_cfg, dict):
                fmt_candidate = stream_cfg.get('format')
                cfa_candidates.append(stream_cfg.get('colour_filter_array'))
                cfa_candidates.append(stream_cfg.get('cfa'))
            else:
                fmt_candidate = getattr(stream_cfg, 'format', None)
                cfa_candidates.append(getattr(stream_cfg, 'colour_filter_array', None))
                cfa_candidates.append(getattr(stream_cfg, 'cfa', None))
            refined_pattern = infer_cfa_pattern(
                fmt_candidate,
                *cfa_candidates,
                raw_capture_config.get('raw').get('format') if isinstance(raw_capture_config, dict) else None,
            )
            if refined_pattern:
                cfa_pattern = refined_pattern
        except Exception as cfg_exc:
            print(f"Warning: unable to refine CFA after configure: {cfg_exc}")

        temp_picam2.set_controls({"ExposureTime": exposure_time, "AnalogueGain": gain})
        temp_picam2.start()
        time.sleep(max(0.1, capture_duration_ms / 1000.0 / 2))

        raw_frames = []
        for _ in range(accumulations):
            request_obj = temp_picam2.capture_request()
            try:
                captured = np.array(request_obj.make_array('raw'), copy=True)
                captured = _normalize_raw_capture_like_core(captured, mode_str, 1)
                raw_frames.append(captured)
            finally:
                request_obj.release()
        if not raw_frames:
            raise RuntimeError("No raw frames captured")
        raw_stack = np.stack(raw_frames, axis=0)
        if combine_mode in {"mean", "avg", "average"}:
            combine_mode = "mean"
            accumulator = raw_stack.astype(np.uint32, copy=False)
            summed = accumulator.sum(axis=0, dtype=np.uint64)
            raw_data = ((summed + accumulations // 2) // accumulations).astype(raw_stack.dtype, copy=False)
        elif combine_mode in {"sum", "total"}:
            combine_mode = "sum"
            accumulator = raw_stack.astype(np.uint32, copy=False)
            raw_data = accumulator.sum(axis=0, dtype=np.uint64)
        else:
            combine_mode = "mean"
            accumulator = raw_stack.astype(np.uint32, copy=False)
            summed = accumulator.sum(axis=0, dtype=np.uint64)
            raw_data = ((summed + accumulations // 2) // accumulations).astype(raw_stack.dtype, copy=False)
        metadata = {
            "timestamp": now,
            "camera_index": current_camera_index,
            "mode": mode_str,
            "shape": list(np.shape(raw_data)),
            "dtype": str(raw_data.dtype),
            "cfa": cfa_pattern,
            "accumulations": accumulations,
            "combine": combine_mode,
            "exposure": exposure_time,
            "gain": gain,
        }
        return raw_data, metadata
    finally:
        if temp_picam2 is not None:
            try:
                if temp_picam2.started:
                    temp_picam2.stop()
                temp_picam2.close()
            except Exception as close_e:
                print(f"Error closing temporary Picamera2 instance: {close_e}")
        if picam2_was_managed:
            _init_picamera_unlocked(current_camera_index)


def _apply_spectral_acquisition_settings_locked(payload: Dict[str, Any] | None):
    settings = update_spectral_acquisition_settings(payload)
    requested_camera_index = _coerce_int(settings.get("camera_index", current_camera_index), default=current_camera_index, minimum=0)
    if requested_camera_index != current_camera_index:
        if not _init_picamera_unlocked(requested_camera_index):
            return {"status": "error", "message": f"Failed to switch to camera {requested_camera_index}"}, 500
    if picam2 is not None and picam2.started:
        try:
            picam2.set_controls({
                "ExposureTime": _coerce_int(settings.get("exposure", 2000000), default=2000000, minimum=1),
                "AnalogueGain": _coerce_float(settings.get("gain", 5.0), default=5.0, minimum=0.0),
            })
            time.sleep(0.05)
        except Exception as exc:
            print(f"Warning: unable to apply spectral exposure/gain to preview camera: {exc}")
    return {"status": "success", "settings": settings}, 200


def _spectral_capture_locked(payload: Dict[str, Any] | None):
    effective_settings = load_spectral_acquisition_settings()
    if payload:
        effective_settings = normalize_spectral_acquisition_settings({**effective_settings, **payload})
        for key in ("fullframe", "single_roi", "multiple_roi", "custom_roi"):
            if key in payload:
                effective_settings[key] = normalize_spectral_acquisition_settings({key: payload.get(key)})[key]
    raw_data, metadata = _capture_raw_data_from_settings(effective_settings)
    if raw_data.ndim == 3:
        frame_for_processing = raw_data.mean(axis=0, dtype=np.float64)
    else:
        frame_for_processing = raw_data.astype(np.float64, copy=False)
    spectra = _extract_spectra_from_frame(frame_for_processing, effective_settings, metadata.get("cfa", "BGGR"))
    return {
        "status": "success",
        "settings": effective_settings,
        "capture_metadata": metadata,
        "result": spectra,
    }, 200
# --- Global Variables ---
ALL_CAMERA_DETAILS = []  # Each item: {'index': int, 'Model': str, 'parsed_modes': [{'format': str, 'resolution': str, 'fps': str, 'crop_info': str}], ...}
picam2 = None  # Active Picamera2 object used for preview/recording endpoints.
preview_config = None  # Cached preview configuration reapplied after one-off captures.
current_camera_index = 0  # Camera index currently initialized on the shared Picamera2 instance.

# --- Recording Globals (Hardware H.264 encoding) ---
recording = False  # Flag indicating whether an H.264 recording session is active.
encoder = None  # Current H264Encoder instance used for hardware recording.
output = None  # FileOutput target handling encoded video packets.
record_filename = None  # Path to the file being written by the active recording.
record_lock = threading.Lock()  # Guards start/stop recording transitions across threads.
camera_lock = threading.Lock()  # Serializes all Picamera2 interactions to prevent races.


class CameraGuard:
    """Context manager that serializes access to the shared Picamera2 instance."""

    def __enter__(self):
        camera_lock.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        camera_lock.release()

# --- JSON Encoder --- may be remove it if not needed
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        """Fallback to string conversion when Flask cannot serialize an object."""
        try:
            return super().default(obj)
        except TypeError:
            # print(f"CustomJSONEncoder: Falling back to str() for type {type(obj)}") # Optional debug
            return str(obj)

app = Flask(__name__)
# app.json_encoder = CustomJSONEncoder # Deprecated in Flask 2.3. Modern Flask handles JSON well.

# --- Camera Mode Population (Python-only, without CLI commands) ---
def populate_all_camera_details():
    """Populate ALL_CAMERA_DETAILS with per-camera metadata and parsed sensor modes."""
    global ALL_CAMERA_DETAILS
    ALL_CAMERA_DETAILS = []
    basic_cam_infos = []

    print("populate_all_camera_details: reset cache and starting global camera query")

    try:
        # Step 1: Get basic camera information using Picamera2's global method.
        # This function identifies connected cameras.
        basic_cam_infos = Picamera2.global_camera_info()
        print(f"populate_all_camera_details: Picamera2.global_camera_info() returned {len(basic_cam_infos)} entries")
        if not basic_cam_infos:
            print("Picamera2.global_camera_info() returned no cameras.")
    except Exception as e:
        print(f"Fatal Error: Could not get Picamera2.global_camera_info(): {e}. Cannot proceed with camera setup.")
        return

    if not basic_cam_infos:
        print("populate_all_camera_details: aborting because basic camera info list is empty")
        return

    print("Attempting to retrieve sensor modes directly from Picamera2 objects...")
    for idx, cam_basic_info_dict in enumerate(basic_cam_infos):
        detailed_info = dict(cam_basic_info_dict)
        detailed_info['index'] = idx
        modes_for_this_cam = []
        temp_picam2_instance = None # Initialize temporary camera instance

        model_name = detailed_info.get('Model', 'N/A')
        print(f"populate_all_camera_details: scanning camera index {idx} (Model: {model_name})")

        try:
            # Step 2: For each detected camera, instantiate a temporary Picamera2 object
            # to access its sensor_modes property. This is a crucial step to get detailed modes.
            # As per picamera2-manual.pdf, querying sensor_modes may involve stopping/reconfiguring
            # the camera if it's already active.
            temp_picam2_instance = Picamera2(camera_num=idx)
            raw_sensor_modes_list = temp_picam2_instance.sensor_modes
            print(f"populate_all_camera_details: raw sensor mode count for index {idx} -> {len(raw_sensor_modes_list) if raw_sensor_modes_list else 0}")

            if raw_sensor_modes_list:
                # Step 3: Iterate through the raw sensor modes and format them
                # to match the structure expected by the original server code (similar to rpicam-hello output).
                for mode in raw_sensor_modes_list:
                    # 'format' might be a libcamera.formats.FMT object, convert to string
                    # 'size' is a tuple (width, height), format as "WxH"
                    # 'crop_limits' is a tuple (x, y, width, height)
                    size_tuple = mode.get('size', ('N/A','N/A'))
                    modes_for_this_cam.append({
                        'format': str(mode.get('format', 'N/A')),
                        'resolution': f"{size_tuple[0]}x{size_tuple[1]}",
                        'fps': str(mode.get('fps', 'N/A')),
                        'crop_info': str(mode.get('crop_limits', 'N/A'))
                    })
                print(f"Successfully retrieved and formatted modes for camera index {idx} (Model: {detailed_info.get('Model', 'N/A')}).")
            else:
                print(f"Note: No sensor modes reported for camera index {idx} (Model: {detailed_info.get('Model', 'N/A')}) via Picamera2.sensor_modes.")

        except Exception as e:
            print(f"Warning: An error occurred while getting sensor modes for camera index {idx}: {e}. Sensor modes unavailable for this camera.")
        finally:
            # Step 4: Ensure the temporary Picamera2 instance is closed to release resources.
            if temp_picam2_instance is not None:
                try:
                    temp_picam2_instance.close()
                except Exception as close_e:
                    print(f"Error closing temporary Picamera2 instance for index {idx}: {close_e}")

        detailed_info['parsed_modes'] = modes_for_this_cam
        ALL_CAMERA_DETAILS.append(detailed_info)
        print(f"populate_all_camera_details: camera index {idx} parsed_modes entries -> {len(modes_for_this_cam)}")

    if not ALL_CAMERA_DETAILS and basic_cam_infos:
        # Fallback if no detailed info could be gathered, but basic info exists.
        print("Fallback: Populating ALL_CAMERA_DETAILS with basic info only, as detailed scan failed.")
        for idx, cam_basic_info_dict in enumerate(basic_cam_infos):
            fallback_info = dict(cam_basic_info_dict)
            fallback_info['index'] = idx
            fallback_info['parsed_modes'] = [] # No parsed modes in fallback
            ALL_CAMERA_DETAILS.append(fallback_info)
            print(f"populate_all_camera_details: fallback entry added for index {idx}")

    print(f"populate_all_camera_details: finished with {len(ALL_CAMERA_DETAILS)} cached camera records")

# --- Picamera2 Initialization ---
def _init_picamera_unlocked(camera_index=0):
    """Initialize Picamera2 for the requested index and apply preview config."""
    global picam2, preview_config, current_camera_index, ALL_CAMERA_DETAILS

    if picam2 is not None: # Close existing instance if any
        try:
            if picam2.started: picam2.stop()
            picam2.close()
        except Exception as e: print(f"Error closing existing picam2: {e}")
        picam2 = None; print("Closed previous camera instance.")

    num_available_cameras = 0 # Get number of available cameras
    try:
        num_available_cameras = len(Picamera2.global_camera_info())
    except Exception as e:
        print(f"Critical error: Cannot get camera count: {e}"); current_camera_index = -1; return False
    # No cameras found
    if num_available_cameras == 0:
        print("No cameras found by Picamera2. Cannot initialize."); current_camera_index = -1; return False
    # Validate requested index
    # Reset to a safe default if caller asked for a camera index outside the detected range.
    if not (0 <= camera_index < num_available_cameras):
        print(f"Error: Requested cam index {camera_index} out of range ({num_available_cameras} cams).")
        camera_index = 0; print(f"Falling back to cam index {camera_index}.")
    
    # Guard against scenarios with zero cameras where the fallback to index 0 is still invalid.
    if not (0 <= camera_index < num_available_cameras):
        print(f"Fallback to cam index {camera_index} invalid."); current_camera_index = -1; return False

    print(f"Initializing Picamera2 with index {camera_index}...")
    try:
        picam2 = Picamera2(camera_num=camera_index)
        # Create config with main (RGB for recording) and lores (BGR for preview)
        preview_config = picam2.create_video_configuration(
            main={"size": (1920, 1080), "format": "RGB888"},
            lores={"size": (640, 480), "format": "BGR888"}
        )
        picam2.configure(preview_config); picam2.start()
        current_camera_index = camera_index
        # Set auto white balance to auto for general use
        picam2.set_controls({"AwbMode": libcamera.controls.AwbModeEnum.Auto})
        # Increased time to ensure proper exposure and white balance settings.
        time.sleep(3.0) # Wait for LED exposure to settle
        # Optional: Lock white balance to current gains after LED stabilization
        led_gains = picam2.capture_metadata()['ColourGains']
        #disable auto white balance and lock gains
        #picam2.set_controls({"AwbEnable": False, "ColourGains": led_gains})
        #print(f"White Balance locked for LED at: {led_gains}")
        # -----------------------------
        # Log successful initialization
        cam_model = "Unknown Model"
        # Get model name from cached details if available
        if ALL_CAMERA_DETAILS and 0 <= camera_index < len(ALL_CAMERA_DETAILS):
            cam_model = ALL_CAMERA_DETAILS[camera_index].get('Model', 'Unknown Model')
        print(f"Picamera2 initialized for cam {camera_index} ({cam_model}).")
        return True
    except Exception as e:
        print(f"Failed to init Picamera2 for index {camera_index}: {e}")
        import traceback; traceback.print_exc()
        picam2 = None; preview_config = None
        return False

# --- Recording Helper (hardware encoder uses Picamera2's built-in threading) ---
# No manual worker thread needed - H264Encoder handles this

# --- Thread-safe Picamera2 Initialization Wrapper ---
def init_picamera(camera_index=0):
    """Thread-safe wrapper for _init_picamera_unlocked."""
    with CameraGuard():
        return _init_picamera_unlocked(camera_index)

# --- Flask App Startup ---
@app.before_first_request
def startup_operations():
    """Perform initial camera scans and open the default device."""
    global current_camera_index
    print("Flask app starting - performing startup operations...")
    load_spectral_acquisition_settings()
    populate_all_camera_details()
    if not init_picamera(current_camera_index):
        print("Initial camera setup FAILED during startup. Check logs.")
    print(f"Startup complete. ALL_CAMERA_DETAILS entries: {len(ALL_CAMERA_DETAILS)}")

# --- Flask Routes ---
@app.route('/switch_camera/<int:index>')
def switch_camera_endpoint(index):
    """Flask route: switch active camera to the requested index."""
    num_available_cameras = 0
    try: num_available_cameras = len(Picamera2.global_camera_info())
    except: pass
    if not (0 <= index < num_available_cameras):
        return jsonify({"status": "error", "message": f"Cam index {index} out of range for {num_available_cameras} cams."}), 400

    if picam2 and index == current_camera_index and picam2.started:
        return jsonify({"status": "success", "message": f"Camera {index} is already active."}), 200

    print(f"Switching to camera index: {index}")
    if init_picamera(index):
        model_name = "N/A"
        if ALL_CAMERA_DETAILS and 0 <= index < len(ALL_CAMERA_DETAILS):
            model_name = ALL_CAMERA_DETAILS[index].get('Model','N/A')
        return jsonify({"status": "success", "message": f"Switched to camera {index} ({model_name})."}), 200
    else:
        return jsonify({"status": "error", "message": f"Failed to switch to camera {index}. Check server logs."}), 500

@app.route('/available_cameras')
def list_cameras_endpoint():
    """Flask route: return the cached camera and sensor mode catalog."""
    if ALL_CAMERA_DETAILS: return jsonify(ALL_CAMERA_DETAILS)
    else:
        basic_info = []; error_msg = "Camera details scan incomplete."
        try: basic_info = [dict(info) for info in Picamera2.global_camera_info()]
        except Exception as e: error_msg = f"Error getting basic camera info: {e}"
        if basic_info: return jsonify(basic_info)
        return jsonify({"message": error_msg, "details": "No cameras found or full scan failed."}), 404

@app.route('/video')
def video_stream():
    """Flask route: MJPEG streaming endpoint backed by the preview stream."""
    with CameraGuard():
        if picam2 is None or not picam2.started:
            return "Camera not initialized or not started. Try /switch_camera/ or check logs.", 503

        exposure_str = request.args.get("exposure")
        if exposure_str:
            try:
                exposure_val = int(float(exposure_str))
                if exposure_val > 0:
                    picam2.set_controls({"ExposureTime": exposure_val})
                    time.sleep(0.05)
            except Exception as e:
                print(f"Error setting exposure for /video: {e}")

    def generate():
        """Yield multipart JPEG frames for the MJPEG response."""
        while True:
            try:
                with CameraGuard():
                    if picam2 is None or not picam2.started:
                        break
                    # Use main stream to match what H.264 encoder sees
                    frame = picam2.capture_array("main")
                # main is RGB888, encode without conversion
                _, jpeg = cv2.imencode('.jpg', frame)
                yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            except Exception as e: print(f"Error in /video generate: {e}"); break

    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')

def _apply_recording_settings_locked(payload: Dict[str, Any]):
    global picam2, preview_config

    if picam2 is None or not picam2.started:
        return {"status": "error", "message": "Camera not started."}, 503

    # Handle resolution reconfiguration
    resolution = payload.get("resolution")
    selected_resolution = resolution if resolution else None

    if selected_resolution:
        try:
            width, height = map(int, selected_resolution.split('x'))
            if picam2.started:
                picam2.stop()

            new_config = picam2.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"}
            )
            picam2.configure(new_config)
            picam2.start()
            preview_config = new_config
            print(f"Reconfigured preview to {width}x{height}")
        except Exception as e:
            print(f"Warning: Failed to reconfigure resolution: {e}")
            selected_resolution = None

    exposure_mode = payload.get("exposure_mode") or "auto"
    exposure_time = payload.get("exposure_time")
    exposure_info = None

    try:
        if exposure_mode == "manual" and exposure_time:
            exposure_us = int(exposure_time)
            #picam2.set_controls({"ExposureTime": exposure_us})
            picam2.set_controls({"AeEnable": False, "ExposureTime": exposure_us})
            exposure_info = f"manual {exposure_us}µs"
            print(f"Set preview manual exposure to {exposure_us} microseconds")
        else:
            picam2.set_controls({"AeEnable": True})
            time.sleep(0.5)
            try:
                metadata = picam2.capture_metadata()
                actual_exposure = metadata.get('ExposureTime', 'unknown')
                actual_gain = metadata.get('AnalogueGain', 'unknown')
                exposure_info = f"auto (exposure: {actual_exposure}µs, gain: {actual_gain}x)"
                print(f"Auto exposure set - Exposure: {actual_exposure}µs, Gain: {actual_gain}x")
            except Exception as meta_e:
                print(f"Warning: Could not read exposure/gain metadata: {meta_e}")
                exposure_info = "auto"
    except Exception as e:
        print(f"Warning: Failed to set exposure: {e}")
        return {"status": "error", "message": f"Failed to apply exposure: {e}"}, 500

    gain_mode = payload.get("gain_mode") or "auto"
    gain_value = payload.get("gain_value")
    gain_info = None

    try:
        if gain_mode == "manual" and gain_value:
            gain_val = float(gain_value)
            picam2.set_controls({"AnalogueGain": gain_val})
            gain_info = f"manual {gain_val}x"
            print(f"Set preview manual gain to {gain_val}x")
        else:
            if exposure_mode != "manual":
                time.sleep(0.3)
            try:
                metadata = picam2.capture_metadata()
                actual_gain = metadata.get('AnalogueGain', 'unknown')
                gain_info = f"auto {actual_gain}x"
            except Exception as meta_e:
                print(f"Warning: Could not read gain metadata: {meta_e}")
                gain_info = "auto"
    except Exception as e:
        print(f"Warning: Failed to set gain: {e}")
        return {"status": "error", "message": f"Failed to apply gain: {e}"}, 500

    return {"status": "success", "resolution": selected_resolution or "current", "exposure_mode": exposure_info, "gain_mode": gain_info}, 200


def _capture_snapshot_locked(download_format: str):
    global picam2, preview_config

    include_preview = download_format == 'zip'
    create_zip = download_format == 'zip'

    files_to_cleanup = []
    debayed_file_path = None
    zip_file_path = None

    # Stop camera FIRST before getting sensor modes
    if picam2.started:
        picam2.stop()

    max_resolution = None
    max_area = 0
    max_bit_depth = 0
    target_mode = None

    try:
        sensor_modes = picam2.sensor_modes
        for i, mode in enumerate(sensor_modes):
            size = mode.get('size')
            if size:
                width, height = size
                area = width * height
                bit_depth = mode.get('bit_depth', 0)

                if area > max_area or (area == max_area and bit_depth > max_bit_depth):
                    max_area = area
                    max_resolution = (width, height)
                    max_bit_depth = bit_depth
                    target_mode = i

        if max_resolution:
            print(f"Maximum sensor resolution detected: {max_resolution[0]}x{max_resolution[1]} ({max_bit_depth}-bit) at sensor mode index {target_mode}")
        else:
            max_resolution = (1920, 1080)
            print(f"Could not detect max resolution, using default: {max_resolution}")
    except Exception as e:
        print(f"Warning: Could not get sensor modes: {e}")
        max_resolution = (1920, 1080)

    try:
        raw_config = picam2.create_still_configuration(
            raw={"size": max_resolution},
            main={"size": max_resolution, "format": "RGB888"},
            queue=True
        )
        picam2.configure(raw_config)
        picam2.start()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        os.makedirs("snapshots", exist_ok=True)

        request_obj = picam2.capture_request()
        raw_array = request_obj.make_array("raw")
        main_array = None
        try:
            main_array = request_obj.make_array("main")
        except Exception:
            main_array = None
        request_obj.release()

        actual_height, actual_width = raw_array.shape[0], raw_array.shape[1]
        print(f"Captured raw array shape: {raw_array.shape}, dtype: {raw_array.dtype}")
        print(f"Actual resolution captured: {actual_width}x{actual_height}")
        print(f"Requested resolution: {max_resolution[0]}x{max_resolution[1]}")

        raw_file_name = f"snapshot_{timestamp}_raw12bit_{max_resolution[0]}x{max_resolution[1]}.tiff"
        raw_file_path = os.path.join("snapshots", raw_file_name)

        if raw_array.dtype == np.uint16:
            raw_16bit = raw_array
        else:
            raw_16bit = (raw_array.astype(np.uint32) << 4).astype(np.uint16)

        success_raw = cv2.imwrite(raw_file_path, raw_16bit)
        if not success_raw:
            return jsonify({"status": "error", "message": "Failed to encode raw snapshot."}), 500

        print(f"Raw 12-bit Bayer snapshot (no demosaicing) saved at {max_resolution[0]}x{max_resolution[1]}: {raw_file_path}")
        files_to_cleanup.append(raw_file_path)

        if include_preview and main_array is not None and len(main_array.shape) == 3 and main_array.shape[2] == 3:
            try:
                debayed_bgr = cv2.cvtColor(main_array, cv2.COLOR_RGB2BGR)
                debayed_file_name = f"snapshot_{timestamp}_preview_{max_resolution[0]}x{max_resolution[1]}.jpg"
                debayed_file_path = os.path.join("snapshots", debayed_file_name)
                if cv2.imwrite(debayed_file_path, debayed_bgr, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                    print(f"Preview JPEG saved: {debayed_file_path}")
                    files_to_cleanup.append(debayed_file_path)
                else:
                    print("Warning: Failed to save preview JPEG")
                    debayed_file_path = None
            except Exception as debayed_e:
                print(f"Warning: Could not create preview JPEG: {debayed_e}")
                debayed_file_path = None

        response_path = raw_file_path
        response_name = raw_file_name
        response_mimetype = 'image/tiff'

        if create_zip:
            zip_file_name = f"snapshot_{timestamp}_raw_{max_resolution[0]}x{max_resolution[1]}.zip"
            zip_file_path = os.path.join("snapshots", zip_file_name)

            with zipfile.ZipFile(zip_file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(raw_file_path, os.path.basename(raw_file_path))
                if debayed_file_path and os.path.isfile(debayed_file_path):
                    zipf.write(debayed_file_path, os.path.basename(debayed_file_path))

            print(f"Created ZIP archive with snapshot files: {zip_file_path}")
            response_path = zip_file_path
            response_name = zip_file_name
            response_mimetype = 'application/zip'
            files_to_cleanup.append(zip_file_path)

    except Exception as e:
        print(f"Raw 12-bit capture failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"Failed to capture raw 12-bit snapshot: {e}"}), 500
    finally:
        @after_this_request
        def schedule_cleanup(response):
            def delayed_cleanup():
                time.sleep(60.0)
                try:
                    for path in files_to_cleanup:
                        if path and os.path.isfile(path):
                            os.remove(path)
                            print(f"Deleted snapshot artifact: {path}")
                except Exception as e:
                    print(f"Cleanup error: {e}")

            threading.Thread(target=delayed_cleanup, daemon=True).start()
            return response

        try:
            if picam2.started:
                picam2.stop()
            picam2.configure(preview_config)
            picam2.start()
            print("Camera restored to preview configuration")
        except Exception as e:
            print(f"Warning: Could not restore preview config: {e}")

    return send_file(
        response_path,
        mimetype=response_mimetype,
        as_attachment=True,
        download_name=response_name
    )


def _capture_rawframe_locked():
    global picam2, current_camera_index, preview_config

    # --- FIX: Force switch to the requested Photo camera ---
    req_cam_str = request.args.get("camera_index")
    if req_cam_str is not None:
        req_cam_idx = int(req_cam_str)
        if req_cam_idx != current_camera_index:
            print(f"RAW capture forcing switch to camera {req_cam_idx}")
            _init_picamera_unlocked(req_cam_idx)
    # -------------------------------------------------------
    print(f"--- Enter /rawframe for camera {current_camera_index} ---")
    now = datetime.now().strftime("%y%m%d_%H%M%S")

    exposure_str = request.args.get("exposure", "2000000")
    gain_str = request.args.get("gain", "5.0")
    duration_str = request.args.get("duration")
    accumulations_str = request.args.get("accumulations", "1")
    mode_str = request.args.get("mode", "4056:3040:12:U")

    temp_picam2 = None
    picam2_was_managed = False

    combine_mode = (request.args.get("combine", "stack") or "stack").strip().lower()

    try:
        exposure_time = int(float(exposure_str))
        gain = float(gain_str)

        mode_parts = mode_str.split(':')
        if len(mode_parts) != 4:
            raise ValueError("Invalid mode format. Expected W:H:B:P_or_U (e.g., 4056:3040:12:U)")

        mode_width = int(mode_parts[0])
        mode_height = int(mode_parts[1])
        mode_bit_depth = int(mode_parts[2])
        mode_packing_type = mode_parts[3].upper()

        if duration_str:
            capture_duration_ms = int(float(duration_str))
        else:
            capture_duration_ms = exposure_time // 1000 + 1000

        try:
            accumulations = int(accumulations_str)
        except (TypeError, ValueError):
            accumulations = 1

        if accumulations < 1:
            accumulations = 1
        if accumulations > 64:
            print(f"Requested accumulations {accumulations} exceeds cap; limiting to 64")
            accumulations = 64

        print(f"Accumulation count for this capture: {accumulations}")

        print(f"Parsed parameters: Width={mode_width}," 
              f"Height={mode_height}, "
              f"BitDepth={mode_bit_depth}, "
              f"Packing={mode_packing_type}, "
              f"Exposure={exposure_time}us, "
              f"Gain={gain}, "
              f"Duration={capture_duration_ms}ms")

        if picam2 is not None:
            print("Stopping/closing active Picamera2 instance for raw capture...")
            if picam2.started:
                picam2.stop()
            picam2.close()
            picam2 = None
            picam2_was_managed = True
            print("Active Picamera2 instance closed.")

        temp_picam2 = Picamera2(camera_num=current_camera_index)

        chosen_sensor_mode = None
        print("Inspecting available sensor modes for raw capture...")
        for idx, smode in enumerate(temp_picam2.sensor_modes):
            mode_size = smode.get('size')
            mode_depth = smode.get('bit_depth')
            mode_format = smode.get('format')
            print(
                f"  Mode {idx}: size={mode_size}, bit_depth={mode_depth}, format={mode_format}, "
                f"unpacked={smode.get('unpacked')}, cfa={smode.get('colour_filter_array') or smode.get('cfa')}"
            )
            if mode_size == (mode_width, mode_height) and mode_depth == mode_bit_depth:
                chosen_sensor_mode = smode
                print(f"--> Selected mode {idx} for capture")
                break

        if chosen_sensor_mode is None:
            temp_picam2.close()
            return f"Error: No matching sensor mode found for {mode_width}x{mode_height} at {mode_bit_depth}-bit.", 500

        cfa_pattern = infer_cfa_pattern(
            chosen_sensor_mode.get('format'),
            chosen_sensor_mode.get('unpacked'),
            chosen_sensor_mode.get('colour_filter_array'),
            chosen_sensor_mode.get('cfa'),
        )
        print(
            "Derived CFA pattern:",
            cfa_pattern,
            "from format=",
            chosen_sensor_mode.get('format'),
            "unpacked=",
            chosen_sensor_mode.get('unpacked'),
            "cfa entries=",
            chosen_sensor_mode.get('colour_filter_array'),
            chosen_sensor_mode.get('cfa'),
        )

        raw_format_for_config = chosen_sensor_mode['unpacked'] if mode_packing_type == 'U' else chosen_sensor_mode['format']
        print(f"Using raw format {raw_format_for_config} for capture (packing {mode_packing_type})")

        raw_capture_config = temp_picam2.create_still_configuration(
            raw={'format': raw_format_for_config, 'size': (mode_width, mode_height)},
            sensor={'output_size': (mode_width, mode_height), 'bit_depth': mode_bit_depth},
            display=None,
            buffer_count=2
        )

        temp_picam2.configure(raw_capture_config)

        stream_cfg = None
        try:
            stream_cfg = temp_picam2.stream_configuration('raw')
        except Exception as stream_cfg_error:
            print(f"Warning: Could not read raw stream configuration: {stream_cfg_error}")

        if stream_cfg is not None:
            fmt_candidate = None
            cfa_candidates = []

            if isinstance(stream_cfg, dict):
                fmt_candidate = stream_cfg.get('format')
                cfa_candidates.append(stream_cfg.get('colour_filter_array'))
                cfa_candidates.append(stream_cfg.get('cfa'))
            else:
                fmt_candidate = getattr(stream_cfg, 'format', None)
                cfa_candidates.append(getattr(stream_cfg, 'colour_filter_array', None))
                cfa_candidates.append(getattr(stream_cfg, 'cfa', None))

            refined_pattern = infer_cfa_pattern(
                fmt_candidate,
                *cfa_candidates,
                raw_capture_config.get('raw').get('format') if isinstance(raw_capture_config, dict) else None,
            )

            if refined_pattern and refined_pattern != cfa_pattern:
                print(f"Updated CFA pattern after configure: {refined_pattern} (was {cfa_pattern})")
                cfa_pattern = refined_pattern

        controls_to_set = {"ExposureTime": exposure_time, 
                           "AnalogueGain": gain}
        temp_picam2.set_controls(controls_to_set)

        print("Starting temporary Picamera2 instance for raw capture...")
        temp_picam2.start()

        time.sleep(max(0.1, capture_duration_ms / 1000.0 / 2))

        raw_frames = []
        for frame_idx in range(accumulations):
            print(f"Capturing raw frame {frame_idx + 1}/{accumulations}...")
            request_obj = temp_picam2.capture_request()
            try:
                frame_array = request_obj.make_array('raw')
                # --- FIX: Convert 8-bit bytes to true 16-bit pixels BEFORE appending/summing! ---
                #captured = np.array(frame_array, copy=True)
                #captured = _normalize_raw_capture_like_core(captured, mode_str, 1)
                #raw_frames.append(captured)
                # --------------------------------------------------------------------------------
                raw_frames.append(np.array(frame_array, copy=True))
            finally:
                request_obj.release()

        if not raw_frames:
            raise RuntimeError("No raw frames captured")

        raw_stack = np.stack(raw_frames, axis=0)

        if combine_mode in {"mean", "avg", "average"}:
            print(f"Combining {accumulations} frames using mean before download")
            accumulator = raw_stack.astype(np.uint32, copy=False)
            summed = accumulator.sum(axis=0, dtype=np.uint64)
            combined = ((summed + accumulations // 2) // accumulations).astype(raw_stack.dtype, copy=False)
            raw_stack = combined
        elif combine_mode in {"sum", "total"}:
            print(f"Combining {accumulations} frames using sum before download")
            accumulator = raw_stack.astype(np.uint32, copy=False)
            raw_stack = accumulator.sum(axis=0, dtype=np.uint64)
        else:
            combine_mode = "stack"

        raw_bytes_output = raw_stack.tobytes()

        output_filename = (
            f"capture_cam{current_camera_index}_{mode_width}x{mode_height}_{mode_bit_depth}bit"
            f"_acc{accumulations}_{now}.raw"
        )

        print(
            f"Raw stack captured. Frames: {accumulations}, per-frame bytes: {raw_frames[0].nbytes}, "
            f"total size: {len(raw_bytes_output)} bytes."
        )
        response = send_file(
            io.BytesIO(raw_bytes_output),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=output_filename,
        )
        response.headers["X-Raw-Shape"] = "x".join(str(dim) for dim in np.shape(raw_stack))
        response.headers["X-Raw-DType"] = str(raw_stack.dtype)
        response.headers["X-Raw-Mode"] = mode_str
        response.headers["X-Raw-CFA"] = cfa_pattern
        response.headers["X-Raw-Accumulations"] = str(accumulations)
        response.headers["X-Raw-Combine"] = combine_mode
        return response

    except Exception as e:
        print("!!!!!! Python exception in /rawframe try block !!!!!!")
        print(f"Traceback: {traceback.format_exc()}")
        return f"Unexpected server error in /rawframe: {e}", 500
    finally:
        print("--- /rawframe finally block ---")
        if temp_picam2 is not None:
            try:
                if temp_picam2.started:
                    temp_picam2.stop()
                temp_picam2.close()
                print("Temporary Picamera2 instance closed.")
            except Exception as close_e:
                print(f"Error closing temporary Picamera2 instance: {close_e}")

        if picam2_was_managed:
            print(f"Attempting to re-initialize main Picamera2 on camera {current_camera_index} to preview state...")
            if not _init_picamera_unlocked(current_camera_index):
                print(f"CRITICAL: Failed to restart main Picamera2 on cam {current_camera_index} after /rawframe operation.")

        print("--- Exit /rawframe ---")


@app.route('/apply_recording_settings', methods=['POST'])
def apply_recording_settings_endpoint():
    """Flask route: apply manual exposure, gain, and preview resolution."""
    payload = request.get_json(silent=True) or {}
    with CameraGuard():
        body, status_code = _apply_recording_settings_locked(payload)
    return jsonify(body), status_code

@app.route('/settings/spectral_acquisition', methods=['GET'])
def get_spectral_acquisition_settings_endpoint():
    return jsonify({"status": "success", "settings": load_spectral_acquisition_settings()})


@app.route('/settings/spectral_acquisition', methods=['POST'])
def set_spectral_acquisition_settings_endpoint():
    payload = request.get_json(silent=True) or {}
    with CameraGuard():
        body, status_code = _apply_spectral_acquisition_settings_locked(payload)
    return jsonify(body), status_code


@app.route('/spectral_capture', methods=['POST'])
def spectral_capture_endpoint():
    payload = request.get_json(silent=True) or {}
    with CameraGuard():
        body, status_code = _spectral_capture_locked(payload)
    return jsonify(body), status_code


@app.route('/capture_snapshot', methods=['GET', 'POST'])
def capture_snapshot_endpoint():
    """Flask route: grab a still JPEG from the main stream with optional save."""
    global picam2, preview_config

    if picam2 is None:
        return jsonify({"status": "error", "message": "Camera not initialized."}), 503
    download_format = (request.args.get('format') or request.form.get('format') or 'zip').lower()
    if download_format not in ('zip', 'raw'):
        download_format = 'zip'

    with CameraGuard():
        try:
            return _capture_snapshot_locked(download_format)
        except Exception as e:
            print(f"Error capturing snapshot: {e}")
            try:
                if picam2 and preview_config:
                    if picam2.started:
                        picam2.stop()
                    picam2.configure(preview_config)
                    picam2.start()
            except Exception as restore_e:
                print(f"Warning: Failed to restore camera after snapshot error: {restore_e}")
            return jsonify({"status": "error", "message": f"Failed to capture snapshot: {e}"}), 500

@app.route('/capture_dng')
def capture_dng_endpoint():
    """Flask route: capture a RAW+DNG file using Picamera2's still pipeline."""
    global picam2, preview_config, current_camera_index
    
    with CameraGuard():
        # --- ADD THIS: Force switch if DNG requested a different camera ---
        req_cam_str = request.args.get("camera_index")
        if req_cam_str is not None:
            req_cam_idx = int(req_cam_str)
            if req_cam_idx != current_camera_index:
                print(f"DNG capture forcing switch to camera {req_cam_idx}")
                if not _init_picamera_unlocked(req_cam_idx):
                    return jsonify({"status": "error", "message": "Failed to switch camera."}), 500
        # ------------------------------------------------------------------

        if picam2 is None:
            return jsonify({"status": "error", "message": "Camera not initialized."}), 503
        with CameraGuard():
            capture_request = None
            dng_path = None
            still_configured = False

            try:
                if picam2.started:
                    picam2.stop()

                still_config = picam2.create_still_configuration(raw={}, display=None)
                picam2.configure(still_config)
                still_configured = True
                picam2.start()

                capture_request = picam2.capture_request()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                os.makedirs("snapshots", exist_ok=True)
                dng_path = os.path.join("snapshots", f"capture_{timestamp}.dng")
                capture_request.save_dng(dng_path)

                capture_request.release()
                capture_request = None

                picam2.stop()
                if preview_config is not None:
                    picam2.configure(preview_config)
                picam2.start()

                if dng_path:
                    @after_this_request
                    def cleanup(response):
                        try:
                            if os.path.exists(dng_path):
                                os.remove(dng_path)
                        except OSError as cleanup_err:
                            print(f"DNG cleanup error: {cleanup_err}")
                        return response

                return send_file(dng_path, as_attachment=True, download_name=os.path.basename(dng_path))

            except Exception as e:
                print(f"DNG Capture Error: {e}")
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)}), 500

            finally:
                if capture_request is not None:
                    try:
                        capture_request.release()
                    except Exception as release_err:
                        print(f"DNG release error: {release_err}")

                if still_configured and preview_config is not None:
                    try:
                        if picam2.started:
                            picam2.stop()
                        picam2.configure(preview_config)
                        picam2.start()
                    except Exception as restore_err:
                        print(f"DNG preview restore error: {restore_err}")

@app.route('/start_recording', methods=['POST'])
def start_recording_endpoint():
    """Flask route: begin hardware-encoded video recording to disk."""
    global recording, encoder, output, record_filename, picam2, preview_config, current_camera_index

    if picam2 is None or not picam2.started:
        return jsonify({"status": "error", "message": "Camera not started."}), 503

    with record_lock:
        if recording:
            return jsonify({"status": "already_recording", "file": record_filename, "file_name": os.path.basename(record_filename) if record_filename else None}), 200

        os.makedirs("recordings", exist_ok=True)

        payload = request.get_json(silent=True) or {}
        base_name = payload.get("base") or request.form.get("base") or request.args.get("base")
        if base_name:
            base_name = os.path.splitext(os.path.basename(base_name))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_stem = base_name if base_name else f"recording_{timestamp}_cam{current_camera_index}"

        # Handle resolution reconfiguration
        resolution = payload.get("resolution") or request.form.get("resolution") or request.args.get("resolution")
        selected_resolution = resolution if resolution else None
        
        if selected_resolution:
            try:
                # Parse resolution string (e.g., "1920x1080")
                width, height = map(int, selected_resolution.split('x'))
                
                # Reconfigure camera with selected resolution
                if picam2.started:
                    picam2.stop()
                
                new_config = picam2.create_video_configuration(
                    main={"size": (width, height), "format": "RGB888"}
                )
                picam2.configure(new_config)
                picam2.start()
                
                print(f"Reconfigured camera to {width}x{height} for recording")
            except Exception as e:
                print(f"Warning: Failed to reconfigure resolution: {e}")
                # Continue with current resolution
                selected_resolution = None

        # Handle exposure settings
        exposure_mode = payload.get("exposure_mode") or request.form.get("exposure_mode") or "auto"
        exposure_time = payload.get("exposure_time") or request.form.get("exposure_time")
        exposure_info = None
        
        try:
            if exposure_mode == "manual" and exposure_time:
                exposure_us = int(exposure_time)
                #picam2.set_controls({"ExposureTime": exposure_us})
                picam2.set_controls({"AeEnable": False, "ExposureTime": exposure_us})
                exposure_info = f"manual {exposure_us}µs"
                print(f"Set manual exposure to {exposure_us} microseconds")
            else:
                # Auto exposure - let camera settle then read back actual values
                picam2.set_controls({"AeEnable": True})
                time.sleep(0.5)  # Give auto exposure time to stabilize
                
                # Read back actual exposure and gain
                try:
                    metadata = picam2.capture_metadata()
                    actual_exposure = metadata.get('ExposureTime', 'unknown')
                    actual_gain = metadata.get('AnalogueGain', 'unknown')
                    exposure_info = f"auto (exposure: {actual_exposure}µs, gain: {actual_gain}x)"
                    print(f"Auto exposure set - Exposure: {actual_exposure}µs, Gain: {actual_gain}x")
                except Exception as meta_e:
                    print(f"Warning: Could not read exposure/gain metadata: {meta_e}")
                    exposure_info = "auto"
        except Exception as e:
            print(f"Warning: Failed to set exposure: {e}")
        
        # Handle gain settings
        gain_mode = payload.get("gain_mode") or request.form.get("gain_mode") or "auto"
        gain_value = payload.get("gain_value") or request.form.get("gain_value")
        gain_info = None
        
        try:
            if gain_mode == "manual" and gain_value:
                gain_val = float(gain_value)
                picam2.set_controls({"AnalogueGain": gain_val})
                gain_info = f"manual {gain_val}x"
                print(f"Set manual gain to {gain_val}x")
            else:
                # Auto gain - read back current value
                if not (exposure_mode == "manual"):
                    time.sleep(0.3)
                try:
                    metadata = picam2.capture_metadata()
                    actual_gain = metadata.get('AnalogueGain', 'unknown')
                    gain_info = f"auto {actual_gain}x"
                except Exception as meta_e:
                    print(f"Warning: Could not read gain metadata: {meta_e}")
                    gain_info = "auto"
        except Exception as e:
            print(f"Warning: Failed to set gain: {e}")

        # Use H.264 format (raw bitstream)
        file_name = f"{file_stem}.h264"
        file_path = os.path.join("recordings", file_name)

        try:
            # Hardware H.264 encoder with 10 Mbps bitrate
            encoder = H264Encoder(bitrate=10_000_000)
            output = FileOutput(file_path)

            # Start hardware recording (non-blocking, runs in encoder thread)
            picam2.start_recording(encoder, output)

            recording = True
            record_filename = file_path

            return jsonify({"status": "recording_started", "file": file_path, "file_name": file_name, "resolution": selected_resolution or "auto", "exposure_mode": exposure_info, "gain_mode": gain_info}), 200
        except Exception as e:
            recording = False
            encoder = None
            output = None
            return jsonify({"status": "error", "message": f"Failed to start recording: {e}"}), 500

@app.route('/stop_recording', methods=['POST'])
def stop_recording_endpoint():
    """Flask route: stop an active recording session and close files."""
    global recording, encoder, output, record_filename, picam2, preview_config, current_camera_index

    if picam2 is None:
        return jsonify({"status": "error", "message": "Camera not initialized."}), 503

    with record_lock:
        if not recording:
            return jsonify({"status": "not_recording"}), 200

        try:
            picam2.stop_recording()
        except Exception as e:
            # Clear state to avoid getting stuck
            recording = False
            encoder = None
            output = None
            return jsonify({"status": "error", "message": f"Error stopping recording: {e}"}), 500

        fname = record_filename
        file_name = os.path.basename(fname) if fname else None
        recording = False
        encoder = None
        output = None
        record_filename = None

    # Restore camera to default video configuration
    try:
        if picam2.started:
            picam2.stop()
        
        # Reconfigure to default video configuration
        preview_config = picam2.create_video_configuration(
            main={"size": (1920, 1080), "format": "RGB888"}
        )
        picam2.configure(preview_config)
        picam2.start()
        print(f"Camera restored to default 1920x1080 configuration after recording")
    except Exception as e:
        print(f"Warning: Failed to restore camera after recording: {e}")

    return jsonify({"status": "stopped", "file": fname, "file_name": file_name}), 200

@app.route('/download_recording')
def download_recording_endpoint():
    """Flask route: stream the most recent H.264 recording as a download."""
    file_param = request.args.get('file')
    if not file_param:
        return jsonify({"status": "error", "message": "Missing file parameter."}), 400

    safe_name = os.path.basename(file_param)
    full_path = os.path.join("recordings", safe_name)

    if not os.path.isfile(full_path):
        return jsonify({"status": "error", "message": "File not found."}), 404

    def delayed_cleanup():
        """Delete file after 5 seconds to allow download to complete."""
        time.sleep(5.0)
        try:
            if os.path.isfile(full_path):
                os.remove(full_path)
                print(f"Deleted recording after download: {full_path}")
        except Exception as e:
            print(f"Cleanup error removing {full_path}: {e}")

    # Start cleanup in background thread
    threading.Thread(target=delayed_cleanup, daemon=True).start()

    # Determine MIME type based on extension
    mime = "video/mp4" if safe_name.endswith(".mp4") else "video/h264"
    return send_file(full_path, mimetype=mime, as_attachment=True, download_name=safe_name)

@app.route('/record')
def record_page():
    """Flask route: render a minimal HTML control panel for recording."""
    global current_camera_index, ALL_CAMERA_DETAILS
    
    # Get available resolutions from current camera
    resolutions_html = '<option value="">Auto (camera default)</option>'
    if current_camera_index >= 0 and current_camera_index < len(ALL_CAMERA_DETAILS):
        cam_detail = ALL_CAMERA_DETAILS[current_camera_index]
        modes = cam_detail.get('parsed_modes', [])
        # Extract unique resolutions
        seen_res = set()
        for mode in modes:
            res = mode.get('resolution', '')
            if res and res not in seen_res:
                seen_res.add(res)
                resolutions_html += f'<option value="{res}">{res}</option>'
    
    # Get gain range from camera
    gain_range_str = "N/A"
    if picam2 is not None and picam2.started:
        try:
            ctrls_meta = picam2.camera_ctrl_info
            gain_info = ctrls_meta.get("AnalogueGain")
            if gain_info and isinstance(gain_info, tuple) and len(gain_info) >= 2:
                gain_range_str = f"{gain_info[0]} - {gain_info[1]}"
        except Exception as e:
            print(f"Could not get gain range: {e}")
    
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Recording Controls</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>
        body {{ padding: 20px; }}
        .preview-frame {{ max-width: 100%; border: 1px solid #ccc; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <h2 class="my-4">Recording Controls</h2>
        <div class="mb-3">
            <img id="liveStream" class="preview-frame" src="/video" alt="Live stream" />
        </div>
        <div class="form-group">
            <label for="baseInput">File base name (optional, no extension):</label>
            <input id="baseInput" type="text" class="form-control" placeholder="e.g., session1">
            <small class="form-text text-muted">
                Hardware H.264 encoding at 10 Mbps. Files saved as .h264 format.<br>
                To convert to MP4: <code>ffmpeg -framerate 30 -i file.h264 -c copy file.mp4</code>
            </small>
        </div>
        <div class="form-group">
            <label for="resolutionSelect">Recording Resolution:</label>
            <select id="resolutionSelect" class="form-control">
                {resolutions_html}
            </select>
            <small class="form-text text-muted">Select resolution or leave as auto for camera defaults</small>
        </div>
        <div class="form-group">
            <label for="exposureMode">Exposure Mode:</label>
            <select id="exposureMode" class="form-control" onchange="toggleExposureInput()">
                <option value="auto">Auto Exposure</option>
                <option value="manual">Manual Exposure</option>
            </select>
        </div>
        <div class="form-group" id="exposureInputGroup" style="display: none;">
            <label for="exposureInput">Exposure Time (microseconds):</label>
            <input id="exposureInput" type="number" class="form-control" placeholder="e.g., 1000000" min="0" value="1000000">
            <small class="form-text text-muted">Example: 1000000 = 1 second exposure</small>
        </div>
        <div class="form-group">
            <label for="gainMode">Gain Mode:</label>
            <select id="gainMode" class="form-control" onchange="toggleGainInput()">
                <option value="auto">Auto Gain</option>
                <option value="manual">Manual Gain</option>
            </select>
            <small class="form-text text-muted">Range: {gain_range_str}x</small>
        </div>
        <div class="form-group" id="gainInputGroup" style="display: none;">
            <label for="gainInput">Gain (multiplier):</label>
            <input id="gainInput" type="number" class="form-control" placeholder="e.g., 2.5" min="0" step="0.1" value="1.0">
            <small class="form-text text-muted">Example: 1.0 = no gain, 2.5 = 2.5x amplification</small>
        </div>
        <div class="btn-group mb-3" role="group" aria-label="Recording controls">
            <button class="btn btn-info" onclick="applySettings()">Apply Settings to Preview</button>
            <button class="btn btn-warning" onclick="captureSnapshot()">Capture & Download Snapshot</button>
            <button class="btn btn-success" onclick="startRecording()">Start Recording</button>
            <button class="btn btn-danger" onclick="stopRecording()">Stop Recording</button>
            <button id="downloadBtn" class="btn btn-primary" onclick="downloadRecording()" disabled>Download Last</button>
        </div>
        <div id="status" class="alert alert-info" role="alert">Idle (Hardware encoder ready)</div>
        <p><a href="/" class="btn btn-secondary">Back to Home</a></p>
    </div>

    <script>
        let lastFile = null;

        function setStatus(text, cssClass) {{
            const box = document.getElementById('status');
            box.textContent = text;
            box.className = 'alert ' + cssClass;
        }}

        function toggleExposureInput() {{
            const mode = document.getElementById('exposureMode').value;
            const inputGroup = document.getElementById('exposureInputGroup');
            inputGroup.style.display = mode === 'manual' ? 'block' : 'none';
        }}

        function toggleGainInput() {{
            const mode = document.getElementById('gainMode').value;
            const inputGroup = document.getElementById('gainInputGroup');
            inputGroup.style.display = mode === 'manual' ? 'block' : 'none';
        }}

        async function applySettings() {{
            setStatus('Applying settings to preview...', 'alert-warning');
            try {{
                const resolution = document.getElementById('resolutionSelect').value;
                const exposureMode = document.getElementById('exposureMode').value;
                const exposureTime = exposureMode === 'manual' ? parseInt(document.getElementById('exposureInput').value) : null;
                const gainMode = document.getElementById('gainMode').value;
                const gainValue = gainMode === 'manual' ? parseFloat(document.getElementById('gainInput').value) : null;
                
                const res = await fetch('/apply_recording_settings', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        resolution: resolution || undefined,
                        exposure_mode: exposureMode,
                        exposure_time: exposureTime,
                        gain_mode: gainMode,
                        gain_value: gainValue
                    }})
                }});
                const data = await res.json();
                if (res.ok) {{
                    const expInfo = data.exposure_mode ? `, exposure: ${{data.exposure_mode}}` : '';
                    const gainInfo = data.gain_mode ? `, gain: ${{data.gain_mode}}` : '';
                    setStatus(`Preview updated: ${{data.resolution || 'auto'}}${{expInfo}}${{gainInfo}}`, 'alert-success');
                }} else {{
                    setStatus(data.message || 'Failed to apply settings.', 'alert-danger');
                }}
            }} catch (err) {{
                console.error(err);
                setStatus('Error applying settings.', 'alert-danger');
            }}
        }}

        async function captureSnapshot() {{
            setStatus('Capturing snapshot with current settings...', 'alert-info');
            try {{
                const link = document.createElement('a');
                link.href = '/capture_snapshot?format=raw';
                link.style.display = 'none';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                setStatus('Snapshot capture requested. Browser will download raw TIFF once ready.', 'alert-success');
            }} catch (err) {{
                console.error(err);
                setStatus('Error capturing snapshot.', 'alert-danger');
            }}
        }}

        async function startRecording() {{
            setStatus('Starting recording...', 'alert-warning');
            try {{
                const base = document.getElementById('baseInput').value.trim();
                const resolution = document.getElementById('resolutionSelect').value;
                const exposureMode = document.getElementById('exposureMode').value;
                const exposureTime = exposureMode === 'manual' ? parseInt(document.getElementById('exposureInput').value) : null;
                const gainMode = document.getElementById('gainMode').value;
                const gainValue = gainMode === 'manual' ? parseFloat(document.getElementById('gainInput').value) : null;
                
                const res = await fetch('/start_recording', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        base: base || undefined,
                        resolution: resolution || undefined,
                        exposure_mode: exposureMode,
                        exposure_time: exposureTime,
                        gain_mode: gainMode,
                        gain_value: gainValue
                    }})
                }});
                const data = await res.json();
                if (res.ok && data.status === 'recording_started') {{
                    lastFile = data.file_name || data.file;
                    document.getElementById('downloadBtn').disabled = true;
                    const expInfo = data.exposure_mode ? `, ${{data.exposure_mode}}` : '';
                    const gainInfo = data.gain_mode ? `, ${{data.gain_mode}}` : '';
                    setStatus(`Recording at ${{data.resolution || 'auto'}}${{expInfo}}${{gainInfo}}... Saving to ${{data.file_name || data.file || ''}}`, 'alert-success');
                }} else if (data.status === 'already_recording') {{
                    lastFile = data.file_name || data.file;
                    document.getElementById('downloadBtn').disabled = true;
                    setStatus(`Already recording: ${{data.file_name || data.file || ''}}`, 'alert-info');
                }} else {{
                    setStatus(data.message || 'Could not start recording.', 'alert-danger');
                }}
            }} catch (err) {{
                console.error(err);
                setStatus('Error starting recording.', 'alert-danger');
            }}
        }}

        async function stopRecording() {{
            setStatus('Stopping recording...', 'alert-warning');
            try {{
                const res = await fetch('/stop_recording', {{ method: 'POST' }});
                const data = await res.json();
                if (res.ok) {{
                    lastFile = data.file_name || data.file;
                    document.getElementById('downloadBtn').disabled = !lastFile;
                    setStatus(data.file ? `Stopped. Saved: ${{data.file}}` : 'Stopped.', 'alert-secondary');
                }} else {{
                    setStatus(data.message || 'Could not stop recording.', 'alert-danger');
                }}
            }} catch (err) {{
                console.error(err);
                setStatus('Error stopping recording.', 'alert-danger');
            }}
        }}

        function downloadRecording() {{
            if (!lastFile) {{
                setStatus('No recording to download.', 'alert-info');
                return;
            }}
            setStatus('Downloading (file will be cleaned up after download)...', 'alert-info');
            document.getElementById('downloadBtn').disabled = true;
            const url = `/download_recording?file=${{encodeURIComponent(lastFile)}}`;
            window.location = url;
            lastFile = null;
        }}
    </script>
</body>
</html>
"""
    return render_template_string(html_content)

#@app.route('/snapshot.jpg')
#def snapshot_jpeg():
#    """Capture a single JPEG frame from the current configuration."""
#    if picam2 is None or not picam2.started: return "Camera not initialized or not started for snapshot.", 503
#    # Use main stream for high-res snapshots
#    frame = picam2.capture_array("main")
#    # main is RGB888, encode directly without conversion
#    _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
#    return Response(jpeg.tobytes(), mimetype='image/jpeg')

@app.route('/snapshot.jpg')
def snapshot_jpeg():
    """Capture a single JPEG frame from the current configuration."""
    global picam2, current_camera_index
    
    req_cam_str = request.args.get("camera_index")
    
    # We must wrap the switch in CameraGuard to prevent threading crashes
    with CameraGuard():
        # --- FIX: Force switch if the Live View requested a different camera ---
        if req_cam_str is not None:
            req_cam = int(req_cam_str)
            if req_cam != current_camera_index:
                print(f"Live View forcing switch to camera {req_cam}")
                if not _init_picamera_unlocked(req_cam):
                    return "Camera switch failed", 500
        # -----------------------------------------------------------------------

        if picam2 is None or not picam2.started: 
            return "Camera not initialized or not started for snapshot.", 503
        
        # Use main stream for high-res snapshots
        frame = picam2.capture_array("main")
        # main is RGB888, encode directly without conversion
        _, jpeg = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        return Response(jpeg.tobytes(), mimetype='image/jpeg')


@app.route('/rawframe')
def raw_segment_frame():
    """Capture a raw frame buffer and return the bytes with headers."""
    global picam2, current_camera_index, preview_config # preview_config needed to restore later

    if current_camera_index == -1:
        return "No active camera. Use /switch_camera/.", 503
    with CameraGuard():
        return _capture_rawframe_locked()


@app.route('/camera-info')
def camera_info():
    """Flask route: return current camera settings, ranges, and modes."""
    global current_camera_index, ALL_CAMERA_DETAILS, picam2
    cam_data_to_display = None
    if 0 <= current_camera_index < len(ALL_CAMERA_DETAILS):
        cam_data_to_display = ALL_CAMERA_DETAILS[current_camera_index]

    if picam2 is None and cam_data_to_display is None:
        return "Camera not initialized and no startup details available. Check server logs.", 503

    if picam2 is None and cam_data_to_display:
        html_model = cam_data_to_display.get('Model', 'N/A'); html_id = cam_data_to_display.get('Id', 'N/A')
        parsed_modes = cam_data_to_display.get('parsed_modes', [])
        sensor_modes_list_html = []
        if parsed_modes:
            for mode in parsed_modes:
                sensor_modes_list_html.append(f"<li class=\"list-group-item\">- Fmt: {mode.get('format','?')}, Res: {mode.get('resolution','?')}, FPS: {mode.get('fps','?')}, Crop: {mode.get('crop_info','?')}</li>")
        else: sensor_modes_list_html.append("<li class=\"list-group-item\">- Sensor modes not parsed/unavailable (from startup scan).</li>")
        
        # Use .format() for safety in this template too, and escape style braces
        html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Camera Info (Index: {current_camera_index})</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>body{{padding:20px;}}</style>
</head>
<body>
    <div class="container">
        <h2 class="my-4">Camera Info (Index: {current_camera_index}) - Picamera2 Object Not Active</h2>
        <ul class="list-group mb-4">
            <li class="list-group-item"><strong>Model:</strong> {html_model}</li>
            <li class="list-group-item"><strong>ID:</strong> {html_id}</li>
        </ul>
        <p class="alert alert-warning">Controls ranges and current settings require an active Picamera2 instance.</p>
        <h3 class="my-3">Available Sensor Modes (from startup scan)</h3>
        <ul class="list-group mb-4">
            {sensor_modes_list_html}
        </ul>
        <hr>
        <p><a href="/" class="btn btn-primary">Home</a></p>
    </div>
</body>
</html>
"""
        return render_template_string(html_content.format(
            current_camera_index=current_camera_index,
            html_model=html_model,
            html_id=html_id,
            sensor_modes_list_html=''.join(sensor_modes_list_html) if sensor_modes_list_html else "<li class=\"list-group-item\">- N/A</li>"
        ))

    try:
        selected_cam_details_dict = {}
        parsed_modes_for_cam = []
        if cam_data_to_display:
            selected_cam_details_dict = cam_data_to_display
            parsed_modes_for_cam = cam_data_to_display.get('parsed_modes', [])
        else:
            try:
                basic_infos = Picamera2.global_camera_info()
                if 0 <= current_camera_index < len(basic_infos):
                    selected_cam_details_dict = dict(basic_infos[current_camera_index])
            except Exception as e: print(f"CamInfo: Error getting fallback details: {e}")

        ctrls_meta = picam2.camera_ctrl_info
        gain_info_tuple = ctrls_meta.get("AnalogueGain"); gain_range_str = "N/A"
        if gain_info_tuple and isinstance(gain_info_tuple, tuple):
            if len(gain_info_tuple) >= 2:
                gain_range_desc = str(gain_info_tuple); gain_range_str = gain_range_desc
            if len(gain_info_tuple) >= 3 and gain_info_tuple is not None:
                gain_range_str += f" (Default: {str(gain_info_tuple)}x)"
            elif len(gain_info_tuple) == 1: gain_range_str = str(gain_info_tuple)

        exp_info_tuple = ctrls_meta.get("ExposureTime"); exp_range_str = "N/A"
        if exp_info_tuple and isinstance(exp_info_tuple, tuple):
            if len(exp_info_tuple) >= 2:
                exp_range_desc = str(exp_info_tuple)
                exp_range_str = f"{exp_range_desc} µs" if "µs" not in exp_range_desc else exp_range_desc
            if len(exp_info_tuple) >= 3 and exp_info_tuple is not None:
                exp_range_str += f" (Default: {str(exp_info_tuple)} µs)"
            elif len(exp_info_tuple) == 1: exp_range_str = str(exp_info_tuple)

        sensor_modes_list_html = []
        if parsed_modes_for_cam:
            for mode in parsed_modes_for_cam:
                sensor_modes_list_html.append(f"<li class=\"list-group-item\">- Fmt: {mode.get('format','?')}, Res: {mode.get('resolution','?')}, FPS: {mode.get('fps','?')}, Crop: {mode.get('crop_info','?')}</li>")
        else: sensor_modes_list_html.append("<li class=\"list-group-item\">- Sensor modes not parsed or unavailable from startup scan.</li>")

        current_settings_html = ""
        if picam2.started:
            meta = picam2.capture_metadata(); stream_config = picam2.stream_configuration("main")
            size_tuple = stream_config.get("size", ('N/A','N/A'))
            current_settings_html = f"""
            <li class="list-group-item">- <strong>Exposure:</strong> {meta.get('ExposureTime', 'N/A')}µs</li>
            <li class="list-group-item">- <strong>Gain:</strong> {meta.get('AnalogueGain', 'N/A')}x</li>
            <li class="list-group-item">- <strong>Resolution:</strong> {size_tuple[0]}x{size_tuple[1]}</li>"""
        else: current_settings_html = "<li class=\"list-group-item\">- Camera not streaming for live metadata.</li>"

        html_model = selected_cam_details_dict.get('Model', 'N/A'); html_id = selected_cam_details_dict.get('Id', 'N/A')
        
        # Use .format() for safety in this template too, and escape style braces
        html_content = """
<!DOCTYPE html>
<html>
<head>
    <title>Camera Info (Index: {current_camera_index})</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>body{{padding:20px;}}</style>
</head>
<body>
    <div class="container">
        <h2 class="my-4">Camera Info (Index: {current_camera_index})</h2>
        <ul class="list-group mb-3">
            <li class="list-group-item"><strong>Model:</strong> {html_model}</li>
            <li class="list-group-item"><strong>ID:</strong> {html_id}</li>
        </ul>
        <h3 class="my-3">Controls Ranges</h3>
        <ul class="list-group mb-3">
            <li class="list-group-item">- <strong>Gain:</strong> {gain_range_str}</li>
            <li class="list-group-item">- <strong>Exposure:</strong> {exp_range_str}</li>
        </ul>
        <h3 class="my-3">Current Settings</h3>
        <ul class="list-group mb-4">
            {current_settings_html}
        </ul>
        <h3 class="my-3">Sensor Modes (from startup scan)</h3>
        <ul class="list-group mb-4">
            {sensor_modes_list_html}
        </ul>
        <hr>
        <p><a href="/" class="btn btn-primary">Home</a></p>
    </div>
</body>
</html>
"""
        return render_template_string(html_content.format(
            current_camera_index=current_camera_index,
            html_model=html_model,
            html_id=html_id,
            gain_range_str=gain_range_str,
            exp_range_str=exp_range_str,
            current_settings_html=current_settings_html,
            sensor_modes_list_html=''.join(sensor_modes_list_html)
        ))

    except Exception as e:
        import traceback; print(f"Error in /camera-info: {traceback.format_exc()}")
        return f"Error Fetching Camera Info<br>{e}<br>", 500

@app.route('/control')
def camera_control_endpoint():
    """Flask route: apply arbitrary control updates supplied in JSON."""
    if picam2 is None or not picam2.started: return "Camera not initialized/started.", 503

    controls_to_set = {}
    if 'exposure' in request.args:
        try: controls_to_set["ExposureTime"] = int(float(request.args['exposure']))
        except ValueError: return "Invalid exposure value.", 400
    if 'gain' in request.args:
        try: controls_to_set["AnalogueGain"] = float(request.args['gain'])
        except ValueError: return "Invalid gain value.", 400

    if controls_to_set:
        try: picam2.set_controls(controls_to_set); time.sleep(0.05); return f"Set: {controls_to_set}", 200
        except Exception as e: return f"Error applying controls: {e}", 500
    return "No valid controls provided.", 400

@app.route('/shutdown', methods=['POST'])
def shutdown_server():
    """Flask route: request a clean shutdown of the Flask development server."""
    global picam2
    # Close camera gracefully
    try:
        if picam2 is not None:
            try:
                if getattr(picam2, 'started', False):
                    picam2.stop()
            except Exception as e:
                print(f"Error stopping camera: {e}")
            try:
                picam2.close()
            except Exception as e:
                print(f"Error closing camera: {e}")
            picam2 = None
            print("Main Picamera2 instance closed.")
    except Exception as e:
        print(f"Shutdown: Unexpected error handling camera close: {e}")

    # Trigger Werkzeug server shutdown if available
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        print("Werkzeug shutdown not available; using fallback exit timer.")
        # Fallback: schedule process exit shortly after responding
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return "Shutdown initiated (fallback). Camera closed.", 200
    else:
        func()
        return "Server shutting down gracefully. Camera closed.", 200

@app.route('/')
def index():
    """Flask route: serve the landing page with quick control links."""
    global current_camera_index, ALL_CAMERA_DETAILS

    main_page_cam_list_html = ""
    if ALL_CAMERA_DETAILS:
        for cam_info in ALL_CAMERA_DETAILS:
            idx = cam_info.get('index', -1); model = cam_info.get('Model','Cam N/A')
            active = "*(Active)*" if idx == current_camera_index and picam2 is not None else ""
            main_page_cam_list_html += f"""
            <li class="list-group-item">
                Cam {idx}: {model} {active}
                <a href="/switch_camera/{idx}" class="btn btn-sm btn-info ml-2">Switch</a>
                <a href="/camera-info" class="btn btn-sm btn-secondary ml-1">Info for Active</a>
            </li>
            """
            parsed_modes = cam_info.get('parsed_modes', [])
            if parsed_modes:
                for mode in parsed_modes[:3]:
                    main_page_cam_list_html += (f"<li class=\"list-group-item list-group-item-light ml-3\">- Fmt: {mode.get('format','?')}, Res: {mode.get('resolution','?')}, "
                                                 f"FPS: {mode.get('fps','?')}fps (Crop: {mode.get('crop_info','?')})</li>")
                if len(parsed_modes) > 3: main_page_cam_list_html += "<li class=\"list-group-item list-group-item-light ml-3\">- ... (more modes in Camera Info page)</li>"
            else:
                main_page_cam_list_html += "<li class=\"list-group-item list-group-item-light ml-3\">- Sensor modes not available or not parsed for this camera.</li>"
        main_page_cam_list_html = f"<ul class=\"list-group\">{main_page_cam_list_html}</ul>"
    else:
        main_page_cam_list_html = """
        <div class="alert alert-warning" role="alert">
            No camera details found (startup scan may have failed - check server logs).
        </div>
        """

    try:
        basic_cams = Picamera2.global_camera_info()
        if basic_cams and not ALL_CAMERA_DETAILS:
            main_page_cam_list_html += "<div class=\"alert alert-info mt-3\">- Basic camera info (detailed mode scan failed):</div><ul class=\"list-group\">"
            for i, basic_cam in enumerate(basic_cams):
                main_page_cam_list_html += f"<li class=\"list-group-item\">- Cam {i}: {basic_cam.get('Model', 'Unknown')} <a href=\"/switch_camera/{i}\" class=\"btn btn-sm btn-info ml-2\">Switch</a></li>"
            main_page_cam_list_html += "</ul>"
    except Exception: pass

    active_status = f"Active Camera Index: <strong>{current_camera_index if current_camera_index!=-1 else 'None'}</strong>"
    if picam2 is None and current_camera_index != -1: active_status += " (Picamera2 object not active)"

    # FIX: Using a standard string template and .format() and escaping ALL braces in script/style blocks
    html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>Pi Cam Server</title>
    <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
    <style>
        body {{ padding: 20px; }}
        h1 {{ color: #333; }}
        .status-box {{
            background-color: #e9ecef;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1 class="my-4">Pi Camera Server</h1>
        <div class="status-box">
            {active_status}
        </div>

        <h2 class="my-3">Available Cameras & Modes (from startup scan):</h2>
        {main_page_cam_list_html}

        <p class="mt-4"><a href="/available_cameras" class="btn btn-secondary">Full Camera Details JSON</a></p>

        <hr>

        <h2 class="my-3">Endpoints (for active cam):</h2>
        <ul class="list-group">
            <li class="list-group-item"><a href="/record" target="_blank">/record</a> (Live preview with hardware H.264 recording)</li>
            <li class="list-group-item"><a href="/video" target="_blank">/video</a> (MJPEG stream)?exposure=&lt;µs&gt;</li>
            <li class="list-group-item"><a href="/snapshot.jpg" target="_blank">/snapshot.jpg</a></li>
            <li class="list-group-item"><a href="/capture_dng" target="_blank">/capture_dng</a> (Capture 12-bit RAW DNG still)</li>
            <li class="list-group-item">
                <a href="#" onclick="promptForRawCapture()">/rawframe</a>?exposure=&lt;µs&gt;&gain=&lt;f&gt;&mode=&lt;W:H:B:P_or_U&gt;
                <div class="mt-2" id="raw-capture-prompt">
                    <input type="number" id="exposureInput" placeholder="Exposure (µs, e.g., 1000000)" class="form-control mb-1">
                    <input type="number" step="0.1" id="gainInput" placeholder="Gain (float, e.g., 5.0)" class="form-control mb-1">
                    <input type="text" id="modeInput" placeholder="Mode (W:H:B:P_or_U, e.g., 4056:3040:12:U)" class="form-control mb-1">
                    <button class="btn btn-primary btn-sm" onclick="triggerRawCapture()">Capture Raw</button>
                </div>
            </li>
            <li class="list-group-item">
                <a href="#" onclick="promptForControl()">/control</a>?exposure=&lt;µs&gt;&gain=&lt;f&gt;
                <div class="mt-2" id="control-prompt">
                    <input type="number" id="controlExposureInput" placeholder="Exposure (µs)" class="form-control mb-1">
                    <input type="number" step="0.1" id="controlGainInput" placeholder="Gain (float)" class="form-control mb-1">
                    <button class="btn btn-primary btn-sm" onclick="triggerControl()">Set Controls</button>
                </div>
            </li>
            <li class="list-group-item d-flex justify-content-between align-items-center">
                <span>Shutdown server and close camera</span>
                <button class="btn btn-danger btn-sm" onclick="triggerShutdown()">Shutdown Server</button>
            </li>
        </ul>
    </div>

    <script>
        function promptForRawCapture() {{
            document.getElementById('raw-capture-prompt').style.display = 'block';
        }}

        function triggerRawCapture() {{
            const exposure = document.getElementById('exposureInput').value;
            const gain = document.getElementById('gainInput').value;
            const mode = document.getElementById('modeInput').value;
            let url = `/rawframe?exposure=${{exposure}}&gain=${{gain}}&mode=${{mode}}`;
            window.open(url, '_blank');
        }}

        function promptForControl() {{
            document.getElementById('control-prompt').style.display = 'block';
        }}

        function triggerControl() {{
            const exposure = document.getElementById('controlExposureInput').value;
            const gain = document.getElementById('controlGainInput').value;
            let url = `/control?`;
            if (exposure) url += `exposure=${{exposure}}`;
            if (gain) url += `${{exposure ? '&' : ''}}gain=${{gain}}`;
            fetch(url)
                .then(response => response.text())
                .then(data => alert('Controls Set: ' + data))
                .catch(error => console.error('Error setting controls:', error));
        }}

        function triggerShutdown() {{
            if (!confirm('Shutdown server and close camera?')) return;
            fetch('/shutdown', {{ method: 'POST' }})
                .then(response => response.text())
                .then(text => {{
                    alert(text);
                    // Attempt to close the tab/window shortly after
                    setTimeout(() => window.close(), 500);
                }})
                .catch(error => console.error('Shutdown error:', error));
        }}
    </script>
</body>
</html>
"""
    return render_template_string(html_template.format(
        active_status=active_status,
        main_page_cam_list_html=main_page_cam_list_html
    ))

if __name__ == '__main__':
    print("Starting Flask app (python_server.py)...")
    # startup_operations() is called by @app.before_first_request
    app.run(host='0.0.0.0', port=8080, threaded=True)



