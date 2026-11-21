#!/usr/bin/env python3
"""
YT Screen Extractor - CLI Agent Tool
Takes a YouTube URL, extracts unique screens, eliminates duplicate frames,
saves high-res images, and compiles a landscape PDF with clickable YouTube timestamp links.
Features:
  - Embedded compressed JPEGs via ImageReader (solving file bloating).
  - Quality presets: Balanced (1440px/q80), High (1920px/q88), Compact (1280px/q70).
  - AI auto-split limit (e.g. 25 MB for Claude, 90 MB for Gemini).
  - Laplacian blur skip & Tutorial Mode.
"""

import argparse
import json
import os
import sys
import time

# Ensure project root is in python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure safe UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.config import ExtractionConfig
from core.detector import extract_unique_screens
from core.downloader import YouTubeDownloadError, download_video_stream
from core.pdf_builder import build_pdf
from core.utils import cleanup_file, create_zip_archive, parse_time_str


def print_banner():
    banner = """
=================================================================
                     YT SCREEN EXTRACTOR                         
     Slide, Code & Diagram Capture Tool with Clickable PDF Links 
=================================================================
"""
    print(banner)


def main():
    parser = argparse.ArgumentParser(
        description="YT Screen Extractor: Ingest YouTube videos, extract unique screens, and compile into a clickable PDF.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("url", help="YouTube video URL (watch, share link, shorts, embed)")
    parser.add_argument(
        "--interval",
        "-i",
        type=str,
        default="auto",
        help="Sampling interval: 'auto' for adaptive coarse-to-fine scan (default), or numeric seconds (e.g. 2, 4)",
    )
    parser.add_argument(
        "--sensitivity",
        "-s",
        choices=["low", "medium", "high"],
        default="medium",
        help="Sensitivity threshold for screen change detection",
    )
    parser.add_argument(
        "--mask-webcam",
        "-m",
        nargs="*",
        default=[],
        help="Webcam corners to ignore during difference comparison: br (bottom-right), bl (bottom-left), tr, tl",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=None,
        help="Optional start timestamp (HH:MM:SS or seconds, e.g. 00:01:30 or 90)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default=None,
        help="Optional end timestamp (HH:MM:SS or seconds, e.g. 00:05:00 or 300)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=str,
        default="outputs",
        help="Directory where output folders will be created",
    )
    parser.add_argument(
        "--keep-video",
        action="store_true",
        help="Keep the downloaded video file instead of auto-deleting it",
    )
    parser.add_argument(
        "--cookies-from-browser",
        type=str,
        default=None,
        help="Extract browser cookies if YouTube blocks bot requests (e.g. chrome, edge, firefox)",
    )
    parser.add_argument(
        "--pdf-quality",
        choices=["balanced", "high", "compact"],
        default="balanced",
        help="PDF Quality Preset: balanced (1440px/q80), high (1920px/q88), compact (1280px/q70)",
    )
    parser.add_argument(
        "--max-pdf-mb",
        type=float,
        default=None,
        help="Auto-split PDF if exceeding this size limit in MB (e.g. 25 for Claude, 90 for Gemini)",
    )
    parser.add_argument(
        "--skip-blurry",
        dest="skip_blurry",
        action="store_true",
        default=True,
        help="Skip out-of-focus fade/intro transition frames using Laplacian variance (default: True)",
    )
    parser.add_argument(
        "--no-skip-blurry",
        dest="skip_blurry",
        action="store_false",
        help="Disable skipping of blurry frames",
    )
    parser.add_argument(
        "--tutorial-mode",
        action="store_true",
        default=False,
        help="Tutorial Mode: only commit screens steady across >= 2 consecutive samples (skips B-roll)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON summary to stdout upon completion",
    )

    args = parser.parse_args()

    if not args.json:
        print_banner()

    start_sec = parse_time_str(args.start) if args.start else None
    end_sec = parse_time_str(args.end) if args.end else None

    interval_str = str(args.interval).strip().lower()
    if interval_str == "auto":
        auto_interval = True
        interval_sec = 2.0
    else:
        auto_interval = False
        try:
            interval_sec = float(interval_str)
        except ValueError:
            auto_interval = True
            interval_sec = 2.0

    config = ExtractionConfig(
        video_url=args.url,
        auto_interval=auto_interval,
        interval_seconds=interval_sec,
        sensitivity=args.sensitivity,
        mask_webcam_corners=args.mask_webcam,
        start_time=start_sec,
        end_time=end_sec,
        keep_video=args.keep_video,
        output_dir=args.output_dir,
        cookies_from_browser=args.cookies_from_browser,
        skip_blurry=args.skip_blurry,
        tutorial_mode=args.tutorial_mode,
        pdf_quality=args.pdf_quality,
        max_pdf_mb=args.max_pdf_mb,
    )

    t0 = time.time()
    downloaded_video_path = None

    try:
        # Step 1: Download video-only stream
        if not args.json:
            print(f"[1/3] Ingesting YouTube video: {args.url}")

        def download_progress(pct, msg):
            if not args.json:
                sys.stdout.write(f"\r      {msg}")
                sys.stdout.flush()

        temp_dir = os.path.join(args.output_dir, "_temp")
        dl_cb = None if args.json else download_progress
        downloaded_video_path, info = download_video_stream(
            args.url,
            output_dir=temp_dir,
            cookies_from_browser=args.cookies_from_browser,
            progress_callback=dl_cb,
        )
        if not args.json:
            sys.stdout.write("\n")
            print(f"      Title:    {info['title']}")
            print(f"      Duration: {info['duration']}s")
            print(f"      Saved:    {downloaded_video_path}")

        # Step 2: Screen extraction & deduplication
        video_output_dir = os.path.join(args.output_dir, info["sanitized_title"])
        screens_dir = os.path.join(video_output_dir, "screens")
        os.makedirs(screens_dir, exist_ok=True)

        if not args.json:
            interval_label = "Auto (Adaptive coarse-to-fine)" if auto_interval else f"{interval_sec}s (Manual)"
            mode_desc = f"Interval: {interval_label}, Sensitivity: {args.sensitivity}"
            if args.tutorial_mode:
                mode_desc += ", Tutorial Mode: ON"
            if args.skip_blurry:
                mode_desc += ", Skip Blurry: ON"
            print(f"\n[2/3] Analyzing frames with Last-Stable-Frame deduplication ({mode_desc})...")

        def detector_progress(pct, msg):
            if not args.json:
                sys.stdout.write(f"\r      [{pct:5.1f}%] {msg}")
                sys.stdout.flush()

        det_cb = None if args.json else detector_progress
        screens = extract_unique_screens(
            video_path=downloaded_video_path,
            video_id=info["id"],
            video_title=info["title"],
            output_screens_dir=screens_dir,
            config=config,
            progress_callback=det_cb,
        )

        if not args.json:
            sys.stdout.write("\n")
            print(f"      Extracted {len(screens)} unique screens.")

        if not screens:
            print("Warning: No screens could be extracted from the specified section.", file=sys.stderr)
            sys.exit(1)

        # Step 3: Compile Landscape PDF with clickable timestamps
        pdf_path = os.path.join(video_output_dir, f"{info['sanitized_title']}.pdf")
        if not args.json:
            split_desc = f" (Auto-split limit: {args.max_pdf_mb} MB)" if args.max_pdf_mb else ""
            print(f"\n[3/3] Building landscape PDF with clickable timestamp links [Preset: {args.pdf_quality}{split_desc}]...")

        def pdf_progress(pct, msg):
            if not args.json:
                sys.stdout.write(f"\r      [{pct:5.1f}%] {msg}")
                sys.stdout.flush()

        pdf_cb = None if args.json else pdf_progress
        generated_pdf_paths = build_pdf(
            screens=screens,
            output_pdf_path=pdf_path,
            video_title=info["title"],
            quality_preset=args.pdf_quality,
            max_pdf_mb=args.max_pdf_mb,
            progress_callback=pdf_cb,
        )

        if not args.json:
            sys.stdout.write("\n")
            for p in generated_pdf_paths:
                sz_mb = os.path.getsize(p) / (1024 * 1024)
                print(f"      PDF generated: {p} ({sz_mb:.2f} MB)")

        # Create ZIP archive for screens
        zip_path = os.path.join(video_output_dir, f"{info['sanitized_title']}_screens.zip")
        create_zip_archive(screens_dir, zip_path)

        elapsed = time.time() - t0

        pdf_info = []
        for p in generated_pdf_paths:
            pdf_info.append({
                "path": os.path.abspath(p),
                "size_mb": round(os.path.getsize(p) / (1024 * 1024), 2),
            })

        result_data = {
            "status": "success",
            "video_id": info["id"],
            "video_title": info["title"],
            "total_screens": len(screens),
            "output_dir": os.path.abspath(video_output_dir),
            "screens_dir": os.path.abspath(screens_dir),
            "pdf_path": os.path.abspath(generated_pdf_paths[0]),
            "pdf_paths": [os.path.abspath(p) for p in generated_pdf_paths],
            "pdf_info": pdf_info,
            "zip_path": os.path.abspath(zip_path),
            "elapsed_seconds": round(elapsed, 2),
            "screens": [
                {
                    "index": s.index,
                    "timestamp": s.timestamp_str,
                    "youtube_url": s.youtube_url,
                    "image_path": os.path.abspath(s.image_path),
                }
                for s in screens
            ],
        }

        if args.json:
            print(json.dumps(result_data, indent=2))
        else:
            print(f"\nExtraction completed in {elapsed:.1f}s!")
            print(f"  * Extracted Screens: {len(screens)}")
            for idx, p in enumerate(generated_pdf_paths, 1):
                sz_mb = os.path.getsize(p) / (1024 * 1024)
                lbl = f"Part {idx}" if len(generated_pdf_paths) > 1 else "Single PDF"
                print(f"  * PDF ({lbl}):     {os.path.abspath(p)} ({sz_mb:.2f} MB)")
            print(f"  * Screens Archive:   {os.path.abspath(zip_path)}")
            print(f"  * Screens Directory: {os.path.abspath(screens_dir)}")

    except YouTubeDownloadError as e:
        print(f"\n[ERROR] YouTube download failed: {str(e)}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"\n[ERROR] Extraction failed: {str(e)}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Automatic temporary video cleanup unless requested to keep
        if not args.keep_video and downloaded_video_path:
            cleanup_file(downloaded_video_path)
            temp_dir = os.path.join(args.output_dir, "_temp")
            try:
                if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
            except OSError:
                pass


if __name__ == "__main__":
    main()
