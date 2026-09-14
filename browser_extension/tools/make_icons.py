"""Generate the ReviewHunter Connector toolbar icons.

Draws a rounded-square "R" monogram on a sky-blue (#0284c7) to teal (#0d9488)
diagonal gradient, plus a grey variant used when ReviewHunter is not running.
Each size is rendered at 8x and downsampled (LANCZOS) for crisp anti-aliasing.

Usage (from the repository root):
    .venv/Scripts/python.exe browser_extension/tools/make_icons.py

Requires Pillow (``pip install pillow``).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ----------------------------------------------------------------------------- configuration
SIZES = (16, 32, 48, 128)
SUPERSAMPLE = 8
BRAND_START = (2, 132, 199)  # #0284c7 sky
BRAND_END = (13, 148, 136)  # #0d9488 teal
GREY_START = (148, 163, 184)  # #94a3b8
GREY_END = (100, 116, 139)  # #64748b
CORNER_RATIO = 0.24  # corner radius / icon size
OUT_DIR = Path(__file__).resolve().parents[1] / "icons"
FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeuib.ttf",  # Segoe UI Bold (Windows)
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


def load_font(px: int) -> ImageFont.FreeTypeFont:
    """Return the first available bold font at the given pixel size."""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, px)
    return ImageFont.load_default()


def gradient(size: int, start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    """Diagonal (top-left to bottom-right) linear gradient as an RGBA image."""
    img = Image.new("RGBA", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(s + (e - s) * t) for s, e in zip(start, end)) + (255,)
    return img


def render(size: int, start: tuple[int, int, int], end: tuple[int, int, int]) -> Image.Image:
    """Render one icon at `size` pixels."""
    big = size * SUPERSAMPLE
    # Small sizes use a slightly larger letter and smaller margin to stay legible.
    margin = round(big * (0.02 if size <= 16 else 0.04))
    base = gradient(big, start, end)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, big - margin - 1, big - margin - 1),
                                           radius=round(big * CORNER_RATIO), fill=255)
    icon = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)

    # Subtle top highlight for depth (skipped at 16 px where it would only blur).
    if size >= 32:
        shine = Image.new("L", (big, big), 0)
        ImageDraw.Draw(shine).ellipse((-big * 0.3, -big * 0.9, big * 1.3, big * 0.45), fill=34)
        white = Image.new("RGBA", (big, big), (255, 255, 255, 255))
        icon.paste(white, (0, 0), Image.composite(shine, Image.new("L", (big, big), 0), mask))

    draw = ImageDraw.Draw(icon)
    font = load_font(round(big * (0.80 if size <= 16 else 0.70)))
    box = draw.textbbox((0, 0), "R", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (big - w) / 2 - box[0] + big * 0.01
    y = (big - h) / 2 - box[1]
    draw.text((x, y), "R", font=font, fill=(255, 255, 255, 255))
    return icon.resize((size, size), Image.LANCZOS)


def main() -> None:
    """Write icon-<size>.png and icon-grey-<size>.png for every size."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        render(size, BRAND_START, BRAND_END).save(OUT_DIR / f"icon-{size}.png", optimize=True)
        render(size, GREY_START, GREY_END).save(OUT_DIR / f"icon-grey-{size}.png", optimize=True)
        print(f"icons {size}px written")


if __name__ == "__main__":
    main()
