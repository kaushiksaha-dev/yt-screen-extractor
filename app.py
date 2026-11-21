"""
YT Screen Extractor - Streamlit Web UI
Ingests YouTube videos, extracts unique presentation screens (slides, code, diagrams),
and compiles a clean landscape PDF with clickable YouTube timestamp hyperlinks.
Features:
  - Fast, compressed landscape PDF generation (fixing uncompressed bitmap bug).
  - Quality presets: Balanced (1440px/q80), High Quality (1920px/q88), AI-Ready Compact (1280px/q70).
  - AI Upload Auto-Split: None, Claude Safe (25 MB), Gemini Safe (90 MB).
  - Paginated gallery (24 screens/page) with lightweight cached thumbnails.
  - Skip Blurry Transition Frames (Laplacian variance) & Tutorial Mode (skip fast cuts / B-roll).
"""

import os
import sys
import time
from typing import Dict, List, Optional
from PIL import Image
import streamlit as st

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import (
    ExtractionConfig,
    ExtractedScreen,
    PDF_QUALITY_PRESETS,
    PDFQualityPreset,
    SPLIT_PRESETS,
    get_coarse_stride,
)
from core.detector import extract_unique_screens
from core.downloader import YouTubeDownloadError, download_video_stream, get_video_info
from core.pdf_builder import build_pdf
from core.utils import cleanup_file, create_zip_archive, format_timestamp, parse_time_str


st.set_page_config(
    page_title="YT Screen Extractor",
    page_icon="📺",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom Styling
st.markdown(
    """
    <style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        font-size: 1.05rem;
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .metric-card {
        background-color: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .screen-card {
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 8px;
        padding: 0.75rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .badge {
        display: inline-block;
        padding: 0.2rem 0.5rem;
        font-size: 0.8rem;
        font-weight: 600;
        border-radius: 4px;
        background-color: #EFF6FF;
        color: #1D4ED8;
    }
    .page-indicator {
        font-size: 1rem;
        font-weight: 600;
        color: #334155;
        text-align: center;
        padding-top: 0.5rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def ensure_thumbnail(screen: ExtractedScreen) -> str:
    """Ensure lightweight cached thumbnail exists on disk for instantaneous UI loading."""
    if screen.thumbnail_path and os.path.isfile(screen.thumbnail_path):
        return screen.thumbnail_path

    screen_dir = os.path.dirname(screen.image_path)
    thumbs_dir = os.path.join(screen_dir, ".thumbnails")
    os.makedirs(thumbs_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(screen.image_path))[0]
    thumb_path = os.path.join(thumbs_dir, f"{base}.jpg")

    if os.path.isfile(thumb_path):
        screen.thumbnail_path = thumb_path
        return thumb_path

    try:
        with Image.open(screen.image_path) as img:
            w, h = img.size
            thumb_w = min(w, 480)
            thumb_h = max(1, int(h * (thumb_w / w)))
            resample = getattr(Image.Resampling, "BILINEAR", Image.BILINEAR)
            thumb = img.resize((thumb_w, thumb_h), resample=resample)
            thumb.convert("RGB").save(thumb_path, format="JPEG", quality=75)
        screen.thumbnail_path = thumb_path
        return thumb_path
    except Exception:
        return screen.image_path


def init_session():
    if "extracted_screens" not in st.session_state:
        st.session_state.extracted_screens = []
    if "video_info" not in st.session_state:
        st.session_state.video_info = None
    if "pdf_paths" not in st.session_state:
        st.session_state.pdf_paths = []
    if "zip_path" not in st.session_state:
        st.session_state.zip_path = None
    if "selected_indices" not in st.session_state:
        st.session_state.selected_indices = set()
    if "output_dir" not in st.session_state:
        st.session_state.output_dir = None
    if "gallery_page" not in st.session_state:
        st.session_state.gallery_page = 1


init_session()

# Header
st.markdown('<div class="main-header">📺 YT Screen Extractor</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Extract slides, code editors, and diagrams from YouTube presentations into a clean, clickable PDF.</div>',
    unsafe_allow_html=True,
)

# Sidebar: Advanced Extraction Controls
st.sidebar.header("⚙️ Extraction Settings")

sampling_mode = st.sidebar.radio(
    "Sampling Mode",
    options=["Auto (Adaptive)", "Manual"],
    index=0,
    help="Auto (Adaptive) selects initial 2s–10s coarse stride based on video length and runs a 1s refinement pass on transitions.",
)
is_auto = (sampling_mode == "Auto (Adaptive)")

if is_auto:
    st.sidebar.caption("⚡ **Auto (Adaptive)**: Under 15m → 2s, 15–60m → 4s, 1–3h → 6s, >3h → 10s. Refines transitions at 1s intervals.")
    interval_val = 2.0
else:
    interval_val = st.sidebar.slider(
        "Sampling Interval (seconds)",
        min_value=0.5,
        max_value=10.0,
        value=2.0,
        step=0.5,
        help="Fixed interval between frame checks.",
    )

sensitivity = st.sidebar.select_slider(
    "Change Sensitivity",
    options=["low", "medium", "high"],
    value="medium",
    help="Medium ignores blinking cursors and minor noise; High catches subtle incremental code lines.",
)

st.sidebar.markdown("---")
st.sidebar.subheader("Detection Filters")

skip_blurry = st.sidebar.checkbox(
    "Skip Blurry Transition Frames",
    value=True,
    help="Uses Laplacian variance to automatically skip out-of-focus fade/intro transition frames.",
)

tutorial_mode = st.sidebar.checkbox(
    "Tutorial Mode — Skip Fast-Moving / B-Roll Clips",
    value=False,
    help="Only commits screens that stay steady across at least 2 consecutive sampled frames (filters out B-roll cuts).",
)

st.sidebar.markdown("---")
st.sidebar.subheader("Ignore Webcam Corners (Mask 20%)")
st.sidebar.caption("Masks out corner regions during comparison only; output frames remain full and unmasked.")

col_w1, col_w2 = st.sidebar.columns(2)
with col_w1:
    mask_br = st.checkbox("Bottom-Right", value=True)
    mask_bl = st.checkbox("Bottom-Left", value=False)
with col_w2:
    mask_tr = st.checkbox("Top-Right", value=False)
    mask_tl = st.checkbox("Top-Left", value=False)

webcam_corners = []
if mask_br:
    webcam_corners.append("bottom-right")
if mask_bl:
    webcam_corners.append("bottom-left")
if mask_tr:
    webcam_corners.append("top-right")
if mask_tl:
    webcam_corners.append("top-left")

st.sidebar.markdown("---")
st.sidebar.subheader("📄 PDF Quality & Split Options")

pdf_quality_options = {
    "Balanced (Default — Sharp Code & Text)": "balanced",
    "High Quality (1080p Archival)": "high",
    "AI-Ready Compact": "compact",
}
quality_label = st.sidebar.selectbox(
    "PDF Quality Preset",
    options=list(pdf_quality_options.keys()),
    index=0,
    help="Balanced: 1440px max width, quality 80. High: 1920px archival. Compact: 1280px, quality 70.",
)
chosen_quality = pdf_quality_options[quality_label]

split_options = {
    "None (Single PDF)": None,
    "Claude Safe (25 MB)": 25,
    "Gemini Safe (90 MB)": 90,
}
split_label = st.sidebar.selectbox(
    "Auto-Split PDF Size Limit",
    options=list(split_options.keys()),
    index=0,
    help="Automatically splits screens into sequential part files (each strictly under the MB cap) if the PDF would exceed it.",
)
chosen_split = split_options[split_label]

st.sidebar.markdown("---")
st.sidebar.subheader("Time Range (Optional)")
col_t1, col_t2 = st.sidebar.columns(2)
with col_t1:
    start_str = st.text_input("Start Time", placeholder="00:00:00 or sec", help="e.g., 00:01:30 or 90")
with col_t2:
    end_str = st.text_input("End Time", placeholder="End of video", help="e.g., 00:10:00 or 600")

st.sidebar.markdown("---")
st.sidebar.subheader("System Options")
keep_video = st.sidebar.checkbox("Keep downloaded video file", value=False, help="Retain local MP4 in outputs directory.")
cookies_browser = st.sidebar.selectbox(
    "Browser Cookies (If blocked)",
    options=["None", "chrome", "edge", "firefox", "brave"],
    index=0,
    help="Pass browser cookies if YouTube displays bot/sign-in verification.",
)
cookies_param = None if cookies_browser == "None" else cookies_browser

# Main Input Section
url_input = st.text_input(
    "YouTube Video URL",
    placeholder="https://www.youtube.com/watch?v=...",
    help="Paste any standard YouTube watch link, share link, or embed URL.",
)

col_run, col_clear = st.columns([1, 5])
with col_run:
    run_button = st.button("🚀 Extract Screens", type="primary", use_container_width=True)
with col_clear:
    if st.button("Reset / Clear", use_container_width=False):
        st.session_state.extracted_screens = []
        st.session_state.video_info = None
        st.session_state.pdf_paths = []
        st.session_state.zip_path = None
        st.session_state.selected_indices = set()
        st.session_state.output_dir = None
        st.session_state.gallery_page = 1
        st.rerun()

# Execution Logic
if run_button:
    if not url_input.strip():
        st.error("Please enter a valid YouTube video URL.")
    else:
        start_sec = parse_time_str(start_str)
        end_sec = parse_time_str(end_str)

        config = ExtractionConfig(
            video_url=url_input.strip(),
            auto_interval=is_auto,
            interval_seconds=interval_val,
            sensitivity=sensitivity,
            mask_webcam_corners=webcam_corners,
            start_time=start_sec,
            end_time=end_sec,
            keep_video=keep_video,
            cookies_from_browser=cookies_param,
            skip_blurry=skip_blurry,
            tutorial_mode=tutorial_mode,
            pdf_quality=chosen_quality,
            max_pdf_mb=chosen_split,
        )

        progress_container = st.container()
        with progress_container:
            status_text = st.empty()
            progress_bar = st.progress(0.0)

        temp_video_path = None
        try:
            # 1. Video Download
            status_text.markdown("**Step 1 of 3:** Fetching video stream via yt-dlp...")
            progress_bar.progress(0.1)

            def dl_hook(pct, msg):
                progress_bar.progress(min(0.35, 0.05 + (pct / 100.0) * 0.30))
                status_text.markdown(f"**Step 1 of 3:** {msg}")

            temp_dir = os.path.join("outputs", "_temp")
            temp_video_path, info = download_video_stream(
                config.video_url,
                output_dir=temp_dir,
                cookies_from_browser=config.cookies_from_browser,
                progress_callback=dl_hook,
            )

            st.session_state.video_info = info
            video_output_dir = os.path.join("outputs", info["sanitized_title"])
            screens_dir = os.path.join(video_output_dir, "screens")
            st.session_state.output_dir = video_output_dir

            # 2. Screen Detection & Deduplication
            if is_auto:
                eff_stride = get_coarse_stride(info.get("duration", 0))
                mode_desc = f"Scanning frames (Auto mode: {eff_stride:.0f}s coarse scan with 1s transition refinement)"
            else:
                mode_desc = f"Scanning frames (Manual mode: {interval_val:.1f}s interval)"
            if tutorial_mode:
                mode_desc += " [Tutorial Mode: Active]"
            status_text.markdown(f"**Step 2 of 3:** {mode_desc}...")
            progress_bar.progress(0.40)

            def det_hook(pct, msg):
                progress_bar.progress(min(0.85, 0.40 + (pct / 100.0) * 0.45))
                status_text.markdown(f"**Step 2 of 3:** {msg}")

            screens = extract_unique_screens(
                video_path=temp_video_path,
                video_id=info["id"],
                video_title=info["title"],
                output_screens_dir=screens_dir,
                config=config,
                progress_callback=det_hook,
            )

            if not screens:
                st.warning("No unique screens detected in the given time range.")
                st.stop()

            st.session_state.extracted_screens = screens
            st.session_state.selected_indices = {s.index for s in screens}
            st.session_state.gallery_page = 1

            # 3. PDF Compilation & ZIP bundling
            status_text.markdown("**Step 3 of 3:** Compiling landscape PDF with clickable timestamp links...")
            progress_bar.progress(0.90)

            pdf_file = os.path.join(video_output_dir, f"{info['sanitized_title']}.pdf")

            def pdf_hook(pct, msg):
                progress_bar.progress(min(0.98, 0.90 + (pct / 100.0) * 0.08))
                status_text.markdown(f"**Step 3 of 3:** {msg}")

            generated_paths = build_pdf(
                screens=screens,
                output_pdf_path=pdf_file,
                video_title=info["title"],
                quality_preset=chosen_quality,
                max_pdf_mb=chosen_split,
                progress_callback=pdf_hook,
            )
            st.session_state.pdf_paths = generated_paths

            zip_file = os.path.join(video_output_dir, f"{info['sanitized_title']}_screens.zip")
            create_zip_archive(screens_dir, zip_file)
            st.session_state.zip_path = zip_file

            progress_bar.progress(1.0)
            status_text.success(f"Extraction complete! Extracted {len(screens)} unique screens.")
            time.sleep(1)
            progress_container.empty()
            st.rerun()

        except YouTubeDownloadError as e:
            st.error(f"Download Error: {str(e)}")
        except Exception as e:
            st.error(f"Extraction Error: {str(e)}")
        finally:
            if not keep_video and temp_video_path:
                cleanup_file(temp_video_path)


# Results View & Interactive Review Gallery
if st.session_state.extracted_screens and st.session_state.video_info:
    info = st.session_state.video_info
    screens = st.session_state.extracted_screens
    pdf_paths = st.session_state.pdf_paths

    st.markdown("---")
    st.subheader("📊 Extraction Summary")

    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Video Title", info["title"][:28] + ("..." if len(info["title"]) > 28 else ""))
    with m_col2:
        st.metric("Video Duration", format_timestamp(info["duration"]))
    with m_col3:
        st.metric("Total Extracted Screens", len(screens))
    with m_col4:
        selected_count = len(st.session_state.selected_indices)
        st.metric("Selected for PDF", f"{selected_count} / {len(screens)}")

    # Action Toolbar & Downloads
    st.markdown("### 📥 Download Assets")

    # Download buttons for PDF(s)
    if pdf_paths:
        if len(pdf_paths) == 1:
            p = pdf_paths[0]
            if os.path.isfile(p):
                sz_mb = os.path.getsize(p) / (1024 * 1024)
                with open(p, "rb") as f:
                    st.download_button(
                        label=f"📄 Download Landscape PDF ({sz_mb:.2f} MB)",
                        data=f.read(),
                        file_name=os.path.basename(p),
                        mime="application/pdf",
                        type="primary",
                    )
        else:
            st.info(f"PDF automatically split into **{len(pdf_paths)} parts** to remain strictly within the upload size limit:")
            part_cols = st.columns(min(len(pdf_paths), 4))
            for p_idx, p in enumerate(pdf_paths):
                if os.path.isfile(p):
                    sz_mb = os.path.getsize(p) / (1024 * 1024)
                    col = part_cols[p_idx % min(len(pdf_paths), 4)]
                    with col:
                        with open(p, "rb") as f:
                            st.download_button(
                                label=f"📄 Part {p_idx + 1} ({sz_mb:.2f} MB)",
                                data=f.read(),
                                file_name=os.path.basename(p),
                                mime="application/pdf",
                                use_container_width=True,
                            )

            # ZIP containing all PDF parts
            pdf_parts_zip = os.path.join(st.session_state.output_dir, f"{info['sanitized_title']}_pdf_parts.zip")
            import zipfile
            with zipfile.ZipFile(pdf_parts_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in pdf_paths:
                    if os.path.isfile(p):
                        zf.write(p, arcname=os.path.basename(p))
            if os.path.isfile(pdf_parts_zip):
                zip_sz = os.path.getsize(pdf_parts_zip) / (1024 * 1024)
                with open(pdf_parts_zip, "rb") as f:
                    st.download_button(
                        label=f"📦 Download All PDF Parts (.ZIP — {zip_sz:.2f} MB)",
                        data=f.read(),
                        file_name=os.path.basename(pdf_parts_zip),
                        mime="application/zip",
                        type="primary",
                    )

    # Downloads for Screens ZIP & Rebuild Button
    d_row1, d_row2 = st.columns([1.5, 2.5])
    with d_row1:
        if st.session_state.zip_path and os.path.isfile(st.session_state.zip_path):
            sz_mb = os.path.getsize(st.session_state.zip_path) / (1024 * 1024)
            with open(st.session_state.zip_path, "rb") as f:
                st.download_button(
                    label=f"📦 Download All Full-Res Screens (.ZIP — {sz_mb:.2f} MB)",
                    data=f.read(),
                    file_name=os.path.basename(st.session_state.zip_path),
                    mime="application/zip",
                    use_container_width=True,
                )

    with d_row2:
        if st.button(f"🔄 Rebuild PDF from Current Selection [{quality_label} | {split_label}]", use_container_width=True):
            for s in screens:
                s.is_selected = (s.index in st.session_state.selected_indices)

            selected_list = [s for s in screens if s.is_selected]
            if not selected_list:
                st.warning("Please select at least one screen to compile into PDF.")
            else:
                base_pdf = os.path.join(st.session_state.output_dir, f"{info['sanitized_title']}.pdf")
                with st.spinner("Regenerating optimized PDF..."):
                    new_paths = build_pdf(
                        screens=screens,
                        output_pdf_path=base_pdf,
                        video_title=info["title"],
                        quality_preset=chosen_quality,
                        max_pdf_mb=chosen_split,
                    )
                st.session_state.pdf_paths = new_paths
                st.success(f"PDF rebuilt successfully ({len(new_paths)} file{'s' if len(new_paths) > 1 else ''})!")
                time.sleep(1)
                st.rerun()

    # Paginated Gallery
    st.markdown("---")
    st.markdown("### 🖼️ Extracted Screen Gallery")
    st.caption("Review extracted screens below. Uncheck any screen to exclude it from the PDF, then click 'Rebuild PDF'.")

    # Select All / Deselect All
    col_sel1, col_sel2, col_space = st.columns([1, 1, 4])
    with col_sel1:
        if st.button("Select All", use_container_width=True):
            st.session_state.selected_indices = {s.index for s in screens}
            st.rerun()
    with col_sel2:
        if st.button("Deselect All", use_container_width=True):
            st.session_state.selected_indices = set()
            st.rerun()

    # Pagination Logic: 24 screens per page
    PAGE_SIZE = 24
    total_screens = len(screens)
    total_pages = max(1, (total_screens + PAGE_SIZE - 1) // PAGE_SIZE)
    st.session_state.gallery_page = max(1, min(st.session_state.gallery_page, total_pages))
    current_page = st.session_state.gallery_page

    start_idx = (current_page - 1) * PAGE_SIZE
    end_idx = min(start_idx + PAGE_SIZE, total_screens)
    page_screens = screens[start_idx:end_idx]

    # Pagination navigation controls
    nav_prev, nav_info, nav_next = st.columns([1, 3, 1])
    with nav_prev:
        if st.button("◀ Previous Page", disabled=(current_page <= 1), use_container_width=True):
            st.session_state.gallery_page -= 1
            st.rerun()
    with nav_info:
        st.markdown(
            f'<div class="page-indicator">Page {current_page} of {total_pages} &nbsp;•&nbsp; Showing Screens {start_idx + 1}–{end_idx} of {total_screens}</div>',
            unsafe_allow_html=True,
        )
    with nav_next:
        if st.button("Next Page ▶", disabled=(current_page >= total_pages), use_container_width=True):
            st.session_state.gallery_page += 1
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    # Render current page in 3 responsive columns with cached lightweight thumbnails
    gallery_cols = st.columns(3)
    for idx, screen in enumerate(page_screens):
        col = gallery_cols[idx % 3]
        with col:
            with st.container():
                thumb_path = ensure_thumbnail(screen)
                st.image(thumb_path, use_container_width=True)

                is_checked = screen.index in st.session_state.selected_indices
                checked = st.checkbox(
                    f"#{screen.index:03d} • {screen.timestamp_str}",
                    value=is_checked,
                    key=f"chk_screen_{screen.index}",
                )
                if checked and screen.index not in st.session_state.selected_indices:
                    st.session_state.selected_indices.add(screen.index)
                elif not checked and screen.index in st.session_state.selected_indices:
                    st.session_state.selected_indices.remove(screen.index)

                st.markdown(
                    f"[▶ Open at {screen.timestamp_str} on YouTube ↗]({screen.youtube_url})",
                    unsafe_allow_html=True,
                )
                st.markdown("<br>", unsafe_allow_html=True)

    # Bottom pagination controls for convenience
    if total_pages > 1:
        b_prev, b_info, b_next = st.columns([1, 3, 1])
        with b_prev:
            if st.button("◀ Previous", key="bottom_prev", disabled=(current_page <= 1), use_container_width=True):
                st.session_state.gallery_page -= 1
                st.rerun()
        with b_info:
            st.markdown(
                f'<div class="page-indicator">Page {current_page} of {total_pages}</div>',
                unsafe_allow_html=True,
            )
        with b_next:
            if st.button("Next ▶", key="bottom_next", disabled=(current_page >= total_pages), use_container_width=True):
                st.session_state.gallery_page += 1
                st.rerun()
