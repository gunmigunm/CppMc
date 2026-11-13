"""Render the four promo frames for the C++mc trailer (1920x1080, Pillow only).

No screenshots of the real UI are used here on purpose: these are brand frames
(logo, code rain, stats, end card), so nothing is faked.

Usage:  python tools/make_promo_frames.py [output_dir]
"""

from __future__ import annotations

import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1920, 1080
BG = (13, 16, 23)
GREEN = (62, 207, 142)
MUTED = (154, 163, 178)
WHITE = (232, 234, 240)

FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"
FONT_CJK_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_LATIN = r"C:\Windows\Fonts\segoeui.ttf"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_icon import draw_icon  # noqa: E402  (local tool module)


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def vignette(img: Image.Image, strength: int = 150) -> Image.Image:
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    for i in range(60):
        a = int(strength * (i / 60))
        md.ellipse([-W * 0.3 + i * 12, -H * 0.3 + i * 8, W * 1.3 - i * 12, H * 1.3 - i * 8],
                   outline=a, width=12)
    blur = mask.filter(ImageFilter.GaussianBlur(120))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    alpha = blur.point(lambda v: min(255, int(v * 1.4)))
    return Image.composite(black, img, alpha)


def glow(img: Image.Image, center: tuple[int, int], radius: int, color=GREEN,
         alpha: int = 70) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for i in range(50, 0, -1):
        r = int(radius * i / 50)
        ld.ellipse([center[0] - r, center[1] - r, center[0] + r, center[1] + r],
                   fill=(color[0], color[1], color[2], int(alpha * (1 - i / 50) ** 1.5)))
    return Image.alpha_composite(img.convert("RGBA"),
                                 layer.filter(ImageFilter.GaussianBlur(60))).convert("RGB")


def center_text(d: ImageDraw.ImageDraw, y: int, text: str, f: ImageFont.FreeTypeFont,
                fill=WHITE) -> None:
    bbox = d.textbbox((0, 0), text, font=f)
    d.text(((W - (bbox[2] - bbox[0])) / 2, y), text, font=f, fill=fill)


def frame_logo(path: str) -> None:
    img = glow(Image.new("RGB", (W, H), BG), (W // 2, 470), 620, alpha=90)
    img = vignette(img)               # darken the background before drawing type
    img = img.convert("RGBA")
    img.alpha_composite(draw_icon(1024).resize((700, 700), Image.LANCZOS), (W // 2 - 350, 120))
    d = ImageDraw.Draw(img)
    center_text(d, 850, "C++mc", font(FONT_LATIN, 92), WHITE)
    center_text(d, 970, "纯 Python 标准库写成的 Minecraft 启动器", font(FONT_CJK, 38), MUTED)
    img.convert("RGB").save(path)


def frame_code(path: str) -> None:
    random.seed(7)
    img = Image.new("RGB", (W, H), (8, 11, 16))
    d = ImageDraw.Draw(img)
    glyphs = "{}[]()<>/\\=+-*;$#01abcxyz"
    f_small = font(FONT_MONO, 26)
    for col in range(0, W, 34):
        x = col + random.randint(-6, 6)
        for row in range(-2, H // 32 + 2):
            y = row * 32
            shade = random.randint(40, 120)
            d.text((x, y), random.choice(glyphs), font=f_small,
                   fill=(int(shade * 0.35), shade, int(shade * 0.6)))
    for _ in range(26):
        y = random.randint(0, H)
        x = random.randint(0, W - 200)
        d.line([(x, y), (x + random.randint(120, 520), y)], fill=(20, 70, 50), width=2)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    dark = Image.new("RGBA", (W, H), (5, 7, 11, 150))
    img = Image.alpha_composite(img.convert("RGBA"), dark).convert("RGB")
    img = glow(img, (W // 2, H // 2), 700, alpha=60)
    d = ImageDraw.Draw(img)
    center_text(d, 430, "零第三方依赖", font(FONT_CJK_BOLD, 86), WHITE)
    center_text(d, 570, "只需要一个 Python 和一个 Java", font(FONT_CJK, 44), MUTED)
    vignette(img, 190).save(path)


def frame_stats(path: str) -> None:
    img = glow(Image.new("RGB", (W, H), BG), (W // 2, H // 2), 780, alpha=55)
    img = img.convert("RGBA")
    img.alpha_composite(draw_icon(256).resize((120, 120), Image.LANCZOS), (W // 2 - 60, 90))
    d = ImageDraw.Draw(img)
    for (num, label), x in zip([("923", "个版本"), ("5709", "个文件校验"), ("0", "个三方依赖")],
                               [250, 780, 1310]):
        d.text((x, 430), num, font=font(FONT_LATIN, 150), fill=GREEN)
        d.text((x + 6, 620), label, font=font(FONT_CJK, 46), fill=WHITE)
    d.line([(200, 770), (1720, 770)], fill=(43, 48, 64), width=2)
    center_text(d, 810, "SHA1 逐个校验    ·    并发下载    ·    失败自动重试", font(FONT_CJK, 44), MUTED)
    vignette(img.convert("RGB")).save(path)


def frame_end(path: str) -> None:
    img = glow(Image.new("RGB", (W, H), BG), (W // 2, 380), 660, alpha=80)
    img = img.convert("RGBA")
    img.alpha_composite(draw_icon(512).resize((260, 260), Image.LANCZOS), (W // 2 - 130, 250))
    d = ImageDraw.Draw(img)
    center_text(d, 570, "C++mc", font(FONT_LATIN, 110), WHITE)
    mono = FONT_MONO if os.path.exists(FONT_MONO) else FONT_LATIN
    center_text(d, 710, "github.com/gunmigunm/CppMc", font(mono, 54), GREEN)
    center_text(d, 810, "Release 里直接下 CppMc.exe，双击开玩", font(FONT_CJK, 38), MUTED)
    vignette(img.convert("RGB")).save(path)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "promo/frames"
    os.makedirs(out, exist_ok=True)
    builders = [("frame1_logo.png", frame_logo), ("frame2_code.png", frame_code),
                ("frame3_stats.png", frame_stats), ("frame4_end.png", frame_end)]
    for name, fn in builders:
        path = os.path.join(out, name)
        fn(path)
        print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
