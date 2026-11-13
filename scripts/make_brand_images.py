"""Generate the integration's brand images.

Since Home Assistant 2026.3 an integration can ship its own brand images in a
``brand/`` directory inside the integration, and they override the CDN with no
pull request to the central brands repository:

    custom_components/nesto/brand/
        icon.png       256x256
        icon@2x.png    512x512

They follow the brands repository's specification, which the developer docs
still defer to: PNG, square, transparent, trimmed of empty edges.

The icon is original: a round panel with a metal ring and a dark screen,
showing a dial (turning the ring) around a menu with its middle item chosen
(the room's items). It is deliberately nothing like Google's or Nest's logos,
so it can't suggest an official association. No logo.png (a wordmark) and no
dark_ variants: the ring reads on light and dark backgrounds, and Home
Assistant falls back to the plain image when a dark_ one is absent.

    pip install pillow
    python scripts/make_brand_images.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "nesto" / "brand"

RING_OUTER = (196, 201, 207, 255)
RING_INNER = (140, 146, 153, 255)
SCREEN = (22, 27, 34, 255)
TRACK = (60, 66, 74, 255)
ITEM = (90, 97, 106, 255)
AMBER = (255, 167, 38, 255)
SUPERSAMPLE = 4


def draw_icon(size: int) -> Image.Image:
    s = size * SUPERSAMPLE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    u = s / 100.0  # one percent of the image

    # The panel: a metal ring, its inner edge, and the screen.
    draw.ellipse([2 * u, 2 * u, 98 * u, 98 * u], fill=RING_OUTER)
    draw.ellipse([8 * u, 8 * u, 92 * u, 92 * u], fill=RING_INNER)
    draw.ellipse([11 * u, 11 * u, 89 * u, 89 * u], fill=SCREEN)

    # The dial: a track from bottom left round to bottom right, set to about
    # two thirds. Angles in degrees, clockwise from three o'clock.
    dial = [19 * u, 19 * u, 81 * u, 81 * u]
    draw.arc(dial, 135, 405, fill=TRACK, width=round(6 * u))
    draw.arc(dial, 135, 300, fill=AMBER, width=round(6 * u))

    # The menu: three items, the middle one chosen.
    for y, width, colour in ((38, 22, ITEM), (50, 30, AMBER), (62, 22, ITEM)):
        half = 3.2
        draw.rounded_rectangle(
            [
                (50 - width / 2) * u,
                (y - half) * u,
                (50 + width / 2) * u,
                (y + half) * u,
            ],
            radius=half * u,
            fill=colour,
        )

    # Trimmed of the empty edge round the ring, as the specification asks.
    return image.crop(image.getbbox()).resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        draw_icon(size).save(OUT / name, optimize=True)
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
