#!/usr/bin/env python3
"""Composite a game frame from the shipped art, exactly as the game does.

This exists so that art can be judged *in situ* without launching the game, and
so that a review never has to restate the layout. Every constant comes from
`featherdash/config.py` and `featherdash/display.py`'s rule -- the layer
anchors, the parallax heights, the cloud band, the sky stretch, the ground
skirt. A hand-tuned copy of those numbers is how a review ends up critiquing a
composition the player never sees: a previous rig had the far layer's bottom at
world y 920 while the game puts it somewhere else entirely, and the resulting
notes pointed at the wrong end of the asset.

Importing the runtime package is safe in one direction only: `tools/` may read
`featherdash/`, never the reverse (`tests/test_contract.py` enforces that, since
numpy and Pillow are not in the APK).

Usage::

    .venv/bin/python tools/mock_scene.py                     # four themes, phone
    .venv/bin/python tools/mock_scene.py --size 2560x1600    # landscape tablet
    .venv/bin/python tools/mock_scene.py --theme dusk --out /tmp/dusk.png
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import manifest  # noqa: E402

from featherdash import config  # noqa: E402

_cache: dict[str, Image.Image] = {}


def load(name: str) -> Image.Image:
    img = _cache.get(name)
    if img is None:
        img = Image.open(manifest.image_path(name)).convert("RGBA")
        _cache[name] = img
    return img


def fade(img: Image.Image, alpha: int) -> Image.Image:
    if alpha >= 255:
        return img
    out = img.copy()
    band = out.getchannel("A").point(lambda v: v * alpha // 255)
    out.putalpha(band)
    return out


class Viewport:
    """`featherdash/display.py::compute`, reimplemented on top of PIL."""

    def __init__(self, screen_w: int, screen_h: int) -> None:
        self.screen_w = max(1, int(screen_w))
        self.screen_h = max(1, int(screen_h))
        scale = self.screen_h / config.WORLD_H
        units_wide = self.screen_w / scale
        if units_wide < config.WORLD_W_MIN:
            scale = self.screen_w / config.WORLD_W_MIN
            self.world_w = float(config.WORLD_W_MIN)
        else:
            self.world_w = min(units_wide, float(config.WORLD_W_MAX))
        self.scale = scale
        self.origin_x = (self.screen_w - self.world_w * scale) * 0.5
        self.origin_y = (self.screen_h - config.WORLD_H * scale) * 0.5
        self.view_left = -self.origin_x / scale
        self.view_right = (self.screen_w - self.origin_x) / scale
        self.view_top = -self.origin_y / scale
        self.view_bottom = (self.screen_h - self.origin_y) / scale
        self.ground_y = config.WORLD_H - config.GROUND_H

    def px(self, units: float) -> int:
        return max(1, int(round(units * self.scale)))

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return (int(round(self.origin_x + x * self.scale)),
                int(round(self.origin_y + y * self.scale)))


def _hash01(n: int) -> float:
    """`featherdash/world.py::_hash01` -- the game's deterministic cloud layout."""
    x = math.sin(n * 127.1) * 43758.5453
    return x - math.floor(x)


def _tile_row(frame: Image.Image, img: Image.Image, sx: int, sy: int) -> None:
    tile_w = max(1, img.width)
    count = int((frame.width - sx) / tile_w) + 2
    for i in range(max(1, min(count, 32))):
        frame.alpha_composite(img, (sx + i * tile_w, sy))


def frame(theme: str, screen_w: int, screen_h: int, *, offsets=None,
          parts=("sky", "stars", "sun", "clouds", "far", "mid", "near", "ground"),
          alpha: int = 255, onto: Image.Image | None = None) -> Image.Image:
    vp = Viewport(screen_w, screen_h)
    offs = offsets or {}
    fr = onto if onto is not None else Image.new("RGBA", (vp.screen_w, vp.screen_h),
                                                 (0, 0, 0, 255))

    if "sky" in parts:
        fr.alpha_composite(fade(load(f"sky_{theme}.png").resize(
            (vp.screen_w, vp.screen_h), Image.LANCZOS), alpha))

    if "stars" in parts and config.STAR_ALPHA.get(theme, 0) > 0:
        src = load("stars.png")
        h = vp.screen_h
        w = max(1, int(round(h * src.width / src.height)))
        img = fade(src.resize((w, h), Image.LANCZOS),
                   config.STAR_ALPHA[theme] * alpha // 255)
        for x in range(0, vp.screen_w, w):
            fr.alpha_composite(img, (x, 0))

    if "sun" in parts:
        size = vp.px(config.SUN_DRAW[theme])
        img = fade(load(f"sundisc_{theme}.png").resize((size, size), Image.LANCZOS), alpha)
        fx, fy = config.SUN_POS[theme]
        fr.alpha_composite(img, (int(round(fx * vp.screen_w - size * 0.5)),
                                 int(round(fy * vp.screen_h - size * 0.5))))

    if "clouds" in parts:
        span = max(640.0, config.WORLD_W_MAX * 1.6)
        lo, hi = config.CLOUD_BAND
        for i in range(config.CLOUD_COUNT):
            fx, fy = _hash01(i * 7 + 3), _hash01(i * 13 + 5)
            scale = 0.7 + _hash01(i * 17 + 11) * 0.65
            src = load(f"cloud_{theme}_{i % manifest.CLOUD_VARIANTS}.png")
            ch = config.CLOUD_H * scale
            cw = ch * src.width / src.height
            img = fade(src.resize((vp.px(cw), vp.px(ch)), Image.LANCZOS), alpha)
            y = config.WORLD_H * (lo + fy * (hi - lo))
            base = fx * span - offs.get("cloud", 0.0)
            for rep in (0, 1, 2):
                x = vp.view_left + ((base + rep * span) % span) - cw
                if x > vp.view_right or x + cw < vp.view_left:
                    continue
                fr.alpha_composite(img, vp.to_screen(x, y))

    for prefix, height in (("far", config.LAYER_FAR_H), ("mid", config.LAYER_MID_H),
                           ("near", config.LAYER_NEAR_H)):
        if prefix not in parts:
            continue
        src = load(f"{prefix}_{theme}.png")
        width = height * src.width / src.height
        img = fade(src.resize((vp.px(width), vp.px(height)), Image.LANCZOS), alpha)
        top = vp.ground_y + config.LAYER_ANCHORS[prefix] - height
        start = vp.view_left - (offs.get(prefix, 0.0) % width) - width
        _tile_row(fr, img, *vp.to_screen(start, top))

    if "ground" in parts:
        src = load(f"ground_{theme}.png")
        art_h = config.GROUND_ART_H
        tile_w = art_h * src.width / src.height
        img = fade(src.resize((vp.px(tile_w), vp.px(art_h)), Image.LANCZOS), alpha)
        top = vp.ground_y
        start = vp.view_left - (offs.get("ground", 0.0) % tile_w) - tile_w
        _tile_row(fr, img, *vp.to_screen(start, top))
        # world.py extends the strip's last row to the bottom of the screen
        visible_h = max(art_h, vp.view_bottom - vp.ground_y + 8.0)
        if visible_h > art_h:
            fill_top = vp.to_screen(0, top + art_h)[1]
            row = src.crop((0, src.height - 1, src.width, src.height))
            colour = row.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
            if fill_top < fr.height:
                fr.alpha_composite(
                    Image.new("RGBA", (fr.width, fr.height - fill_top), colour),
                    (0, fill_top))
    return fr


def row(images, gap: int = 12, bg=(22, 22, 28, 255)) -> Image.Image:
    h = max(i.height for i in images)
    w = sum(i.width for i in images) + gap * (len(images) - 1)
    out = Image.new("RGBA", (w, h), bg)
    x = 0
    for img in images:
        out.alpha_composite(img, (x, 0))
        x += img.width + gap
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", default="480x854", help="framebuffer size, WxH")
    ap.add_argument("--theme", default=None, choices=list(manifest.THEMES))
    ap.add_argument("--out", default=None,
                    help="output PNG (default: a temp file, so the repo stays clean)")
    args = ap.parse_args()

    sw, sh = (int(v) for v in args.size.lower().split("x"))
    themes = [args.theme] if args.theme else list(manifest.THEMES)
    sheet = row([frame(t, sw, sh) for t in themes])
    out = args.out or os.path.join(tempfile.gettempdir(), "mock_scene.png")
    sheet.convert("RGB").save(out)
    print(f"wrote {out}  {sheet.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
