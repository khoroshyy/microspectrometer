# PiCam Server

PiCam Server provides a Flask based control surface for Raspberry Pi camera modules. It exposes high level HTTP endpoints for preview, still capture, raw frame export, and hardware encoded video recording while also shipping with a Napari dock widget that drives the API from a desktop viewer. The server runs directly on a Raspberry Pi and is intended for remote control over a LAN.

## Features
- Detects every connected camera, reports supported sensor modes, and allows hot switching between devices.
- Streams live MJPEG preview, captures full resolution JPEGs, and records H.264 video with the on board encoder.
- Exports science friendly data via `/capture_dng` and `/rawframe`, delivering 12 bit DNG files or unpacked Bayer mosaics.
- Applies manual exposure and gain, or reconfigures preview resolution on the fly through JSON calls.
- Ships with a Napari dock widget in [napari_plugin.py](napari_plugin.py) for interactive browsing, recording, and data download from a workstation.
- Includes example scripts and notebooks for raw processing experiments, such as [picamera_example.py](picamera_example.py), [fetch_and_read_dng.ipynb](fetch_and_read_dng.ipynb), and [fetch_and_read_raw.ipynb](fetch_and_read_raw.ipynb).

## Repository Layout
- [python_server.py](python_server.py) – Main Flask application that wraps Picamera2 for HTTP control.
- [python_server_clean_api.py](python_server_clean_api.py) – Experimental server exposing a reduced, easier-to-consume REST surface.
- [napari_plugin.py](napari_plugin.py) – Napari dock widget that drives the primary server API from a desktop viewer.
- [napari_plugin_clean_api.py](napari_plugin_clean_api.py) – Companion widget targeting the clean API server variant.
- [experimenting_scripts/picamera_example.py](experimenting_scripts/picamera_example.py) and notebooks in [experimenting_scripts](experimenting_scripts) – Capture and raw-processing walkthroughs.
- [example_files](example_files) – Reference media for testing and calibration.
- [tools/show_grid.py](tools/show_grid.py) – Utility to render an exposure grid overlay via the server.
- [snapshots](snapshots) and [recordings](recordings) – Sample outputs produced during development and testing.

## Requirements
- Raspberry Pi 4 or 5 running a recent 64 bit Raspberry Pi OS with libcamera and Picamera2 packages installed.
- Python 3.10 or newer with pip.
- System packages: `libatlas-base-dev`, `python3-pyqt5`, and other Picamera2 prerequisites supplied by Raspberry Pi OS.
- Python packages:
	- Flask and its dependencies.
	- picamera2 (comes from Raspberry Pi repositories).
	- numpy, opencv-python, requests.
	- rawpy (optional; only needed if using the Napari DNG preview).
	- napari and qtpy on the workstation that loads the plugin.

## Installation
1. Clone the repository onto the Raspberry Pi.
2. Install Picamera2 system packages (`sudo apt install python3-picamera2 python3-libcamera python3-numpy libcamera-apps`).
3. Create a Python environment (virtualenv or system) and install Python dependencies.

		pip install flask opencv-python requests rawpy

		The Napari plugin is typically executed on a desktop machine: create a separate environment there and install `napari[all]` or `napari[pyqt5]` plus `requests`.

## Running the HTTP Server
1. Ensure the camera module is attached and enabled in firmware.
2. From the project root, start the server.

				python python_server2.py

3. The Flask app listens on `0.0.0.0:8080`. Visit `http://<pi-ip>:8080/` to view the built in dashboard, camera list, and endpoint shortcuts.
4. Use the `/shutdown` button or send a POST request to `/shutdown` for a clean stop.

## Core API Endpoints
| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/available_cameras` | GET | JSON catalog of detected cameras and parsed sensor modes. |
| `/switch_camera/<index>` | GET | Activate another camera by numeric index. |
| `/video` | GET | MJPEG stream. Supports `?exposure=` microseconds query parameter. |
| `/snapshot.jpg` | GET | Current RGB frame as JPEG. |
| `/capture_snapshot` | GET or POST | Captures a still JPEG, optionally bundles preview and raw channels in a ZIP. |
| `/capture_dng` | GET | Captures a 12 bit DNG still using the highest resolution mode. |
| `/rawframe` | GET | Captures raw Bayer data. Accepts `shutter`, `gain`, and `mode` (e.g. `4056:3040:12:U`). |
| `/apply_recording_settings` | POST | JSON payload controlling preview resolution, exposure, and gain. |
| `/start_recording` / `/stop_recording` | POST | Begin or end hardware encoded H.264 recording. |
| `/download_recording` | GET | Retrieve the most recent recording. |
| `/control` | GET | Quick exposure and gain adjustments from the landing page. |
| `/shutdown` | POST | Stops the server and releases the camera. |

Review [python_server2.py](python_server2.py) for detailed parameter handling and error responses.

## Napari Control Panel
The repository ships a ready to run dock widget in [napari_plugin.py](napari_plugin.py). To use it:
1. Launch Napari on a desktop with network access to the Pi.
6. The Video tab triggers `/start_recording` and `/stop_recording` for hands free H.264 capture.

## Development Utilities
The notebooks [fetch_and_read_dng.ipynb](fetch_and_read_dng.ipynb) and [fetch_and_read_raw.ipynb](fetch_and_read_raw.ipynb) demonstrate how to retrieve captures from the API, deserialize raw payloads, and visualize them with NumPy or rawpy. Use [read_raw.ipynb](read_raw.ipynb) for additional experiments decoding packed Bayer data.

### Bayer vs CFA Terminology
Picamera2 reports a `sensor_format` such as `SRGGB12` that encodes the pixel packing (bit depth, endianness, and whether the frame is still in its Bayer mosaic). The Color Filter Array (CFA) string, on the other hand, names just the RGGB/BGGR pattern that ends up inside the DNG metadata. When you request raw output, binning, HDR compositing, or ISP cropping can cause `sensor_format` and the exported CFA tag to diverge, so treat the format string as a transport description and the CFA as the definitive mosaic layout for demosaicing.

Example: a `/rawframe` capture might report `sensor_format=SRGGB12_CSI2P` but the configured stream exposes a final `CFA=BGGR`. The payload is still a 12-bit packed CSI-2 stream; only the mosaic order differs, so downstream processing should honour the CFA tag for demosaicing and treat the `sensor_format` as the transport description.

Field log excerpt:

```
Derived CFA pattern: RGGB from format=SRGGB12_CSI2P unpacked=SRGGB12 cfa entries=None None
Using raw format SRGGB12 for capture (packing U)
[22:35:18.032433013] [20486] INFO RPI pisp.cpp:1483 Sensor: ... - Selected sensor format: 4056x3040-SBGGR12_1X12 - Selected CFE format: 4056x3040-PC1B
[22:35:18.043842753] [20486] INFO RPI pisp.cpp:1483 Sensor: ... - Selected sensor format: 4056x3040-SBGGR12_1X12 - Selected CFE format: 4056x3040-BYR2
Updated CFA pattern after configure: BGGR (was RGGB)
Raw file captured. Size: 24709120 bytes. Sending as attachment.
```

Here the pre-configuration probe infers an RGGB CFA from the mode table, but once libcamera configures the full-resolution stream it reports BGGR and switches the CFE output from `PC1B` to `BYR2` to match the ISP routing. That flip reflects the ISP applying a transform (such as a sensor mirror or binning layout) before exposing the stream. Always trust the final CFA reported after configuration when selecting a demosaic kernel; the transport format remains `SRGGB12_CSI2P` either way.
Date: 2026-01-02

2. DETAILED CONTROL DESCRIPTIONS
================================================================================
- What it does: artificially enhances edges in the image.
- < 1.0: Softens the image (useful to hide noise in low light).
- Default: 1.0
- What it does: Adjusts the difference between the lightest and darkest parts.
- Higher: "Punchy" look, deeper blacks, brighter whites.
- Lower: "Flat" look, more gray. Better for post-processing/CV analysis.

SATURATION (0.0 - 32.0)
- Default: 1.0
- What it does: Controls color intensity.
- 0.0: Black and White (Grayscale).

AWB MODE (Auto White Balance)
- Range: 0-7
- Purpose: Compensates for the color tint of your light source.
- 0: Auto         (Camera analyzes scene and guesses)
- 1: Incandescent (For standard domestic bulbs - removes yellow tint)
- 2: Tungsten     (For studio hot lights - similar to Incandescent)
- 3: Fluorescent  (For office tube lights - removes green tint)
- 4: Indoor       (General calibration for mixed artificial light)
- 5: Daylight     (For sunny outdoor scenes - removes blue tint)
- 6: Cloudy       (For overcast outdoor scenes - warms up the image)
- 7: Custom       (Disables algos; uses manual ColourGains if provided)

METERING MODE (Auto Exposure)
- Range: 0-3
- Purpose: Decides which part of the image dictates the brightness.
- 0 (Center-Weighted): Prioritizes the middle 50% of the frame.
- 1 (Spot): Only measures a tiny dot in the center. Essential for backlit subjects.
- 2 (Matrix): Analyzes the whole frame to find a balanced average.

HDR MODE (High Dynamic Range)
- Range: 0-4
- Purpose: Helps when the scene has both very bright sun and dark shadows.
- How: The PiSP captures different exposures and merges them.

================================================================================
3. DIGITAL ZOOM (SCALERCROP) & HARDWARE EXECUTION

	| Mode | CCT Range (K) | Lighting Description |
	| --- | --- | --- |
	| Incandescent | 2500 – 3000 | Warm domestic bulbs |
	| Tungsten | 3000 – 3500 | Studio hot lights |
	| Fluorescent | 4000 – 4700 | Green-shift office tubes |
	| Daylight | 5500 – 6500 | Standard sun |
	| Cloudy | 7000 – 8000 | Overcast sky |
Coordinates:    ALWAYS relative to full 4056 x 3040 sensor.

IS DIGITAL ZOOM "HARDWARE" OR "SOFTWARE"?
-----------------------------------------
It is **Hardware-Accelerated Digital Zoom**.

1. It is "Digital":
	 - Unlike a physical lens zoom, this throws away pixels. 
	 - If you zoom 2x, you are using only the center 25% of the sensor. 
	 - You lose resolution, but the image does not get "blocky" if you stay 
		 within the sensor's limits (e.g., zooming into 1080p from a 12MP sensor 
		 looks perfect).

2. It is done in "Hardware" (The ISP):
	 - The cropping is NOT done by your Python script or the CPU.
	 - It is handled by the Raspberry Pi 5's dedicated Image Signal Processor (PiSP).
	 - **Benefit:** It causes ZERO delay or CPU load.
	 - **Benefit:** It can actually INCREASE framerate. If you crop to a small 
		 window, the sensor has fewer lines to read, potentially allowing faster FPS.

================================================================================
4. RESOLUTION MATH (DIVISIONS OF FULL SENSOR)
================================================================================
Scale 1/1:  4056 x 3040  (Native Full - 12MP)
Scale 1/2:  2028 x 1520  (Native Binning - 3MP - Best General Mode)
Scale 1/4:  1014 x 760   (Integer Scale)
Scale 1/8:   507 x 380   (Integer Scale - close to 480p)

## PiSP IMX477 Tuning Profile
The Raspberry Pi 5 ships distinct Image Signal Processor tuning files for each supported sensor. The PiSP profile located at `/usr/share/libcamera/ipa/rpi/pisp/imx477.json` defines the default behavior for the IMX477 (HQ) camera on RP1-based boards.

- Target Platform: `target` is set to `pisp`, meaning the profile is tailored for the Pi 5 hardware pipeline. Earlier SoCs use BCM2835-oriented tuning and do not expose PiSP-only features such as temporal denoise or hardware HDR blending.
- Auto Exposure: The `rpi.agc` block defines metering masks for matrix (uniform), center-weighted (gradient emphasis), and spot (tight central cluster) modes. Automatic shutter is capped at 66 ms (normal) or 120 ms (long exposure), while gain tops out at 8.0 or 12.0 respectively; manual overrides can exceed these limits when required.
- White Balance: `rpi.awb` enumerates fixed correlated color temperature bands, mapping Incandescent (2500–3000 K), Tungsten (3000–3500 K), Fluorescent (4000–4700 K), Daylight (5500–6500 K), and Cloudy (7000–8000 K) to the ISP’s AWB presets.
- HDR Pipeline: `rpi.hdr` enables dual-exposure fusion with `channel_map` assigning short and long reads. A dedicated Night mode tweaks tone mapping to lift deep shadows (e.g., input 20000 → output 47000) for low-light scenes.
- Lens Shading: The `rpi.alsc` luminance and chroma LUTs flatten vignetting and color shifts, with separate calibration tables for warm (3000 K) and daylight (5000 K) illumination to accommodate changing white points.
- Black Level: `rpi.black_level` fixes the sensor’s electrical floor at 4096, ensuring downstream processing treats anything lower as true black.
- Practical Tips: Night captures benefit from selecting the HDR Night profile or explicitly raising shutter/gain beyond the auto limits. Swapping lenses requires retuning the ALSC tables to avoid color rings or dark corners.

## Troubleshooting
- If `/available_cameras` returns empty, confirm Picamera2 is installed and the camera ribbon cable is seated. Run `libcamera-hello` to validate the hardware.
- For permission errors, add the executing user to the `video` group and reboot.
- High resolution captures and long exposures can take several seconds. Increase HTTP client timeouts when requesting `/capture_dng` or `/rawframe`.
- When running on Wi-Fi, prefer Ethernet for high throughput raw downloads.

## Custom Raw File Format Specification

**File Type:** Headerless Binary Dump  
**Extension:** `.raw`  
**Endianness:** Little-Endian  
**Metadata source:** Filename only (no internal header)

### 1. Filename Convention

All critical metadata required to parse the file is encoded in the filename string.

Format:  
`capture_cam{ID}_{Width}x{Height}_{Depth}bit_acc{N}_{Timestamp}.raw`

- **Width / Height:** The valid image resolution (e.g., 4056, 3040).
- **Depth:** The sensor readout bit-depth (`10bit` or `12bit`).
- **acc{N}:** The accumulation count. `acc1` is a single frame; `acc4` is a stack of 4 frames.

### 2. Binary Data Structure

The file consists of a contiguous stream of pixels with no file header.

- **Container Type:**
  - Standard / Mean Mode: `uint16` (Unsigned 16-bit integer).
  - Sum Mode: `uint64` (Unsigned 64-bit integer).
- **Bit Alignment (Critical):** Data is Left-Aligned (MSB aligned) inside the 16-bit container.
  - **12-bit Mode:** Values range from 0 to 65,520. (Shift right by 4 bits to get 0–4095).
  - **10-bit Mode:** Values range from 0 to 65,472. (Shift right by 6 bits to get 0–1023).

### 3. Hardware Padding (Stride)

The ISP (Image Signal Processor) adds invisible padding pixels to the end of every row for memory alignment. Parsers must read the "Padded Width" and crop to the "Valid Width".

| Valid Width | Stride (Padded Width) | Padding Pixels | Bytes per Row (16-bit) |
| ----------- | --------------------- | -------------- | ---------------------- |
| 4056        | 4064                  | +8             | 8,128 bytes            |
| 2028        | 2048                  | +20            | 4,096 bytes            |
| 1332        | 1344                  | +12            | 2,688 bytes            |

TODO: add saturation detection
