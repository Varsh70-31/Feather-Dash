#!/usr/bin/env python3
"""Feather Dash -- procedural generator for the world environment art.

Produces, for every theme in ``manifest.THEMES``::

    sky_<theme>.png          vertical gradient + sun/moon + stars / horizon bloom
    far_<theme>.png          hazed distant mountain range with snow caps
    mid_<theme>.png          rolling hills, lit and shaded faces, small conifers
    near_<theme>.png         dark foreground bank, bush clusters, tall trees
    ground_<theme>.png       scrolling strip: lit turf, textured soil, dark base
    cloud_<theme>_0..3.png   four puffy clouds lit from the upper left

Every pixel is computed here from the primitives in ``tools/artlib.py``; no
imagery is sampled, traced or downloaded.

Seamless tiling in X is structural rather than patched up afterwards:

* silhouettes come from whole-period profiles -- ``artlib.ridge_profile`` and
  the triangle-wave variant below -- so column 0 continues column w-1 exactly;
* ``artlib.value_noise`` is tileable by construction;
* every hand-placed detail (tree, bush, pebble, crack, tuft) is stamped three
  times at x-w, x and x+w, so anything straddling the seam continues over it;
* blurs run on a horizontally tripled copy and are cropped back (``blur_wrap``),
  because PIL's Gaussian clamps at the edge and would otherwise leave a line.

Lighting is one key from the upper left across every asset: lit faces warm
toward the theme's ``sun``, shaded faces fall toward a darkened body colour,
and silhouettes carry a thin warm crest highlight.

Run::

    .venv/bin/python tools/gen_world.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import artlib  # noqa: E402
import manifest  # noqa: E402

SS = artlib.SS
PAL = manifest.THEME_PALETTE

# The sky strip is 256px wide and is stretched across the whole framebuffer at
# run time, so its horizontal scale depends on the display's aspect ratio --
# 1.90x on a 20:9 phone against 6.75x on a landscape tablet. No pre-squash can
# be right for more than one of those, so the strip now carries only content
# that is *uniform in x* (the gradient, the horizon ramp, the dither, the
# grain). Everything round -- the sun, the moon, the stars -- is a separate
# sprite drawn at a uniform scale, and is therefore round everywhere.


# ---------------------------------------------------------------------------
# numeric helpers
# ---------------------------------------------------------------------------

def rgb3(c) -> np.ndarray:
    return np.array(artlib.rgba(c)[:3], dtype=np.float64)


def mix(c1, c2, t):
    return artlib.mix(c1, c2, t)


def smoothstep(x) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def smooth_circ(a: np.ndarray, k: int) -> np.ndarray:
    """Box-smooth a 1-D signal, wrapping around the ends."""
    k = int(k)
    if k < 2:
        return np.asarray(a, dtype=np.float64)
    if k % 2 == 0:
        k += 1
    pad = k // 2
    ext = np.concatenate([a[-pad:], a, a[:pad]])
    return np.convolve(ext, np.ones(k) / k, mode="valid")


def grad_circ(a: np.ndarray) -> np.ndarray:
    """Central-difference derivative that wraps, so the seam has no kink."""
    return (np.roll(a, -1) - np.roll(a, 1)) * 0.5


def tri_profile(width: int, peaks: int, seed: int, roughness: float = 0.4,
                octaves: int = 4) -> np.ndarray:
    """Seamless 0..1 profile of triangle waves -> angular mountain ridges.

    Frequencies are whole numbers of periods across ``width`` and double each
    octave, so the profile wraps exactly like ``artlib.ridge_profile`` does.
    """
    rng = np.random.default_rng(seed)
    x = np.linspace(0.0, 1.0, width, endpoint=False)
    out = np.zeros(width)
    amp, freq = 1.0, float(int(peaks))
    for _ in range(octaves):
        t = (x * freq + rng.random()) % 1.0
        out += amp * (1.0 - 2.0 * np.abs(t - 0.5))
        amp *= roughness
        freq *= 2
    out -= out.min()
    return out / max(out.max(), 1e-9)


def coverage(w: int, h: int, top_hi: np.ndarray) -> np.ndarray:
    """Analytic alpha coverage for a height field.

    ``top_hi`` holds the silhouette's y for several sub-columns per output
    column: vertical coverage is exact and the sub-columns supply horizontal
    antialiasing, which is cleaner than rasterising a polygon at 4x.
    """
    ss = len(top_hi) // w
    yy = np.arange(h, dtype=np.float64)[:, None]
    cov = np.clip(yy + 1.0 - top_hi[None, :], 0.0, 1.0)
    return cov.reshape(h, w, ss).mean(axis=2)


def to_image(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    h, w = alpha.shape
    out = np.empty((h, w, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255).round().astype(np.uint8)
    out[..., 3] = np.clip(alpha * 255.0, 0, 255).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA")


# ---------------------------------------------------------------------------
# wrap-safe image helpers
# ---------------------------------------------------------------------------

def blur_wrap(img: Image.Image, radius: float) -> Image.Image:
    """Gaussian blur that wraps horizontally, so tiling layers keep their seam."""
    w, h = img.size
    tiled = Image.new(img.mode, (w * 3, h))
    for i in range(3):
        tiled.paste(img, (i * w, 0))
    tiled = tiled.filter(ImageFilter.GaussianBlur(radius))
    return tiled.crop((w, 0, w * 2, h))


def resolve_wrap(cv: Image.Image, w: int, h: int, pad: int = 8) -> Image.Image:
    """Downsample a supersampled canvas with wrap-around at the left/right edge.

    ``Image.resize`` renormalises its filter kernel against the image border,
    so the first and last output column of a tiling layer come out slightly
    wrong -- enough for a one-pixel line at the join. Padding with wrapped
    content before the reduction and cropping afterwards removes it.
    """
    full_w, full_h = cv.size
    p = pad * SS
    big = Image.new("RGBA", (full_w + 2 * p, full_h))
    big.paste(cv.crop((full_w - p, 0, full_w, full_h)), (0, 0))
    big.paste(cv, (p, 0))
    big.paste(cv.crop((0, 0, p, full_h)), (p + full_w, 0))
    out = big.resize((w + 2 * pad, h), Image.LANCZOS)
    return out.crop((pad, 0, pad + w, h))


def blur_wrap_np(a: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(np.clip(a * 255.0, 0, 255).astype(np.uint8), "L")
    return np.asarray(blur_wrap(img, radius), dtype=np.float64) / 255.0


def vgrad_through(img: Image.Image, stops) -> Image.Image:
    """Paint a vertical gradient through an image's alpha, without a dark fringe.

    ``artlib.apply_gradient`` pastes the gradient into a transparent image using
    the sprite as the mask, so an edge pixel at 40% alpha ends up with its RGB
    scaled to 40% as well -- premultiplied colour stored in a straight-alpha
    PNG, which composites as a dark outline. Dividing the colour back out by the
    alpha restores the real edge colour and the halo disappears.
    """
    grad = artlib.vertical_gradient(img.width, img.height, stops)
    out = np.asarray(artlib.apply_gradient(img, grad), dtype=np.float64).copy()
    a = out[..., 3:4] / 255.0
    np.divide(out[..., :3], np.maximum(a, 1.0 / 255.0), out=out[..., :3])
    # fully transparent pixels keep the gradient's own colour rather than black,
    # so any later softening of the alpha cannot bleed a dark rim outward
    g = np.asarray(grad, dtype=np.float64)
    out[..., :3] = np.where(a > 1.5 / 255.0, out[..., :3], g[..., :3])
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGBA")


def soft_edges(img: Image.Image, radius: float) -> Image.Image:
    """Blur a sprite's edge in premultiplied space, so no dark rim appears.

    Blurring the alpha channel on its own pulls alpha out over pixels that hold
    no colour; the colour has to travel with it. Premultiplying, blurring both,
    then dividing back out is the only way to get a genuinely soft edge instead
    of a fringed one.
    """
    arr = np.asarray(img, dtype=np.float64)
    a = arr[..., 3] / 255.0
    chans = []
    for c in range(3):
        src = Image.fromarray(np.clip(arr[..., c] * a, 0, 255).astype(np.uint8), "L")
        chans.append(np.asarray(src.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64))
    ab = np.asarray(Image.fromarray(np.clip(a * 255.0, 0, 255).astype(np.uint8), "L")
                    .filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64) / 255.0
    out = np.empty_like(arr)
    denom = np.maximum(ab, 1.0 / 255.0)
    for c in range(3):
        out[..., c] = chans[c] / denom
    out[..., 3] = np.clip(ab * 255.0, 0, 255)
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGBA")


def crescent(img: Image.Image, dx: int, dy: int, blur: float, color, opacity: float,
             wrap: bool = True) -> Image.Image:
    """Composite a soft light or dark crescent along one side of a silhouette.

    Positive dx/dy give the upper-left crescent (rim light); negative values
    give the lower-right one (contact shading).
    """
    a = img.getchannel("A")
    band = ImageChops.subtract(a, ImageChops.offset(a, dx, dy))
    band = blur_wrap(band, blur) if wrap else band.filter(ImageFilter.GaussianBlur(blur))
    band = ImageChops.multiply(band, a)
    band = band.point(lambda v: int(min(255, v * 1.35) * opacity))
    layer = Image.new("RGBA", img.size, artlib.rgba(color, 255))
    layer.putalpha(band)
    return Image.alpha_composite(img, layer)


def tint_clipped(base: Image.Image, mask: np.ndarray, color, opacity: float) -> Image.Image:
    """Composite a flat colour through ``mask``, clipped to ``base``'s alpha."""
    m = np.clip(mask * opacity, 0.0, 1.0)
    a = np.asarray(base.getchannel("A"), dtype=np.float64) / 255.0
    lay = Image.new("RGBA", base.size, artlib.rgba(color, 255))
    lay.putalpha(Image.fromarray((m * a * 255.0).round().astype(np.uint8), "L"))
    return Image.alpha_composite(base, lay)


def wrap_xs(w: int, x: float):
    return (x - w, x, x + w)


def top_at(top: np.ndarray, x: float) -> float:
    w = len(top)
    return float(np.interp(x % w, np.arange(w), top, period=w))


# ---------------------------------------------------------------------------
# per-lobe volume shading
# ---------------------------------------------------------------------------
# Foliage and clouds are unions of ellipses. Shading only the outer silhouette
# flattens them into one mass, so each lobe also contributes a light crescent
# on its upper-left and a dark one on its lower-right; the union of those arcs
# is what makes a cluster read as separate rounded volumes.

def _ell_cov(gx, gy, cx, cy, rx, ry, soft):
    d = np.hypot((gx - cx) / max(rx, 1e-6), (gy - cy) / max(ry, 1e-6))
    return np.clip((1.0 - d) * (min(rx, ry) / soft) + 0.5, 0.0, 1.0)


def lobe_crescents(lobes, w, h, ox, oy, soft=1.4, wrap=True):
    light = np.zeros((h, w))
    dark = np.zeros((h, w))
    offs = (-w, 0, w) if wrap else (0,)
    for (cx, cy, rx, ry) in lobes:
        for xo in offs:
            X = cx + xo
            x0 = max(0, int(X - rx - abs(ox) - 3))
            x1 = min(w, int(X + rx + abs(ox) + 4))
            y0 = max(0, int(cy - ry - abs(oy) - 3))
            y1 = min(h, int(cy + ry + abs(oy) + 4))
            if x1 <= x0 or y1 <= y0:
                continue
            gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
            a = _ell_cov(gx, gy, X, cy, rx, ry, soft)
            b = _ell_cov(gx, gy, X + ox, cy + oy, rx, ry, soft)
            c = _ell_cov(gx, gy, X - ox, cy - oy, rx, ry, soft)
            sl = light[y0:y1, x0:x1]
            np.maximum(sl, np.clip(a - b, 0.0, 1.0), out=sl)
            sd = dark[y0:y1, x0:x1]
            np.maximum(sd, np.clip(a - c, 0.0, 1.0), out=sd)
    return light, dark


# ---------------------------------------------------------------------------
# shaded height-field layer (mountains / hills / banks)
# ---------------------------------------------------------------------------

def ridge_layer(w: int, h: int, top_hi: np.ndarray, *, top_col, bot_col,
                light_col, dark_col, lit: float = 0.30, shaded: float = 0.24,
                band: float = 110.0, slope_gain: float = 2.4, form: int = 81,
                crest: float = 0.0, crest_col=None, crest_w: float = 3.0,
                snow: float = 0.0, snow_col=None, snow_y: float = 0.0,
                snow_amp: float = 0.0, snow_soft: float = 26.0, snow_seed: int = 0,
                base_dark: float = 0.0, base_col=None, base_band: float = 60.0):
    """Rasterise a silhouette from a height field and light it.

    Facets are shaded from the sign of the slope of a *heavily smoothed* copy
    of the profile: where the skyline rises to the right the face points
    up-left and catches the key light, where it falls it turns away. Using the
    smoothed form rather than the raw profile is what keeps the shading reading
    as broad faces instead of vertical streaks hanging off every small peak.
    """
    ss = len(top_hi) // w
    alpha = coverage(w, h, top_hi)
    top = top_hi.reshape(w, ss).mean(axis=1)
    form_top = smooth_circ(top, form)

    yy = np.arange(h, dtype=np.float64)[:, None]
    depth_f = yy - form_top[None, :]
    depth_a = yy - top[None, :]

    lo = float(top.min())
    t = np.clip((yy - lo) / max(h - lo, 1.0), 0.0, 1.0)
    rgb = (rgb3(top_col)[None, None, :] * (1.0 - t[..., None])
           + rgb3(bot_col)[None, None, :] * t[..., None])
    rgb = np.broadcast_to(rgb, (h, w, 3)).copy()

    fall = np.exp(-np.clip(depth_f, 0.0, None) / band)
    slope = smooth_circ(grad_circ(form_top), max(5, form // 3))
    sn = slope / (np.abs(slope).max() + 1e-9)
    facing = np.clip(-sn * slope_gain, -1.0, 1.0)[None, :]

    rgb += (rgb3(light_col)[None, None, :] - rgb) * (np.clip(facing, 0, 1) * fall * lit)[..., None]
    rgb += (rgb3(dark_col)[None, None, :] - rgb) * (np.clip(-facing, 0, 1) * fall * shaded)[..., None]

    if snow > 0.0 and snow_col is not None:
        line = (snow_y
                + (artlib.ridge_profile(w, 3, snow_seed, 0.5) - 0.5) * 2.0 * snow_amp
                + (artlib.ridge_profile(w, 11, snow_seed + 1, 0.5) - 0.5) * snow_amp * 0.9)
        m = smoothstep((line[None, :] - yy) / snow_soft)
        m = m * (0.55 + 0.45 * np.clip(facing, 0, 1)) * snow
        rgb += (rgb3(snow_col)[None, None, :] - rgb) * m[..., None]

    if crest > 0.0 and crest_col is not None:
        cm = np.exp(-np.clip(depth_a, 0.0, None) / crest_w) * crest
        cm = cm * (0.40 + 0.60 * np.clip(facing * 0.5 + 0.5, 0, 1))
        rgb += (rgb3(crest_col)[None, None, :] - rgb) * cm[..., None]

    if base_dark > 0.0 and base_col is not None:
        bm = smoothstep((yy - (h - base_band)) / base_band) * base_dark
        rgb += (rgb3(base_col)[None, None, :] - rgb) * bm[..., None]

    return to_image(rgb, alpha), top


# ---------------------------------------------------------------------------
# vegetation
# ---------------------------------------------------------------------------

def draw_fir(cv, lobes, x, base_y, height, half_w, color, tiers=5, lean=0.0):
    """A stylised fir: stacked triangles over a stub of trunk."""
    trunk_h = height * 0.14
    artlib.rounded(cv, x - half_w * 0.11, base_y - trunk_h * 1.5,
                   half_w * 0.22, trunk_h * 1.6, half_w * 0.09, color)
    body = height - trunk_h
    for i in range(tiers):
        f0 = i / tiers
        f1 = min(1.06, (i + 2.1) / tiers)
        by = base_y - trunk_h - body * f0
        ty = base_y - trunk_h - body * f1
        hw = half_w * (1.0 - 0.21 * i)
        sx = x + lean * (i / max(tiers - 1, 1)) * half_w
        artlib.poly(cv, [(sx - hw, by), (sx + hw, by), (sx, ty)], color)
        if lobes is not None:
            lobes.append((sx - hw * 0.15, (by + ty) * 0.5, hw * 0.72, (by - ty) * 0.42))


def draw_tree_round(cv, lobes, x, base_y, height, color, trunk_col, rng):
    """A broadleaf tree: visible tapered trunk under a wide, bold crown.

    Few large lobes rather than many small ones -- at the size these layers are
    drawn on a phone, a crown of small blobs dissolves into cauliflower.
    """
    cw = height * 0.88
    ch = height * 0.60
    cy = base_y - height + ch * 0.50
    tw = max(3.0, height * 0.070)
    trunk_top = cy + ch * 0.34
    artlib.poly(cv, [(x - tw, base_y + 3), (x + tw, base_y + 3),
                     (x + tw * 0.42, trunk_top), (x - tw * 0.42, trunk_top)], trunk_col)
    artlib.poly(cv, [(x - tw * 0.3, trunk_top + ch * 0.10), (x + tw * 0.5, trunk_top + ch * 0.10),
                     (x + cw * 0.22, cy + ch * 0.02), (x + cw * 0.15, cy + ch * 0.14)], trunk_col)

    spec = [(0.0, -0.04, 0.56, 0.58), (-0.40, 0.14, 0.40, 0.40),
            (0.42, 0.16, 0.38, 0.38), (-0.22, -0.30, 0.40, 0.38),
            (0.26, -0.28, 0.37, 0.36)]
    for fx, fy, frx, fry in spec:
        cx = x + fx * cw
        cyy = cy + fy * ch
        rx = cw * frx * float(rng.uniform(0.94, 1.06))
        ry = ch * fry * float(rng.uniform(0.96, 1.10))
        artlib.blob(cv, cx, cyy, rx, ry, color)
        if lobes is not None:
            lobes.append((cx, cyy, rx, ry))


def draw_tree_slender(cv, lobes, x, base_y, height, color, trunk_col, rng):
    """A tall narrow tree: a column of lobes on a clearly visible trunk."""
    cw = height * 0.36
    tw = max(3.0, height * 0.052)
    trunk_top = base_y - height * 0.72
    artlib.poly(cv, [(x - tw, base_y + 3), (x + tw, base_y + 3),
                     (x + tw * 0.40, trunk_top), (x - tw * 0.40, trunk_top)], trunk_col)
    steps = 4
    for i in range(steps):
        f = i / (steps - 1)
        cyy = base_y - height * (0.40 + 0.46 * f)
        taper = 0.26 + 0.74 * (1.0 - f ** 1.7)
        rx = cw * 0.5 * taper * float(rng.uniform(0.94, 1.08))
        ry = height * 0.16 * (1.0 - 0.26 * f)
        cx = x + math.sin(f * 3.3 + float(rng.uniform(0, 2))) * cw * 0.09
        artlib.blob(cv, cx, cyy, rx, ry, color)
        if lobes is not None:
            lobes.append((cx, cyy, rx, ry))
    artlib.poly(cv, [(x - cw * 0.15, base_y - height * 0.84),
                     (x + cw * 0.15, base_y - height * 0.84),
                     (x, base_y - height * 1.03)], color)


def draw_bush(cv, lobes, x, base_y, w, h, color, rng):
    """A chunky rounded bush: three or four big lobes on a flat base."""
    spec = [(0.0, -0.06, 0.50, 0.56), (-0.44, 0.16, 0.38, 0.40),
            (0.46, 0.18, 0.35, 0.37)]
    if rng.random() < 0.55:
        spec.append((float(rng.uniform(-0.30, 0.30)), -0.34, 0.33, 0.32))
    for fx, fy, frx, fry in spec:
        cx = x + fx * w
        cy = base_y - h * 0.5 + fy * h
        rx = w * frx * float(rng.uniform(0.94, 1.06))
        ry = h * fry * float(rng.uniform(0.94, 1.08))
        artlib.blob(cv, cx, cy, rx, ry, color)
        if lobes is not None:
            lobes.append((cx, cy, rx, ry))
    artlib.rounded(cv, x - w * 0.52, base_y - h * 0.38, w * 1.04, h * 0.44, h * 0.18, color)


def draw_rock(cv, x, y, rx, ry, col_mid, col_hi, col_lo, rng, verts=20):
    """An embedded stone: one body, one lit facet, one contact shadow.

    The facet is the body polygon clipped to the half-plane above a line through
    the centroid, not a shrunken copy of the outline. Shrinking the outline
    makes concentric rings -- a button, or a bottle cap -- and at the 0.65
    downscale the game draws these at, that is exactly what it reads as. A
    single straight-edged highlight reads as a plane catching the key light,
    which is what a stone actually looks like.
    """
    pts = []
    for i in range(verts):
        a = i / verts * math.tau
        rr = 1.0 + (float(rng.random()) - 0.5) * 0.17
        pts.append((x + math.cos(a) * rx * rr, y + math.sin(a) * ry * rr))

    artlib.poly(cv, [(px, py + ry * 0.13 + 1.6) for px, py in pts], artlib.rgba(col_lo, 105))
    artlib.poly(cv, pts, artlib.rgba(col_mid, 255))

    # the facet: everything on the upper-left side of a line through the body,
    # tilted so the break runs with the light rather than across it
    tilt = float(rng.uniform(0.28, 0.52))          # fall per unit of x, downhill right
    off = float(rng.uniform(-0.16, 0.10)) * ry
    facet = []
    prev = None
    for px, py in pts + [pts[0]]:
        above = py - y < (px - x) * tilt + off
        if prev is not None and above != prev[2]:
            # intersect the edge with the facet line so the cut is clean
            ax, ay, _ = prev
            dx, dy = px - ax, py - ay
            den = dy - dx * tilt
            t = 0.5 if abs(den) < 1e-9 else ((ax - x) * tilt + off + y - ay) / den
            t = min(1.0, max(0.0, t))
            facet.append((ax + dx * t, ay + dy * t))
        if above:
            facet.append((px, py))
        prev = (px, py, above)
    if len(facet) >= 3:
        artlib.poly(cv, facet, artlib.rgba(col_hi, 104))


# ---------------------------------------------------------------------------
# sky
# ---------------------------------------------------------------------------

# Where the sun or moon sits, as a fraction of the framebuffer, plus the shape
# of its disc and glow. Position and draw size are mirrored in
# `manifest.SUN_POS` / `SUN_DRAW` because the runtime has to blit the sprite
# there; `tests/test_contract.py` keeps the two copies honest.
#
# Dawn is low on the LEFT and dusk low on the RIGHT. That mirror, the opposed
# sky ramps and the opposed cloud lighting are what stop the two twilight
# themes from being one picture at two exposures.
SUN = {
    # `core` lifts the disc toward white. Dawn and dusk need it most: their sun
    # sits against the brightest part of their own sky, and a disc painted in
    # the sun colour alone has nothing to read against.
    "dawn":  dict(r=44.0, edge=0.085, core=0.54, bloom_amt=0.54, halo=0.30, horizon=0.50),
    "day":   dict(r=31.0, edge=0.070, core=0.22, bloom_amt=0.46, halo=0.26, horizon=0.22),
    "dusk":  dict(r=50.0, edge=0.095, core=0.42, bloom_amt=0.58, halo=0.32, horizon=0.56),
    "night": dict(r=33.0, edge=0.045, core=0.00, bloom_amt=0.28, halo=0.22, horizon=0.20),
}
for _t, _c in SUN.items():
    _c["cx"], _c["cy"] = manifest.SUN_POS[_t]
    _c["draw"] = manifest.SUN_DRAW[_t]


def sky_sample(theme: str, pos: float):
    stops = PAL[theme]["sky"]
    ps = np.array([q for q, _ in stops], dtype=np.float64)
    cs = np.array([c for _, c in stops], dtype=np.float64)
    return tuple(int(round(float(np.interp(pos, ps, cs[:, i])))) for i in range(3))


def build_stars() -> Image.Image:
    """The star field: one tile, shared by dusk and night, drawn at two alphas.

    Stars are round here because the sprite is scaled by one factor in both
    axes at run time. They used to live in the sky strip, which the game
    stretches to the framebuffer, so every one of them came out a horizontal
    dash -- 1.66x too wide on the reference phone and 2.84x on a wide window.

    The tile is drawn at the screen's height and repeated in x, so it has to
    wrap: every star is stamped at x-w, x and x+w, and the reduction is done
    with `resolve_wrap`.
    """
    w, h = manifest.STARS_SIZE
    rng = np.random.default_rng(90210)
    cv = artlib.canvas(w, h)
    d = artlib.draw_on(cv)
    tints = [(255, 255, 255), (206, 224, 255), (255, 236, 212), (186, 212, 255)]
    y_limit = 0.80
    band_x0, band_x1 = 0.22, 0.86

    def dot(x, y, r, col):
        for ox in wrap_xs(w, x):
            d.ellipse((artlib.s(ox - r), artlib.s(y - r),
                       artlib.s(ox + r), artlib.s(y + r)), fill=col)

    for _ in range(620):
        y = (rng.random() ** 1.9) * h * y_limit
        if rng.random() < 0.34:
            # a loose galactic band leaning across the field, so the scatter is
            # not uniform noise -- the eye reads structure as "sky", not "dots"
            fy = y / (h * y_limit)
            bx = band_x0 + (band_x1 - band_x0) * fy
            x = float(rng.normal(bx, 0.085) % 1.0) * w
        else:
            x = rng.random() * w
        depth = 1.0 - y / (h * y_limit)
        r = float(rng.uniform(0.85, 1.95)) * (0.78 + 0.46 * depth)
        a = int(float(rng.uniform(105, 255)) * (0.58 + 0.42 * depth))
        dot(x, y, r, artlib.rgba(tints[rng.integers(0, len(tints))], a))

    # a handful of bright ones carry a halo and a four-armed glint; both arms
    # are the same length, so the cross stays square instead of stretching
    for _ in range(15):
        x = rng.random() * w
        y = (rng.random() ** 2.1) * h * y_limit * 0.9
        r = float(rng.uniform(2.0, 3.2))
        col = artlib.rgba(tints[rng.integers(0, 2)], 255)
        dot(x, y, r * 2.6, artlib.rgba(col, 86))
        dot(x, y, r, col)
        arm = r * 5.4
        glint = artlib.rgba(col, 150)
        for ox in wrap_xs(w, x):
            d.polygon([(artlib.s(ox - arm), artlib.s(y)), (artlib.s(ox), artlib.s(y - r * 0.5)),
                       (artlib.s(ox + arm), artlib.s(y)), (artlib.s(ox), artlib.s(y + r * 0.5))],
                      fill=glint)
            d.polygon([(artlib.s(ox), artlib.s(y - arm)), (artlib.s(ox - r * 0.5), artlib.s(y)),
                       (artlib.s(ox), artlib.s(y + arm)), (artlib.s(ox + r * 0.5), artlib.s(y))],
                      fill=glint)

    out = resolve_wrap(cv, w, h)
    del cv
    return soft_edges(out, 0.45)


def build_sundisc(theme: str) -> Image.Image:
    """The sun or moon: disc, halo and bloom, as one square straight-alpha sprite.

    Keeping it out of the sky strip is what makes it circular on every display.
    The glow is baked as colour-plus-alpha rather than as a screen blend because
    the game composites it normally; the sun colour is close enough to white
    that alpha-over and screen agree to within a couple of levels over every
    sky in the set, and this way the sprite is self-contained.
    """
    n = manifest.SUNDISC_SIZE[0]
    big = n * 2                       # drawn at 2x and reduced, like everything else
    cfg = SUN[theme]
    p = PAL[theme]
    seed = 1200 + manifest.THEMES.index(theme) * 17

    # px per world unit inside the sprite, from the world size it is drawn at
    k = big / cfg["draw"]
    r = cfg["r"] * k
    cx = cy = big * 0.5
    yy, xx = np.mgrid[0:big, 0:big].astype(np.float64)
    dist = np.hypot(xx - cx, yy - cy)

    bloom_r = big * 0.47
    halo_r = r * 3.4
    glow = (np.clip(1.0 - dist / bloom_r, 0.0, 1.0) ** 2.2 * cfg["bloom_amt"]
            + 0.0)
    halo = np.clip(1.0 - dist / halo_r, 0.0, 1.0) ** 2.0 * cfg["halo"]
    disc = smoothstep((1.0 + cfg["edge"] - dist / r) / max(cfg["edge"] * 2.0, 0.02))

    col = np.broadcast_to(rgb3(p["sun"])[None, None, :], (big, big, 3)).copy()
    if cfg["core"] > 0.0:
        # brightest at the centre, falling to the sun colour at the limb, so the
        # disc has a form of its own instead of being a flat counter
        hot = smoothstep((1.0 - dist / r) / 0.85) * cfg["core"]
        col += (rgb3((255, 255, 255))[None, None, :] - col) * hot[..., None]
    if theme == "night":
        # a cratered, limb-darkened moon -- the one place in the set where the
        # light source itself carries detail
        rngm = np.random.default_rng(seed + 5)
        shade_m = 1.0 - 0.17 * np.clip(dist / r, 0, 1) ** 2
        col *= np.where(disc > 0.0, shade_m, 1.0)[..., None]
        for _ in range(6):
            a = rngm.uniform(0, math.tau)
            rad = rngm.uniform(0.15, 0.72)
            cd = np.hypot(xx - (cx + math.cos(a) * rad * r), yy - (cy + math.sin(a) * rad * r))
            cr = rngm.uniform(0.11, 0.26) * r
            col *= (1.0 - smoothstep((1.0 - cd / cr) / 0.55) * rngm.uniform(0.07, 0.15)
                    * (disc > 0.0))[..., None]
        for _ in range(3):
            a = rngm.uniform(0, math.tau)
            rad = rngm.uniform(0.2, 0.6)
            cd = np.hypot(xx - (cx + math.cos(a) * rad * r), yy - (cy + math.sin(a) * rad * r))
            cr = rngm.uniform(0.10, 0.18) * r
            col *= (1.0 + smoothstep((1.0 - cd / cr) / 0.6) * 0.06 * (disc > 0.0))[..., None]

    # combine by screening the three terms, so the disc stays fully opaque and
    # the halo does not add a step where it meets the bloom
    alpha = 1.0 - (1.0 - np.clip(glow, 0, 1)) * (1.0 - np.clip(halo, 0, 1)) * (1.0 - disc)
    # and make sure nothing survives in the corners, where the square sprite
    # would otherwise clip its own glow against a straight edge
    alpha *= smoothstep((bloom_r - dist) / (big * 0.04))

    out = np.empty((big, big, 4), dtype=np.float64)
    out[..., :3] = col
    out[..., 3] = np.clip(alpha, 0.0, 1.0) * 255.0
    img = Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGBA")
    return artlib.resolve(img, n, n)


def build_sky(theme: str) -> Image.Image:
    """The sky gradient alone: nothing in here varies with x except the dither.

    The game stretches this strip to the whole framebuffer, so x-varying content
    is scaled by an aspect-dependent factor and cannot be authored correctly.
    The sun and the stars are separate sprites for exactly that reason, and the
    horizon glow is a pure vertical ramp rather than the wide ellipse it was.
    """
    w, h = manifest.SKY_SIZE
    p = PAL[theme]
    cfg = SUN[theme]
    seed = 1000 + manifest.THEMES.index(theme) * 17

    arr = np.asarray(artlib.vertical_gradient(w, h, p["sky"]), dtype=np.float64).copy()
    yy = np.mgrid[0:h, 0:w][0].astype(np.float64)

    hz_col = mix(p["sun"], p["accent"], 0.55 if theme in ("dawn", "dusk") else 0.25)
    band = np.clip(1.0 - np.abs(yy - h * 0.885) / (h * 0.205), 0.0, 1.0) ** 1.8 * cfg["horizon"]
    src = rgb3(hz_col)[None, None, :] * band[..., None]
    arr[..., :3] = 255.0 - (255.0 - arr[..., :3]) * (255.0 - src) / 255.0

    # dither: 1080px of smooth gradient bands badly on cheap panels
    n = artlib.value_noise(w, h, 24, seed + 77, octaves=2)
    arr[..., :3] += (n[..., None] - 0.5) * 4.2
    arr[..., 3] = 255.0

    img = Image.fromarray(np.clip(arr, 0, 255).round().astype(np.uint8), "RGBA")
    return artlib.grain(img, 0.026, seed + 9, cells=10)


# ---------------------------------------------------------------------------
# far layer -- distant mountains
# ---------------------------------------------------------------------------

def build_far(theme: str) -> Image.Image:
    """Distant mountains, composed for the window they are actually seen through.

    The far layer only ever shows above the mid skyline -- about 150 world units
    of sky, from y 606 down to where mid's back ridge starts. The silhouette is
    therefore pushed up against the top of the canvas, and everything below the
    reachable window is cropped out of the texture rather than shipped hidden:
    that is why this layer is 380 rows rather than 560.
    """
    w, h = manifest.LAYER_FAR
    p = PAL[theme]
    fog = p["fog"]
    seed = 2000 + manifest.THEMES.index(theme) * 23
    # The silhouette is deliberately the SAME range in all four themes; only its
    # colour changes. The themes cross-fade into one another, so a per-theme
    # skyline dissolves one mountain range into a different one every twelve
    # points -- the player is meant to be travelling through a day, not
    # teleporting to another country. This also keeps the fade clean now that
    # the far layer is actually visible.
    shape = 2000
    hi = w * 4

    back = tri_profile(hi, 3, shape, roughness=0.34, octaves=4)
    front = tri_profile(hi, 4, shape + 1, roughness=0.32, octaves=4)
    roll = artlib.ridge_profile(hi, 2, shape + 2, 0.5)

    top_back = h - (108.0 + 166.0 * back + 20.0 * roll)
    top_front = h - (125.0 + 120.0 * front)

    col_b = mix(p["far"], fog, 0.66)
    col_f = mix(p["far"], fog, 0.44)
    snow_col = mix((252, 253, 255), mix(fog, p["sun"], 0.5), 0.45)

    img_b, _ = ridge_layer(
        w, h, top_back,
        top_col=col_b, bot_col=mix(col_b, fog, 0.58),
        light_col=mix(col_b, p["sun"], 0.34), dark_col=artlib.shade(col_b, -0.20),
        lit=0.36, shaded=0.26, band=95.0, slope_gain=2.8, form=121,
        crest=0.26, crest_col=mix(col_b, p["sun"], 0.60), crest_w=2.6,
        snow=0.42, snow_col=snow_col, snow_y=h - 201.0, snow_amp=30.0,
        snow_soft=30.0, snow_seed=shape + 40)
    img_f, _ = ridge_layer(
        w, h, top_front,
        top_col=col_f, bot_col=mix(col_f, fog, 0.52),
        light_col=mix(col_f, p["sun"], 0.34), dark_col=artlib.shade(col_f, -0.26),
        lit=0.36, shaded=0.30, band=80.0, slope_gain=2.8, form=95,
        crest=0.26, crest_col=mix(col_f, p["sun"], 0.58), crest_w=2.4,
        snow=0.34, snow_col=mix(snow_col, fog, 0.25), snow_y=h - 195.0, snow_amp=24.0,
        snow_soft=26.0, snow_seed=shape + 41)

    out = Image.alpha_composite(img_b, img_f)
    return artlib.grain(out, 0.028, seed + 3, cells=7)


# ---------------------------------------------------------------------------
# mid layer -- rolling hills with small conifers
# ---------------------------------------------------------------------------

def build_mid(theme: str) -> Image.Image:
    w, h = manifest.LAYER_MID
    p = PAL[theme]
    fog = p["fog"]
    seed = 3000 + manifest.THEMES.index(theme) * 29
    hi = w * 4

    pb = artlib.ridge_profile(hi, 2, seed, 0.30)
    pf = artlib.ridge_profile(hi, 4, seed + 1, 0.28)
    top_back = h - (210.0 + 120.0 * pb)
    top_front = h - (135.0 + 85.0 * pf)

    col_b = artlib.saturate(mix(p["mid"], fog, 0.34), 1.08)
    col_f = artlib.shade(artlib.saturate(mix(p["mid"], fog, 0.12), 1.12), -0.06)

    img_b, tb = ridge_layer(
        w, h, top_back,
        top_col=col_b, bot_col=mix(col_b, fog, 0.34),
        light_col=mix(col_b, p["sun"], 0.42), dark_col=artlib.shade(col_b, -0.32),
        lit=0.46, shaded=0.34, band=62.0, slope_gain=2.3, form=161,
        crest=0.22, crest_col=mix(col_b, p["sun"], 0.55), crest_w=2.6)
    img_f, tf = ridge_layer(
        w, h, top_front,
        top_col=artlib.saturate(col_f, 1.06), bot_col=artlib.shade(col_f, -0.22),
        light_col=mix(col_f, p["sun"], 0.40), dark_col=artlib.shade(col_f, -0.36),
        lit=0.48, shaded=0.36, band=52.0, slope_gain=2.3, form=121,
        crest=0.24, crest_col=mix(col_f, p["sun"], 0.52), crest_w=2.4,
        base_dark=0.34, base_col=artlib.shade(p["mid"], -0.58), base_band=80.0)

    # a hint of forest cover just under each ridgeline
    yy = np.arange(h, dtype=np.float64)[:, None]
    for img, top, amt in ((img_b, tb, 0.22), (img_f, tf, 0.26)):
        n = artlib.value_noise(w, h, 26, seed + 5, octaves=3)
        m = np.exp(-np.clip(yy - top[None, :], 0.0, None) / 34.0) * smoothstep((n - 0.46) / 0.3)
        img.alpha_composite(_as_tint(img, m * amt, artlib.shade(p["mid"], -0.44)))

    # small conifers clustered along both ridgelines
    rng = np.random.default_rng(seed + 7)
    cv = artlib.canvas(w, h)
    lobes: list[tuple[float, float, float, float]] = []
    for top, col, groups, lo, hgh in ((tb, artlib.shade(col_b, -0.20), 13, 11.0, 20.0),
                                      (tf, artlib.shade(col_f, -0.28), 11, 15.0, 27.0)):
        for g in range(groups):
            gx = (g + rng.uniform(0.15, 0.85)) * (w / groups)
            for _ in range(int(rng.integers(3, 7))):
                x = gx + rng.uniform(-42, 42)
                base_y = top_at(top, x) + rng.uniform(1.0, 5.0)
                th = rng.uniform(lo, hgh)
                for ox in wrap_xs(w, x):
                    draw_fir(cv, lobes if ox == x else None, ox, base_y, th, th * 0.30,
                             col, tiers=4)
    trees = resolve_wrap(cv, w, h)
    del cv
    lt, dk = lobe_crescents(lobes, w, h, 1.4, 2.0, soft=1.2)
    trees = tint_clipped(trees, blur_wrap_np(lt, 1.2), mix(col_f, p["sun"], 0.60), 0.45)
    trees = tint_clipped(trees, blur_wrap_np(dk, 1.6), artlib.shade(p["mid"], -0.55), 0.30)

    out = Image.alpha_composite(img_b, trees)
    out = Image.alpha_composite(out, img_f)

    # soft occlusion where the front hills overlap the back ones
    sh = blur_wrap(ImageChops.offset(img_f.getchannel("A"), 0, -9), 7.0)
    sh = ImageChops.multiply(sh, ImageChops.invert(img_f.getchannel("A")))
    sh = ImageChops.multiply(sh, out.getchannel("A")).point(lambda v: int(v * 0.24))
    layer = Image.new("RGBA", out.size, artlib.rgba(artlib.shade(p["mid"], -0.70), 255))
    layer.putalpha(sh)
    out = Image.alpha_composite(out, layer)

    return artlib.grain(out, 0.032, seed + 11, cells=8)


def _as_tint(base: Image.Image, mask: np.ndarray, color) -> Image.Image:
    a = np.asarray(base.getchannel("A"), dtype=np.float64) / 255.0
    lay = Image.new("RGBA", base.size, artlib.rgba(color, 255))
    lay.putalpha(Image.fromarray(np.clip(mask * a * 255.0, 0, 255).astype(np.uint8), "L"))
    return lay


# ---------------------------------------------------------------------------
# near layer -- foreground bank, bush clusters, tall trees
# ---------------------------------------------------------------------------

def build_near(theme: str) -> Image.Image:
    w, h = manifest.LAYER_NEAR
    p = PAL[theme]
    seed = 4000 + manifest.THEMES.index(theme) * 31
    hi = w * 4

    base = artlib.saturate(p["near"], 1.14)
    pb = artlib.ridge_profile(hi, 2, seed, 0.38)
    pf = artlib.ridge_profile(hi, 3, seed + 1, 0.34)
    top_back = h - (108.0 + 50.0 * pb)
    top_front = h - (72.0 + 36.0 * pf)

    col_b = mix(base, p["fog"], 0.13)
    col_f = artlib.shade(base, -0.14)
    rim_col = mix(p["near"], p["sun"], 0.55)

    img_b, tb = ridge_layer(
        w, h, top_back,
        top_col=col_b, bot_col=artlib.shade(col_b, -0.28),
        light_col=mix(col_b, p["sun"], 0.44), dark_col=artlib.shade(col_b, -0.46),
        lit=0.48, shaded=0.32, band=48.0, slope_gain=2.2, form=181,
        crest=0.34, crest_col=rim_col, crest_w=2.4)
    img_f, tf = ridge_layer(
        w, h, top_front,
        top_col=col_f, bot_col=artlib.shade(col_f, -0.40),
        light_col=mix(col_f, p["sun"], 0.40), dark_col=artlib.shade(col_f, -0.54),
        lit=0.48, shaded=0.34, band=42.0, slope_gain=2.2, form=141,
        crest=0.34, crest_col=mix(rim_col, p["near"], 0.25), crest_w=2.2,
        base_dark=0.46, base_col=artlib.shade(p["near"], -0.74), base_band=72.0)

    rng = np.random.default_rng(seed + 13)
    fol_col = artlib.shade(artlib.saturate(p["near"], 1.20), -0.04)
    trunk_col = artlib.shade(p["near"], -0.46)

    cvc = artlib.canvas(w, h)
    lobes: list[tuple[float, float, float, float]] = []

    def stamp(fn, x, *a, **k):
        """Draw an element three times so it survives the x wrap."""
        for ox in wrap_xs(w, x):
            fn(cvc, lobes if ox == x else None, ox, *a, **k)

    # Vegetation arrives in a few clumps with real gaps between them, so the
    # mid layer still shows through the foreground instead of being walled off.
    groups = 5
    slot = w / groups
    for g in range(groups):
        gx = (g + 0.5) * slot + float(rng.uniform(-28.0, 28.0))
        kind = g % 3
        tall = float(rng.uniform(136.0, 170.0))
        by = top_at(tb, gx) + float(rng.uniform(4.0, 12.0))
        rr = np.random.default_rng(seed + g * 13 + 1)
        if kind == 0:
            stamp(draw_tree_slender, gx, by, tall, fol_col, trunk_col, rr)
        elif kind == 1:
            stamp(draw_tree_round, gx, by, tall * 0.74, fol_col, trunk_col, rr)
        else:
            for ox in wrap_xs(w, gx):
                draw_fir(cvc, lobes if ox == gx else None, ox, by, tall * 0.80,
                         tall * 0.21, fol_col, tiers=5, lean=0.05)

        # one companion tree, set to one side, noticeably shorter
        cx2 = gx + float(rng.uniform(42.0, 74.0)) * (1 if g % 2 else -1)
        h2 = tall * float(rng.uniform(0.44, 0.60))
        rr2 = np.random.default_rng(seed + g * 29 + 7)
        if kind == 1:
            stamp(draw_tree_slender, cx2, top_at(tb, cx2) + 7.0, h2 * 1.15,
                  fol_col, trunk_col, rr2)
        else:
            stamp(draw_tree_round, cx2, top_at(tb, cx2) + 7.0, h2, fol_col, trunk_col, rr2)

        # a bush tucked against the trunks
        bx = gx + float(rng.uniform(-62.0, 62.0))
        bw = float(rng.uniform(62.0, 96.0))
        stamp(draw_bush, bx, top_at(tb, bx) + 8.0, bw, bw * float(rng.uniform(0.44, 0.56)),
              fol_col, np.random.default_rng(seed + int(bx)))

    # low bushes on the front bank, below the skyline, also clumped
    for g in range(5):
        bx = (g + 0.5) * slot + float(rng.uniform(-70.0, 70.0))
        for j in range(2):
            x = bx + j * float(rng.uniform(46.0, 78.0))
            bw = float(rng.uniform(72.0, 122.0))
            stamp(draw_bush, x, top_at(tf, x) + 11.0, bw,
                  bw * float(rng.uniform(0.38, 0.50)), artlib.shade(fol_col, -0.14),
                  np.random.default_rng(seed + 500 + int(x)))

    canopy = resolve_wrap(cvc, w, h)
    del cvc

    canopy = vgrad_through(canopy, [
        (0.00, mix(fol_col, p["sun"], 0.30)),
        (0.40, fol_col),
        (1.00, artlib.shade(fol_col, -0.44)),
    ])
    lt, dk = lobe_crescents(lobes, w, h, 4.0, 5.0, soft=1.8)
    canopy = tint_clipped(canopy, blur_wrap_np(lt, 3.4), rim_col, 0.48)
    canopy = tint_clipped(canopy, blur_wrap_np(dk, 4.6), artlib.shade(p["near"], -0.68), 0.24)
    canopy = crescent(canopy, 2, 3, 1.5, rim_col, 0.55)

    out = Image.alpha_composite(img_b, canopy)

    cs = blur_wrap(ImageChops.offset(canopy.getchannel("A"), 3, 7), 5.5)
    cs = ImageChops.multiply(cs, ImageChops.invert(canopy.getchannel("A")))
    cs = ImageChops.multiply(cs, img_b.getchannel("A")).point(lambda v: int(v * 0.42))
    cl = Image.new("RGBA", (w, h), artlib.rgba(artlib.shade(p["near"], -0.78), 255))
    cl.putalpha(cs)
    out = Image.alpha_composite(out, cl)
    out = Image.alpha_composite(out, img_f)

    return artlib.grain(out, 0.036, seed + 17, cells=9)


# ---------------------------------------------------------------------------
# ground strip
# ---------------------------------------------------------------------------

def bump_field(xs: np.ndarray, w: float, items) -> np.ndarray:
    """Max of wrapped bumps -- used to carve clumps into a silhouette.

    Taking the maximum rather than the sum keeps overlapping clumps from
    piling into a single tall mound, and the wrapped offset means a clump
    sitting on x=0 continues correctly at x=w.
    """
    out = np.zeros_like(xs)
    for xc, half, hgt, power in items:
        dx = (xs - xc + w * 0.5) % w - w * 0.5
        np.maximum(out, hgt * np.exp(-(np.abs(dx) / half) ** power), out=out)
    return out


def build_ground(theme: str) -> Image.Image:
    """The scrolling strip: lit turf lid, topsoil, rocky subsoil, dark base.

    The grass clumps and blades are carved into the silhouette itself rather
    than stamped on as separate shapes, so the turf gradient and the warm rim
    light shade them for free and the top edge reads as one material instead of
    a band with decals glued to it.
    """
    w, h = manifest.GROUND_SIZE
    p = PAL[theme]
    seed = 5000 + manifest.THEMES.index(theme) * 37
    ss = 4
    hi = w * ss

    g_top = p["ground_top"]
    g_body = p["ground_body"]
    rng = np.random.default_rng(seed + 21)

    xs = np.arange(hi, dtype=np.float64) / ss
    p1 = artlib.ridge_profile(hi, 5, seed, 0.36)
    p2 = artlib.ridge_profile(hi, 15, seed + 1, 0.44)
    base_top = 36.0 - 13.0 * p1 - 6.0 * p2

    clumps = [(float(rng.uniform(0, w)), float(rng.uniform(12.0, 22.0)),
               float(rng.uniform(8.0, 15.0)), 2.2) for _ in range(16)]
    blades = []
    for _ in range(8):
        gx = float(rng.uniform(0, w))
        for k in (-1, 0, 1):
            blades.append((gx + k * float(rng.uniform(3.5, 6.5)),
                           float(rng.uniform(2.2, 3.6)),
                           float(rng.uniform(12.0, 20.0)) * (0.66 if k else 1.0),
                           1.15))
    top_hi = base_top - bump_field(xs, float(w), clumps + blades)

    alpha = coverage(w, h, top_hi)
    top = top_hi.reshape(w, ss).mean(axis=1)
    # the turf band hangs off a smoothed baseline, so a blade does not drag the
    # whole band up with it
    base = smooth_circ(base_top.reshape(w, ss).mean(axis=1), 41)

    yy = np.arange(h, dtype=np.float64)[:, None]
    depth = yy - top[None, :]

    turf_h = 56.0
    turf_bot = base + turf_h + (artlib.ridge_profile(w, 6, seed + 6, 0.45) - 0.5) * 15.0

    # --- subsoil --------------------------------------------------------
    # nudge the soil toward the theme accent: warm brown by day and at dawn,
    # cool slate at night, which keeps it off the turf's own hue
    soil_rich = mix(artlib.saturate(g_body, 1.12), p["accent"], 0.12)
    t = np.clip((yy - 60.0) / (h - 60.0), 0.0, 1.0)
    rgb = np.broadcast_to(
        rgb3(artlib.shade(soil_rich, 0.06))[None, None, :] * (1.0 - t[..., None])
        + rgb3(artlib.shade(soil_rich, -0.42))[None, None, :] * t[..., None],
        (h, w, 3)).copy()

    soil = smoothstep((yy - turf_bot[None, :]) / 16.0)
    n_fine = artlib.value_noise(w, h, 7, seed + 2, octaves=3)
    n_big = artlib.value_noise(w, h, 3, seed + 3, octaves=2)
    # the texture now survives almost to the last row; it used to be gone by
    # row 188 of 260, which left the bottom of the strip -- a sixth of the
    # screen on a 20:9 phone -- as one undifferentiated dark mass
    fade = soil * (1.0 - smoothstep((yy - (h - 24.0)) / 20.0))
    rgb *= (1.0 + ((n_fine - 0.5) * 0.16 + (n_big - 0.5) * 0.34) * fade)[..., None]

    # Two more horizons, each at a different period from the turf line and from
    # each other. Without them the only boundary in the strip is the turf edge,
    # and a single boundary at a single period is exactly the feature the eye
    # latches onto to spot the repeat.
    #
    # The contour comes from value noise rather than `ridge_profile`: a ridge
    # profile is a sum of sines on a whole number of periods, so its lowest
    # harmonic dominates and the line comes out as an even sawtooth -- a
    # decorative zigzag, which is worse than no stratum at all. Noise wanders.
    for depth, cells, amp, strength, sd in ((64.0, 3, 46.0, 0.52, 12),
                                            (172.0, 2, 62.0, 0.34, 13)):
        contour = artlib.value_noise(w, 8, cells, seed + sd, octaves=4)[0]
        line = turf_bot + depth + (contour - 0.5) * amp
        m_strat = smoothstep((yy - line[None, :]) / 13.0)
        rgb += (rgb3(artlib.shade(soil_rich, -0.15))[None, None, :] - rgb) * (
            m_strat * strength)[..., None]
        # a soft lighter lip along the break, so it reads as the top of a bed of
        # rock catching the light rather than as a line ruled across the soil
        lip = np.exp(-np.abs(yy - line[None, :]) / 7.0) * 0.17 * strength
        rgb += (rgb3(artlib.shade(soil_rich, 0.20))[None, None, :] - rgb) * lip[..., None]

    # a warmer topsoil horizon hugging the underside of the turf
    tops_col = artlib.shade(artlib.saturate(mix(g_body, g_top, 0.44), 1.12), 0.10)
    m_tops = (smoothstep((yy - turf_bot[None, :]) / 6.0)
              * smoothstep((turf_bot[None, :] + 42.0 - yy) / 24.0))
    rgb += (rgb3(tops_col)[None, None, :] - rgb) * (m_tops * 0.78)[..., None]

    # --- turf lid -------------------------------------------------------
    lum = (0.299 * g_top[0] + 0.587 * g_top[1] + 0.114 * g_top[2]) / 255.0
    turf_rich = artlib.saturate(g_top, 1.10)
    bt = np.clip((yy - base[None, :]) / np.maximum(turf_bot - base, 1.0)[None, :], 0.0, 1.0)
    band_col = (rgb3(artlib.shade(turf_rich, 0.04 + 0.18 * lum))[None, None, :] * (1.0 - bt[..., None])
                + rgb3(artlib.shade(turf_rich, -0.26 - 0.10 * lum))[None, None, :] * bt[..., None])
    n_top = artlib.value_noise(w, h, 14, seed + 4, octaves=3)
    band_col = band_col * (1.0 + (n_top - 0.5) * 0.12)[..., None]
    m_band = smoothstep((turf_bot[None, :] - yy) / 3.5)
    rgb = rgb * (1.0 - m_band[..., None]) + band_col * m_band[..., None]

    # occlusion under the turf so it reads as a lid sitting on the soil
    # A wide, shallow falloff rather than a narrow deep one. At 3.4px of falloff
    # on a strip this tall the occlusion collapsed, under the game's downscale,
    # into a 1-2px ink contour -- the hardest edge in the asset, and the single
    # feature that made the tile repeat obvious.
    below = np.clip(yy - turf_bot[None, :], 0.0, None)
    rgb *= (1.0 - np.exp(-below / 8.0) * (below > 0) * 0.18
            - np.exp(-below / 22.0) * (below > 0) * 0.13)[..., None]

    # warm rim light along the very top edge, following every clump and blade
    rim = np.exp(-np.clip(depth, 0.0, None) / 1.7) * (0.40 + 0.55 * lum)
    rgb += (rgb3(mix(artlib.shade(turf_rich, 0.14 + 0.26 * lum), p["sun"],
                     0.08 + 0.24 * lum))[None, None, :] - rgb) * rim[..., None]
    # and a whisper of bounce a little further down, to give the blades body
    rgb += (rgb3(artlib.shade(turf_rich, 0.06 + 0.22 * lum))[None, None, :] - rgb) * (
        np.exp(-np.clip(depth - 2.0, 0.0, None) / 5.0) * (0.08 + 0.14 * lum))[..., None]

    # --- dark base band --------------------------------------------------
    # darker, but no longer dead: the band used to sink to -0.76 of the soil at
    # 80% strength, which flattened the deepest fifth of the strip into one
    # value just as the soil noise was fading out above it
    base_wave = (artlib.ridge_profile(w, 4, seed + 8, 0.26) - 0.5) * 22.0
    bm = smoothstep((yy - (h - 68.0 + base_wave[None, :])) / 22.0) * 0.62
    rgb += (rgb3(artlib.shade(soil_rich, -0.72))[None, None, :] - rgb) * bm[..., None]
    # the very last row has to stay flat: the game extends it downward as a
    # solid skirt when the screen is taller than the strip
    flat = smoothstep((yy - (h - 4.0)) / 3.0)
    rgb += (rgb3(artlib.shade(soil_rich, -0.74))[None, None, :] - rgb) * flat[..., None]

    img = to_image(rgb, alpha)

    # --- embedded stones -------------------------------------------------
    # Stratified rather than clustered. Three clusters in one horizontal band
    # march past at a fixed depth and a fixed spacing, and that regularity is
    # what the eye locks onto; seven cells across the tile with the depth
    # spread over the whole soil body reads as scattered instead.
    cv = artlib.canvas(w, h)
    # The stone shares the soil's hue at low saturation instead of being a
    # neutral grey blob dropped onto a coloured bed -- a stone *is* the soil,
    # compacted. Kept close to the soil in value too, so it reads as embedded
    # rather than as a bright counter sitting on top.
    rock_mid = mix(artlib.saturate(artlib.shade(g_body, 0.15), 0.52), g_body, 0.35)
    rock_hi = mix(artlib.saturate(artlib.shade(g_body, 0.40), 0.6), p["sun"], 0.26)
    rock_lo = artlib.shade(artlib.saturate(g_body, 0.8), -0.56)

    def turf_bot_at(x):
        return float(np.interp(x % w, np.arange(w), turf_bot, period=w))

    cells = 7
    for c in range(cells):
        x = (c + float(rng.uniform(0.15, 0.85))) * w / cells
        y = float(rng.uniform(turf_bot_at(x) + 18.0, h - 78.0))
        rx = float(rng.uniform(14.0, 40.0))
        ry = rx * float(rng.uniform(0.46, 0.62))
        rr = np.random.default_rng(seed + int(x) * 3 + 1)
        for ox in wrap_xs(w, x):
            draw_rock(cv, ox, y, rx, ry, rock_mid, rock_hi, rock_lo, rr)
        for _ in range(int(rng.integers(1, 4))):
            px = x + float(rng.uniform(-52, 52))
            py = y + float(rng.uniform(-34, 40))
            prx = float(rng.uniform(6.0, 13.0))
            rr2 = np.random.default_rng(seed + int(px) * 5 + 3)
            for ox in wrap_xs(w, px):
                draw_rock(cv, ox, py, prx, prx * float(rng.uniform(0.58, 0.80)),
                          rock_mid, rock_hi, rock_lo, rr2, verts=14)

    # half-buried boulders in the base band, so the deepest zone has a shape in
    # it rather than being the place where the texture ran out
    deep_mid = artlib.shade(rock_mid, -0.30)
    deep_hi = artlib.shade(rock_hi, -0.34)
    for c in range(3):
        x = (c + float(rng.uniform(0.2, 0.8))) * w / 3.0
        y = float(rng.uniform(h - 92.0, h - 56.0))
        rx = float(rng.uniform(44.0, 60.0))
        rr = np.random.default_rng(seed + 700 + int(x))
        for ox in wrap_xs(w, x):
            draw_rock(cv, ox, y, rx, rx * float(rng.uniform(0.52, 0.66)),
                      deep_mid, deep_hi, artlib.shade(rock_lo, -0.2), rr)

    det = resolve_wrap(cv, w, h)
    del cv
    # the boulders sit *under* the base band's darkening, so they sink into it
    # instead of floating on top as cut-outs
    det_a = np.asarray(det.getchannel("A"), dtype=np.float64) / 255.0
    out = Image.alpha_composite(img, artlib.clip_to(det, img))
    sink = np.asarray(out, dtype=np.float64)
    sink[..., :3] += (rgb3(artlib.shade(soil_rich, -0.72))[None, None, :] - sink[..., :3]) * (
        bm * det_a * 0.78)[..., None]
    sink[..., :3] += (rgb3(artlib.shade(soil_rich, -0.74))[None, None, :] - sink[..., :3]) * (
        flat * det_a)[..., None]
    out = Image.fromarray(np.clip(sink, 0, 255).round().astype(np.uint8), "RGBA")
    out = artlib.grain(out, 0.026, seed + 29, cells=9)

    # The game extends the last row downward as a solid fill when the screen is
    # taller than the strip, so that row has to be exactly one colour -- the
    # grain above would otherwise leave a one-level step at the join.
    fin = np.asarray(out, dtype=np.float64).copy()
    fin[-1, :, :3] = fin[-1, :, :3].mean(axis=0)
    return Image.fromarray(np.clip(fin, 0, 255).round().astype(np.uint8), "RGBA")


# ---------------------------------------------------------------------------
# clouds
# ---------------------------------------------------------------------------

def cloud_lobes(idx: int):
    """Four silhouettes: wide cumulus, tower, long streak, compact puff."""
    if idx == 0:
        base = [(120, 196, 96, 30), (240, 200, 118, 32), (360, 196, 96, 28), (432, 198, 64, 24)]
        top = [(150, 160, 66, 48), (236, 128, 96, 72), (330, 150, 74, 56), (396, 170, 52, 38)]
    elif idx == 1:
        base = [(190, 206, 92, 28), (284, 208, 86, 26), (120, 204, 60, 22)]
        top = [(214, 150, 84, 64), (238, 96, 74, 60), (218, 56, 50, 40),
               (160, 162, 58, 44), (300, 168, 62, 46)]
    elif idx == 2:
        base = [(110, 190, 78, 22), (210, 194, 90, 24), (310, 190, 84, 22),
                (400, 192, 62, 20), (452, 194, 40, 16)]
        top = [(150, 166, 54, 34), (236, 156, 68, 42), (320, 166, 56, 34), (392, 174, 40, 26)]
    else:
        base = [(230, 198, 92, 30), (320, 202, 60, 24), (150, 200, 54, 24)]
        top = [(236, 152, 82, 60), (300, 170, 54, 40), (178, 170, 56, 42), (252, 112, 50, 38)]
    return base + top


def build_cloud(theme: str, idx: int) -> Image.Image:
    """Four silhouettes per theme, lit from wherever that theme's sun actually is.

    At dawn and dusk the sun sits on the horizon, below the entire cloud band,
    so the clouds are lit from *underneath* -- hot rims along their bellies and
    cool sky-mixed tops. Lighting them from above instead is what made the two
    twilight themes read as "day clouds tinted pink", and underlighting is the
    single most recognisable sunrise/sunset cue there is.
    """
    w, h = manifest.CLOUD_SIZE
    p = PAL[theme]
    seed = 6000 + manifest.THEMES.index(theme) * 41 + idx
    rng = np.random.default_rng(seed)
    cfg = SUN[theme]
    # the sun is below the cloud band at dawn and dusk, above it by day and at
    # night; and it is on the left at dawn, on the right at dusk
    under = cfg["cy"] > 0.5
    vsign = -1.0 if under else 1.0
    hsign = -1.0 if cfg["cx"] > 0.5 else 1.0

    lobes = [(float(a) + float(rng.uniform(-6, 6)), float(b) + float(rng.uniform(-4, 4)),
              float(c) * float(rng.uniform(0.94, 1.07)), float(d) * float(rng.uniform(0.94, 1.07)))
             for a, b, c, d in cloud_lobes(idx)]

    cv = artlib.canvas(w, h)
    for cx, cy, rx, ry in lobes:
        artlib.blob(cv, cx, cy, rx, ry, artlib.rgba(p["cloud"], 255))
    cloud = artlib.resolve(cv, w, h)
    del cv

    sky_mid = sky_sample(theme, 0.50)
    hot = artlib.shade(p["cloud"], 0.22)
    cool = mix(p["cloud"], sky_mid, 0.74)
    if under:
        # Hot stop at the bottom, cool stop at the top -- and the cool stop is
        # mixed toward the sky *above* the cloud band rather than the middle of
        # the gradient, because at twilight the middle is nearly as warm as the
        # cloud and the top would come out barely cooler than the belly.
        sky_hi = sky_sample(theme, 0.20)
        warm_bot = mix(artlib.shade(p["cloud"], 0.08), p["accent"], 0.52)
        stops = [(0.00, mix(p["cloud"], sky_hi, 0.70)),
                 (0.32, mix(p["cloud"], sky_hi, 0.40)),
                 (0.70, p["cloud"]), (1.00, warm_bot)]
    else:
        stops = [(0.00, hot), (0.34, p["cloud"]),
                 (0.72, mix(p["cloud"], sky_mid, 0.42)), (1.00, cool)]
    cloud = vgrad_through(cloud, stops)

    lt, dk = lobe_crescents(lobes, w, h, 9.0 * hsign, 11.0 * vsign, soft=2.4, wrap=False)
    lt = np.asarray(Image.fromarray((lt * 255).astype(np.uint8), "L")
                    .filter(ImageFilter.GaussianBlur(7.0)), dtype=np.float64) / 255.0
    dk = np.asarray(Image.fromarray((dk * 255).astype(np.uint8), "L")
                    .filter(ImageFilter.GaussianBlur(8.5)), dtype=np.float64) / 255.0
    # the lit edge goes toward the theme accent at twilight, so it reads as
    # orange light raking the underside rather than as a pale highlight
    lit_col = mix(p["sun"], p["accent"], 0.62) if under else mix((255, 255, 255), p["sun"], 0.50)
    rim_col = mix(p["sun"], p["accent"], 0.45) if under else mix((255, 255, 255), p["sun"], 0.35)
    cloud = tint_clipped(cloud, lt, lit_col, 0.38 if under else 0.34)
    cloud = tint_clipped(cloud, dk, mix(sky_mid, (0, 0, 0), 0.30), 0.17)

    cloud = crescent(cloud, int(3 * hsign), int(4 * vsign), 2.4, rim_col, 0.50, wrap=False)
    cloud = soft_edges(cloud, 2.0)

    return artlib.grain(cloud, 0.030, seed + 3, cells=7)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def seam_score(img: Image.Image) -> tuple[float, float]:
    """Test the wrap-around join against every other column join in the image.

    Rolling the tile by half its width moves the join to the middle, where the
    only new adjacent-column pair is (w-1, 0). So: measure the mean absolute
    step for every interior pair, then ask where the wrap pair falls in that
    distribution. A seamless tile puts it somewhere unremarkable; a broken one
    makes it the largest step in the image by a wide margin. A steep edge that
    genuinely lands on x=0 raises the step without making it an outlier, which
    is why this is scored by rank rather than by magnitude.

    Returns (rank in 0..1, mean step at the seam in 0-255 levels).
    """
    a = np.asarray(img, dtype=np.float64)
    pre = a[..., :3] * (a[..., 3:4] / 255.0)
    full = np.concatenate([pre, a[..., 3:4]], axis=2)
    cols = np.abs(np.diff(full, axis=1)).mean(axis=(0, 2))        # w-1 interior joins
    seam = float(np.abs(full[:, 0, :] - full[:, -1, :]).mean())
    rank = float((cols < seam).mean())
    return rank, seam


def check(path: str, size, cutout: str) -> list[str]:
    """cutout: 'corners' (transparent top corners), 'toprow', 'sparse' or 'none'.

    'sparse' is for layers that are *meant* to be mostly empty -- a star field is
    a few hundred dots on transparency, so the coverage and full-opacity
    heuristics below would report it as broken when it is exactly right.
    """
    problems: list[str] = []
    name = os.path.basename(path)
    img = Image.open(path)
    if img.mode != "RGBA":
        problems.append(f"{name}: mode {img.mode} != RGBA")
    if img.size != tuple(size):
        problems.append(f"{name}: size {img.size} != {tuple(size)}")
    a = np.asarray(img.convert("RGBA"), dtype=np.float64)
    al = a[..., 3]
    sparse = cutout == "sparse"
    if sparse:
        lit = int((al > 8).sum())
        if lit < 200:
            problems.append(f"{name}: only {lit} lit pixels -- the field is empty")
        if al.max() < 180:
            problems.append(f"{name}: brightest point is only alpha {al.max():.0f}")
        return problems
    if al.max() < 250:
        problems.append(f"{name}: never reaches opaque (max alpha {al.max():.0f})")
    opaque = al > 8
    if opaque.sum() < al.size * 0.02:
        problems.append(f"{name}: almost nothing drawn")
    rgbv = a[..., :3][opaque]
    if rgbv.size and rgbv.std() < 3.0:
        problems.append(f"{name}: RGB variance {rgbv.std():.2f} -- looks flat")
    if cutout == "corners":
        corners = [float(al[0, 0]), float(al[0, -1])]
        if max(corners) > 2:
            problems.append(f"{name}: top corners not transparent {corners}")
    elif cutout == "toprow":
        clear = float((al[0] < 8).mean())
        if clear < 0.6:
            problems.append(f"{name}: top row only {clear:.0%} transparent")
    return problems


def main() -> int:
    manifest.ensure_dirs()
    written: list[str] = []
    problems: list[str] = []
    seams: list[tuple[str, float, float]] = []

    for theme in manifest.THEMES:
        jobs = [
            (f"sky_{theme}.png", build_sky(theme), manifest.SKY_SIZE, "none", False),
            (f"far_{theme}.png", build_far(theme), manifest.LAYER_FAR, "corners", True),
            (f"mid_{theme}.png", build_mid(theme), manifest.LAYER_MID, "corners", True),
            (f"near_{theme}.png", build_near(theme), manifest.LAYER_NEAR, "corners", True),
            (f"ground_{theme}.png", build_ground(theme), manifest.GROUND_SIZE, "toprow", True),
        ]
        # The sun/moon is its own square sprite rather than part of the sky,
        # because the sky strip is stretched to the framebuffer's aspect ratio
        # and anything round baked into it becomes an ellipse on a wide screen.
        jobs.append((f"sundisc_{theme}.png", build_sundisc(theme),
                     manifest.SUNDISC_SIZE, "none", False))
        for i in range(manifest.CLOUD_VARIANTS):
            jobs.append((f"cloud_{theme}_{i}.png", build_cloud(theme, i),
                         manifest.CLOUD_SIZE, "corners", False))

        for name, img, size, cutout, tiles in jobs:
            path = manifest.image_path(name)
            img.save(path)
            written.append(name)
            problems += check(path, size, cutout)
            if tiles:
                rank, step = seam_score(img)
                seams.append((name, rank, step))
                if rank > 0.99 and step > 2.0:
                    problems.append(
                        f"{name}: wrap join is in the top {100 * (1 - rank):.1f}% of column "
                        f"steps ({step:.2f} levels) -- visible tiling join")
        print(f"  {theme}: {len(jobs)} files")

    # One star field serves every starry theme, tiled in x at a uniform scale so
    # each star stays round whatever the display's aspect ratio.
    star_path = manifest.image_path("stars.png")
    stars = build_stars()
    stars.save(star_path)
    written.append("stars.png")
    problems += check(star_path, manifest.STARS_SIZE, "sparse")
    rank, step = seam_score(stars)
    seams.append(("stars.png", rank, step))
    if rank > 0.99 and step > 2.0:
        problems.append(f"stars.png: visible tiling join ({step:.2f} levels)")
    print("  stars: 1 file")

    print(f"\nwrote {len(written)} images to {manifest.IMAGES}")
    print("seam check (rank of the wrap join among all column joins; <0.99 passes):")
    for name, rank, step in seams:
        print(f"  rank {rank:5.3f}  step {step:5.2f}  {name}")
    if problems:
        print("\nPROBLEMS:")
        for prob in problems:
            print("  " + prob)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
