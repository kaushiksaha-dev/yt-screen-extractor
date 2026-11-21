# YT Screen Extractor

A local Python application and CLI agent tool that takes a YouTube video URL, extracts every unique presentation screen (slides, code editors, whiteboard sketches, diagrams), eliminates duplicate frames, saves high-resolution image assets, and compiles them into a clean landscape PDF with clickable YouTube timestamp hyperlinks.

---

## Key Features & Upgrades

- **Direct YouTube Video Ingestion**:
  - Uses `yt-dlp` to download single-file video streams up to 1080p without requiring an external system FFmpeg binary.
  - Supports video sections via `--start` and `--end` timestamps and browser cookies (`--cookies-from-browser`).
  - Automatic temporary video cleanup once extraction finishes.

- **Auto (Adaptive) Coarse-to-Fine Detection**:
  - Automatically selects an optimal initial coarse stride based on video length:
    - **< 15 min**: 2s coarse stride
    - **15–60 min**: 4s coarse stride
    - **1–3 hours**: 6s coarse stride
    - **> 3 hours**: 10s coarse stride
  - Seamlessly skips non-changing frames during static stretches (2x–5x faster scan speeds).
  - Triggers a 1-second precision refinement pass upon transition detection to capture the exact last-stable frame before any cut.
  - Manual interval control remains available at any time.

- **Optimized PDF Generation (Bug Fix)**:
  - Embeds compressed JPEG streams via `io.BytesIO` and `ImageReader`, with `pageCompression=1` enabled on the ReportLab canvas.
  - Prevents uncompressed bitmap file size bloating (saving 50x–80x storage space without quality loss).
  - Never modifies or downscales the original full-resolution screen PNGs saved on disk.

- **PDF Quality Presets & AI Upload Auto-Split**:
  - **Quality Presets**:
    - `Balanced (Default)`: 1440px max width, JPEG quality 80 (optimal balance for sharp code and text).
    - `High Quality (1080p Archival)`: 1920px max width, JPEG quality 88.
    - `AI-Ready Compact`: 1280px max width, JPEG quality 70.
  - **AI Upload Auto-Split**:
    - `None (Single PDF)`: Single consolidated document.
    - `Claude Safe (25 MB)`: Automatically splits into `<title>_part1.pdf`, `<title>_part2.pdf`, etc. (using a 24 MB safety margin) if the document would exceed 25 MB.
    - `Gemini Safe (90 MB)`: Automatically splits using an 88 MB safety margin if exceeding 90 MB.
    - Preserves landscape layout, footers, and clickable YouTube timestamp links across all parts.
    - Includes a one-click "Download All PDF Parts (.ZIP)" button in the UI.

- **Fast Paginated Gallery**:
  - Paginated at 24 screens per page with Previous / Next navigation.
  - Generates lightweight cached thumbnails (`.thumbnails/`) for instantaneous gallery rendering even on videos with 300+ screens.

- **Cleaner Tutorial Detection**:
  - **Skip Blurry Transition Frames** (default: ON): Uses Laplacian variance (`cv2.Laplacian(gray, cv2.CV_64F).var()`) to skip out-of-focus fade/intro transition frames.
  - **Tutorial Mode — Skip Fast-Moving / B-Roll Clips** (default: OFF): Only commits a screen if it stays steady across at least 2 consecutive sampled frames, filtering out transient cuts and camera B-roll.
  - **Webcam Corner Masking**: Optional 20% corner masking (Bottom-Right, Bottom-Left, Top-Right, Top-Left) during comparison only; all saved screen captures are 100% full unmasked frames.

- **Landscape PDF with Clickable YouTube Links**:
  - Standard landscape A4 presentation layout.
  - Screens fitted cleanly without distortion or stretching.
  - Subtitle footer with screen number, video title, and timestamp.
  - Vector clickable link (`https://youtu.be/<id>?t=<sec>`) on each page that jumps directly to that second in the YouTube video.

---

## Installation

Ensure Python 3.10+ is installed.

```bash
# Navigate to project directory
cd C:\Users\User\.gemini\antigravity\scratch\yt-screen-extractor

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Windows Command Prompt:
.venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt
```

---

## Usage

### 1. Web UI (Streamlit)

Launch the interactive web application:

```bash
.venv\Scripts\streamlit run app.py
```

Open `http://localhost:8501` in your browser.
1. Paste a YouTube URL.
2. Select desired settings: Sampling Interval, Sensitivity, PDF Quality Preset (`Balanced`, `High`, `Compact`), and Auto-Split size limit (`None`, `Claude Safe (25 MB)`, `Gemini Safe (90 MB)`).
3. Click **🚀 Extract Screens**.
4. In the gallery, review extracted screens, deselect unwanted slides, click **Rebuild PDF from Current Selection**, and download your optimized `.pdf` and `.zip`.

### 2. CLI Mode (`extract.py`)

Run directly from the terminal or automate within agent workflows:

```bash
# Basic extraction with balanced preset
.venv\Scripts\python extract.py "https://www.youtube.com/watch?v=VIDEO_ID"

# With Claude Safe auto-split (25 MB) and tutorial mode
.venv\Scripts\python extract.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --pdf-quality balanced \
  --max-pdf-mb 25 \
  --tutorial-mode \
  --mask-webcam br

# Machine-readable JSON output for automated agents
.venv\Scripts\python extract.py "https://www.youtube.com/watch?v=VIDEO_ID" --json
```

#### CLI Options

| Option | Flag | Description | Default |
| :--- | :--- | :--- | :--- |
| `url` | Positional | YouTube video URL | Required |
| `--interval` | `-i` | Sampling interval: `'auto'` for adaptive coarse-to-fine scan, or numeric seconds | `auto` |
| `--sensitivity` | `-s` | Change sensitivity: `low`, `medium`, `high` | `medium` |
| `--pdf-quality` | None | PDF quality preset: `balanced`, `high`, `compact` | `balanced` |
| `--max-pdf-mb` | None | Auto-split PDF if exceeding this size limit in MB (e.g. `25` or `90`) | None |
| `--skip-blurry` | None | Skip blurry fade/intro transition frames | True |
| `--tutorial-mode`| None | Only commit screens steady across $\ge 2$ consecutive samples | False |
| `--mask-webcam` | `-m` | Corner to mask during comparison: `br`, `bl`, `tr`, `tl` | None |
| `--start` | None | Start timestamp (e.g., `00:01:30` or `90`) | Video start |
| `--end` | None | End timestamp (e.g., `00:05:00` or `300`) | Video end |
| `--output-dir` | `-o` | Output directory root | `outputs` |
| `--keep-video` | None | Keep downloaded MP4 file after extraction | False |
| `--cookies-from-browser` | None | Browser name to extract cookies if YouTube blocks request | None |
| `--json` | None | Output structured JSON to stdout | False |
