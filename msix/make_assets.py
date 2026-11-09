"""Build the MSIX tile/logo PNGs from the Android launcher icon.

Usage: python msix/make_assets.py <output Assets dir> [store-listing-icon.png]

Needs Pillow. Reads the repo's Android adaptive-icon foreground + background
colour, so the Store tile matches the Android app.
"""
import re
import sys
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
RES = REPO / "android/app/src/main/res"
FG = RES / "mipmap-xxxhdpi/ic_launcher_foreground.png"
COLORS = RES / "values/ic_launcher_colors.xml"


def background() -> tuple[int, int, int]:
    m = re.search(r'ic_launcher_bg">#([0-9A-Fa-f]{2})?([0-9A-Fa-f]{6})', COLORS.read_text())
    h = m.group(2)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def square_icon(size: int, bg) -> Image.Image:
    fg = Image.open(FG).convert("RGBA")
    box = fg.getchannel("A").getbbox()  # the actual artwork
    art = fg.crop(box)
    side = max(art.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(art, ((side - art.width) // 2, (side - art.height) // 2), art)
    pad = int(side * 0.22)  # breathing room around the artwork
    full = Image.new("RGBA", (side + 2 * pad,) * 2, bg + (255,))
    full.alpha_composite(canvas, (pad, pad))
    return full.resize((size, size), Image.LANCZOS)


def wide(w: int, h: int, bg) -> Image.Image:
    icon = square_icon(int(h * 0.8), bg)
    out = Image.new("RGBA", (w, h), bg + (255,))
    out.alpha_composite(icon, ((w - icon.width) // 2, (h - icon.height) // 2))
    return out


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    bg = background()
    sq = {
        "Square44x44Logo.png": 44,
        "Square150x150Logo.png": 150,
        "StoreLogo.png": 50,
        "Square44x44Logo.targetsize-16.png": 16,
        "Square44x44Logo.targetsize-24.png": 24,
        "Square44x44Logo.targetsize-32.png": 32,
        "Square44x44Logo.targetsize-48.png": 48,
        "Square44x44Logo.targetsize-256.png": 256,
    }
    for name, size in sq.items():
        square_icon(size, bg).save(out / name)
    wide(310, 150, bg).save(out / "Wide310x150Logo.png")
    if len(sys.argv) > 2:  # 300x300 icon to upload in Partner Center's Store listing
        square_icon(300, bg).save(sys.argv[2])
    print(f"wrote {len(sq) + 1} assets to {out}")


if __name__ == "__main__":
    main()
