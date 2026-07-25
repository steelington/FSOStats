"""Build the app icon assets from the logo mockup sheet.

fsostats.png is a presentation mockup - the 512 tile sits on a lit
background with a drop shadow, alongside 16x16 and 32x32 studies - so
the tile is cropped out by hand-found pixel bounds rather than by a
colour key, which the panel's gradient defeats.

Run after editing fsostats.png:  python -m fsostats.icons
"""
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "fsostats.png"
OUT = Path(__file__).resolve().parent / "assets"

# The large "APPLICATION ICON" tile, inside its border and clear of the
# drop shadow on the right and bottom edges.
TILE_BOX = (319, 392, 1109, 1264)

# The aircraft alone. The wordmark's first row is y=595 within the tile,
# so this stops short of it.
PLANE_BOX = (30, 30, 760, 585)

# Below roughly 48px the wordmark renders as an illegible smear, and the
# aircraft silhouette is the only thing that still reads - which is what
# the mockup's own 16x16 studies show.
WORDMARK_MIN_SIZE = 48

ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tile = Image.open(SRC).convert("RGBA").crop(TILE_BOX)
    w, h = tile.size

    # Pad to square by replicating the edge columns, so the panel's
    # subtle gradient continues instead of banding against a flat fill.
    pad = (h - w) // 2
    arr = np.array(tile)
    left = np.repeat(arr[:, :1, :], pad, axis=1)
    right = np.repeat(arr[:, -1:, :], h - w - pad, axis=1)
    square = Image.fromarray(np.concatenate([left, arr, right], axis=1))
    assert square.size == (h, h), square.size

    plane = tile.crop(PLANE_BOX)
    pw, ph = plane.size
    side = max(pw, ph)
    plane_sq = Image.new("RGBA", (side, side), tile.getpixel((6, 6)))
    plane_sq.paste(plane, ((side - pw) // 2, (side - ph) // 2))

    square.resize((512, 512), Image.LANCZOS).save(OUT / "app_icon.png")

    frames = [
        (square if size >= WORDMARK_MIN_SIZE else plane_sq).resize((size, size), Image.LANCZOS)
        for size in ICO_SIZES
    ]
    frames[-1].save(
        OUT / "app_icon.ico", format="ICO",
        sizes=[(f.width, f.height) for f in frames],
        append_images=frames[:-1],
    )

    # Masthead badge: the aircraft without the wordmark, since the
    # masthead already carries the app name as a text label beside it.
    plane_sq.resize((128, 128), Image.LANCZOS).save(OUT / "masthead_logo.png")

    for path in sorted(OUT.iterdir()):
        print(f"  {path.name:<20} {path.stat().st_size:>8,} bytes")


if __name__ == "__main__":
    main()
