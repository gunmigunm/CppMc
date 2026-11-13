"""Draw the C++mc icon with Pillow and write a multi-size Windows .ico.

Pure text script: no external image is needed, so CI can rebuild the icon
from source. Geometry mirrors assets/logo.svg.

Usage:  python tools/make_icon.py [output_dir]
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

S = 1024  # supersampled canvas, downscaled to 256 at the end for clean edges
PLATE_BG = (18, 22, 31, 255)
PLATE_EDGE = (43, 48, 64, 255)
TOP_FACE = (74, 222, 155, 255)
LEFT_FACE = (43, 157, 110, 255)
RIGHT_FACE = (31, 125, 85, 255)
EDGE = (13, 16, 23, 255)
BRACKET = (62, 207, 142, 255)


def _p(canvas_xy: tuple[float, float]) -> tuple[int, int]:
    """Scale a 256-space coordinate onto the supersampled canvas."""
    return (int(round(canvas_xy[0] * S / 256)), int(round(canvas_xy[1] * S / 256)))


def draw_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded plate
    d.rounded_rectangle([_p((8, 8)), _p((248, 248))], radius=int(48 * S / 256),
                        fill=PLATE_BG, outline=PLATE_EDGE, width=max(2, S // 256 * 2))

    # soft halo behind the block
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    center = _p((128, 134))
    for i in range(40, 0, -1):
        r = int(96 * S / 256 * i / 40)
        alpha = int(18 * (1 - i / 40))
        hd.ellipse([center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                   fill=(62, 207, 142, alpha))
    img = Image.alpha_composite(img, halo)

    d = ImageDraw.Draw(img)
    # isometric block
    top = [_p((128, 80)), _p((176, 108)), _p((128, 136)), _p((80, 108))]
    left = [_p((80, 108)), _p((128, 136)), _p((128, 192)), _p((80, 164))]
    right = [_p((176, 108)), _p((176, 164)), _p((128, 192)), _p((128, 136))]
    for face, color in ((top, TOP_FACE), (left, LEFT_FACE), (right, RIGHT_FACE)):
        d.polygon(face, fill=color)
    for face in (top, left, right):
        d.line(face + [face[0]], fill=EDGE, width=max(2, S // 128), joint="curve")

    # code brackets framing the block
    w = max(3, S // 90)
    for pts in (
        [_p((62, 98)), _p((44, 98)), _p((44, 176)), _p((62, 176))],
        [_p((194, 98)), _p((212, 98)), _p((212, 176)), _p((194, 176))],
    ):
        d.line(pts, fill=BRACKET, width=w, joint="curve")

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "assets"
    os.makedirs(out_dir, exist_ok=True)

    master = draw_icon(256)
    png_path = os.path.join(out_dir, "icon-256.png")
    master.save(png_path)

    ico_path = os.path.join(out_dir, "CppMc.ico")
    master.save(ico_path, format="ICO",
                sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)])

    print(f"wrote {png_path}")
    print(f"wrote {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
