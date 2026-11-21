"""Configuration models, sensitivity presets, and PDF quality options for YT Screen Extractor."""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SensitivityThresholds:
    structural_diff: float  # Normalized pixel difference (0.0 to 1.0)
    phash_distance: int     # Perceptual hash Hamming distance (0 to 64)


SENSITIVITY_PRESETS = {
    "low": SensitivityThresholds(structural_diff=0.22, phash_distance=14),
    "medium": SensitivityThresholds(structural_diff=0.12, phash_distance=8),
    "high": SensitivityThresholds(structural_diff=0.06, phash_distance=4),
}


@dataclass
class PDFQualityPreset:
    name: str
    label: str
    max_width: int
    jpeg_quality: int


PDF_QUALITY_PRESETS: Dict[str, PDFQualityPreset] = {
    "balanced": PDFQualityPreset(
        name="balanced",
        label="Balanced (Default — Sharp Code & Text)",
        max_width=1440,
        jpeg_quality=80,
    ),
    "high": PDFQualityPreset(
        name="high",
        label="High Quality (1080p Archival)",
        max_width=1920,
        jpeg_quality=88,
    ),
    "compact": PDFQualityPreset(
        name="compact",
        label="AI-Ready Compact",
        max_width=1280,
        jpeg_quality=70,
    ),
}

SPLIT_PRESETS = {
    "none": None,
    "claude": 25,
    "gemini": 90,
}


def get_coarse_stride(duration_sec: float) -> float:
    """
    Pick initial coarse stride automatically based on selected time range / video duration:
      - Under 15 min: 2s coarse stride
      - 15 to 60 min: 4s coarse stride
      - 1 to 3 hours: 6s coarse stride
      - Over 3 hours: 10s coarse stride
    """
    if duration_sec < 900:  # Under 15 min
        return 2.0
    elif duration_sec <= 3600:  # 15 to 60 min
        return 4.0
    elif duration_sec <= 10800:  # 1 to 3 hours
        return 6.0
    else:  # Over 3 hours
        return 10.0


@dataclass
class ExtractionConfig:
    video_url: str
    auto_interval: bool = True
    interval_seconds: float = 2.0
    sensitivity: str = "medium"  # "low", "medium", "high"
    mask_webcam_corners: List[str] = field(default_factory=list)  # "bottom-right", "bottom-left", etc.
    corner_mask_ratio: float = 0.20  # 20% width x 20% height
    start_time: Optional[float] = None  # in seconds
    end_time: Optional[float] = None    # in seconds
    keep_video: bool = False
    output_dir: str = "outputs"
    cookies_from_browser: Optional[str] = None
    # Tutorial and transition filters
    skip_blurry: bool = True
    blur_threshold: float = 60.0
    tutorial_mode: bool = False  # Only commit if stable across >=2 consecutive sampled frames
    # PDF generation options
    pdf_quality: str = "balanced"  # "balanced", "high", "compact"
    max_pdf_mb: Optional[int] = None  # None, 25, 90

    @property
    def thresholds(self) -> SensitivityThresholds:
        preset = self.sensitivity.lower()
        return SENSITIVITY_PRESETS.get(preset, SENSITIVITY_PRESETS["medium"])

    @property
    def quality_preset(self) -> PDFQualityPreset:
        preset = self.pdf_quality.lower()
        return PDF_QUALITY_PRESETS.get(preset, PDF_QUALITY_PRESETS["balanced"])

    def get_stride(self, duration_sec: float) -> float:
        if self.auto_interval:
            return get_coarse_stride(duration_sec)
        return max(0.5, self.interval_seconds)


@dataclass
class ExtractedScreen:
    index: int
    timestamp_seconds: float
    timestamp_str: str  # "HH:MM:SS"
    image_path: str
    video_id: str
    video_title: str
    is_selected: bool = True
    thumbnail_path: Optional[str] = None

    @property
    def youtube_url(self) -> str:
        sec = int(self.timestamp_seconds)
        return f"https://youtu.be/{self.video_id}?t={sec}"

    @property
    def display_path(self) -> str:
        if self.thumbnail_path and os.path.isfile(self.thumbnail_path):
            return self.thumbnail_path
        return self.image_path
