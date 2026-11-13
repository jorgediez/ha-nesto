"""Validate the shipped brand images.

Since Home Assistant 2026.3 an integration serves its own brand images from a
``brand/`` directory, so a malformed or wrongly sized file is this repo's
problem rather than something a brands-repository reviewer would catch.

The PNG header is parsed directly, so these tests need no imaging library.
"""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

BRAND = Path(__file__).resolve().parents[1] / "custom_components" / "nesto" / "brand"

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Colour type 6 is truecolour with alpha; 3 is a palette, which may still carry
# transparency through a tRNS chunk.
TRANSPARENT_COLOUR_TYPES = (3, 6)

EXPECTED = {"icon.png": 256, "icon@2x.png": 512}


def read_png_header(path: Path) -> tuple[int, int, int]:
    """Return (width, height, colour type) from a PNG's IHDR chunk."""
    data = path.read_bytes()
    assert data.startswith(PNG_SIGNATURE), f"{path.name} is not a PNG"
    assert data[12:16] == b"IHDR", f"{path.name} does not start with IHDR"
    width, height = struct.unpack(">II", data[16:24])
    return width, height, data[25]


@pytest.mark.parametrize(("name", "size"), sorted(EXPECTED.items()))
def test_brand_image(name, size):
    """Each icon is there, square at its size, and transparent."""
    path = BRAND / name
    assert path.is_file(), f"{name} is missing from brand/"
    width, height, colour_type = read_png_header(path)
    assert (width, height) == (size, size)
    assert colour_type in TRANSPARENT_COLOUR_TYPES


def test_only_the_expected_images():
    """A stray or misnamed file in brand/ would be served, or silently ignored."""
    assert sorted(p.name for p in BRAND.iterdir()) == sorted(EXPECTED)
