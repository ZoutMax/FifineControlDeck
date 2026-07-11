#!/usr/bin/env python3
"""
Generate the app icon and a library of action icons for fifine Control Deck,
styled after the original app's icon pack: dark glyphs on soft pastel gradient
tiles. Drawn at 4x and downsampled for crisp anti-aliased edges.

    python3 tools/make_icons.py
"""
import json
import math
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(HERE, "assets", "app")
LIB_DIR = os.path.join(HERE, "assets", "icons", "library")
SS = 4  # supersample factor

ACCENT = (21, 81, 255)
ACCENT2 = (64, 158, 255)
GLYPH = (46, 46, 64)      # dark slate glyph colour (like the original)
HOLE = (240, 242, 250)    # light colour for cut-outs inside a glyph


def gradient(size, c1, c2):
    """Smooth diagonal (top-left -> bottom-right) gradient via a 2x2 upscale."""
    mid = tuple((a + b) // 2 for a, b in zip(c1, c2))
    g = Image.new("RGB", (2, 2))
    g.putpixel((0, 0), c1)
    g.putpixel((1, 0), mid)
    g.putpixel((0, 1), mid)
    g.putpixel((1, 1), c2)
    return g.resize((size, size), Image.BILINEAR)


def rounded(d, box, r, fill):
    d.rounded_rectangle(box, radius=r, fill=fill)


def finish(img, size):
    return img.resize((size, size), Image.LANCZOS)


def tile(size, c1, c2, draw_glyph):
    """A rounded pastel-gradient tile with a dark glyph drawn by draw_glyph."""
    s = size * SS
    grad = gradient(s, c1, c2)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [s * 0.05, s * 0.05, s * 0.95, s * 0.95], radius=s * 0.20, fill=255)
    img.paste(grad, (0, 0), mask)
    draw_glyph(ImageDraw.Draw(img), s)
    return finish(img, size)


# ----- glyphs (dark GLYPH on the pastel tile) -------------------------------
def g_speaker(d, s, waves=1, badge=None):
    cx, cy = s * 0.38, s * 0.5
    bw, bh = s * 0.12, s * 0.16
    d.rectangle([cx - bw, cy - bh * 0.6, cx, cy + bh * 0.6], fill=GLYPH)
    d.polygon([(cx, cy - bh * 0.6), (cx + s * 0.16, cy - bh * 1.2),
               (cx + s * 0.16, cy + bh * 1.2), (cx, cy + bh * 0.6)], fill=GLYPH)
    for i in range(waves):
        rr = s * (0.13 + i * 0.075)
        bbox = [cx + s * 0.17 - rr, cy - rr, cx + s * 0.17 + rr, cy + rr]
        d.arc(bbox, -50, 50, fill=GLYPH, width=int(s * 0.032))
    if badge == "x":
        x0, y0 = s * 0.64, s * 0.36
        w = int(s * 0.05)
        d.line([x0, y0, x0 + s * 0.15, y0 + s * 0.15], fill=GLYPH, width=w)
        d.line([x0 + s * 0.15, y0, x0, y0 + s * 0.15], fill=GLYPH, width=w)


def g_vol(d, s, badge):
    """Speaker + a large +/- sign, matching the original volume icons."""
    g_speaker(d, s, waves=1)
    bx, by, L, w = s * 0.74, s * 0.5, s * 0.12, int(s * 0.075)
    d.line([bx - L, by, bx + L, by], fill=GLYPH, width=w)
    if badge == "+":
        d.line([bx, by - L, bx, by + L], fill=GLYPH, width=w)


def g_play(d, s):
    d.polygon([(s * 0.36, s * 0.30), (s * 0.72, s * 0.5), (s * 0.36, s * 0.70)], fill=GLYPH)


def g_pause(d, s):
    d.rectangle([s * 0.36, s * 0.30, s * 0.45, s * 0.70], fill=GLYPH)
    d.rectangle([s * 0.55, s * 0.30, s * 0.64, s * 0.70], fill=GLYPH)


def g_stop(d, s):
    rounded(d, [s * 0.34, s * 0.34, s * 0.66, s * 0.66], s * 0.03, GLYPH)


def g_next(d, s):
    d.polygon([(s * 0.30, s * 0.32), (s * 0.52, s * 0.5), (s * 0.30, s * 0.68)], fill=GLYPH)
    d.polygon([(s * 0.50, s * 0.32), (s * 0.72, s * 0.5), (s * 0.50, s * 0.68)], fill=GLYPH)
    d.rectangle([s * 0.72, s * 0.32, s * 0.78, s * 0.68], fill=GLYPH)


def g_prev(d, s):
    d.polygon([(s * 0.70, s * 0.32), (s * 0.48, s * 0.5), (s * 0.70, s * 0.68)], fill=GLYPH)
    d.polygon([(s * 0.50, s * 0.32), (s * 0.28, s * 0.5), (s * 0.50, s * 0.68)], fill=GLYPH)
    d.rectangle([s * 0.22, s * 0.32, s * 0.28, s * 0.68], fill=GLYPH)


def g_chevron(d, s, right=True):
    w = int(s * 0.07)
    if right:
        pts = [(s * 0.42, s * 0.28), (s * 0.64, s * 0.5), (s * 0.42, s * 0.72)]
    else:
        pts = [(s * 0.58, s * 0.28), (s * 0.36, s * 0.5), (s * 0.58, s * 0.72)]
    d.line(pts, fill=GLYPH, width=w, joint="curve")


def g_sun(d, s, badge=None):
    cx, cy, r = s * 0.5, s * 0.5, s * 0.12
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GLYPH)
    for i in range(8):
        a = i * math.pi / 4
        d.line([cx + math.cos(a) * r * 1.6, cy + math.sin(a) * r * 1.6,
                cx + math.cos(a) * r * 2.3, cy + math.sin(a) * r * 2.3],
               fill=GLYPH, width=int(s * 0.03))
    if badge:
        L, w = s * 0.05, int(s * 0.03)
        d.line([cx - L, cy, cx + L, cy], fill=HOLE, width=w)
        if badge == "+":
            d.line([cx, cy - L, cx, cy + L], fill=HOLE, width=w)


def g_folder(d, s):
    d.polygon([(s * 0.28, s * 0.36), (s * 0.44, s * 0.36), (s * 0.50, s * 0.42),
               (s * 0.72, s * 0.42), (s * 0.72, s * 0.36)], fill=GLYPH)
    rounded(d, [s * 0.28, s * 0.40, s * 0.72, s * 0.66], s * 0.02, GLYPH)


def g_terminal(d, s):
    rounded(d, [s * 0.26, s * 0.30, s * 0.74, s * 0.70], s * 0.03, GLYPH)
    rounded(d, [s * 0.29, s * 0.33, s * 0.71, s * 0.67], s * 0.02, HOLE)
    w = int(s * 0.03)
    d.line([s * 0.34, s * 0.42, s * 0.42, s * 0.50], fill=GLYPH, width=w, joint="curve")
    d.line([s * 0.42, s * 0.50, s * 0.34, s * 0.58], fill=GLYPH, width=w, joint="curve")
    d.line([s * 0.46, s * 0.58, s * 0.58, s * 0.58], fill=GLYPH, width=w)


def g_globe(d, s):
    cx, cy, r = s * 0.5, s * 0.5, s * 0.20
    w = int(s * 0.028)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GLYPH, width=w)
    d.ellipse([cx - r * 0.5, cy - r, cx + r * 0.5, cy + r], outline=GLYPH, width=w)
    d.line([cx - r, cy, cx + r, cy], fill=GLYPH, width=w)
    d.arc([cx - r, cy - r * 0.5, cx + r, cy + r * 1.5], 200, 340, fill=GLYPH, width=w)
    d.arc([cx - r, cy - r * 1.5, cx + r, cy + r * 0.5], 20, 160, fill=GLYPH, width=w)


def g_mic(d, s):
    cx = s * 0.5
    rounded(d, [cx - s * 0.09, s * 0.26, cx + s * 0.09, s * 0.54], s * 0.09, GLYPH)
    w = int(s * 0.03)
    d.arc([cx - s * 0.16, s * 0.34, cx + s * 0.16, s * 0.62], 20, 160, fill=GLYPH, width=w)
    d.line([cx, s * 0.62, cx, s * 0.70], fill=GLYPH, width=w)
    d.line([cx - s * 0.08, s * 0.70, cx + s * 0.08, s * 0.70], fill=GLYPH, width=w)


def g_camera(d, s):
    rounded(d, [s * 0.26, s * 0.36, s * 0.74, s * 0.66], s * 0.04, GLYPH)
    d.rectangle([s * 0.40, s * 0.31, s * 0.52, s * 0.37], fill=GLYPH)
    cx, cy, r = s * 0.5, s * 0.51, s * 0.08
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=HOLE)


def g_power(d, s):
    cx, cy, r = s * 0.5, s * 0.52, s * 0.16
    w = int(s * 0.045)
    d.arc([cx - r, cy - r, cx + r, cy + r], 300, 240, fill=GLYPH, width=w)
    d.line([cx, s * 0.30, cx, s * 0.52], fill=GLYPH, width=w)


def g_star(d, s):
    cx, cy, R, r = s * 0.5, s * 0.52, s * 0.22, s * 0.09
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append((cx + math.cos(ang) * rad, cy + math.sin(ang) * rad))
    d.polygon(pts, fill=GLYPH)


def g_heart(d, s):
    cx, cy, r = s * 0.5, s * 0.46, s * 0.11
    d.ellipse([cx - 2 * r, cy - r, cx, cy + r], fill=GLYPH)
    d.ellipse([cx, cy - r, cx + 2 * r, cy + r], fill=GLYPH)
    d.polygon([(cx - 2 * r, cy), (cx + 2 * r, cy), (cx, cy + s * 0.22)], fill=GLYPH)


def g_home(d, s):
    d.polygon([(s * 0.5, s * 0.28), (s * 0.74, s * 0.5), (s * 0.26, s * 0.5)], fill=GLYPH)
    d.rectangle([s * 0.33, s * 0.5, s * 0.67, s * 0.70], fill=GLYPH)
    d.rectangle([s * 0.45, s * 0.56, s * 0.55, s * 0.70], fill=HOLE)


def g_gear(d, s):
    cx, cy = s * 0.5, s * 0.5
    R, r = s * 0.20, s * 0.11
    for i in range(8):
        a = i * math.pi / 4
        x, y = cx + math.cos(a) * R, cy + math.sin(a) * R
        d.ellipse([x - s * 0.05, y - s * 0.05, x + s * 0.05, y + s * 0.05], fill=GLYPH)
    d.ellipse([cx - R * 0.9, cy - R * 0.9, cx + R * 0.9, cy + R * 0.9], fill=GLYPH)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=HOLE)


def g_lock(d, s):
    cx = s * 0.5
    w = int(s * 0.035)
    d.arc([cx - s * 0.10, s * 0.30, cx + s * 0.10, s * 0.52], 180, 360, fill=GLYPH, width=w)
    rounded(d, [cx - s * 0.15, s * 0.44, cx + s * 0.15, s * 0.70], s * 0.03, GLYPH)


def g_dot(d, s):
    d.ellipse([s * 0.40, s * 0.40, s * 0.60, s * 0.60], fill=GLYPH)


# ----- pastel gradient palette + library ------------------------------------
GREEN = ((150, 232, 190), (196, 224, 168))
TEAL = ((150, 226, 214), (162, 205, 255))
PURPLE = ((176, 158, 255), (150, 205, 255))
PINK = ((255, 168, 206), (255, 196, 168))
BLUE = ((160, 205, 255), (194, 190, 255))
WARM = ((255, 226, 150), (255, 194, 158))
SLATE = ((214, 220, 236), (190, 200, 226))
LILAC = ((210, 190, 255), (255, 194, 230))

LIBRARY = [
    ("volume_up",   "Volume +",  PURPLE, lambda d, s: g_vol(d, s, "+"), "Audio"),
    ("volume_down", "Volume −",  PINK,   lambda d, s: g_vol(d, s, "-"), "Audio"),
    ("mute",        "Mute",      GREEN,  lambda d, s: g_speaker(d, s, 0, "x"), "Audio"),
    ("play",        "Play",      TEAL,   g_play, "Media"),
    ("pause",       "Pause",     TEAL,   g_pause, "Media"),
    ("stop",        "Stop",      SLATE,  g_stop, "Media"),
    ("next",        "Next",      GREEN,  g_next, "Media"),
    ("prev",        "Previous",  GREEN,  g_prev, "Media"),
    ("brightness_up",   "Bright +", WARM, lambda d, s: g_sun(d, s, "+"), "Device"),
    ("brightness_down", "Bright −", WARM, lambda d, s: g_sun(d, s, "-"), "Device"),
    ("next_page",   "Next page", BLUE,   lambda d, s: g_chevron(d, s, True), "Navigation"),
    ("prev_page",   "Prev page", BLUE,   lambda d, s: g_chevron(d, s, False), "Navigation"),
    ("folder",      "Folder",    WARM,   g_folder, "Apps"),
    ("terminal",    "Terminal",  SLATE,  g_terminal, "Apps"),
    ("web",         "Website",   TEAL,   g_globe, "Apps"),
    ("mic",         "Mic",       LILAC,  g_mic, "Media"),
    ("camera",      "Camera",    LILAC,  g_camera, "Media"),
    ("power",       "Power",     PINK,   g_power, "System"),
    ("lock",        "Lock",      SLATE,  g_lock, "System"),
    ("settings",    "Settings",  SLATE,  g_gear, "System"),
    ("home",        "Home",      BLUE,   g_home, "Apps"),
    ("star",        "Star",      WARM,   g_star, "Generic"),
    ("heart",       "Heart",     PINK,   g_heart, "Generic"),
    ("dot",         "Dot",       SLATE,  g_dot, "Generic"),
]


def make_library():
    os.makedirs(LIB_DIR, exist_ok=True)
    index = {}
    for name, label, (c1, c2), glyph, cat in LIBRARY:
        tile(256, c1, c2, glyph).save(os.path.join(LIB_DIR, f"{name}.png"))
        index[name] = {"file": f"{name}.png", "label": label, "category": cat}
    with open(os.path.join(LIB_DIR, "index.json"), "w") as f:
        json.dump(index, f, indent=2)
    print(f"library: {len(LIBRARY)} icons -> {LIB_DIR}")


def make_app_icon():
    """Original-spirit app icon: rounded blue square with a grid of colored keys."""
    os.makedirs(APP_DIR, exist_ok=True)
    S = 512 * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([S * 0.04, S * 0.04, S * 0.96, S * 0.96], radius=S * 0.22, fill=ACCENT)
    keys = [(0, 0, (235, 60, 90)), (1, 0, (240, 120, 60)), (2, 0, None),
            (0, 1, (60, 220, 160)), (1, 1, (120, 90, 235)), (2, 1, (240, 90, 200))]
    gx0, gy0, cell, gap = S * 0.16, S * 0.20, S * 0.22, S * 0.045
    for col, row, color in keys:
        x, y = gx0 + col * (cell + gap), gy0 + row * (cell + gap)
        d.rounded_rectangle([x + cell * 0.12, y + cell * 0.12,
                             x + cell * 1.06, y + cell * 1.06],
                            radius=cell * 0.10, outline=ACCENT2, width=int(S * 0.008))
        if color is None:
            cx, cy, r = x + cell * 0.5, y + cell * 0.5, cell * 0.42
            d.rounded_rectangle([cx - r, cy - r, cx + r, cy + r], radius=r * 0.3,
                                fill=(150, 130, 240))
        else:
            d.rounded_rectangle([x, y, x + cell, y + cell], radius=cell * 0.10,
                                fill=color, outline=(10, 10, 10), width=int(S * 0.01))
    base = img.resize((512, 512), Image.LANCZOS)
    base.save(os.path.join(APP_DIR, "fifine-deck.png"))
    for sz in (16, 24, 32, 48, 64, 128, 256):
        base.resize((sz, sz), Image.LANCZOS).save(os.path.join(APP_DIR, f"fifine-deck-{sz}.png"))
    print(f"app icon: 512 + hicolor sizes -> {APP_DIR}")


if __name__ == "__main__":
    make_library()
    make_app_icon()
