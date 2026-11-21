"""ReportLab landscape PDF generator with JPEG stream compression, quality presets, and auto-splitting."""

import io
import os
from typing import Callable, List, Optional, Tuple, Union
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.config import ExtractedScreen, PDF_QUALITY_PRESETS, PDFQualityPreset


def _prepare_jpeg_buffer(
    image_path: str,
    preset: PDFQualityPreset,
) -> Tuple[io.BytesIO, Tuple[int, int], int]:
    """
    Open image, convert to RGB, resize if wider than preset max_width using LANCZOS,
    and encode as optimized JPEG in BytesIO.
    NEVER modifies the original image on disk.
    Returns (BytesIO_buffer, (orig_w, orig_h), compressed_byte_size).
    """
    with Image.open(image_path) as img:
        img_rgb = img.convert("RGB")
        orig_w, orig_h = img_rgb.size

        if orig_w > preset.max_width:
            new_w = preset.max_width
            new_h = max(1, int(orig_h * (new_w / orig_w)))
            resample = getattr(Image.Resampling, "LANCZOS", Image.LANCZOS)
            img_rgb = img_rgb.resize((new_w, new_h), resample=resample)

        buf = io.BytesIO()
        img_rgb.save(buf, format="JPEG", quality=preset.jpeg_quality, optimize=True)
        buf.seek(0)
        byte_size = len(buf.getvalue())
        return buf, (orig_w, orig_h), byte_size


def build_pdf(
    screens: List[ExtractedScreen],
    output_pdf_path: str,
    video_title: str,
    quality_preset: Union[str, PDFQualityPreset] = "balanced",
    max_pdf_mb: Optional[float] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> List[str]:
    """
    Generate landscape PDF document(s) containing one extracted screen per page.
    Features:
      - Embedded compressed JPEGs via ImageReader(BytesIO) (resolving file-size bloating).
      - pageCompression=1 enabled.
      - Clickable YouTube timestamp hyperlink on every page.
      - Quality presets (Balanced 1440px/q80, High 1920px/q88, Compact 1280px/q70).
      - Auto-splitting when size limit (e.g. 25MB, 90MB) is exceeded.
    Returns list of generated PDF file paths.
    """
    selected_screens = [s for s in screens if s.is_selected]
    if not selected_screens:
        raise ValueError("No screens selected to include in PDF.")

    if isinstance(quality_preset, str):
        preset = PDF_QUALITY_PRESETS.get(quality_preset.lower(), PDF_QUALITY_PRESETS["balanced"])
    else:
        preset = quality_preset

    out_dir = os.path.dirname(os.path.abspath(output_pdf_path))
    os.makedirs(out_dir, exist_ok=True)

    # Calculate safety threshold in bytes if max_pdf_mb is configured
    safety_limit_bytes: Optional[int] = None
    if max_pdf_mb and max_pdf_mb > 0:
        if max_pdf_mb <= 2:
            safety_limit_bytes = int(max_pdf_mb * 0.90 * 1024 * 1024)
        elif max_pdf_mb <= 25:
            # For 25 MB (Claude limit), aim for 24 MB safety margin
            safety_limit_bytes = int((max_pdf_mb - 1.0) * 1024 * 1024)
        else:
            # For 90 MB (Gemini limit), aim for 88 MB safety margin
            safety_limit_bytes = int((max_pdf_mb - 2.0) * 1024 * 1024)

    # Pre-encode or analyze screen sizes to plan partitioning
    screen_payloads = []
    total_estimated_bytes = 12000  # Base PDF structure overhead

    for idx, s in enumerate(selected_screens):
        if not os.path.isfile(s.image_path):
            continue
        try:
            buf, dims, sz = _prepare_jpeg_buffer(s.image_path, preset)
            screen_payloads.append((s, buf, dims, sz))
            total_estimated_bytes += sz + 3200  # Stream & annotation overhead
        except Exception:
            pass

    if not screen_payloads:
        raise ValueError("No valid image files found among selected screens.")

    # Determine partitions
    partitions: List[List[Tuple[ExtractedScreen, io.BytesIO, Tuple[int, int], int]]] = []

    if safety_limit_bytes and total_estimated_bytes > safety_limit_bytes:
        # Split into sequential parts strictly under the cap
        cur_part = []
        cur_bytes = 12000
        for item in screen_payloads:
            item_cost = item[3] + 3200
            if cur_part and (cur_bytes + item_cost > safety_limit_bytes):
                partitions.append(cur_part)
                cur_part = [item]
                cur_bytes = 12000 + item_cost
            else:
                cur_part.append(item)
                cur_bytes += item_cost
        if cur_part:
            partitions.append(cur_part)
    else:
        # Single document fits within limit
        partitions = [screen_payloads]

    total_parts = len(partitions)
    base_name, ext = os.path.splitext(output_pdf_path)
    generated_paths: List[str] = []

    page_width, page_height = landscape(A4)  # 841.89 x 595.28 points
    margin_x = 24.0
    margin_top = 20.0
    footer_height = 42.0
    avail_w = page_width - (margin_x * 2)
    avail_h = page_height - margin_top - footer_height

    total_all_pages = len(screen_payloads)
    processed_page_count = 0

    for part_idx, part_items in enumerate(partitions, start=1):
        if total_parts > 1:
            part_path = f"{base_name}_part{part_idx}{ext}"
        else:
            part_path = output_pdf_path

        c = canvas.Canvas(part_path, pagesize=(page_width, page_height), pageCompression=1)
        part_title = f"{video_title} - Part {part_idx}" if total_parts > 1 else video_title
        c.setTitle(part_title)
        c.setAuthor("YT Screen Extractor")

        part_page_total = len(part_items)

        for page_in_part, (screen, buf, (img_w, img_h), _) in enumerate(part_items, start=1):
            processed_page_count += 1
            buf.seek(0)
            reader = ImageReader(buf)

            # Calculate fitted dimensions maintaining aspect ratio
            scale = min(avail_w / img_w, avail_h / img_h)
            draw_w = img_w * scale
            draw_h = img_h * scale
            draw_x = margin_x + (avail_w - draw_w) / 2.0
            draw_y = footer_height + (avail_h - draw_h) / 2.0

            # Draw image via ImageReader
            c.drawImage(
                reader,
                draw_x,
                draw_y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
            )

            # Footer line
            divider_y = footer_height - 6.0
            c.setStrokeColor(colors.HexColor("#D8DEE9"))
            c.setLineWidth(0.75)
            c.line(margin_x, divider_y, page_width - margin_x, divider_y)

            # Footer Left: Screen index & title
            c.setFont("Helvetica", 9)
            c.setFillColor(colors.HexColor("#4C566A"))
            short_title = (video_title[:38] + "...") if len(video_title) > 40 else video_title
            if total_parts > 1:
                screen_label = f"Screen {screen.index} ({page_in_part}/{part_page_total} Part {part_idx}/{total_parts})  •  {short_title}"
            else:
                screen_label = f"Screen {screen.index} of {total_all_pages}  •  {short_title}"
            c.drawString(margin_x, 16.0, screen_label)

            # Footer Center: Timestamp
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor("#2E3440"))
            ts_text = f"Timestamp: {screen.timestamp_str}"
            ts_w = c.stringWidth(ts_text, "Helvetica-Bold", 9)
            c.drawString((page_width - ts_w) / 2.0, 16.0, ts_text)

            # Footer Right: Clickable YouTube link
            yt_url = screen.youtube_url
            link_text = f"Watch on YouTube ({screen.timestamp_str}) ↗"
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.HexColor("#D08770"))
            link_w = c.stringWidth(link_text, "Helvetica-Bold", 9)
            link_x = page_width - margin_x - link_w
            link_y = 16.0
            c.drawString(link_x, link_y, link_text)

            # Clickable rectangular annotation over link text
            click_pad = 4.0
            rect = (
                link_x - click_pad,
                link_y - click_pad,
                link_x + link_w + click_pad,
                link_y + 10.0 + click_pad,
            )
            c.linkURL(yt_url, rect, relative=0)

            c.showPage()

            if progress_callback:
                pct = (processed_page_count / total_all_pages) * 100.0
                progress_callback(pct, f"Compiling PDF page {processed_page_count}/{total_all_pages}...")

        c.save()
        generated_paths.append(part_path)

    return generated_paths
