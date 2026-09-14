"""Generate the ReviewHunter Word add-in icons (16, 32, 80 px).

Design: rounded square with a diagonal sky-blue (#0284c7) to teal (#0d9488) gradient
and a white bold "R" monogram. Each size is rendered at 8x and downsampled with a
Lanczos filter so the edges stay crisp at 16 px.

Usage (from the repository root):
    .venv/Scripts/python.exe word_addin/tools/make_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

# ------------------------------------------------------------------ configuration
SIZES = (16, 32, 80)                 # output sizes required by the manifest
SUPERSAMPLE = 8                      # render scale before downsampling
COLOR_START = (2, 132, 199)          # #0284c7 sky-blue (top-left)
COLOR_END = (13, 148, 136)           # #0d9488 teal (bottom-right)
CORNER_RATIO = 0.24                  # corner radius as a fraction of the side
FONT_CANDIDATES = ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf")
OUT_DIR = Path(__file__).resolve().parents[1] / "assets"


def gradient(side: int) -> Image.Image:
    """Return a side x side RGBA image with a diagonal two-color gradient."""
    # Mask value grows linearly along the top-left to bottom-right diagonal:
    # average of a vertical ramp and a horizontal ramp.
    vertical = Image.linear_gradient("L").resize((side, side))
    horizontal = vertical.rotate(90)      # rotate so the ramp runs left to right
    mask = ImageChops.add(vertical, horizontal, scale=2.0)
    a = Image.new("RGBA", (side, side), COLOR_START + (255,))
    b = Image.new("RGBA", (side, side), COLOR_END + (255,))
    return Image.composite(b, a, mask)


def load_font(px: int) -> ImageFont.FreeTypeFont:
    """Load the first available bold font at the requested pixel size."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, px)
    return ImageFont.load_default()


def render(size: int) -> Image.Image:
    """Render one icon of the requested size."""
    side = size * SUPERSAMPLE
    base = gradient(side)
    # Rounded-square alpha mask.
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, side - 1, side - 1), radius=int(side * CORNER_RATIO), fill=255)
    icon = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)
    # Centered "R": measure the glyph box so optical centering is exact.
    draw = ImageDraw.Draw(icon)
    font = load_font(int(side * 0.66))
    left, top, right, bottom = draw.textbbox((0, 0), "R", font=font)
    x = (side - (right - left)) / 2 - left
    y = (side - (bottom - top)) / 2 - top
    draw.text((x, y), "R", font=font, fill=(255, 255, 255, 255))
    return icon.resize((size, size), Image.LANCZOS)


def main() -> None:
    """Write icon-16.png, icon-32.png and icon-80.png into word_addin/assets."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        target = OUT_DIR / f"icon-{size}.png"
        render(size).save(target, optimize=True)
        print(f"wrote {target}")


if __name__ == "__main__":
    main()
