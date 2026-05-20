"""Generate the HIVE app icon set.

Produces, into ``desktop/src-tauri/icons/``:

    icon.png          1024x1024 master (used as Tauri "main" icon)
    32x32.png
    128x128.png
    128x128@2x.png    (256x256, Tauri convention)
    icon.ico          multi-size Windows resource (16/32/48/64/128/256)
    icon.icns         macOS bundle icon (best-effort via Pillow)

Run from anywhere::

    python desktop/scripts/generate_icons.py

Requires Pillow (``pip install Pillow``). The output files are committed
to the repo so ``tauri build`` works on a fresh checkout without anyone
needing to install Python.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("Pillow is required: pip install Pillow", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
ICONS_DIR = HERE.parent / "src-tauri" / "icons"

MASTER = 1024
ORANGE_LIGHT = (245, 166, 35)   # #F5A623
ORANGE_DARK = (216, 90, 48)     # #D85A30


def hex_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """Pointy-top hexagon (matches the HiveLogo.tsx orientation)."""
    pts = []
    for i in range(6):
        angle = math.radians(60 * i - 90)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return pts


def vertical_gradient(size: int, top: tuple[int, int, int],
                      bottom: tuple[int, int, int]) -> Image.Image:
    """Return a `size x size` RGBA image with a top→bottom RGB gradient."""
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    assert px is not None
    for y in range(size):
        t = y / max(size - 1, 1)
        r = round(top[0] + (bottom[0] - top[0]) * t)
        g = round(top[1] + (bottom[1] - top[1]) * t)
        b = round(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b, 255)
    return grad


def hex_layer(size: int, cx: float, cy: float, r: float,
              alpha: int) -> Image.Image:
    """A single hexagon stamped onto a transparent layer with gradient fill."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).polygon(hex_points(cx, cy, r), fill=alpha)
    fill = vertical_gradient(size, ORANGE_LIGHT, ORANGE_DARK)
    layer.paste(fill, (0, 0), mask)
    return layer


def make_master(size: int) -> Image.Image:
    """Three orange honeycomb hexagons on transparent background."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    s = size / 40.0  # match viewBox 0 0 40 40 from HiveLogo.tsx

    # bottom-left first, then bottom-right, then top (so top sits on top)
    img = Image.alpha_composite(img, hex_layer(size, 8 * s, 28 * s, 9.5 * s, 191))
    img = Image.alpha_composite(img, hex_layer(size, 32 * s, 28 * s, 9.5 * s, 217))
    img = Image.alpha_composite(img, hex_layer(size, 20 * s, 12 * s, 10.5 * s, 242))
    return img


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)

    master = make_master(MASTER)
    master.save(ICONS_DIR / "icon.png")

    sizes_png = {
        "32x32.png": 32,
        "128x128.png": 128,
        "128x128@2x.png": 256,
    }
    for name, dim in sizes_png.items():
        master.resize((dim, dim), Image.Resampling.LANCZOS).save(ICONS_DIR / name)

    # Multi-resolution Windows .ico
    ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ICONS_DIR / "icon.ico", format="ICO", sizes=ico_sizes)

    # macOS .icns (best-effort; some Pillow builds can't write ICNS)
    try:
        # ICNS expects 16, 32, 64, 128, 256, 512, 1024 — Pillow picks subset.
        master.save(ICONS_DIR / "icon.icns", format="ICNS")
    except Exception as exc:  # noqa: BLE001
        print(f"[icns] skipped: {exc}")

    print(f"Wrote icons into {ICONS_DIR}")
    for f in sorted(ICONS_DIR.glob("*")):
        print(f"  {f.name:24s}  {f.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
