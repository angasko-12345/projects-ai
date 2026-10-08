"""Generate every image asset the Expo config references.

Deterministic output: the module pattern comes from a seeded generator, so
re-running this script reproduces byte-identical assets.

Usage: python scripts/generate_assets.py
"""

import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

WHITE = (255, 255, 255, 255)
INK = (14, 16, 19, 255)  # #0E1013
TEAL = (11, 110, 95, 255)  # #0B6E5F
CLEAR = (0, 0, 0, 0)

GRID = 15
EYES = [(0, 0), (0, 8), (8, 0)]
SEED = 20261008


def _in_eye(row: int, col: int) -> bool:
    for row0, col0 in EYES:
        if row0 <= row < row0 + 7 and col0 <= col < col0 + 7:
            return True
    return False


def shape_masks(size: int, scale: float) -> tuple[Image.Image, Image.Image]:
    """Return (full mark mask, top-left eye mask) as 8-bit images."""
    full = Image.new("L", (size, size), 0)
    top_left = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(full)
    tl_draw = ImageDraw.Draw(top_left)

    mark = size * scale
    cell = mark / GRID
    offset = (size - mark) / 2

    def px(index: float) -> int:
        return round(offset + index * cell)

    def eye(target: ImageDraw.ImageDraw, row0: int, col0: int) -> None:
        outer = [px(col0), px(row0), px(col0 + 7) - 1, px(row0 + 7) - 1]
        target.rounded_rectangle(outer, radius=cell, outline=255, width=max(1, round(cell)))
        center = [px(col0 + 2), px(row0 + 2), px(col0 + 5) - 1, px(row0 + 5) - 1]
        target.rounded_rectangle(center, radius=cell * 0.6, fill=255)

    for row0, col0 in EYES:
        eye(draw, row0, col0)
    eye(tl_draw, *EYES[0])

    rng = random.Random(SEED)
    for row in range(GRID):
        for col in range(GRID):
            if _in_eye(row, col):
                continue
            if rng.random() < 0.4:
                box = [px(col), px(row), px(col + 1) - 1, px(row + 1) - 1]
                draw.rounded_rectangle(box, radius=cell * 0.28, fill=255)

    return full, top_left


def compose(size: int, background, scale: float, ink=INK, accent=TEAL) -> Image.Image:
    image = Image.new("RGBA", (size, size), background)
    full, top_left = shape_masks(size, scale)
    image = Image.composite(Image.new("RGBA", (size, size), ink), image, full)
    if accent is not None:
        image = Image.composite(Image.new("RGBA", (size, size), accent), image, top_left)
    return image


def monochrome(size: int) -> Image.Image:
    """Solid squircle plate with the QR mark punched out for themed launchers."""
    plate = Image.new("L", (size, size), 0)
    ImageDraw.Draw(plate).rounded_rectangle(
        [size * 0.02, size * 0.02, size * 0.98, size * 0.98],
        radius=size * 0.2,
        fill=255,
    )
    full, _ = shape_masks(size, 0.6)
    plate = Image.composite(Image.new("L", (size, size), 0), plate, full)
    image = Image.new("RGBA", (size, size), WHITE)
    image.putalpha(plate)
    return image


def save(image: Image.Image, relative: str) -> None:
    path = ASSETS / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")
    print(f"wrote {path.relative_to(ROOT)} ({image.size[0]}x{image.size[1]})")


def main() -> None:
    save(compose(1024, WHITE, 0.64), "icon.png")
    save(compose(1024, CLEAR, 0.58), "adaptive-icon/foreground.png")
    save(Image.new("RGBA", (1024, 1024), WHITE), "adaptive-icon/background.png")
    save(monochrome(1024), "adaptive-icon/monochrome.png")
    save(compose(1024, WHITE, 0.5), "splash.png")
    save(compose(48, WHITE, 0.66), "favicon.png")


if __name__ == "__main__":
    main()
