"""Frame processing: JPEG for the model, change detection, black-frame detection."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

DIFF_SIZE = (160, 90)
PIXEL_DELTA = 25


def to_jpeg(rgb: np.ndarray, max_width: int = 1024, quality: int = 80) -> tuple[bytes, int, int]:
    img = Image.fromarray(rgb)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=max(1, min(quality, 95)))
    return buf.getvalue(), img.width, img.height


def small_gray(rgb: np.ndarray) -> np.ndarray:
    img = Image.fromarray(rgb).convert("L").resize(DIFF_SIZE, Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.int16)


def changed_fraction(a: np.ndarray, b: np.ndarray) -> float:
    return float((np.abs(a - b) > PIXEL_DELTA).mean())


def is_black(rgb: np.ndarray) -> bool:
    """Near-uniform dark frame: what PS5 sends for content blocked from Remote Play."""
    gray = small_gray(rgb)
    return float(gray.mean()) < 12 and float(gray.std()) < 6
