r"""Draw the app icon (glimpse/assets/icon.png + icon.ico): a magnifier on a blue-violet tile.

  .venv\Scripts\python.exe tools\make_icon.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "glimpse", "assets")


def tile(px: int = 1024) -> Image.Image:
    s = px / 1024
    # diagonal gradient #3466f6 -> #8b5cf6
    grad = Image.new("RGBA", (px, px))
    a, b = (52, 102, 246), (139, 92, 246)
    g = ImageDraw.Draw(grad)
    for i in range(2 * px):
        t = i / (2 * px - 1)
        c = tuple(round(a[k] + (b[k] - a[k]) * t) for k in range(3)) + (255,)
        g.line([(i, 0), (0, i)], fill=c, width=2)
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, px - 1, px - 1], radius=int(230 * s), fill=255)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    # soft top highlight
    hi = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(hi).ellipse([-int(200 * s), -int(620 * s), px + int(200 * s), int(420 * s)], fill=(255, 255, 255, 34))
    img = Image.alpha_composite(img, Image.composite(hi, Image.new("RGBA", (px, px)), mask))
    # magnifier with a shadow
    glyph = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(glyph)
    cx, cy, r, w = 455 * s, 445 * s, 215 * s, int(78 * s)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 255), width=w)
    d.line([cx + r * 0.72, cy + r * 0.72, 790 * s, 780 * s], fill=(255, 255, 255, 255), width=int(96 * s))
    d.ellipse([790 * s - 48 * s, 780 * s - 48 * s, 790 * s + 48 * s, 780 * s + 48 * s], fill=(255, 255, 255, 255))
    # sparkle inside the lens
    sx, sy, k = cx, cy, 92 * s
    d.polygon([(sx, sy - k), (sx + k * 0.26, sy - k * 0.26), (sx + k, sy), (sx + k * 0.26, sy + k * 0.26),
               (sx, sy + k), (sx - k * 0.26, sy + k * 0.26), (sx - k, sy), (sx - k * 0.26, sy - k * 0.26)],
              fill=(255, 255, 255, 235))
    shadow = glyph.split()[3].filter(ImageFilter.GaussianBlur(18 * s)).point(lambda v: v * 0.35)
    img = Image.alpha_composite(img, Image.merge("RGBA", (*[Image.new("L", (px, px), 20)] * 3, shadow)).transform(
        (px, px), Image.AFFINE, (1, 0, 0, 0, 1, -int(14 * s))))
    return Image.alpha_composite(img, glyph)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    big = tile(1024)
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    big.save(os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(os.path.join(OUT, "icon-1024.png"))
    print("wrote", OUT)
