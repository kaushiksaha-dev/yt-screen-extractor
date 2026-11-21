"""Smart screen detection and deduplication engine using OpenCV, Laplacian variance, and adaptive coarse-to-fine sampling."""

import os
from typing import Callable, List, Optional, Tuple
import cv2
import imagehash
import numpy as np
from PIL import Image

from core.config import ExtractionConfig, ExtractedScreen, get_coarse_stride
from core.utils import format_filename_timestamp, format_timestamp


def apply_webcam_masks(frame_gray: np.ndarray, corners: List[str], ratio: float = 0.20) -> np.ndarray:
    """Mask out specified corner regions in grayscale frame for comparison only."""
    if not corners:
        return frame_gray
    masked = frame_gray.copy()
    h, w = masked.shape[:2]
    mh = max(1, int(h * ratio))
    mw = max(1, int(w * ratio))

    normalized = [c.lower().replace("_", "-").strip() for c in corners]

    for corner in normalized:
        if corner in ("bottom-right", "br"):
            masked[h - mh : h, w - mw : w] = 0
        elif corner in ("bottom-left", "bl"):
            masked[h - mh : h, 0:mw] = 0
        elif corner in ("top-right", "tr"):
            masked[0:mh, w - mw : w] = 0
        elif corner in ("top-left", "tl"):
            masked[0:mh, 0:mw] = 0

    return masked


def is_blank_or_black_frame(frame_gray: np.ndarray, brightness_thresh: float = 6.0, std_thresh: float = 2.5) -> bool:
    """Detect if a frame is a black, blank, or monochromatic transition frame."""
    mean_val = float(np.mean(frame_gray))
    std_val = float(np.std(frame_gray))
    return (mean_val < brightness_thresh) or (std_val < std_thresh)


def is_blurry_frame(frame_gray: np.ndarray, threshold: float = 60.0) -> bool:
    """
    Check if a frame is heavily out-of-focus or a blurred transition frame
    using the variance of the Laplacian.
    """
    variance = float(cv2.Laplacian(frame_gray, cv2.CV_64F).var())
    return variance < threshold


def compute_frame_signature(
    frame_bgr: np.ndarray,
    corners: List[str],
    ratio: float = 0.20,
) -> Tuple[np.ndarray, imagehash.ImageHash, bool, np.ndarray]:
    """
    Generate normalized comparison buffer, perceptual hash, and blank status.
    Returns (downsampled_masked_gray, phash, is_blank, original_gray).
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    is_blank = is_blank_or_black_frame(gray)
    # Downsample to standardized size for fast, noise-tolerant analysis
    small_gray = cv2.resize(gray, (320, 180), interpolation=cv2.INTER_AREA)
    masked_gray = apply_webcam_masks(small_gray, corners, ratio)
    pil_img = Image.fromarray(masked_gray)
    ph = imagehash.phash(pil_img)
    return masked_gray, ph, is_blank, gray


def are_frames_different(
    sig1: Tuple[np.ndarray, imagehash.ImageHash, bool, np.ndarray],
    sig2: Tuple[np.ndarray, imagehash.ImageHash, bool, np.ndarray],
    config: ExtractionConfig,
) -> Tuple[bool, float, int]:
    """
    Hybrid difference check combining absolute pixel difference and perceptual hash Hamming distance.
    Returns (is_different, structural_diff, hash_distance).
    """
    masked1, ph1, _, _ = sig1
    masked2, ph2, _, _ = sig2

    # Structural pixel difference
    abs_diff = cv2.absdiff(masked1, masked2)
    structural_diff = float(np.mean(abs_diff)) / 255.0

    # Perceptual hash distance
    hash_dist = int(ph1 - ph2)

    thresh = config.thresholds

    # A transition is declared if either:
    # 1. Both structural and perceptual metrics exceed moderate thresholds.
    # 2. Or either metric strongly exceeds its individual threshold (significant cut/change).
    is_diff = (
        (hash_dist >= thresh.phash_distance and structural_diff >= thresh.structural_diff * 0.7)
        or (structural_diff >= thresh.structural_diff * 1.5)
        or (hash_dist >= thresh.phash_distance * 2)
    )
    return is_diff, structural_diff, hash_dist


def extract_unique_screens(
    video_path: str,
    video_id: str,
    video_title: str,
    output_screens_dir: str,
    config: ExtractionConfig,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> List[ExtractedScreen]:
    """
    Scan video using adaptive coarse-to-fine sampling with the Last-Stable-Frame deduplication rule.
    Saves full unmasked high-resolution frames to output_screens_dir and lightweight thumbnails
    in .thumbnails/ for fast UI loading.
    """
    os.makedirs(output_screens_dir, exist_ok=True)
    thumbs_dir = os.path.join(output_screens_dir, ".thumbnails")
    os.makedirs(thumbs_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"Could not open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_sec = total_frames / fps if fps > 0 else 0.0

    start_sec = max(0.0, config.start_time or 0.0)
    end_sec = min(duration_sec, config.end_time) if config.end_time else duration_sec
    if end_sec <= start_sec:
        end_sec = duration_sec

    duration_range = max(1.0, end_sec - start_sec)

    # Determine coarse stride
    if config.auto_interval:
        coarse_stride = get_coarse_stride(duration_range)
        is_adaptive = True
        stride_info = f"Auto mode: {coarse_stride:.0f}s coarse scan with 1s transition refinement"
    else:
        coarse_stride = max(0.5, config.interval_seconds)
        is_adaptive = False
        stride_info = f"Manual mode: {coarse_stride:.1f}s fixed interval"

    extracted_screens: List[ExtractedScreen] = []
    screen_counter = 0

    # State for Last-Stable-Frame rule & Tutorial Mode
    last_stable_frame: Optional[np.ndarray] = None
    last_stable_sec: float = 0.0
    last_stable_sig: Optional[Tuple[np.ndarray, imagehash.ImageHash, bool, np.ndarray]] = None
    stable_consecutive_count: int = 0
    prev_coarse_sec: float = start_sec

    def commit_screen(frame_to_save: np.ndarray, timestamp_sec: float):
        nonlocal screen_counter
        screen_counter += 1
        ts_str = format_timestamp(timestamp_sec)
        fn_ts = format_filename_timestamp(timestamp_sec)
        filename = f"{screen_counter:03d}_{fn_ts}.png"
        img_path = os.path.join(output_screens_dir, filename)

        # 1. Save full-resolution unmasked PNG (never downscaled on disk)
        cv2.imwrite(img_path, frame_to_save, [cv2.IMWRITE_PNG_COMPRESSION, 3])

        # 2. Save lightweight cached thumbnail for high-performance UI rendering
        thumb_filename = f"{screen_counter:03d}_{fn_ts}.jpg"
        thumb_path = os.path.join(thumbs_dir, thumb_filename)
        h, w = frame_to_save.shape[:2]
        thumb_w = min(w, 480)
        thumb_h = max(1, int(h * (thumb_w / w)))
        thumb_img = cv2.resize(frame_to_save, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
        cv2.imwrite(thumb_path, thumb_img, [cv2.IMWRITE_JPEG_QUALITY, 75])

        screen_obj = ExtractedScreen(
            index=screen_counter,
            timestamp_seconds=timestamp_sec,
            timestamp_str=ts_str,
            image_path=img_path,
            video_id=video_id,
            video_title=video_title,
            is_selected=True,
            thumbnail_path=thumb_path,
        )
        extracted_screens.append(screen_obj)

    def probe_frame_at(sec: float) -> Optional[Tuple[np.ndarray, Tuple[np.ndarray, imagehash.ImageHash, bool, np.ndarray]]]:
        bounded_sec = min(max(0.0, sec), duration_sec)
        cap.set(cv2.CAP_PROP_POS_MSEC, bounded_sec * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            return None
        sig = compute_frame_signature(
            frame,
            corners=config.mask_webcam_corners,
            ratio=config.corner_mask_ratio,
        )
        return frame, sig

    try:
        current_sec = start_sec

        while current_sec <= end_sec:
            probe_result = probe_frame_at(current_sec)
            if probe_result is None:
                break
            frame, current_sig = probe_result

            is_blank = current_sig[2]
            gray = current_sig[3]

            # 1. Skip blank or black transition frames
            if is_blank:
                current_sec += coarse_stride
                if progress_callback:
                    pct = min(100.0, ((current_sec - start_sec) / duration_range) * 100.0)
                    progress_callback(pct, f"Scanning: {format_timestamp(current_sec)} / {format_timestamp(end_sec)} (Skipped black frame)")
                continue

            # 2. Skip blurry transition frames (Laplacian variance check)
            if config.skip_blurry and is_blurry_frame(gray, threshold=config.blur_threshold):
                current_sec += coarse_stride
                if progress_callback:
                    pct = min(100.0, ((current_sec - start_sec) / duration_range) * 100.0)
                    progress_callback(pct, f"Scanning: {format_timestamp(current_sec)} / {format_timestamp(end_sec)} (Skipped blurry frame)")
                continue

            if last_stable_sig is None:
                # First valid candidate screen
                last_stable_frame = frame.copy()
                last_stable_sec = current_sec
                last_stable_sig = current_sig
                stable_consecutive_count = 1
                prev_coarse_sec = current_sec
                current_sec += coarse_stride
            else:
                is_different, s_diff, h_dist = are_frames_different(last_stable_sig, current_sig, config)

                if not is_different:
                    # Current coarse frame matches the previous stable screen:
                    # Skip frames in between and advance stable frame state
                    stable_consecutive_count += 1
                    last_stable_frame = frame.copy()
                    last_stable_sec = current_sec
                    last_stable_sig = current_sig
                    prev_coarse_sec = current_sec
                    current_sec += coarse_stride
                else:
                    # A screen change is detected!
                    # If adaptive mode is active and the gap is > 2 seconds, run a quick refinement pass inside [prev_coarse_sec, current_sec]
                    gap_sec = current_sec - prev_coarse_sec
                    if is_adaptive and gap_sec > 2.0:
                        # Step every 1 second inside (prev_coarse_sec, current_sec)
                        refine_sec = prev_coarse_sec + 1.0
                        while refine_sec < current_sec - 0.2:
                            r_result = probe_frame_at(refine_sec)
                            if r_result is not None:
                                r_frame, r_sig = r_result
                                r_blank = r_sig[2]
                                r_gray = r_sig[3]
                                if not r_blank and not (config.skip_blurry and is_blurry_frame(r_gray, threshold=config.blur_threshold)):
                                    r_diff, _, _ = are_frames_different(last_stable_sig, r_sig, config)
                                    if not r_diff:
                                        # Still the preceding screen: capture incremental update
                                        stable_consecutive_count += 1
                                        last_stable_frame = r_frame.copy()
                                        last_stable_sec = refine_sec
                                        last_stable_sig = r_sig
                                    else:
                                        # Transition boundary reached right here!
                                        # Commit the exact last-stable frame of the preceding screen
                                        qualifies = (not config.tutorial_mode) or (stable_consecutive_count >= 2)
                                        if qualifies:
                                            commit_screen(last_stable_frame, last_stable_sec)
                                        # Start new screen candidate
                                        last_stable_frame = r_frame.copy()
                                        last_stable_sec = refine_sec
                                        last_stable_sig = r_sig
                                        stable_consecutive_count = 1
                            refine_sec += 1.0

                    # Now evaluate the current coarse frame at current_sec against the refined last_stable_sig
                    diff_at_curr, _, _ = are_frames_different(last_stable_sig, current_sig, config)
                    if not diff_at_curr:
                        stable_consecutive_count += 1
                        last_stable_frame = frame.copy()
                        last_stable_sec = current_sec
                        last_stable_sig = current_sig
                    else:
                        qualifies = (not config.tutorial_mode) or (stable_consecutive_count >= 2)
                        if qualifies:
                            commit_screen(last_stable_frame, last_stable_sec)
                        last_stable_frame = frame.copy()
                        last_stable_sec = current_sec
                        last_stable_sig = current_sig
                        stable_consecutive_count = 1

                    prev_coarse_sec = current_sec
                    current_sec += coarse_stride

            if progress_callback:
                pct = min(100.0, ((current_sec - start_sec) / duration_range) * 100.0)
                msg = f"Scanning ({stride_info}): {format_timestamp(min(current_sec, end_sec))} / {format_timestamp(end_sec)} | Extracted screens: {screen_counter}"
                progress_callback(pct, msg)

        # Commit final stable screen at the end of the video
        if last_stable_frame is not None:
            qualifies = (not config.tutorial_mode) or (stable_consecutive_count >= 2)
            if qualifies:
                commit_screen(last_stable_frame, last_stable_sec)

    finally:
        cap.release()
        del cap

    return extracted_screens
