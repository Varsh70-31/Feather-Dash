#!/usr/bin/env python3
"""Feather Dash -- obstacles, pickups and particle textures.

Writes into ``assets/images``:

* ``pillar_body_<theme>.png``   -- a carved monolith shaft segment that tiles
  seamlessly in Y, so the game can stack it to any height.
* ``pillar_cap_<theme>.png``    -- the flared capital at the mouth of the gap,
  authored for the *lower* pillar (gap above, so the sprite's top edge is the
  gap edge).
* ``pillar_cap_up_<theme>.png`` -- the same capital authored for the *upper*
  pillar (gap below).  It is a separate sprite rather than a vertical flip of
  the lower one because flipping also flips the lighting: a crown lit from
  above becomes a tray lit from below, and the collar's cast shadow ends up
  pointing at the light.  The two sprites share one silhouette table and
  differ only in their vertical value ramp, so they still read as one object.
* ``coin_0..7.png``             -- one full spin of the feather token about its
  vertical axis.  The sample angles are offset by half a step so no frame is
  exactly edge-on; a 1-in-8 frame that nearly disappears reads as a blink
  rather than a spin.
* ``fx_<name>.png``             -- white luminance particle textures; RGB is
  pure white everywhere and all shaping lives in the alpha channel, so the game
  can tint them to any colour.

Source resolutions come from ``manifest`` and every measurement here is derived
from them, because the game *upscales* this family on current hardware:
``display.compute`` sets scale = screen_h / 1080, which is 2.2-2.9 device px
per world unit on a modern phone.  Raising a size in the manifest is therefore
the whole fix -- nothing in this file has to be retouched.

Everything here is computed from the primitives in ``tools/artlib.py``.  No
imagery is sampled, traced, fetched or derived from any existing game.  The
script is idempotent: running it again regenerates every file from scratch.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import artlib  # noqa: E402
import manifest  # noqa: E402
from artlib import SS  # noqa: E402

try:  # the previews have to compose the art at the sizes the *game* draws it
    from featherdash import config as game_config  # noqa: E402
except Exception:  # pragma: no cover - the tool still works standalone
    game_config = None

COIN_DRAW_W = float(getattr(game_config, "COIN_SIZE", 56))     # world units
PILLAR_DRAW_W = float(getattr(game_config, "PILLAR_W", manifest.PILLAR_DRAW_W))
GROUND_DRAW_H = float(getattr(game_config, "GROUND_H", manifest.GROUND_DRAW_H))

PREVIEW_DIR = os.environ.get("FD_PREVIEW", os.path.join(tempfile.gettempdir(), "featherdash_preview"))


# ==========================================================================
# small numeric helpers
# ==========================================================================

def _to_img(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    """(H,W,3) colour + (H,W) alpha, both 0..255 floats, into an RGBA image."""
    out = np.empty(rgb.shape[:2] + (4,), np.uint8)
    out[:, :, :3] = np.clip(rgb, 0, 255).round().astype(np.uint8)
    out[:, :, 3] = np.clip(alpha, 0, 255).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def resolve_rgba(img: Image.Image, w: int, h: int) -> Image.Image:
    """`artlib.resolve` through premultiplied alpha.

    Lanczos on straight alpha drags the colour of fully transparent pixels into
    the silhouette edge, which fringes a cut-out sprite.  Premultiplying first
    keeps the edge the colour of the sprite.
    """
    a = np.asarray(img).astype(np.float64)
    pre = a.copy()
    pre[:, :, :3] *= a[:, :, 3:4] / 255.0
    small = np.asarray(
        artlib.resolve(Image.fromarray(pre.round().astype(np.uint8), "RGBA"), w, h)
    ).astype(np.float64)
    al = small[:, :, 3:4]
    rgb = np.where(al > 0.5, small[:, :, :3] * 255.0 / np.maximum(al, 0.5), 0.0)
    return _to_img(rgb, small[:, :, 3])


def resolve_tiled_y(img: Image.Image, w: int, h: int) -> Image.Image:
    """`artlib.resolve` for an image that has to keep tiling vertically.

    A plain downsample clamps at the top and bottom rows, which leaves a faint
    band at the join.  Stacking three copies first means the rows that survive
    were filtered against their real periodic neighbours.
    """
    arr = np.asarray(img)
    tall = Image.fromarray(np.concatenate([arr, arr, arr], 0), "RGBA")
    return artlib.resolve(tall, w, h * 3).crop((0, h, w, h * 2))


def _blur(a: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian blur of a 0..255 float mask; radius in supersampled pixels."""
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius))).astype(np.float64)


def _shift(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate an array by whole pixels, padding with zeros."""
    out = np.zeros_like(a)
    h, w = a.shape
    out[max(0, dy):min(h, h + dy), max(0, dx):min(w, w + dx)] = \
        a[max(0, -dy):min(h, h - dy), max(0, -dx):min(w, w - dx)]
    return out


def _ramp(xs: np.ndarray, pts) -> np.ndarray:
    """Piecewise-linear lookup over (position, value) control points."""
    return np.interp(xs, [p for p, _ in pts], [v for _, v in pts])


def _scale_profile(pts, k: float):
    """Rescale a cross-section whose positions are in pixels."""
    return tuple((p * k, v) for p, v in pts)


def _interp_stops(stops, u: np.ndarray) -> np.ndarray:
    pos = np.array([p for p, _ in stops], float)
    cols = np.array([artlib.rgba(c)[:3] for _, c in stops], float)
    out = np.empty(u.shape + (3,), float)
    for ch in range(3):
        out[..., ch] = np.interp(u, pos, cols[:, ch])
    return out


def _punch(rgb: np.ndarray, amount: float = 1.10) -> np.ndarray:
    """Push saturation a little; foreground art has to out-saturate the sky."""
    grey = (rgb * np.array([0.299, 0.587, 0.114])).sum(-1, keepdims=True)
    return grey + (rgb - grey) * amount


def _capsule(xx: np.ndarray, yy: np.ndarray, x0: float, x1: float, cy: float) -> np.ndarray:
    """Distance from a horizontal segment, for drawing rounded bars."""
    return np.hypot(xx - np.clip(xx, x0, x1), yy - cy)


def _alpha_floor(img: Image.Image, floor: int = 3) -> Image.Image:
    """Clip the last breath of a glow to zero so cut-outs have clean corners."""
    a = np.asarray(img).copy()
    al = a[:, :, 3]
    al[al <= floor] = 0
    a[:, :, 3] = al
    return Image.fromarray(a, "RGBA")


def _grid(w: int, h: int):
    """Target-space x/y fields over a supersampled canvas."""
    W, H = w * SS, h * SS
    xt = (np.arange(W) + 0.5) / SS
    yt = (np.arange(H) + 0.5) / SS
    return np.repeat(xt[None, :], H, 0), np.repeat(yt[:, None], W, 1), W, H


def srgb_to_lab(rgb) -> np.ndarray:
    """CIELab (D65) for a colour or an array of colours, used by `verify`."""
    c = np.asarray(rgb, float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    t = (c @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(t > (6 / 29) ** 3, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


# ==========================================================================
# PILLARS
# ==========================================================================
# The obstacle is a carved monolith, not a pipe: a faceted prism with a lit
# bevel down its left side, two cut flutes, carved rings every segment, and a
# capital whose gap-facing rim carries a glowing inlay.  Light comes from the
# upper left everywhere in the game, so the bright bevel is always on the left
# and the facet steps are crisp rather than airbrushed.

def _shaft_stops(pal):
    """Horizontal colour ramp across the shaft: four facets, crisp edges."""
    p = artlib.rgba(pal["pillar"])
    pl = artlib.rgba(pal["pillar_light"])
    white = (255, 255, 255, 255)
    sh, mix = artlib.shade, artlib.mix
    return [
        (0.000, sh(p, -0.86)),            # dark contact edge
        (0.011, sh(p, -0.52)),
        (0.015, mix(pl, white, 0.55)),    # rim light: crisp bright sliver
        (0.038, mix(pl, white, 0.26)),
        (0.043, pl),                      # step onto the bevel facet
        (0.090, mix(p, pl, 0.88)),
        (0.097, mix(p, pl, 0.46)),        # crisp facet edge onto the main face
        (0.300, mix(p, pl, 0.28)),
        (0.540, mix(p, pl, 0.07)),
        (0.572, mix(p, pl, 0.03)),
        (0.580, sh(p, -0.28)),            # crisp facet edge onto the shadow facet
        (0.772, sh(p, -0.41)),
        (0.780, sh(p, -0.59)),            # crisp facet edge onto the dark facet
        (0.896, sh(p, -0.65)),
        (0.928, sh(p, -0.34)),            # bounce light off the shadow side
        (0.956, sh(p, -0.44)),
        (0.975, sh(p, -0.72)),
        (1.000, sh(p, -0.88)),
    ]


# The shaft's main face, between the two crisp facet edges.  `verify` measures
# its colour against the sky, because that pairing is what decides whether the
# obstacle is visible at all.
FACE_U = (0.10, 0.57)

# flute cross-section: shadowed left wall, crease, lit right wall
FLUTE = ((-1.00, 0.00), (-0.93, -0.24), (-0.60, -0.34), (-0.03, -0.30),
         (0.03, 0.19), (0.40, 0.23), (0.86, 0.07), (1.00, 0.00))
FLUTES = ((0.255, 0.046, 1.00), (0.460, 0.040, 0.84))


def _flute_mul(u: np.ndarray) -> np.ndarray:
    m = np.ones_like(u)
    for uc, hw, k in FLUTES:
        d = np.clip((u - uc) / hw, -1.0, 1.0)
        m *= 1.0 + _ramp(d, FLUTE) * k
    return m


def shaft_rgb(pal, u: np.ndarray, flute=1.0) -> np.ndarray:
    base = _interp_stops(_shaft_stops(pal), u)
    return base * (1.0 + (_flute_mul(u) - 1.0) * flute)[..., None]


# Carved ring cross-sections, in pixels either side of the seam at the
# resolution the shaft was authored at.  `_band_mul` rescales them, so a groove
# keeps the same width in world units however large the source becomes.
BODY_REF_H = 128.0
RING = ((-7.0, 0.00), (-5.6, 0.08), (-5.1, -0.44), (-3.6, -0.33),
        (1.1, -0.17), (1.9, 0.21), (3.5, 0.25), (4.1, 0.05), (6.0, 0.00))
HAIRLINE = ((-3.2, 0.00), (-1.7, -0.15), (-0.12, -0.19),
            (0.12, 0.11), (1.5, 0.09), (2.8, 0.00))
# (position in the tile, strength, profile, wobble amp, wobble freq, phase)
BANDS = (
    (0.295, 1.00, RING, 0.9, 2.0, 0.40),
    (0.800, 0.62, HAIRLINE, 0.6, 3.0, 2.30),
)


def _band_mul(xs: np.ndarray, ys: np.ndarray, h: float) -> np.ndarray:
    """Carved rings.  Distance to each seam wraps, so the shaft keeps tiling."""
    k = h / BODY_REF_H
    m = np.ones((ys.size, xs.size))
    for pos, amp, prof, wamp, freq, ph in BANDS:
        wob = wamp * k * (np.sin(xs * math.tau * freq + ph)
                          + 0.5 * np.sin(xs * math.tau * freq * 2.7 + ph * 1.7))
        d = ys[:, None] * h - pos * h - wob[None, :]
        d = (d + h / 2) % h - h / 2                     # wrapped signed distance, px
        weather = 0.72 + 0.56 * np.interp(xs, np.linspace(0, 1, 9),
                                          np.array([.3, .9, .5, 1., .4, .8, .35, .95, .3]))
        m *= 1.0 + _ramp(d, _scale_profile(prof, k)) * amp * weather[None, :]
    return m


def build_pillar_body(theme: str) -> Image.Image:
    pal = manifest.THEME_PALETTE[theme]
    w, h = manifest.PILLAR_BODY
    W, H = w * SS, h * SS
    seed = 2200 + manifest.THEMES.index(theme) * 31

    xs = (np.arange(W) + 0.5) / W
    ys = (np.arange(H) + 0.5) / H

    rgb = np.repeat(shaft_rgb(pal, xs)[None, :, :], H, axis=0)
    rgb *= _band_mul(xs, ys, float(h))[:, :, None]

    # broad mottling, then pitting that only collects on the shadow side
    mottle = artlib.value_noise(W, H, 3, seed)
    rgb *= (1.0 + (mottle - 0.5) * 0.10)[:, :, None]
    pits = artlib.value_noise(W, H, 12, seed + 1)
    shadow_side = np.clip((xs - 0.46) / 0.40, 0.0, 1.0)
    rgb *= (1.0 - np.clip(pits - 0.60, 0, 1) * 0.80 * shadow_side[None, :])[:, :, None]

    # fine vertical striation (constant in y, so tiling is untouched)
    stria = artlib.value_noise(W, 8, 15, seed + 2)[0]
    rgb *= (1.0 + (stria - 0.5) * 0.085)[None, :, None]

    img = _to_img(_punch(rgb), np.full((H, W), 255.0))
    out = resolve_tiled_y(img, w, h)
    return artlib.grain(out, strength=0.05, seed=seed + 3, cells=6)


# -- cap -------------------------------------------------------------------
# One bold flared crown over a dark collar.  An earlier two-tier version turned
# to mush at the size this is actually drawn (about 151 x 74 world units), so
# the shape language here is deliberately simple: a big slab, a hard shadow
# line against the shaft, and the glowing inlay near the gap edge doing the
# heavy work.
#
# Everything is measured as a *depth* from the gap-facing lip, so one table
# describes both the lower pillar's cap (depth running down the sprite) and the
# upper pillar's (depth running up it).  Depths are in the pixels the shape was
# drawn in and are rescaled by the manifest's height; half-widths are in shaft
# half-widths, which makes the cap's shaft section exactly the body's width at
# any resolution.
CAP_REF_H = 132.0

CAP_HW = (
    (4.0, 1.1818),     # the crown: a bold flared slab
    (74.0, 1.0636),
    (74.9, 1.0000),    # hard ledge onto the collar
    (96.0, 0.9636),    # the collar, cut in from the shaft
    (108.0, 1.0000),   # flare back out to the shaft
    (140.0, 1.0000),
)

# Vertical value ramps.  The light is from the upper left in *screen* space, so
# the two orientations cannot share one ramp: in the lower cap the crown's top
# lip faces the light and the collar sits in the crown's cast shadow, while in
# the upper cap the crown's underside is the shaded face and the lit surface is
# the annulus on top of the crown, with the collar shaded by the shaft above.
CAP_V_DOWN = (
    (0.0, 1.38), (8.0, 1.34), (12.5, 1.22),     # the top lip catches the light
    (36.0, 1.13), (58.0, 1.03),
    (61.5, 1.12), (66.0, 1.10),                 # bottom chamfer of the crown
    (69.5, 0.95), (73.9, 0.88),
    (74.9, 0.42), (79.0, 0.52), (90.0, 0.62),   # the crown's cast shadow
    (95.4, 0.64), (96.4, 0.86),                 # the shaft's flare faces up into the light
    (99.0, 0.80), (104.0, 0.70),
    (112.0, 0.83), (122.0, 0.96), (132.0, 1.00),
)

CAP_V_UP = (
    (0.0, 0.62), (5.0, 0.66), (11.0, 0.80),     # the lip now faces down: a narrow shaded band
    (18.0, 0.98), (36.0, 1.03), (58.0, 1.07),   # the crown's front face
    (61.5, 1.15), (66.0, 1.21),                 # the crown's chamfer, now facing up
    (69.5, 1.32), (73.9, 1.40),                 # the annulus on top of the crown
    (74.9, 0.98), (79.0, 0.88), (90.0, 0.74),   # the collar, shaded by the shaft above
    (95.4, 0.66), (96.4, 0.62),
    (99.0, 0.62), (104.0, 0.68),
    (112.0, 0.84), (122.0, 0.96), (132.0, 1.00),
)

CAP_TOP = 4.0                       # depth at which the silhouette starts
CAP_CORNER = 12.0                   # radius of the two lip corners
BAR_CY, BAR_RY = 23.0, 4.8          # the glowing inlay strip
CH_RY = 8.2                         # the channel it is recessed into
INLAY_INSET = 0.1818                # in shaft half-widths


def _cap_metrics():
    """Cap geometry resolved against the manifest's current source size."""
    w, h = manifest.PILLAR_CAP
    k = h / CAP_REF_H
    shaft_hw = manifest.PILLAR_BODY[0] / 2.0
    hw = tuple((d * k, f * shaft_hw) for d, f in CAP_HW)
    return w, h, k, shaft_hw, hw


def _cap_fields(up: bool):
    """Depth, half-width, silhouette coverage and local-u fields, supersampled."""
    w, h, k, shaft_hw, hw_tab = _cap_metrics()
    xx, yy, W, H = _grid(w, h)
    cx = w / 2.0
    dd = (h - yy) if up else yy                      # depth from the gap-facing lip
    dcol = dd[:, 0]

    hw = _ramp(dcol, hw_tab)
    r = CAP_CORNER * k                               # round the two lip corners
    dy = np.clip(dcol - CAP_TOP * k, 0.0, r)
    hw = np.where(dcol < CAP_TOP * k + r,
                  hw - r + np.sqrt(np.maximum(r * r - (r - dy) ** 2, 0.0)), hw)
    hw = hw[:, None]

    dx = np.abs(xx - cx)
    aa = 0.34                                        # ~1.4 supersampled pixels
    cov = (np.clip((hw - dx) / aa + 0.5, 0.0, 1.0)
           * np.clip((dd - CAP_TOP * k) / aa + 0.5, 0.0, 1.0))   # far end runs off canvas

    # Shading coordinate.  Measured from whichever silhouette edge is nearer and
    # scaled by the *shaft* width, so the facet edges, the rim light and the
    # flutes all line up with the body no matter how far the cap flares.
    shaft = manifest.PILLAR_BODY[0]
    dl, dr = xx - (cx - hw), (cx + hw) - xx
    wgt = np.clip(dl / np.maximum(dl + dr, 1e-6), 0.0, 1.0)
    u = (dl / shaft) * (1.0 - wgt) + (1.0 - dr / shaft) * wgt
    return xx, yy, dd, hw, cov, np.clip(u, 0.0, 1.0), cx, (w, h, k, shaft_hw, hw_tab)


def build_pillar_cap(theme: str, up: bool = False) -> Image.Image:
    """The capital.  `up` authors it for the upper pillar (the gap below it)."""
    pal = manifest.THEME_PALETTE[theme]
    seed = 5100 + manifest.THEMES.index(theme) * 17 + (7 if up else 0)
    accent = artlib.rgba(pal["accent"])
    p = artlib.rgba(pal["pillar"])
    white = (255, 255, 255, 255)

    xx, yy, dd, hw, cov, u, cx, (w, h, k, shaft_hw, hw_tab) = _cap_fields(up)
    W, H = w * SS, h * SS

    # the flutes run up out of the shaft and die under the crown
    flute = np.clip((dd - 80.0 * k) / (14.0 * k), 0.0, 1.0)
    rgb = shaft_rgb(pal, u, flute)
    rgb *= _ramp(dd[:, 0], _scale_profile(CAP_V_UP if up else CAP_V_DOWN, k))[:, None, None]

    # Recessed inlay channel.  Its walls are shaded in *screen* space, so the
    # groove reads the same way in both orientations.
    half = float(_ramp(np.array([BAR_CY * k]), hw_tab)[0]) - INLAY_INSET * shaft_hw
    d_ch = _capsule(xx, dd, cx - half, cx + half, BAR_CY * k)
    ch = np.clip((CH_RY * k - d_ch) / 0.45, 0.0, 1.0)
    rgb *= (1.0 - 0.40 * ch)[:, :, None]
    step = max(1, int(round(2.0 * k * SS)))
    upper = np.clip(_shift(ch, 0, step) - ch, 0, 1)
    lower = np.clip(_shift(ch, 0, -step) - ch, 0, 1)
    rgb *= (1.0 - 0.34 * upper)[:, :, None]
    rgb *= (1.0 + 0.30 * lower)[:, :, None]

    # dark contact edge down the sides, so the silhouette never dissolves
    edge = np.clip(1.0 - (hw - np.abs(xx - cx)) / (2.6 * k), 0.0, 1.0)
    edge = np.where(dd > (CAP_TOP + 3.0) * k, edge, edge * 0.20)
    rgb *= (1.0 - 0.46 * edge ** 1.25)[:, :, None]

    mottle = artlib.value_noise(W, H, 5, seed)
    rgb *= (1.0 + (mottle - 0.5) * 0.09)[:, :, None]

    rgb = _punch(rgb) * cov[:, :, None]
    alpha = cov * 255.0

    # --- emissive pass: this is what marks the mouth of the gap ------------
    bar = np.clip((BAR_RY * k - d_ch) / 0.42, 0.0, 1.0) * cov
    bar_core = np.clip(((BAR_RY - 2.3) * k - d_ch) / 0.42, 0.0, 1.0) * cov
    rim = (np.clip((dd - CAP_TOP * k) / 0.6, 0.0, 1.0)
           * np.clip(((CAP_TOP + 3.4) * k - dd) / (1.2 * k), 0.0, 1.0)) * cov

    glow_rgb = np.zeros((H, W, 3))
    glow_a = np.zeros((H, W))
    for mask, col, core, halo, hstr in (
        (bar, accent, 1.00, 6.0 * k * SS, 0.38),
        (bar_core, artlib.mix(accent, white, 0.72), 0.80, 1.6 * k * SS, 0.30),
        (rim, artlib.mix(accent, white, 0.60), 0.90, 3.0 * k * SS, 0.26),
    ):
        amount = np.clip(mask * core + _blur(mask * 255.0, halo) / 255.0 * hstr, 0.0, 1.0)
        glow_rgb += amount[:, :, None] * np.array(artlib.rgba(col)[:3], float)[None, None, :]
        glow_a += amount * 255.0

    rgb = rgb * np.clip(1.0 - glow_a / 255.0, 0.0, 1.0)[:, :, None] + glow_rgb
    alpha = np.clip(alpha + glow_a, 0, 255)

    img = _to_img(rgb, alpha)

    # a whisper of darkness hugging the outside, so the cap separates from any sky
    halo = _blur(cov * 255.0, 3.0 * k * SS) * 0.34
    dark = _to_img(np.zeros((H, W, 3)) + np.array(artlib.shade(p, -0.84)[:3], float),
                   np.minimum(halo, 255.0))
    img = Image.alpha_composite(dark, img)

    out = resolve_rgba(img, w, h)
    out = artlib.grain(out, strength=0.04, seed=seed + 2, cells=5)
    return _alpha_floor(out, 3)


# ==========================================================================
# THE FEATHER GLYPH
# ==========================================================================
# One definition serves the coin's struck emblem and the fx_feather particle.
#
# Every feather cue lives in the *silhouette*: a bare calamus at the base, a
# vane that is widest just past halfway and noticeably wider on one side than
# the other, notches combed out of the narrow edge, and a tip cut back on the
# wide side so it forks instead of coming to a spear point.  An earlier version
# put the cues in interior hatching instead; hatching has almost no tonal mass
# at the size either consumer is drawn, so all that survived was a bright
# kinked line -- a lightning bolt, not a quill.

VANE_H, STEM_H, QUILL_H = 150, 210, 255     # height codes in the glyph's map

# cubic spine; the inflection is what separates a feather from a leaf
SPINE = ((-0.22, 0.64), (-0.06, 0.34), (-0.06, -0.10), (0.26, -0.60))
VANE_T0 = 0.10          # where the vane leaves the bare quill
VANE_MAX = 0.300        # peak half-width
VANE_WIDE = 1.16        # the outer (notched) side
VANE_NARROW = 0.80
TIP_SPLIT = 0.93        # the narrow side stops here; the wide side runs to 1.0
CALAMUS = -0.32         # how far the bare quill runs past the vane
QUILL_W = 0.052
# (centre in t, half extent in t, depth as a fraction of the local half-width)
NOTCHES = ((0.34, 0.085, 0.26), (0.51, 0.085, 0.28),
           (0.67, 0.080, 0.30), (0.82, 0.070, 0.28))
NOTCH_LEAD, NOTCH_FALL = 1.80, 0.70     # each tooth left raked toward the tip


def _bez(t: float):
    it = 1.0 - t
    return tuple(it ** 3 * SPINE[0][i] + 3 * it * it * t * SPINE[1][i]
                 + 3 * it * t * t * SPINE[2][i] + t ** 3 * SPINE[3][i] for i in (0, 1))


def _bez_tangent(t: float):
    it = 1.0 - t
    tx, ty = (3 * it * it * (SPINE[1][i] - SPINE[0][i])
              + 6 * it * t * (SPINE[2][i] - SPINE[1][i])
              + 3 * t * t * (SPINE[3][i] - SPINE[2][i]) for i in (0, 1))
    m = math.hypot(tx, ty) or 1.0
    return tx / m, ty / m


def _vane_half(ts: np.ndarray) -> np.ndarray:
    """Half-width of the vane, peaking a little past the middle of the shaft."""
    x = np.clip((ts - VANE_T0) / (1.0 - VANE_T0), 0.0, 1.0)
    return np.sin(np.pi * x ** 0.92) ** 0.9 * VANE_MAX


def _notch_mul(ts: np.ndarray) -> np.ndarray:
    """Comb the barb separations out of one edge of the vane."""
    m = np.ones_like(ts)
    for tc, half, depth in NOTCHES:
        x = (ts - tc) / half
        cut = np.where(x < 0.0,
                       np.clip(1.0 + x, 0.0, 1.0) ** NOTCH_LEAD,
                       np.clip(1.0 - x, 0.0, 1.0) ** NOTCH_FALL)
        m -= depth * cut
    return np.clip(m, 0.12, 1.0)


def _feather_height(n: int, spread: float = 1.0, fit: str = "circle") -> np.ndarray:
    """A stylised quill, returned as an (n, n) height map in 0..1.

    Heights are codes, not shading: `VANE_H` for the vane, `STEM_H` for the
    bare calamus and `QUILL_H` for the rachis where it overlies the vane, so a
    caller can address the parts separately.

    `fit` decides how the drawing is placed in the canvas.  "circle" scales it
    to sit inside a circle of radius `spread`, which is what the coin needs so
    the emblem cannot spill past the struck ring.  "box" rotates the glyph to
    its own long axis and fills `spread` of the canvas with it, which is what a
    particle needs: the renderer blits a square, so a glyph normalised to a
    circle would emit 40 world units of mostly empty alpha.
    """
    ts_r = np.linspace(0.0, 1.0, 220)
    ts_l = np.linspace(0.0, TIP_SPLIT, 220)
    half_r = _vane_half(ts_r) * VANE_WIDE * _notch_mul(ts_r)
    half_l = _vane_half(ts_l) * VANE_NARROW

    def edge(ts, widths, side):
        out = []
        for t, wdt in zip(ts, widths):
            sx, sy = _bez(float(t))
            tx, ty = _bez_tangent(float(t))
            nx, ny = -ty, tx
            out.append((sx + nx * wdt * side, sy + ny * wdt * side))
        return out

    vane = edge(ts_r, half_r, 1.0) + edge(ts_l, half_l, -1.0)[::-1]

    # rachis: proud of the vane, running out past the base as the calamus
    rl, rr = [], []
    for t in np.linspace(CALAMUS, 0.975, 110):
        tc = min(max(float(t), 0.0), 1.0)
        sx, sy = _bez(tc)
        tx, ty = _bez_tangent(tc)
        if t < 0.0:
            sx, sy = sx + tx * float(t) * 0.95, sy + ty * float(t) * 0.95
        nx, ny = -ty, tx
        wq = QUILL_W * (1.0 - 0.68 * max(float(t), 0.0) ** 0.9)
        if t < 0.0:
            wq *= 1.0 - 0.42 * min(float(t) / CALAMUS, 1.0) ** 1.6
        rl.append((sx + nx * wq, sy + ny * wq))
        rr.append((sx - nx * wq, sy - ny * wq))
    rachis = rl + rr[::-1]

    everything = vane + rachis
    if fit == "box":
        base, tip = _bez(0.0), _bez(1.0)
        ang = -math.atan2(tip[1] - base[1], tip[0] - base[0])
        ca, sa = math.cos(ang), math.sin(ang)

        def rot(q):
            return (q[0] * ca - q[1] * sa, q[0] * sa + q[1] * ca)

        rx = [rot(q)[0] for q in everything]
        ry = [rot(q)[1] for q in everything]
        ox, oy = (min(rx) + max(rx)) * 0.5, (min(ry) + max(ry)) * 0.5
        k = n * spread / max(max(rx) - min(rx), max(ry) - min(ry))

        def px(q):
            q = rot(q)
            return (n * 0.5 + (q[0] - ox) * k, n * 0.5 + (q[1] - oy) * k)
    else:
        k = n * 0.5 * spread / max(math.hypot(*q) for q in everything)

        def px(q):
            return (n * 0.5 + q[0] * k, n * 0.5 + q[1] * k)

    img = Image.new("L", (n, n), 0)
    ImageDraw.Draw(img).polygon([px(q) for q in vane], fill=VANE_H)
    quill = Image.new("L", (n, n), 0)
    ImageDraw.Draw(quill).polygon([px(q) for q in rachis], fill=255)

    arr = np.asarray(img).astype(np.int16)
    qa = np.asarray(quill) > 127
    arr = np.where(qa, np.where(arr > 0, QUILL_H, STEM_H), arr)

    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L")
    return np.asarray(out.filter(ImageFilter.GaussianBlur(n / 360.0))).astype(np.float64) / 255.0


_FEATHER_CACHE: dict[tuple[int, float, str], np.ndarray] = {}


def _feather(n: int, spread: float = 1.0, fit: str = "circle") -> np.ndarray:
    key = (n, spread, fit)
    if key not in _FEATHER_CACHE:
        _FEATHER_CACHE[key] = _feather_height(n, spread, fit)
    return _FEATHER_CACHE[key]


def _rachis_mask(hm: np.ndarray) -> np.ndarray:
    """The whole quill line, calamus included."""
    return np.clip((hm - (STEM_H - 24) / 255.0) / 0.08, 0.0, 1.0)


def _overvane_mask(hm: np.ndarray) -> np.ndarray:
    """Only the stretch of quill that runs across the vane."""
    return np.clip((hm - (QUILL_H - 28) / 255.0) / 0.08, 0.0, 1.0)


# ==========================================================================
# COIN
# ==========================================================================
# A struck token spinning about its vertical axis.  The sample angle is offset
# by half a step so no frame lands exactly edge-on: at 13 fps a frame that is
# 8 world units wide against frame 0's 56 reads as a blink in the middle of
# every spin, which is a readability cost on a pickup the player has to spot
# while threading a gap.  The cycle stays symmetric and still loops.

COIN_REF = 112.0
COIN_R_F = 45.0 / COIN_REF
COIN_T_F = 10.0 / COIN_REF
GOLD = (
    (0.00, (74, 36, 8)),
    (0.18, (136, 70, 16)),
    (0.38, (196, 122, 28)),
    (0.58, (232, 168, 50)),
    (0.78, (250, 204, 96)),
    (0.92, (255, 228, 152)),
    (1.00, (255, 244, 206)),
)
GOLD_SPEC = np.array([255.0, 251.0, 232.0])
GOLD_EDGE = np.array([74.0, 36.0, 8.0])

# struck relief across the face: field, ring recess, raised rim, outer bevel
COIN_RELIEF = ((0.00, 0.50), (0.52, 0.52), (0.620, 0.50),
               (0.655, 0.26), (0.700, 0.25),
               (0.735, 0.80), (0.868, 0.82), (0.905, 0.66),
               (0.948, 0.38), (1.00, 0.14))
COIN_RING = ((0.00, 0.0), (0.705, 0.0), (0.745, 1.0), (0.875, 1.0), (0.912, 0.0), (1.00, 0.0))
# The emblem's own field.  The fade has to begin *outside* the circle the glyph
# is normalised into, or it eats the two parts that carry the read -- the tip
# and the bare quill, which are exactly the points furthest from the centre.
GLYPH_SPREAD = 0.645
GLYPH_FADE = (0.700, 0.045)


def build_coin(index: int) -> Image.Image:
    w, h = manifest.COIN_SIZE
    kc = w / COIN_REF
    xx, yy, W, H = _grid(w, h)
    cx, cy = w / 2.0, h / 2.0 - 1.0 * kc
    R, T = w * COIN_R_F, w * COIN_T_F

    theta = math.pi * (index + 0.5) / manifest.COIN_FRAMES
    ct, st = math.cos(theta), math.sin(theta)
    facing = 1.0 if ct >= 0 else -1.0          # which face points at us
    rx = R * abs(ct)
    face_cx = cx + (T * 0.5) * st * facing
    aa = 0.30

    dy = (yy - cy) / R
    k = np.sqrt(np.clip(1.0 - dy * dy, 0.0, 1.0))
    vcov = np.clip((R - np.abs(yy - cy)) / aa + 0.5, 0.0, 1.0)

    hull_hw = rx * k + (T * 0.5) * st
    hull = np.clip((hull_hw - np.abs(xx - cx)) / aa + 0.5, 0.0, 1.0) * vcov
    face = np.clip((rx * k - np.abs(xx - face_cx)) / aa + 0.5, 0.0, 1.0) * vcov
    rim = np.clip(hull - face, 0.0, 1.0)

    # ---- face ------------------------------------------------------------
    us = (xx - face_cx) / max(rx, 1e-6)         # screen-space, across the ellipse
    uu = us.copy()
    rr = np.sqrt(np.clip(uu * uu + dy * dy, 0.0, 4.0))
    if facing < 0:
        uu = -uu                                # we are looking at the far side
    tilt = uu * 0.60 + dy * 0.80                # along the light, upper-left to lower-right

    # a flat disc takes one diffuse value, and it swings as the coin turns
    nrm = np.array([st * facing, 0.0, ct * facing])
    lightv = np.array([-0.46, -0.34, 0.82])
    lightv /= np.linalg.norm(lightv)
    level = 0.82 + 0.30 * max(float(np.dot(nrm, lightv)), 0.0)

    lum = _ramp(rr, COIN_RELIEF)
    lum -= tilt * (0.15 + 0.26 * _ramp(rr, COIN_RING))

    # Struck feather, squashed by the same perspective as the face.  It is a
    # single flat tonal step rather than an emboss: a bevel on a mark this thin
    # resolves into a bright line plus its own shadow, which at a glance is a
    # bolt struck on a gold disc.  Sinking the vane into the field keeps the
    # silhouette doing the work at every size the game draws the coin.
    gn = int(2 * R * SS)
    glyph = np.zeros((H, W))
    gw = int(round(2 * rx * SS))
    if gw >= 2:
        hm = _feather(gn, GLYPH_SPREAD)
        gi = Image.fromarray((hm * 255).astype(np.uint8), "L")
        if facing < 0:
            gi = gi.transpose(Image.FLIP_LEFT_RIGHT)
        pane = Image.new("L", (W, H), 0)
        pane.paste(gi.resize((gw, gn), Image.LANCZOS),
                   (int(round((face_cx - rx) * SS)), int(round((cy - R) * SS))))
        glyph = np.asarray(pane).astype(np.float64) / 255.0
        glyph *= np.clip((GLYPH_FADE[0] - rr) / GLYPH_FADE[1], 0.0, 1.0)
    rach = _rachis_mask(glyph)
    lum -= 0.20 * np.clip((glyph - 0.22) / 0.18, 0.0, 1.0)
    lum += 0.17 * rach                          # the quill stands proud of the vane

    base = _interp_stops(GOLD, np.clip(lum * level + 0.06, 0.0, 1.0))

    # Specular sweep.  It is placed in screen space and driven by cos(2*theta),
    # so it swings out to one side, back across as the far face comes round, and
    # arrives where it started -- frame 7 hands off to frame 0 without a jump.
    uspec = -0.76 * math.cos(2.0 * theta)
    sl = 0.42
    perp = (us - uspec) * math.cos(sl) - dy * math.sin(sl)
    spec = np.exp(-(perp / 0.24) ** 2) * np.clip(1.0 - (rr / 1.02) ** 4, 0.0, 1.0)
    spec *= 0.72 * (0.26 + 0.74 * abs(ct))
    base = base + spec[:, :, None] * (GOLD_SPEC - base) * 0.82

    # ---- milled rim ------------------------------------------------------
    phi = np.arcsin(np.clip(dy, -1.0, 1.0))
    mill = 0.5 + 0.5 * np.cos(phi * 27.0)
    rim_lum = np.clip(0.42 + 0.40 * k - 0.10 * dy, 0.0, 1.2) * (0.72 + 0.56 * mill)
    rim_lum += 0.46 * np.exp(-((dy + 0.20) / 0.40) ** 2) * (0.40 + 0.60 * abs(st))
    rim_rgb = _interp_stops(GOLD, np.clip(rim_lum * 0.82, 0.0, 1.0))

    cover = np.clip(face + rim, 0.0, 1.0)
    rgb = base * face[:, :, None] + rim_rgb * rim[:, :, None]
    rgb = np.where(cover[:, :, None] > 1e-4, rgb / np.maximum(cover, 1e-4)[:, :, None], 0.0)

    # dark edge all the way round, so gold never bleeds into a bright sky
    lip = np.maximum(np.clip(1.0 - (hull_hw - np.abs(xx - cx)) / (2.4 * kc), 0.0, 1.0),
                     np.clip(1.0 - (R - np.abs(yy - cy)) / (2.4 * kc), 0.0, 1.0)) ** 1.3 * hull
    rgb = rgb * (1.0 - 0.66 * lip)[:, :, None] + GOLD_EDGE[None, None, :] * (0.66 * lip)[:, :, None]

    img = _to_img(_punch(rgb, 1.06), hull * 255.0)

    # soft contact shadow underneath; it also stops the token vanishing on pale sky
    sh = np.clip(_shift(_blur(hull * 255.0, 2.4 * kc * SS), 0, int(2.0 * kc * SS)) * 0.36, 0, 255)
    shadow = _to_img(np.zeros((H, W, 3)) + np.array([40.0, 22.0, 8.0]), sh)
    img = Image.alpha_composite(shadow, img)

    out = resolve_rgba(img, w, h)
    out = artlib.grain(out, strength=0.03, seed=900 + index, cells=7)
    return _alpha_floor(out, 3)


# ==========================================================================
# PARTICLES
# ==========================================================================
# Pure luminance: RGB is white everywhere, every shape lives in alpha, so the
# game can multiply in whatever colour the moment calls for.

def _white(alpha_ss: np.ndarray, w: int, h: int) -> Image.Image:
    """Downsample an alpha field and wrap it in an all-white RGBA image.

    The field is renormalised so every particle peaks at full opacity; the game
    fades them with its own alpha, so the texture should arrive at full range.
    """
    a = Image.fromarray(np.clip(alpha_ss, 0, 255).astype(np.uint8), "L").resize((w, h), Image.LANCZOS)
    arr = np.asarray(a).astype(np.float64)
    peak = arr.max()
    if peak > 0:
        arr = arr * (255.0 / peak)
    arr[arr <= 2] = 0.0
    return _to_img(np.full((h, w, 3), 255.0), arr)


def fx_soft(w, h):
    xx, yy, W, H = _grid(w, h)
    t = np.clip(1.0 - np.hypot(xx - w / 2, yy - h / 2) / (w * 0.5), 0.0, 1.0)
    return _white((t * t * (3 - 2 * t)) ** 1.5 * 255.0, w, h)


def fx_spark(w, h):
    xx, yy, W, H = _grid(w, h)
    dx, dy = xx - w / 2, yy - h / 2
    r = np.hypot(dx, dy)
    core = np.clip(1.0 - r / (w * 0.165), 0.0, 1.0) ** 0.40
    halo = np.exp(-(r / (w * 0.205)) ** 2) * 0.90
    arm_h = np.exp(-(dy / (w * 0.040)) ** 2) * np.clip(1.0 - np.abs(dx) / (w * 0.50), 0, 1) ** 1.25
    arm_v = np.exp(-(dx / (w * 0.040)) ** 2) * np.clip(1.0 - np.abs(dy) / (w * 0.50), 0, 1) ** 1.25
    return _white(np.clip(core + halo + 1.0 * (arm_h + arm_v), 0.0, 1.0) * 255.0, w, h)


def fx_star(w, h):
    img = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(img)
    ro, ri = w * 0.435, w * 0.183
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = ro if i % 2 == 0 else ri
        pts.append((artlib.s(w / 2 + rad * math.cos(ang)), artlib.s(h / 2 + rad * math.sin(ang))))
    d.polygon(pts, fill=255)
    core = np.asarray(img).astype(np.float64)
    halo = _blur(core, w * 0.085 * SS) * 0.60 + _blur(core, w * 0.028 * SS) * 0.45
    return _white(np.clip(core + halo, 0, 255), w, h)


def fx_feather(w, h):
    """The death burst's feather, and the brand element it shares with the coin.

    Two rules decide whether this reads at the 40 world units it is emitted at.
    One silhouette value, not a translucent vane behind an opaque spine -- the
    two-tier version washed out against a bright sky and left only the rachis,
    which is a knife blade.  And the ink has to fill the canvas: the renderer
    blits a square, so a glyph normalised to a circle spends most of its size
    on empty alpha.
    """
    n = w * SS
    hm = _feather(n, 0.94, fit="box")
    a = np.clip((hm - 0.02) / 0.30, 0.0, 1.0)
    a = _blur(a * 255.0, w * 0.007 * SS) / 255.0
    # the quill reads as a crease in the plume rather than a bright spine, so
    # every part of the feather carries the same weight
    crease = _blur(_overvane_mask(hm) * 255.0, w * 0.005 * SS) / 255.0
    return _white(np.clip(a - 0.26 * crease, 0.0, 1.0) * 255.0, w, h)


def fx_ring(w, h):
    xx, yy, W, H = _grid(w, h)
    r = np.hypot(xx - w / 2, yy - h / 2) / (w * 0.5)
    r0, r1 = 0.845, 0.975
    inner = np.clip((r - r0) / 0.009, 0.0, 1.0)              # crisp bright inner edge
    hold = np.clip(1.0 - (r - r0 - 0.016) / (r1 - r0 - 0.016), 0.0, 1.0) ** 2.0
    return _white(np.clip(inner * hold, 0, 1) * 255.0, w, h)


def fx_dust(w, h):
    xx, yy, W, H = _grid(w, h)
    rng = np.random.default_rng(5)
    a = np.zeros((H, W))
    for _ in range(6):
        ang = rng.random() * math.tau
        dist = rng.random() ** 0.7 * w * 0.11
        bx, by = w / 2 + math.cos(ang) * dist, h / 2 + math.sin(ang) * dist
        rad = w * (0.34 + rng.random() * 0.10)
        t = np.clip(1.0 - np.hypot(xx - bx, yy - by) / rad, 0.0, 1.0)
        a = np.maximum(a, t * t * (3 - 2 * t))
    n = artlib.value_noise(W, H, 2, 31, octaves=4)
    a *= 0.78 + 0.44 * n
    a *= np.clip(1.0 - np.hypot(xx - w / 2, yy - h / 2) / (w * 0.5), 0.0, 1.0) ** 0.35
    return _white(np.clip(a / a.max(), 0, 1) * 255.0, w, h)


PARTICLE_BUILDERS = {
    "soft": fx_soft, "spark": fx_spark, "star": fx_star,
    "feather": fx_feather, "ring": fx_ring, "dust": fx_dust,
}


# ==========================================================================
# previews
# ==========================================================================

def _sky(theme: str, w: int, h: int) -> Image.Image:
    return artlib.vertical_gradient(w, h, manifest.THEME_PALETTE[theme]["sky"]).convert("RGBA")


def _scaled(img: Image.Image, w: int) -> Image.Image:
    return img.resize((w, max(1, round(img.height * w / img.width))), Image.LANCZOS)


def _device_resize(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Resample the way the game does, so a preview cannot flatter the art.

    `assets.scaled` goes through `pygame.transform.smoothscale`, which is
    bilinear when it enlarges -- and on every current phone this family *is*
    enlarged.  Lanczos here would invent sharpness the device never sees.
    """
    if size == img.size:
        return img
    grow = size[0] > img.width or size[1] > img.height
    return img.resize(size, Image.BILINEAR if grow else Image.LANCZOS)


# the reference obstacle the previews compose, in world units
GAP_TOP, GAP_BOTTOM = 392.0, 636.0


def mock_frame(theme: str, scale: float = 1.0, world_w: float | None = None) -> Image.Image:
    """A gameplay frame composited exactly the way the game composites one.

    `scale` is device pixels per world unit -- the number `display.compute`
    produces, which is screen_h / 1080.  At scale 1 this is the 608 x 1080
    reference world; at 2400/1080 it is a 1080 x 2400 phone, and that is the
    density the art is really resampled to.  Everything below mirrors
    `PillarPair.draw` and `Coin.draw`, including the fact that the upper shaft
    is tiled unflipped and the two caps are two different sprites.
    """
    ww = manifest.WORLD_W_REF if world_w is None else world_w
    pw, ph = int(round(ww * scale)), int(round(manifest.WORLD_H * scale))
    scene = _sky(theme, pw, ph)

    bw, bh = manifest.PILLAR_BODY
    cw, ch = manifest.PILLAR_CAP
    tile_h = PILLAR_DRAW_W * bh / bw
    cap_w = PILLAR_DRAW_W * cw / bw
    cap_h = cap_w * ch / cw
    x = ww * 0.5 - PILLAR_DRAW_W * 0.5
    cap_x = x - (cap_w - PILLAR_DRAW_W) * 0.5

    body = Image.open(manifest.image_path(f"pillar_body_{theme}.png")).convert("RGBA")
    cap_lo = Image.open(manifest.image_path(f"pillar_cap_{theme}.png")).convert("RGBA")
    cap_up = Image.open(manifest.image_path(f"pillar_cap_up_{theme}.png")).convert("RGBA")

    tile = _device_resize(body, (max(1, round(PILLAR_DRAW_W * scale)),
                                 max(1, round(tile_h * scale))))
    cap_px = (max(1, round(cap_w * scale)), max(1, round(cap_h * scale)))
    cap_lo = _device_resize(cap_lo, cap_px)
    cap_up = _device_resize(cap_up, cap_px)

    def shaft(top: float, bottom: float) -> None:
        sx = int(round(x * scale))
        y = top
        for _ in range(64):
            scene.alpha_composite(tile, (sx, int(round(y * scale))))
            y += tile_h
            if y >= bottom:
                break
        remainder = bottom - y
        if remainder > 0:
            clip = max(1, min(tile.height, int(round(remainder * scale))))
            scene.alpha_composite(tile.crop((0, 0, tile.width, clip)),
                                  (sx, int(round(y * scale))))

    shaft(-40.0, GAP_TOP - cap_h * 0.35)
    shaft(GAP_BOTTOM + cap_h * 0.35, manifest.WORLD_H + 40.0)
    scene.alpha_composite(cap_up, (int(round(cap_x * scale)), int(round((GAP_TOP - cap_h) * scale))))
    scene.alpha_composite(cap_lo, (int(round(cap_x * scale)), int(round(GAP_BOTTOM * scale))))

    coin = Image.open(manifest.image_path("coin_0.png")).convert("RGBA")
    cd = max(1, round(COIN_DRAW_W * scale))
    coin = _device_resize(coin, (cd, cd))
    ccx = int(round(ww * 0.5 * scale)) - cd // 2
    ccy = int(round((GAP_TOP + GAP_BOTTOM) * 0.5 * scale)) - cd // 2
    scene.alpha_composite(coin, (ccx, ccy))
    return scene


# a 20:9 phone: the common case, and the densest of the sizes measured
DEVICE = (1080, 2400)


def write_previews() -> list[str]:
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    out = []

    frames = [mock_frame(t) for t in manifest.THEMES]
    gw = frames[0].width
    sheet = Image.new("RGBA", (gw * len(frames), frames[0].height), (18, 18, 22, 255))
    for i, f in enumerate(frames):
        sheet.alpha_composite(f, (i * gw, 0))
    p = os.path.join(PREVIEW_DIR, "mock_gameplay.png")
    sheet.convert("RGB").save(p)
    out.append(p)

    # The same scene at the density it actually ships at.  Everything in this
    # family is magnified on a real phone, not minified, and a sheet drawn at
    # 0.79 device px per world unit hides that completely.
    dscale = DEVICE[1] / manifest.WORLD_H
    for theme in manifest.THEMES:
        p = os.path.join(PREVIEW_DIR, f"mock_device_{theme}.png")
        mock_frame(theme, dscale, DEVICE[0] / dscale).convert("RGB").save(p)
        out.append(p)

    for theme in ("dusk", "day"):
        p = os.path.join(PREVIEW_DIR, f"mock_{theme}.png")
        mock_frame(theme).convert("RGB").save(p)
        out.append(p)

    cw, chh = manifest.COIN_SIZE
    rows = ["day", "night", "dawn", "dusk"]
    strip = Image.new("RGBA", (cw * manifest.COIN_FRAMES, chh * len(rows)))
    for r, theme in enumerate(rows):
        band = _sky(theme, cw * manifest.COIN_FRAMES, chh * 6).crop(
            (0, chh * 3, cw * manifest.COIN_FRAMES, chh * 4))
        strip.alpha_composite(band, (0, r * chh))
        for i in range(manifest.COIN_FRAMES):
            c = Image.open(manifest.image_path(f"coin_{i}.png")).convert("RGBA")
            strip.alpha_composite(c, (i * cw, r * chh))
    p = os.path.join(PREVIEW_DIR, "coin_spin.png")
    strip.convert("RGB").save(p)
    out.append(p)

    small = max(8, round(COIN_DRAW_W * dscale))     # the real on-device size
    tiny = Image.new("RGBA", (small * manifest.COIN_FRAMES * 2, small + 8), (28, 34, 54, 255))
    for rep in range(2):
        for i in range(manifest.COIN_FRAMES):
            c = Image.open(manifest.image_path(f"coin_{i}.png")).convert("RGBA")
            tiny.alpha_composite(_device_resize(c, (small, small)),
                                 ((rep * manifest.COIN_FRAMES + i) * small, 4))
    p = os.path.join(PREVIEW_DIR, "coin_small.png")
    tiny.convert("RGB").save(p)
    out.append(p)

    cell = 150
    names = list(manifest.PARTICLES)
    sheet = Image.new("RGBA", (cell * len(names), cell * 3))
    tints = [((255, 255, 255), (16, 18, 28)), ((255, 198, 108), (16, 18, 28)),
             ((70, 44, 96), (226, 238, 246))]
    for r, (tint, bg) in enumerate(tints):
        sheet.paste(Image.new("RGBA", (cell * len(names), cell), bg + (255,)), (0, r * cell))
        for i, name in enumerate(names):
            im = Image.open(manifest.image_path(f"fx_{name}.png")).convert("RGBA")
            a = np.asarray(im).astype(np.float64)
            for c in range(3):
                a[:, :, c] *= tint[c] / 255.0
            im = Image.fromarray(a.round().astype(np.uint8), "RGBA")
            im = _scaled(im, min(cell - 16, im.width))
            sheet.alpha_composite(im, (i * cell + (cell - im.width) // 2,
                                       r * cell + (cell - im.height) // 2))
    p = os.path.join(PREVIEW_DIR, "particles.png")
    sheet.convert("RGB").save(p)
    out.append(p)

    # the feather at the size the death burst really emits it, on both extremes
    emit = max(8, round(40.0 * dscale))
    fs = Image.open(manifest.image_path("fx_feather.png")).convert("RGBA")
    band = Image.new("RGBA", (emit * 8, emit * 2 + 16), (0, 0, 0, 0))
    band.paste(Image.new("RGBA", (emit * 8, emit + 8), (18, 20, 30, 255)), (0, 0))
    band.paste(Image.new("RGBA", (emit * 8, emit + 8), (226, 238, 246, 255)), (0, emit + 8))
    for row, tint in enumerate(((255, 255, 255), (70, 44, 96))):
        a = np.asarray(fs).astype(np.float64)
        for c in range(3):
            a[:, :, c] *= tint[c] / 255.0
        ti = _device_resize(Image.fromarray(a.round().astype(np.uint8), "RGBA"), (emit, emit))
        for i in range(8):
            r = ti.rotate(i * 45, resample=Image.BICUBIC)
            band.alpha_composite(r, (i * emit, row * (emit + 8) + 4))
    p = os.path.join(PREVIEW_DIR, "feather_emit.png")
    band.convert("RGB").save(p)
    out.append(p)

    for theme in manifest.THEMES:
        arr = np.asarray(Image.open(manifest.image_path(f"pillar_body_{theme}.png")).convert("RGBA"))
        rolled = np.roll(arr, arr.shape[0] // 2, axis=0)
        p = os.path.join(PREVIEW_DIR, f"tile_{theme}.png")
        Image.fromarray(np.concatenate([rolled, rolled], 0), "RGBA").convert("RGB").save(p)
        out.append(p)

    bw, bh = manifest.PILLAR_BODY
    cw2, ch2 = manifest.PILLAR_CAP
    sheet = Image.new("RGBA", ((cw2 + 20) * 4, bh * 2 + ch2 * 2 + 40), (60, 62, 70, 255))
    for i, theme in enumerate(manifest.THEMES):
        body = Image.open(manifest.image_path(f"pillar_body_{theme}.png")).convert("RGBA")
        up = Image.open(manifest.image_path(f"pillar_cap_up_{theme}.png")).convert("RGBA")
        lo = Image.open(manifest.image_path(f"pillar_cap_{theme}.png")).convert("RGBA")
        x = i * (cw2 + 20) + 10
        sheet.alpha_composite(body, (x + (cw2 - bw) // 2, 10))
        sheet.alpha_composite(up, (x, 10 + bh))
        sheet.alpha_composite(lo, (x, 10 + bh + ch2 + 20))
        sheet.alpha_composite(body, (x + (cw2 - bw) // 2, 10 + bh + ch2 * 2 + 20))
    p = os.path.join(PREVIEW_DIR, "pillars_flat.png")
    sheet.convert("RGB").save(p)
    out.append(p)
    return out


# ==========================================================================
# verification
# ==========================================================================

SKY_CONTRAST_MIN = 20.0     # dE76 between the shaft's main face and the sky


def my_files() -> list[str]:
    return ([f"pillar_body_{t}.png" for t in manifest.THEMES]
            + [f"pillar_cap_{t}.png" for t in manifest.THEMES]
            + [f"pillar_cap_up_{t}.png" for t in manifest.THEMES]
            + [f"coin_{i}.png" for i in range(manifest.COIN_FRAMES)]
            + [f"fx_{n}.png" for n in manifest.PARTICLES])


def _sky_contrast(theme: str, samples: int = 10) -> tuple[float, float]:
    """Worst dE76 between the shaft's lit main face and the sky behind it.

    Sampled only over the band of screen the sky is actually visible in -- the
    bottom `GROUND_H` units are covered by the ground sprite -- because that is
    where losing the obstacle would cost the player a run.
    """
    a = np.asarray(Image.open(manifest.image_path(f"pillar_body_{theme}.png"))
                   .convert("RGB")).astype(np.float64)
    w = a.shape[1]
    face = a[:, int(FACE_U[0] * w):int(FACE_U[1] * w)].reshape(-1, 3).mean(0)
    sky = np.asarray(_sky(theme, 1, manifest.WORLD_H).convert("RGB")).astype(np.float64)[:, 0, :]
    visible = (manifest.WORLD_H - GROUND_DRAW_H) / manifest.WORLD_H
    where = np.linspace(0.0, visible, samples)
    rows = (where * (manifest.WORLD_H - 1)).astype(int)
    d = np.linalg.norm(srgb_to_lab(face) - srgb_to_lab(sky[rows]), axis=-1)
    return float(d.min()), float(where[int(np.argmin(d))])


def verify() -> list[str]:
    problems = []
    expected = manifest.expected_images()
    mine = my_files()
    # the shaft is a full-bleed tiling texture; everything else is a cut-out
    cutouts = set(mine) - {f"pillar_body_{t}.png" for t in manifest.THEMES}

    for name in mine:
        path = manifest.image_path(name)
        if not os.path.exists(path):
            problems.append(f"{name}: missing")
            continue
        if name not in expected:
            problems.append(f"{name}: not declared in manifest.expected_images()")
            continue
        im = Image.open(path)
        if im.mode != "RGBA":
            problems.append(f"{name}: mode {im.mode}, expected RGBA")
        if im.size != expected[name]:
            problems.append(f"{name}: size {im.size}, expected {expected[name]}")
        a = np.asarray(im.convert("RGBA")).astype(np.float64)
        al = a[:, :, 3]
        if al.max() < 255:
            problems.append(f"{name}: never reaches full opacity (max alpha {al.max():.0f})")
        if name in cutouts:
            corners = [al[0, 0], al[0, -1], al[-1, 0], al[-1, -1]]
            if max(corners) > 0:
                problems.append(f"{name}: corner alpha {corners}, expected 0")
            if al.min() > 0:
                problems.append(f"{name}: no transparent pixels at all")
        elif al.min() < 255:
            problems.append(f"{name}: tiling asset must be fully opaque")

        # premultiplied luminance variance catches blank art for colour sprites
        # and for the pure-white particles alike
        lum = (0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]) * al / 255.0
        if lum.std() < 6.0:
            problems.append(f"{name}: looks blank/uniform (premul luminance std {lum.std():.2f})")

        if name.startswith("fx_"):
            vis = al > 8
            if vis.any() and (a[:, :, :3][vis] < 250).any():
                problems.append(f"{name}: particle RGB is not pure white")
            if al.std() < 10:
                problems.append(f"{name}: particle alpha has no range")
        else:
            opaque = al > 200
            if opaque.sum() > 64:
                rgbstd = float(a[:, :, :3][opaque].std())
                if rgbstd < 8.0:
                    problems.append(f"{name}: flat fill (opaque RGB std {rgbstd:.2f})")

    for theme in manifest.THEMES:
        a = np.asarray(Image.open(manifest.image_path(f"pillar_body_{theme}.png"))
                       .convert("RGB")).astype(np.float64)
        rows = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
        seam = float(np.abs(a[0] - a[-1]).mean())
        typical, worst = float(np.median(rows)), float(np.percentile(rows, 97))
        if seam > max(worst, typical * 3.0 + 1.0):
            problems.append(f"pillar_body_{theme}: Y seam {seam:.2f} vs median row delta "
                            f"{typical:.2f} / p97 {worst:.2f}")

        # Playability, not taste: an obstacle the same colour as the sky behind
        # it is held together only by its 1.5%-wide contact edge.
        de, at = _sky_contrast(theme)
        if de < SKY_CONTRAST_MIN:
            problems.append(f"pillar_body_{theme}: shaft face is dE76 {de:.1f} from the sky at "
                            f"{at * 100:.0f}% screen height, needs {SKY_CONTRAST_MIN:.0f}")

    # the two cap orientations must stay one object: same silhouette, mirrored
    for theme in manifest.THEMES:
        lo = np.asarray(Image.open(manifest.image_path(f"pillar_cap_{theme}.png"))
                        .convert("RGBA")).astype(np.float64)[:, :, 3]
        up = np.asarray(Image.open(manifest.image_path(f"pillar_cap_up_{theme}.png"))
                        .convert("RGBA")).astype(np.float64)[:, :, 3]
        drift = float(np.abs(lo - up[::-1]).mean())
        if drift > 2.0:
            problems.append(f"pillar_cap_up_{theme}: silhouette differs from the lower cap "
                            f"(mean |dA| {drift:.2f})")
    return problems


# ==========================================================================

def main() -> int:
    manifest.ensure_dirs()
    count = 0

    for theme in manifest.THEMES:
        build_pillar_body(theme).save(manifest.image_path(f"pillar_body_{theme}.png"))
        build_pillar_cap(theme, up=False).save(manifest.image_path(f"pillar_cap_{theme}.png"))
        build_pillar_cap(theme, up=True).save(manifest.image_path(f"pillar_cap_up_{theme}.png"))
        count += 3
    for i in range(manifest.COIN_FRAMES):
        build_coin(i).save(manifest.image_path(f"coin_{i}.png"))
        count += 1
    for pname, size in manifest.PARTICLES.items():
        PARTICLE_BUILDERS[pname](*size).save(manifest.image_path(f"fx_{pname}.png"))
        count += 1

    print(f"wrote {count} files into {manifest.IMAGES}")
    for p in write_previews():
        print("preview:", p)

    problems = verify()
    if problems:
        print("\nFAILED CHECKS")
        for p in problems:
            print("  -", p)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
