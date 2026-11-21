"""Utility functions for filename sanitization, timestamps, formatting, and file archives."""

import os
import re
import zipfile
from typing import Optional


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize video titles for valid directory and file naming across Windows/Linux/macOS."""
    if not name:
        return "untitled_video"
    # Remove invalid characters
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    # Collapse multiple whitespaces and underscores
    cleaned = re.sub(r"[\s_]+", "_", cleaned).strip(" ._-")
    if not cleaned:
        cleaned = "video"
    return cleaned[:max_length]


def format_timestamp(seconds: float) -> str:
    """Format seconds into HH:MM:SS."""
    seconds = max(0, int(seconds))
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def format_filename_timestamp(seconds: float) -> str:
    """Format seconds into HH-MM-SS for filenames."""
    seconds = max(0, int(seconds))
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hrs:02d}-{mins:02d}-{secs:02d}"


def parse_time_str(time_str: Optional[str]) -> Optional[float]:
    """Parse time string like '01:23:45', '14:20', or '120' into total seconds."""
    if not time_str or not time_str.strip():
        return None
    time_str = time_str.strip()
    try:
        if ":" in time_str:
            parts = [float(p) for p in time_str.split(":")]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 1:
                return parts[0]
        return float(time_str)
    except ValueError:
        return None


def create_zip_archive(source_dir: str, zip_path: str) -> str:
    """Zip all files inside source_dir into zip_path."""
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(source_dir):
            for file in sorted(files):
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, source_dir)
                zipf.write(file_path, arcname=arcname)
    return zip_path


def cleanup_file(path: Optional[str]) -> None:
    """Silently remove a file if it exists."""
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
