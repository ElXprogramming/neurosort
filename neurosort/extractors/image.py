"""Image metadata and EXIF extractor supporting Pillow, piexif, and optional OCR."""

from __future__ import annotations

from datetime import datetime
import io
import os
from pathlib import Path
import shutil
from typing import Any, Dict, Optional, Tuple

from PIL import Image
import piexif


def _convert_gps_coordinate(coord_tuple: Any, ref: Any) -> Optional[float]:
    """Convert EXIF GPS rational coordinate tuple to decimal degrees."""
    try:
        if not coord_tuple or len(coord_tuple) < 3:
            return None

        def _val(item: Any) -> float:
            if isinstance(item, tuple):
                return float(item[0]) / float(item[1]) if item[1] != 0 else float(item[0])
            return float(item)

        degrees = _val(coord_tuple[0])
        minutes = _val(coord_tuple[1])
        seconds = _val(coord_tuple[2])

        decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)

        ref_str = str(ref).upper() if ref else ""
        if "S" in ref_str or "W" in ref_str:
            decimal = -decimal

        return round(decimal, 6)
    except Exception:
        return None


def extract_image_data(file_path: Path) -> Tuple[str, Dict[str, Any]]:
    """Extract dimensions, color mode, EXIF timestamps, camera, and GPS from images.

    Args:
        file_path: Path to target image file.

    Returns:
        Tuple of (text_summary, metadata_dict).
    """
    metadata: Dict[str, Any] = {"format": "IMAGE"}
    summary_parts = []

    try:
        with Image.open(file_path) as img:
            metadata["width"] = img.width
            metadata["height"] = img.height
            metadata["image_format"] = img.format
            metadata["mode"] = img.mode

            summary_parts.append(
                f"Image: {img.format or 'Unknown'} {img.width}x{img.height} ({img.mode})"
            )

            # Try extracting EXIF via piexif or PIL getexif
            raw_exif = None
            if "exif" in img.info:
                try:
                    raw_exif = piexif.load(img.info["exif"])
                except Exception:
                    pass

            if raw_exif:
                # 0th IFD
                zeroth = raw_exif.get("0th", {})
                make = zeroth.get(piexif.ImageIFD.Make)
                model = zeroth.get(piexif.ImageIFD.Model)
                if make:
                    make_str = make.decode("utf-8", "ignore").strip() if isinstance(make, bytes) else str(make).strip()
                    metadata["camera_make"] = make_str
                if model:
                    model_str = model.decode("utf-8", "ignore").strip() if isinstance(model, bytes) else str(model).strip()
                    metadata["camera_model"] = model_str

                if metadata.get("camera_make") or metadata.get("camera_model"):
                    cam = f"{metadata.get('camera_make', '')} {metadata.get('camera_model', '')}".strip()
                    summary_parts.append(f"Camera: {cam}")

                # Exif IFD
                exif_ifd = raw_exif.get("Exif", {})
                date_original = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal) or exif_ifd.get(
                    piexif.ExifIFD.DateTimeDigitized
                )
                if date_original:
                    date_str = (
                        date_original.decode("utf-8", "ignore").strip()
                        if isinstance(date_original, bytes)
                        else str(date_original).strip()
                    )
                    metadata["exif_datetime"] = date_str
                    summary_parts.append(f"Taken: {date_str}")

                # GPS IFD
                gps_ifd = raw_exif.get("GPS", {})
                if gps_ifd:
                    lat_coord = gps_ifd.get(piexif.GPSIFD.GPSLatitude)
                    lat_ref = gps_ifd.get(piexif.GPSIFD.GPSLatitudeRef)
                    lon_coord = gps_ifd.get(piexif.GPSIFD.GPSLongitude)
                    lon_ref = gps_ifd.get(piexif.GPSIFD.GPSLongitudeRef)

                    lat_dec = _convert_gps_coordinate(lat_coord, lat_ref)
                    lon_dec = _convert_gps_coordinate(lon_coord, lon_ref)

                    if lat_dec is not None and lon_dec is not None:
                        metadata["gps_latitude"] = lat_dec
                        metadata["gps_longitude"] = lon_dec
                        summary_parts.append(f"GPS: ({lat_dec}, {lon_dec})")

            # Check if image looks like a document/screenshot or wallpaper
            aspect_ratio = round(img.width / max(1, img.height), 2)
            metadata["aspect_ratio"] = aspect_ratio
            if aspect_ratio >= 1.7:
                metadata["likely_wallpaper"] = True

            # Optional OCR if pytesseract is available
            if shutil.which("tesseract"):
                try:
                    import pytesseract

                    # Downscale if massive before running OCR
                    ocr_img = img
                    if max(img.width, img.height) > 1920:
                        ocr_img = img.copy()
                        ocr_img.thumbnail((1920, 1920))

                    ocr_text = pytesseract.image_to_string(ocr_img).strip()
                    if ocr_text:
                        metadata["ocr_detected"] = True
                        summary_parts.append(f"Detected Text: {ocr_text[:500]}")
                except Exception:
                    pass

    except Exception as exc:
        metadata["error"] = str(exc)

    return ". ".join(summary_parts), metadata
