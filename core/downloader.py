"""YouTube video metadata extraction and video stream downloading using yt-dlp."""

import os
import re
from typing import Callable, Dict, Optional, Tuple
import yt_dlp

from core.utils import sanitize_filename


class YouTubeDownloadError(Exception):
    """Custom exception with user-actionable instructions for YouTube download failures."""
    pass


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various standard and shortened URL formats."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/embed\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_video_info(url: str, cookies_from_browser: Optional[str] = None) -> Dict:
    """Fetch video metadata without downloading video payload."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser, None, None, None)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise YouTubeDownloadError(f"Could not extract video metadata from URL: {url}")
            return {
                "id": info.get("id", extract_video_id(url) or "video"),
                "title": info.get("title", "YouTube Video"),
                "sanitized_title": sanitize_filename(info.get("title", "YouTube Video")),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail"),
                "uploader": info.get("uploader", "Unknown"),
            }
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "Sign in to confirm you’re not a bot" in err_msg or "403" in err_msg or "Private video" in err_msg:
            raise YouTubeDownloadError(
                "YouTube request blocked or requires verification. "
                "Try passing '--cookies-from-browser chrome' (or edge/firefox/brave) in CLI, "
                "or specify browser cookies in the Advanced Settings."
            ) from e
        raise YouTubeDownloadError(f"Failed to fetch video information: {err_msg}") from e
    except Exception as e:
        raise YouTubeDownloadError(f"Unexpected error retrieving YouTube metadata: {str(e)}") from e


def download_video_stream(
    url: str,
    output_dir: str,
    cookies_from_browser: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Tuple[str, Dict]:
    """
    Download video-only MP4 stream up to 1080p (progressive MP4 fallback) so it operates
    reliably without requiring FFmpeg on the host machine.
    Returns (local_mp4_path, info_dict).
    """
    os.makedirs(output_dir, exist_ok=True)
    info = get_video_info(url, cookies_from_browser=cookies_from_browser)
    sanitized_title = info["sanitized_title"]
    output_template = os.path.join(output_dir, f"{sanitized_title}_%(id)s.%(ext)s")

    def yt_progress_hook(d):
        if progress_callback and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total > 0 else 0.0
            speed = d.get("speed")
            speed_str = f" ({speed / 1024 / 1024:.1f} MB/s)" if speed else ""
            progress_callback(percent, f"Downloading video stream: {percent:.1f}%{speed_str}")

    ydl_opts = {
        # Strictly prefer single mp4 streams (video-only or progressive) to avoid ffmpeg merge requirement
        "format": "bestvideo[ext=mp4][height<=1080]/best[ext=mp4][height<=1080]/bestvideo[ext=mp4]/best[ext=mp4]/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "progress_hooks": [yt_progress_hook] if progress_callback else [],
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser, None, None, None)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            extracted_info = ydl.extract_info(url, download=True)
            downloaded_path = ydl.prepare_filename(extracted_info)
            if not os.path.isfile(downloaded_path):
                # Check for possible extension changes
                base, _ = os.path.splitext(downloaded_path)
                for ext in [".mp4", ".mkv", ".webm"]:
                    candidate = base + ext
                    if os.path.isfile(candidate):
                        downloaded_path = candidate
                        break
            if not os.path.isfile(downloaded_path):
                raise YouTubeDownloadError(f"Downloaded file not found at expected path: {downloaded_path}")
            return downloaded_path, info
    except yt_dlp.utils.DownloadError as e:
        err_msg = str(e)
        if "Sign in to confirm you’re not a bot" in err_msg or "403" in err_msg:
            raise YouTubeDownloadError(
                "YouTube request was blocked by bot detection. "
                "Use '--cookies-from-browser chrome' or select your browser in Advanced Settings to authenticate."
            ) from e
        raise YouTubeDownloadError(f"Download failed: {err_msg}") from e
    except Exception as e:
        raise YouTubeDownloadError(f"Unexpected error downloading video: {str(e)}") from e
