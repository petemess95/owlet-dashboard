from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "app" / "static"


# Night-sky tokens (theme.css dark values, plan v0.2 section 7 D2).
BG = (7, 9, 37)          # --bg
BG_ACCENT = (28, 32, 82)  # --bg-accent-1
SURFACE = (18, 20, 66)    # --surface
INK = (231, 231, 251)     # --ink
STAR_DIM = (201, 204, 255)  # --star-dim (eye discs)
GLOW = (124, 128, 230)    # --sleep-light
MINT = (155, 227, 201)    # --accent / --series-o2
BEAK = (217, 176, 165)    # --awake, a warm tone that is not an alert colour


def rounded_gradient(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    assert px is not None
    for y in range(size):
        for x in range(size):
            t = (x * 0.35 + y * 0.65) / size
            if t < 0.5:
                k, c0, c1 = t / 0.5, BG_ACCENT, SURFACE
            else:
                k, c0, c1 = (t - 0.5) / 0.5, SURFACE, BG
            px[x, y] = tuple(int(c0[i] * (1 - k) + c1[i] * k) for i in range(3)) + (255,)

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gpx = glow.load()
    assert gpx is not None
    cx, cy = int(size * 0.5), int(size * 0.39)
    max_r = size * 0.51
    for y in range(size):
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            a = max(0, 1 - d / max_r) ** 1.6
            gpx[x, y] = GLOW + (int(107 * a),)
    img = Image.alpha_composite(img, glow)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.21875), fill=255)
    img.putalpha(mask)
    return img


def scale_points(points: list[tuple[float, float]], s: int) -> list[tuple[int, int]]:
    return [(round(x * s / 512), round(y * s / 512)) for x, y in points]


def draw_icon(size: int) -> Image.Image:
    s = size
    img = rounded_gradient(s)
    d = ImageDraw.Draw(img)
    def box(x0, y0, x1, y1):
        return tuple(round(v * s / 512) for v in (x0, y0, x1, y1))
    def w(v):
        return max(1, round(v * s / 512))

    # Same design as app/static/logo.svg: owl face, pulse-trace mouth, no sock.
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    owl = scale_points([
        (120, 200), (128, 138), (174, 103), (220, 122), (238, 131), (249, 148), (256, 165),
        (263, 148), (274, 131), (292, 122), (338, 103), (384, 138), (392, 200), (400, 264),
        (350, 310), (300, 288), (277, 278), (264, 259), (256, 238), (248, 259), (235, 278),
        (212, 288), (162, 310), (112, 264),
    ], s)
    sd.polygon([(x, y + w(14)) for x, y in owl], fill=(3, 4, 26, 115))
    shadow = shadow.filter(ImageFilter.GaussianBlur(w(14)))
    img = Image.alpha_composite(img, shadow)
    d = ImageDraw.Draw(img)
    d.polygon(owl, fill=INK + (255,))

    d.ellipse(box(159, 156, 255, 252), fill=STAR_DIM + (255,))
    d.ellipse(box(257, 156, 353, 252), fill=STAR_DIM + (255,))
    d.ellipse(box(185, 182, 229, 226), fill=SURFACE + (255,))
    d.ellipse(box(283, 182, 327, 226), fill=SURFACE + (255,))
    d.ellipse(box(209, 187, 223, 201), fill=INK + (255,))
    d.ellipse(box(307, 187, 321, 201), fill=INK + (255,))
    d.line(scale_points([(244, 242), (256, 262), (268, 242)], s), fill=BEAK + (255,), width=w(16), joint="curve")

    pulse = scale_points([(136, 362), (198, 362), (218, 330), (246, 404), (274, 300), (298, 362), (376, 362)], s)
    d.line(pulse, fill=MINT + (255,), width=w(24), joint="curve")
    r = w(12)
    for x, y in (pulse[0], pulse[-1]):
        d.ellipse((x - r, y - r, x + r, y + r), fill=MINT + (255,))
    return img


for size in (32, 180, 192, 512):
    icon = draw_icon(1024).resize((size, size), Image.Resampling.LANCZOS)
    icon.save(OUT / f"icon-{size}.png")

ico_sizes = [(16, 16), (32, 32), (48, 48)]
ico = draw_icon(256)
ico.save(OUT / "favicon.ico", sizes=ico_sizes)
print("wrote", ", ".join(p.name for p in [OUT / 'favicon.ico', *(OUT / f'icon-{s}.png' for s in (32, 180, 192, 512))]))
