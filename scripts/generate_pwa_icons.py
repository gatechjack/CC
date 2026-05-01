"""Generate PWA icon PNGs from a base design.

Run once after editing the design. Outputs to
trading_corp/web/static/icons/.

  python scripts/generate_pwa_icons.py

Sizes generated:
  - icon-192.png         PWA manifest standard (Android home screen)
  - icon-512.png         PWA manifest splash + larger devices
  - icon-maskable-512.png Maskable variant (Android adaptive icons)
  - apple-touch-icon-180.png  iOS home screen (modern iPhones)
  - apple-touch-icon-152.png  iOS home screen (older iPad)
  - apple-touch-icon-167.png  iPad Pro
  - favicon-32.png       Browser tab favicon
  - favicon-16.png       Browser tab favicon (small)

Why programmatic instead of static PNGs?
  - One source of truth (the design lives in code, easy to tweak)
  - Reproducible — anyone cloning the repo can regenerate
  - No 8 binary blobs in git for one icon design

Requires Pillow:
  pip install Pillow
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit(
        "ERROR: Pillow not installed. Run: pip install Pillow\n"
        "(One-time dependency for icon generation; not needed at runtime.)"
    )


# Output directory
ICONS_DIR = Path(__file__).parent.parent / "trading_corp" / "web" / "static" / "icons"


# Color palette — must mirror Tailwind config in base.html
ACCENT_BLUE = (59, 130, 246)      # #3b82f6
EMERALD = (16, 185, 129)          # #10b981
BG_DARK = (7, 11, 20)             # #070b14 (the TC text color over the gradient)


def _interpolate(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _diagonal_gradient(size: int) -> Image.Image:
    """Top-left to bottom-right gradient from accent blue to emerald."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # Diagonal: progress = (x+y) / (2*size). Use a simple linear blend.
            t = (x + y) / (2 * (size - 1))
            px[x, y] = _interpolate(ACCENT_BLUE, EMERALD, t)
    return img


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners by clipping to a rounded-square mask."""
    size = img.size[0]
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask=mask)
    return out


def _find_font(size: int) -> ImageFont.FreeTypeFont:
    """Find a bold sans-serif font at the given pixel size.

    Falls back through common Windows/macOS/Linux locations. Worst case,
    PIL's default bitmap font is used (looks bad but renders).
    """
    candidates = [
        # Windows
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        # macOS
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVu-Sans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def make_icon(size: int, *, maskable: bool = False) -> Image.Image:
    """Build an icon at the given pixel size.

    `maskable` adds a safe zone (Android adaptive icons may crop the
    outer 20% — text must stay inside the inner 60%-ish circle).
    """
    img = _diagonal_gradient(size)
    draw = ImageDraw.Draw(img)

    # Text: "TC" centered. For maskable variant, scale text smaller so it
    # fits inside Android's safe zone.
    text = "TC"
    text_size_pct = 0.32 if maskable else 0.42
    font = _find_font(int(size * text_size_pct))

    # Use textbbox to compute exact centering
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=BG_DARK)

    # Apple auto-rounds anyway, but for the manifest icons used by Android
    # browsers we apply rounded corners (12% radius is the accepted ratio).
    if not maskable:
        radius = int(size * 0.18)
        img = _round_corners(img, radius)
    else:
        # Maskable icons need to be FULL bleed — no rounding (Android masks)
        img = img.convert("RGBA")

    return img


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    targets = [
        ("icon-192.png",              192, False),
        ("icon-512.png",              512, False),
        ("icon-maskable-512.png",     512, True),
        ("apple-touch-icon-180.png",  180, False),
        ("apple-touch-icon-167.png",  167, False),
        ("apple-touch-icon-152.png",  152, False),
        ("favicon-32.png",             32, False),
        ("favicon-16.png",             16, False),
    ]

    for name, size, maskable in targets:
        img = make_icon(size, maskable=maskable)
        out = ICONS_DIR / name
        img.save(out, "PNG", optimize=True)
        print(f"  wrote {out.relative_to(Path.cwd()) if Path.cwd() in out.parents else out}  "
              f"({size}×{size}{', maskable' if maskable else ''})")

    print(f"\n{len(targets)} icons generated in {ICONS_DIR.relative_to(Path.cwd())}/")


if __name__ == "__main__":
    main()
