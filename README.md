# PiCam Server Spectral

PiCam Server Spectral provides a Flask-based HTTP control server for Raspberry Pi camera modules and a Napari dock widget for desktop control, raw capture, spectral acquisition, calibration, and preview/recording workflows. The server runs on the Raspberry Pi, exposes camera and spectroscopy endpoints over the LAN, and the Napari client drives those endpoints from a workstation.

The current codebase is split into a Raspberry Pi server (`picam_server_spectral_V8.py`) and a modular Napari client (`widgets.py`, `core.py`, `tabs_control.py`, and `tabs_settings.py`). Configuration is saved to and loaded from `cam_config.json`.

## Features

- Detects connected Picamera2 cameras, reports parsed sensor modes, and supports camera switching by index.
- Streams MJPEG preview from `/video` and returns one-shot JPEG preview frames from `/snapshot.jpg`.
- Captures raw still data through `/rawframe`, including accumulation support and `stack`, `mean`, or `sum` combine modes.
- Captures DNG files through `/capture_dng` and can display DNG previews in Napari when `rawpy` is installed.
- Records hardware-encoded H.264 video through `/start_recording` and `/stop_recording`.
- Provides a spectroscopy workflow with full-frame raw capture, single ROI, multiple ROI, and custom ROI acquisition.
- Supports background/reference spectra, Intensity, Intensity-BG, Transmittance (`T`), and Absorbance (`A`) plotting/export.
- Supports wavelength calibration using linear, quadratic, cubic, prism/Hartmann, grating, and Cauchy-style models.
- Saves camera, spectroscopy, saving, and calibration settings in `cam_config.json`.
- Includes a Napari Locate tab with live view, JPEG snapshots, DNG capture, RAW capture, split-channel display, and an adjustable grid overlay.

## Current Repository Layout

| File | Purpose |
| --- | --- |
| `picam_server_spectral_V8.py` | Main Flask/Picamera2 server. Exposes camera control, raw capture, spectral capture, recording, and dashboard endpoints. |
| `PiCamPlugin.py` | Current launcher script for the desktop Napari control panel. Creates the Napari viewer and docks `PiCamPlugin` as `Pi Control`. |
| `widgets.py` | Main Napari dock widget/controller. Assembles tabs, stores shared state, saves/loads `cam_config.json`, and applies settings. |
| `core.py` | Headless client-side logic: settings stores, HTTP request handler, raw decoding, Bayer/CFA channel splitting, spectral math, and calibration functions. |
| `tabs_control.py` | Napari control tabs: Locate, Spectroscopy, and Video. |
| `tabs_settings.py` | Napari settings tabs: Photo Settings, Spectral Settings, Saving Settings, and Calibration. |
| `cam_config.json` | Example/current persisted configuration for camera, spectral acquisition, saving, and wavelength calibration. |
| `snapshots/` | Server-side temporary still-capture artifacts when using snapshot download endpoints. Created at runtime if needed. |
| `recordings/` | Server-side H.264 recording output directory. Created at runtime if needed. |

Older names such as `python_server.py`, `python_server2.py`, `napari_plugin.py`, and `napari_plugin_clean_api.py` refer to previous versions and should not be used for this current file set unless those legacy files also exist in your repository.

## Requirements

### Raspberry Pi server

- Raspberry Pi 4 or 5 running a recent 64-bit Raspberry Pi OS with libcamera and Picamera2 support.
- Python 3.10 or newer.
- System packages commonly needed on the Pi:

```bash
sudo apt update
sudo apt install python3-picamera2 python3-libcamera python3-numpy libcamera-apps python3-opencv
```

- Python packages used by the server:

```bash
pip install "Flask<2.3" numpy opencv-python
```

Picamera2 and libcamera are usually best installed from the Raspberry Pi OS package repositories, not from PyPI. `Flask<2.3` is recommended for the current server code because it still uses Flask's legacy `before_first_request` startup hook; remove this pin after moving startup initialization to a newer Flask-compatible pattern.

### Napari workstation/client

Install the desktop-side packages in a separate environment on the workstation that will run Napari:

```bash
pip install "napari[pyqt5]" qtpy numpy scipy requests matplotlib opencv-python rawpy
```

`rawpy` is optional, but it is needed for DNG preview/loading in the Napari client. `scipy` is needed for the non-polynomial calibration fitting models.

## Running the HTTP Server

1. Attach and validate the camera hardware on the Raspberry Pi.
2. Start the current server from the project directory:

```bash
python picam_server_spectral_V8.py
```

3. The Flask app listens on `0.0.0.0:8080`.
4. Open the dashboard from another machine on the LAN:

```text
http://<pi-ip>:8080/
```

5. Use `/shutdown` from the dashboard or send a POST request to `/shutdown` for a clean stop.

The server initializes camera details before the first request, then opens the default camera index.

## Running the Napari Control Panel

Run the current Napari client launcher from a workstation that can reach the Pi:

```bash
python PiCamPlugin.py
```

`PiCamPlugin.py` creates a Napari viewer, instantiates `PiCamPlugin` from `widgets.py`, docks it as `Pi Control`, and starts the Napari event loop. The project is not yet packaged as an installable Napari plugin, so for now this launcher script is the expected way to start the desktop UI.

Typical workflow:

1. Start `picam_server_spectral_V8.py` on the Raspberry Pi.
2. Start `widgets.py` on the workstation.
3. In **General Settings > Photo Settings**, set the server URL, for example `http://192.168.0.197:8080`.
4. Pick the camera and resolution, then use **Apply Hardware Settings**.
5. Use **Locate** for live view, JPEG snapshots, DNG capture, raw capture, split-channel display, and grid overlay.
6. Use **General Settings > Spectral Settings** to choose spectral camera, binning, exposure/gain, accumulation count/mode, and readout setup.
7. Use **Spectroscopy** for Snap/BG/Ref acquisition, plotting, math mode selection, wavelength-axis display, and CSV export.
8. Use **Save Config** and **Load Config** to persist/restore `cam_config.json`. Loading a config also applies hardware/spectral settings to the server.

## Configuration File

`cam_config.json` stores four top-level sections:

| Section | Contents |
| --- | --- |
| `camera` | Server URL, exposure, gain, and selected resolution. |
| `spectral` | Plot mode, split-channel setting, export filename, and the nested `ui_settings` payload sent to the server. |
| `saving` | Filename root, directory, date/time options, delimiter, numeric suffix state, and DNG auto-save preference. |
| `calibration` | Wavelength calibration coefficients, model type, and pixel/nm calibration points. |

The current sample config uses a polynomial cubic (`poly3`) wavelength calibration and stores spectral ROI/readout settings under `spectral.ui_settings`.

## Core API Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Built-in dashboard with camera list and shortcuts. |
| `/available_cameras` | GET | JSON list of detected cameras and parsed Picamera2 sensor modes. |
| `/switch_camera/<index>` | GET | Switch active camera to the requested numeric index. |
| `/camera-info` | GET | HTML page showing current camera information, controls, and modes. |
| `/video` | GET | MJPEG preview stream. Supports `?exposure=<microseconds>`. |
| `/snapshot.jpg` | GET | One-shot JPEG frame. Supports `?camera_index=<index>` for explicit camera selection. |
| `/capture_snapshot` | GET/POST | Captures a raw TIFF snapshot; default `format=zip` bundles raw TIFF plus preview JPEG when available. `format=raw` returns only the raw TIFF. |
| `/capture_dng` | GET | Captures a DNG file. Supports `?camera_index=<index>`. |
| `/rawframe` | GET | Captures raw Bayer frame data. Query parameters include `camera_index`, `exposure`, `gain`, `duration`, `accumulations`, `combine`, and `mode`. |
| `/apply_recording_settings` | POST | Applies preview/recording resolution, exposure, and gain settings. |
| `/settings/spectral_acquisition` | GET | Returns persisted spectral acquisition settings. |
| `/settings/spectral_acquisition` | POST | Saves/applies spectral acquisition settings. |
| `/spectral_capture` | POST | Captures raw data with spectral ROI settings and returns extracted spectra as JSON. |
| `/start_recording` | POST | Starts hardware H.264 recording. Accepts JSON/form/query settings including `base`, `resolution`, `exposure_mode`, `exposure_time`, `gain_mode`, and `gain_value`. |
| `/stop_recording` | POST | Stops active recording and returns the file name. |
| `/download_recording?file=<name>` | GET | Downloads a recording from `recordings/`; the server deletes the file shortly after download starts. |
| `/record` | GET | Recording-oriented web page. |
| `/control` | GET | Quick manual control endpoint. Supports `?exposure=<microseconds>&gain=<float>`. |
| `/shutdown` | POST | Stops the server and releases the camera. |

## `/rawframe` Details

Example:

```text
/rawframe?camera_index=0&exposure=50000&gain=1.0&mode=4056:3040:12:U&accumulations=20&combine=mean
```

Important query parameters:

| Parameter | Meaning |
| --- | --- |
| `camera_index` | Optional camera index. If it differs from the active camera, the server switches before capture. |
| `exposure` | Exposure time in microseconds. Default is `2000000`. |
| `gain` | Analogue gain. Default is `5.0`. |
| `duration` | Optional capture wait time in milliseconds. If omitted, the server uses exposure-derived timing. |
| `mode` | Raw mode string in `WIDTH:HEIGHT:BIT_DEPTH:P_or_U` format, for example `4056:3040:12:U`. |
| `accumulations` | Number of frames to capture. Values are clamped to `1..64`. |
| `combine` | `stack`, `mean`/`avg`/`average`, or `sum`/`total`. |

Response headers describe how to decode the returned binary payload:

| Header | Meaning |
| --- | --- |
| `X-Raw-Shape` | Shape of the returned NumPy-style array. |
| `X-Raw-DType` | Element dtype, for example `uint16` or `uint64`. |
| `X-Raw-Mode` | Requested raw mode string. |
| `X-Raw-CFA` | Final inferred CFA/Bayer pattern after camera configuration. |
| `X-Raw-Accumulations` | Effective accumulation count. |
| `X-Raw-Combine` | Effective combine mode. |

`core.py` contains `DataHandler.decode_rawframe()`, which reads these headers case-insensitively and reshapes the raw payload for the Napari client.

## Spectral Acquisition

The spectral workflow has two paths:

- **Full frame**: the client calls `/rawframe`, decodes the raw payload locally, splits the Bayer mosaic into CFA channels, and displays the result as Napari image layers.
- **Single ROI / Multiple ROI / Multiple ROI Custom**: the client sends the ROI configuration to `/spectral_capture`; the server captures raw data, extracts 1D spectra from the requested regions, and returns compact JSON.

Supported readout setup values:

- `Full frame`
- `Single ROI`
- `Multiple ROI`
- `Multiple ROI Custom`

The server normalizes ROI bounds to the frame size and returns spectra grouped by ROI label and CFA channel. The client can either keep channels separate or average them into combined spectra.

### Spectroscopy math modes

| Mode | Requirement | Meaning |
| --- | --- | --- |
| `Intensity` | Snap profile only | Raw/intensity profile. |
| `Intensity-BG` | Background profile | Subtracts cached BG/dark profile. |
| `T` | Background and reference profiles | Transmittance-like profile using BG and Ref. |
| `A` | Background and reference profiles | Absorbance-like profile using BG and Ref. |

The Spectroscopy tab disables modes that do not yet have the needed BG/Ref caches.

## Wavelength Calibration

The Calibration tab maps pixel index to wavelength in nm. Supported model IDs saved in `cam_config.json` are:

- `poly1` — linear fit
- `poly2` — quadratic fit
- `poly3` — cubic fit
- `prism` — prism/Hartmann-style fit
- `grating` — physical grating-style fit
- `cauchy` — empirical Cauchy-style fit

After calculation, the client stores coefficients in memory, enables the **X-Axis in nm** option, redraws existing spectra, and saves the updated calibration to `cam_config.json`.

## Napari Tabs

### Locate

- Start/stop live view from `/snapshot.jpg`.
- Snap JPEG into a new Napari layer using the configured filename pattern.
- Capture DNG and display a processed RGB preview when `rawpy` is installed.
- Capture RAW or RAW DNG and display split CFA channels or a grayscale raw image.
- Toggle an adjustable grid overlay as a Napari Shapes layer.

### Spectroscopy

- Acquire Snap/BG/Ref profiles.
- Plot spectra with Matplotlib inside the Napari dock widget.
- Switch between pixel index and calibrated wavelength axis.
- Export plotted spectra to CSV with the currently selected math mode applied.

### Video

- Start/stop H.264 recording through the server.
- Uses the prefix entered in the Video tab as the `base` recording name.
- Current client implementation is intentionally minimal: it starts/stops recording but does not yet expose recording resolution, manual/auto exposure, gain mode, download/playback, elapsed time, or recording status in the Napari UI. Track these under the TODO list below.

### General Settings

Contains nested tabs for:

- Photo settings: server URL, camera, resolution, exposure, and gain.
- Spectral settings: camera, binning, accumulation, readout setup, ROI settings, and BG/Ref buttons.
- Saving settings: capture filename root, date/time suffixes, delimiter, numeric suffix counter, and save directory.
- Calibration: calibration point table, model selection, fitting, and calibration plot.

## Bayer vs CFA Terminology

Picamera2 reports a `sensor_format` such as `SRGGB12` that encodes the pixel packing, bit depth, endianness, and whether the frame is still in its Bayer mosaic. The Color Filter Array (CFA) string names the RGGB/BGGR/GRBG/GBRG mosaic layout used for demosaicing.

When raw output, binning, HDR compositing, transforms, or ISP routing are involved, the mode-table format and the configured stream's final CFA can differ. The server first infers a CFA pattern from sensor-mode metadata, then refines it after configuring the raw stream. Downstream processing should trust the final `X-Raw-CFA` value reported after configuration.

Example from field logs:

```text
Derived CFA pattern: RGGB from format=SRGGB12_CSI2P unpacked=SRGGB12 cfa entries=None None
Using raw format SRGGB12 for capture (packing U)
Selected sensor format: 4056x3040-SBGGR12_1X12 - Selected CFE format: 4056x3040-PC1B
Selected sensor format: 4056x3040-SBGGR12_1X12 - Selected CFE format: 4056x3040-BYR2
Updated CFA pattern after configure: BGGR (was RGGB)
```

In that case, the transport format remains a 12-bit raw stream, but the final mosaic order is BGGR. Use the CFA tag/header for demosaicing.

## Control Notes

### Exposure and gain

- `/video` and `/control` accept exposure in microseconds.
- `/apply_recording_settings` and `/start_recording` support manual/auto exposure and gain modes.
- Manual exposure is applied through Picamera2 controls such as `ExposureTime`; manual gain uses `AnalogueGain`.

### AWB mode reference

| Mode | Lighting description |
| --- | --- |
| Auto | Camera analyzes the scene. |
| Incandescent | Warm domestic bulbs. |
| Tungsten | Studio hot lights. |
| Fluorescent | Green-shift office tubes. |
| Indoor | Mixed artificial light. |
| Daylight | Sunny outdoor scenes. |
| Cloudy | Overcast outdoor scenes. |
| Custom | Manual colour gains if provided by your code/configuration. |

### Digital zoom / `ScalerCrop`

Digital zoom is hardware-accelerated by the Raspberry Pi ISP/PiSP, not by Python code. It crops the sensor area before scaling, so it throws away pixels like any digital zoom, but the operation is handled by the imaging pipeline rather than by CPU-side array slicing.

Coordinates are normally relative to the full sensor frame, for example the IMX477 full-resolution frame of `4056 x 3040`.

### IMX477 resolution reference

| Scale | Resolution | Notes |
| --- | --- | --- |
| 1/1 | `4056 x 3040` | Native full 12 MP frame. |
| 1/2 | `2028 x 1520` | Native binning / useful 3 MP mode. |
| 1/4 | `1014 x 760` | Integer scale. |
| 1/8 | `507 x 380` | Integer scale, near low-resolution preview use. |

## PiSP IMX477 Tuning Profile Notes

On Raspberry Pi 5 / RP1-based boards, the IMX477 tuning file is typically located at:

```text
/usr/share/libcamera/ipa/rpi/pisp/imx477.json
```

Useful concepts from the tuning profile:

- The profile target is `pisp`, so it is tailored for the Pi 5 image pipeline.
- Auto exposure (`rpi.agc`) defines matrix, center-weighted, and spot metering masks.
- Auto white balance (`rpi.awb`) maps common illuminants such as incandescent, tungsten, fluorescent, daylight, and cloudy.
- HDR-related tuning can blend different exposures and adjust tone mapping for night/low-light scenes.
- Lens-shading calibration (`rpi.alsc`) compensates vignetting and colour shifts.
- Black-level handling defines the sensor floor used by downstream processing.

These tuning details remain useful for understanding preview/recording behaviour, but raw scientific processing should rely on the raw payload, final CFA header, and explicit calibration rather than on ISP-rendered colour output.

## Custom Raw File Format Notes

`/rawframe` returns a headerless binary payload plus HTTP headers. If you save the payload to disk, the default filename generated by the server is:

```text
capture_cam{ID}_{Width}x{Height}_{Depth}bit_acc{N}_{Timestamp}.raw
```

The HTTP headers are the authoritative metadata for shape, dtype, mode, CFA, accumulation count, and combine mode.

### Binary data structure

- `stack` mode: returns an accumulated stack-shaped array when multiple frames are captured.
- `mean` mode: returns one frame averaged from the accumulation stack.
- `sum` mode: returns one summed frame, normally with a wider dtype such as `uint64`.
- Raw bytes are emitted exactly as produced/combined by the server. Use `X-Raw-DType` and `X-Raw-Shape` to reconstruct the array.

### Hardware padding / stride

Some raw modes can contain row padding for alignment. Always trust the shape returned by the server and crop to the scientifically valid width if your downstream processing requires a strict active-pixel region.

Reference stride examples that may be relevant for IMX477-style modes:

| Valid width | Padded width | Padding pixels | Bytes per row at 16-bit |
| --- | --- | --- | --- |
| 4056 | 4064 | +8 | 8128 bytes |
| 2028 | 2048 | +20 | 4096 bytes |
| 1332 | 1344 | +12 | 2688 bytes |

## Troubleshooting

- If `/available_cameras` is empty, check the ribbon cable, camera power, and Picamera2 installation. Validate with `libcamera-hello` or another Picamera2 test.
- If the server cannot initialize a camera, make sure no other process is holding the camera device.
- If a capture fails after switching cameras, retry after confirming the requested camera index exists in `/available_cameras`.
- High-resolution raw, DNG, long exposure, and multi-accumulation captures can take several seconds. Increase client HTTP timeouts when needed.
- Prefer Ethernet over Wi-Fi for large raw downloads.
- If DNG preview fails in Napari, install `rawpy` in the workstation environment.
- If calibration fitting fails, check that the selected model has enough valid pixel/nm points and that non-polynomial models have reasonable initial guesses.

## TODO / Missing or Incomplete Work

### Packaging and installation

- Make the client installable as a real Napari plugin instead of starting it manually with `python PiCamPlugin.py`.
- Add plugin metadata, entry points, and packaging files, for example `pyproject.toml`, so the widget can be installed with `pip install -e .` and opened from Napari's plugin menu.
- Split server and client dependency extras, for example `.[server]` and `.[napari]`, so Raspberry Pi and workstation installs stay clean.
- Add a small launcher/CLI command after packaging, for example `picam-napari` for the desktop UI and `picam-server` for the Pi server.

### Video workflow

- Expand the Napari Video tab. The server already has `/start_recording`, `/stop_recording`, and `/download_recording`, but the current client only exposes a recording prefix and start/stop button.
- Add controls for recording resolution, manual/auto exposure, exposure time, gain mode, and gain value.
- Show current recording state, output filename, elapsed time, and errors in the Napari status area.
- Add a download button for the last recording, or a recording browser for files in the server-side `recordings/` directory.
- Decide whether video should use the Photo Settings camera/resolution or its own dedicated video settings section.

### Saving and acquisition workflow

- Implement or verify `auto_save_dng`. The setting is persisted in `cam_config.json`, but the current client-side DNG button primarily loads/displays the DNG; confirm disk-save behaviour before relying on it for unattended acquisition.
- Apply the configured save directory consistently to JPEG snapshots, DNG downloads, raw captures, spectra CSV exports, and recording downloads.
- Add overwrite protection and clearer filename previews for all capture types, not only Locate snapshots.
- Consider saving raw capture metadata next to downloaded arrays, for example as JSON sidecars containing shape, dtype, CFA, exposure, gain, accumulation count, and mode.

### Spectroscopy and calibration

- Add saturation detection and saturation warnings for raw and spectral captures.
- Add clearer validation for ROI bounds, empty custom ROI rows, and calibration point counts before capture/fitting.
- Add a way to export/import calibration independently from the full `cam_config.json`.
- Add tests or reference files for spectral math modes: Intensity, Intensity-BG, T, and A.

### Compatibility and robustness

- Update the Flask startup hook so the server works with current Flask versions without requiring `Flask<2.3`.
- Add connection checks in the Napari UI for unreachable server URLs and failed camera refreshes.
- Improve error handling around camera switching while live view, raw capture, or recording is active.
- Add a short troubleshooting section for common Picamera2/libcamera permission and camera-busy errors.

### Documentation

- Add screenshots or diagrams of the Napari tabs once the UI stabilizes.
- Document example command lines for common API calls, including raw capture, spectral capture, DNG capture, and recording download.
- Keep legacy file names out of the main workflow unless those files are actually present in the repository.

## Known Caveats

- The server uses Flask's development server. For trusted LAN/lab use this is convenient, but for wider network exposure use a production WSGI setup and access controls.
- Some hardware behaviour depends on the exact Raspberry Pi model, camera sensor, libcamera/Picamera2 version, and sensor modes reported at runtime.
