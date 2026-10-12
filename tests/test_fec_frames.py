import io
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from ps5agent import fec, frames


@pytest.mark.parametrize("k,m", [(6, 1), (10, 3), (40, 8), (1, 1)])
def test_fec_recovers_any_erasures_up_to_m(k, m):
    rng = random.Random(k * 100 + m)
    matrix = fec.Matrix("cauchy", k, m, 8)
    size = fec.align_size(matrix, 1001)
    data = bytes(rng.getrandbits(8) for _ in range(k * size))
    encoded = fec.encode_from_bytes(matrix, data, size)
    for _ in range(20):
        erased = rng.sample(range(k + m), rng.randint(1, m))
        damaged = bytearray(encoded)
        for e in erased:
            damaged[e * size : (e + 1) * size] = bytes(size)
        restored = fec.decode_from_bytes(matrix, bytes(damaged), erased, size)
        assert restored == encoded


def test_fec_gives_up_beyond_m():
    matrix = fec.Matrix("cauchy", 4, 1, 8)
    assert fec.decode_from_bytes(matrix, bytes(5 * 16), [0, 1], 16) == b""


def test_vendored_library_uses_numpy_fec():
    src = (Path(fec.__file__).parent / "vendor" / "pyremoteplay" / "av.py").read_text()
    assert "from ... import fec as pyjerasure" in src


def test_jpeg_is_valid_and_resized():
    rgb = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    jpeg, w, h = frames.to_jpeg(rgb, max_width=640, quality=70)
    img = Image.open(io.BytesIO(jpeg))
    assert img.format == "JPEG" and img.size == (640, 360) == (w, h)


def test_black_frame_detection():
    black = np.full((720, 1280, 3), 3, dtype=np.uint8)
    menu = black.copy()
    menu[300:400, 200:900] = 220
    assert frames.is_black(black)
    assert not frames.is_black(menu)


def test_changed_fraction():
    a = np.zeros((90, 160), dtype=np.int16)
    b = a.copy()
    b[:9] = 200
    assert frames.changed_fraction(a, a) == 0.0
    assert frames.changed_fraction(a, b) == pytest.approx(0.1)
