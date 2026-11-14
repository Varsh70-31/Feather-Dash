#!/usr/bin/env python3
"""Google Play launcher and listing artwork for Feather Dash.

Everything here is computed from the primitives in ``artlib`` -- no sampled,
traced or downloaded imagery is involved, so the output is an original work the
project owns outright.

Outputs (into ``assets/branding/``):

    icon_1024.png       Play Store listing icon, full bleed, square
    adaptive_fg.png     Android adaptive icon foreground (inside the safe circle)
    adaptive_bg.png     Android adaptive icon background (full bleed, no content)
    icon_monochrome.png Android 13+ themed icon layer (white on transparency)
    presplash.png       launch screen shown while the runtime boots
    feature_graphic.png Play Store banner, 1024x500

Run with the project venv:

    .venv/bin/python tools/gen_brand.py
"""
from __future__ import annotations

import contextlib
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import artlib
import manifest
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT_DIR = os.path.join(manifest.ROOT, "assets", "branding")
FONT_PATH = os.path.join(manifest.FONTS, "BaiJamjuree-Bold.ttf")
LOGO_PATH = manifest.image_path("logo.png")

WORDMARK_TEXT = "FEATHER DASH"
TAGLINE_TEXT = "ONE TAP · ENDLESS SKY"

# --------------------------------------------------------------------------
# brand palette
# --------------------------------------------------------------------------
# The mark lives at the dusk end of the in-game palette: a deep indigo-violet
# field so the warm Ember bird is the one bright mass. Values were chosen for
# separation rather than prettiness -- the field sits at luma 24..100 and the
# lit side of the bird at 160..215, which still reads as one shape at 48 px.
FIELD_CORE = (196, 66, 120)   # rose heart of the radial field
FIELD_MID = (118, 46, 122)    # violet
FIELD_DEEP = (52, 30, 88)     # indigo
FIELD_EDGE = (16, 12, 44)     # near-black corner
WARM = (255, 164, 96)         # ember accent, matches the dusk theme accent
CREAM = (255, 240, 214)
INK = (28, 14, 32)            # warm black used for every shadow and outline

_EMBER = manifest.BIRD_SKINS[0]
BODY, BELLY, WING, BEAK = _EMBER[2], _EMBER[3], _EMBER[4], _EMBER[5]


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

@contextlib.contextmanager
def supersample(n: int):
    """Temporarily change artlib's supersampling factor."""
    old = artlib.SS
    artlib.SS = n
    try:
        yield
    finally:
        artlib.SS = old


def resize_rgba(img: Image.Image, size, resample=Image.LANCZOS) -> Image.Image:
    """Resize with premultiplied alpha.

    Straight-alpha resampling mixes the RGB of fully transparent pixels (which
    is black on an artlib canvas) into every antialiased edge, leaving a dark
    fringe once the sprite is composited. Premultiplying first and dividing the
    alpha back out afterwards removes it.
    """
    arr = np.asarray(img.convert("RGBA"), dtype=np.float32)
    a = arr[:, :, 3] / 255.0
    out = np.empty((size[1], size[0], 4), dtype=np.float32)
    ra = np.asarray(Image.fromarray(arr[:, :, 3]).resize(size, resample), dtype=np.float32)
    ra = np.clip(ra, 0.0, 255.0)
    denom = np.maximum(ra / 255.0, 1e-5)
    for c in range(3):
        pm = Image.fromarray(arr[:, :, c] * a).resize(size, resample)
        out[:, :, c] = np.clip(np.asarray(pm, dtype=np.float32) / denom, 0.0, 255.0)
    out[:, :, 3] = ra
    return Image.fromarray(np.rint(out).astype(np.uint8), "RGBA")


def resolve_rgba(img: Image.Image, w: int, h: int) -> Image.Image:
    return resize_rgba(img, (w, h))


class Frame:
    """Maps a 0..1 design space onto target-space pixels."""

    __slots__ = ("ox", "oy", "size")

    def __init__(self, ox: float, oy: float, size: float):
        self.ox, self.oy, self.size = float(ox), float(oy), float(size)

    def x(self, u: float) -> float:
        return self.ox + u * self.size

    def y(self, v: float) -> float:
        return self.oy + v * self.size

    def d(self, k: float) -> float:
        return k * self.size

    def p(self, u: float, v: float) -> tuple[float, float]:
        return (self.ox + u * self.size, self.oy + v * self.size)


def white(w: int, h: int) -> Image.Image:
    return artlib.canvas(w, h)


def mask_add(*layers: Image.Image) -> Image.Image:
    out = layers[0].copy()
    for layer in layers[1:]:
        out = Image.alpha_composite(out, layer)
    return out


def mask_sub(base: Image.Image, cut: Image.Image) -> Image.Image:
    out = base.copy()
    keep = ImageChops.invert(cut.getchannel("A"))
    out.putalpha(ImageChops.multiply(base.getchannel("A"), keep))
    return out


def dilate(img: Image.Image, px: float) -> Image.Image:
    """Approximate dilation by `px` target-space pixels (blur + steep curve)."""
    k = max(1.0, px * artlib.SS)
    m = img.getchannel("A").filter(ImageFilter.GaussianBlur(k * 0.62))
    m = m.point(lambda v: min(255, int(v * 7)))
    out = Image.new("RGBA", img.size, (255, 255, 255, 255))
    out.putalpha(m)
    return out


def tint(mask: Image.Image, color) -> Image.Image:
    out = Image.new("RGBA", mask.size, artlib.rgba(color, 255))
    out.putalpha(mask.getchannel("A"))
    return out


def fade(img: Image.Image, opacity: float) -> Image.Image:
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lambda v: int(v * opacity)))
    return out


def soft_mask_layer(mask: Image.Image, color, blur: float, opacity: float) -> Image.Image:
    """A blurred, tinted copy of `mask` -- used for belly/cheek/contact gradients."""
    a = mask.getchannel("A").filter(ImageFilter.GaussianBlur(blur * artlib.SS))
    layer = Image.new("RGBA", mask.size, artlib.rgba(color, 255))
    layer.putalpha(a.point(lambda v: int(v * opacity)))
    return layer


def directional_light(shape: Image.Image, dx: float, dy: float, blur: float,
                      color, opacity: float) -> Image.Image:
    """A light band on the edge of `shape` that faces the (-dx, -dy) direction."""
    m = shape.getchannel("A")
    shifted = ImageChops.offset(m, artlib.s(dx), artlib.s(dy))
    band = ImageChops.subtract(m, shifted)
    band = band.filter(ImageFilter.GaussianBlur(blur * artlib.SS))
    band = ImageChops.multiply(band, m)
    band = band.point(lambda v: min(255, int(v * 1.9 * opacity)))
    layer = Image.new("RGBA", shape.size, artlib.rgba(color, 255))
    layer.putalpha(band)
    return layer


def occlusion(caster: Image.Image, receiver: Image.Image, offset=(3, 5),
              blur: float = 7, opacity: float = 0.5, color=INK) -> Image.Image:
    sh = artlib.drop_shadow(caster, offset=offset, blur=blur, color=color, opacity=opacity)
    return artlib.clip_to(sh, receiver)


# --------------------------------------------------------------------------
# ribbons: tapered strokes used for feathers, tail quills and speed arcs
# --------------------------------------------------------------------------

def _profile(kind: str, t: float) -> float:
    if kind == "leaf":
        return math.sin(math.pi * t) ** 0.62
    if kind == "feather":
        return (math.sin(math.pi * t) ** 0.5) * (0.58 + 0.42 * (1.0 - t))
    if kind == "taper":
        return (1.0 - t) ** 0.75
    if kind == "comet":  # closes to a point at both ends, fattest just past half
        return 1.75 * (t ** 0.6) * ((1.0 - t) ** 0.55)
    return 1.0


def ribbon(img: Image.Image, spine, half_width: float, color, kind: str = "leaf") -> None:
    """Fill a tapered band along `spine` (target-space points)."""
    n = len(spine) - 1
    left, right = [], []
    for i, (px, py) in enumerate(spine):
        t = i / n
        if i == 0:
            tx, ty = spine[1][0] - px, spine[1][1] - py
        elif i == n:
            tx, ty = px - spine[-2][0], py - spine[-2][1]
        else:
            tx, ty = spine[i + 1][0] - spine[i - 1][0], spine[i + 1][1] - spine[i - 1][1]
        ln = math.hypot(tx, ty) or 1e-6
        nx, ny = -ty / ln, tx / ln
        hw = half_width * _profile(kind, t)
        left.append((px + nx * hw, py + ny * hw))
        right.append((px - nx * hw, py - ny * hw))
    artlib.poly(img, left + right[::-1], color)


def seg_spine(p0, p1, curve: float = 0.0, n: int = 40):
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy) or 1e-6
    nx, ny = -dy / length, dx / length
    pts = []
    for i in range(n + 1):
        t = i / n
        bow = curve * length * 4.0 * t * (1.0 - t)
        pts.append((x0 + dx * t + nx * bow, y0 + dy * t + ny * bow))
    return pts


def arc_spine(cx, cy, r, a0, a1, n: int = 80):
    pts = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def feather(img: Image.Image, root, tip, half_width: float, color,
            curve: float = 0.0, rachis=None) -> None:
    spine = seg_spine(root, tip, curve)
    ribbon(img, spine, half_width, color, "feather")
    if rachis is not None:
        ribbon(img, spine, half_width * 0.11, rachis, "taper")


# --------------------------------------------------------------------------
# the Ember bird -- the brand mark
# --------------------------------------------------------------------------
# Design space is a 1x1 box. A teardrop body with the head fused in at the
# upper right, a raised wing driving a hard diagonal to the upper left, splayed
# primaries, three tail quills, a short wedge beak and a two-feather crest.
# The raised wing is what makes this legible at launcher sizes: body mass to
# the lower right, one strong dark diagonal to the upper left.

def _body_mask(img, F):
    artlib.blob(img, F.x(0.470), F.y(0.595), F.d(0.300), F.d(0.250), (255, 255, 255, 255), -14)
    artlib.blob(img, F.x(0.575), F.y(0.500), F.d(0.200), F.d(0.186), (255, 255, 255, 255), -20)
    artlib.blob(img, F.x(0.685), F.y(0.405), F.d(0.190), F.d(0.182), (255, 255, 255, 255))


def _crest_mask(img, F):
    feather(img, F.p(0.700, 0.274), F.p(0.588, 0.150), F.d(0.044), (255, 255, 255, 255), curve=0.15)
    feather(img, F.p(0.762, 0.264), F.p(0.704, 0.118), F.d(0.038), (255, 255, 255, 255), curve=0.13)


def _tail_mask(img, F):
    feather(img, F.p(0.292, 0.538), F.p(0.070, 0.428), F.d(0.052), (255, 255, 255, 255), curve=-0.05)
    feather(img, F.p(0.286, 0.602), F.p(0.034, 0.590), F.d(0.050), (255, 255, 255, 255), curve=0.03)
    feather(img, F.p(0.292, 0.658), F.p(0.092, 0.726), F.d(0.044), (255, 255, 255, 255), curve=0.06)


def _wing_mask(img, F):
    artlib.blob(img, F.x(0.370), F.y(0.272), F.d(0.247), F.d(0.118), (255, 255, 255, 255), -122.4)
    feather(img, F.p(0.312, 0.182), F.p(0.176, -0.004), F.d(0.046), (255, 255, 255, 255), curve=0.07)
    feather(img, F.p(0.298, 0.224), F.p(0.104, 0.082), F.d(0.043), (255, 255, 255, 255), curve=0.06)
    feather(img, F.p(0.292, 0.268), F.p(0.100, 0.198), F.d(0.039), (255, 255, 255, 255), curve=0.05)


def _farwing_mask(img, F):
    artlib.blob(img, F.x(0.446), F.y(0.322), F.d(0.214), F.d(0.088), (255, 255, 255, 255), -114.0)
    feather(img, F.p(0.384, 0.228), F.p(0.288, 0.066), F.d(0.034), (255, 255, 255, 255), curve=0.05)


def _beak_mask(img, F):
    artlib.poly(img, [F.p(0.836, 0.318), F.p(1.026, 0.418), F.p(0.842, 0.504)], (255, 255, 255, 255))


def _eye(F):
    return F.x(0.736), F.y(0.352), F.d(0.058)


def draw_bird(W: int, H: int, F: Frame, mono: bool = False) -> Image.Image:
    """Draw the bird into a supersampled canvas of (W, H) target pixels."""
    body = white(W, H); _body_mask(body, F)
    wing = white(W, H); _wing_mask(wing, F)
    farwing = white(W, H); _farwing_mask(farwing, F)
    tail = white(W, H); _tail_mask(tail, F)
    beak = white(W, H); _beak_mask(beak, F)
    crest = white(W, H); _crest_mask(crest, F)
    ex, ey, er = _eye(F)

    if mono:
        eye = white(W, H)
        artlib.blob(eye, ex, ey, er * 0.78, er * 0.78, (255, 255, 255, 255))
        solid = mask_add(crest, tail, beak, farwing, body)
        gap = max(1.0, F.d(0.016))          # knock a gap so the wing stays legible
        solid = mask_sub(solid, dilate(wing, gap))
        solid = mask_add(solid, wing)
        solid = mask_sub(solid, eye)
        return tint(solid, (255, 255, 255))

    # Value plan: wing is the dark diagonal (luma ~95-135), body the bright mass
    # (luma ~150-175), belly and rim lights the highlights (luma ~200-240).
    body_grad = artlib.radial_gradient(
        256, 256,
        [(0.00, artlib.mix(BODY, CREAM, 0.34)),
         (0.40, BODY),
         (0.74, artlib.shade(BODY, -0.22)),
         (1.00, artlib.shade(BODY, -0.46))],
        center=(0.30, 0.16), radius=1.00)
    wing_grad = artlib.radial_gradient(
        256, 256,
        [(0.00, artlib.mix(artlib.shade(WING, -0.08), CREAM, 0.24)),
         (0.40, artlib.shade(WING, -0.20)),
         (1.00, artlib.shade(WING, -0.54))],
        center=(0.26, 0.10), radius=1.05)
    beak_grad = artlib.vertical_gradient(
        8, 256,
        [(0.00, artlib.mix(BEAK, CREAM, 0.38)),
         (0.52, BEAK),
         (1.00, artlib.shade(BEAK, -0.36))])

    layers = []

    layers.append(artlib.apply_gradient(farwing, artlib.radial_gradient(
        64, 64, [(0.0, artlib.shade(WING, -0.44)), (1.0, artlib.shade(WING, -0.66))],
        center=(0.30, 0.10), radius=1.0)))

    tl = artlib.apply_gradient(tail, artlib.radial_gradient(
        128, 128, [(0.0, WING), (0.6, artlib.shade(WING, -0.20)),
                   (1.0, artlib.shade(WING, -0.48))],
        center=(0.88, 0.22), radius=1.1))
    tl = artlib.inner_shadow(tl, blur=F.d(0.006), color=INK, opacity=0.42)
    tl = artlib.over(tl, directional_light(tail, -F.d(0.006), F.d(0.005), F.d(0.006), CREAM, 0.55))
    layers.append(tl)

    bk = artlib.apply_gradient(beak, beak_grad)
    bk = artlib.over(bk, directional_light(beak, -F.d(0.005), F.d(0.004), F.d(0.004),
                                           (255, 255, 244), 0.60))
    mouth = white(W, H)
    artlib.poly(mouth, [F.p(0.846, 0.422), F.p(1.012, 0.414), F.p(1.006, 0.430),
                        F.p(0.848, 0.452)], artlib.rgba(artlib.shade(BEAK, -0.55), 200))
    bk = artlib.over(bk, artlib.clip_to(mouth, beak))
    layers.append(bk)

    cr = artlib.apply_gradient(crest, artlib.vertical_gradient(
        8, 128, [(0.0, artlib.shade(WING, -0.04)), (1.0, artlib.shade(WING, -0.38))]))
    layers.append(cr)

    # ---- body ------------------------------------------------------------
    bd = artlib.apply_gradient(body, body_grad)

    # A low belly band and a small throat patch only: the body must still read
    # as ember orange, so the pale areas stay well under half the silhouette.
    belly = white(W, H)
    artlib.blob(belly, F.x(0.545), F.y(0.745), F.d(0.198), F.d(0.096), (255, 255, 255, 255), -8)
    artlib.blob(belly, F.x(0.700), F.y(0.500), F.d(0.086), F.d(0.070), (255, 255, 255, 255))
    bd = artlib.over(bd, artlib.clip_to(soft_mask_layer(belly, BELLY, F.d(0.028), 0.95), body))

    cheek = white(W, H)
    artlib.blob(cheek, F.x(0.726), F.y(0.374), F.d(0.096), F.d(0.086), (255, 255, 255, 255))
    bd = artlib.over(bd, artlib.clip_to(
        soft_mask_layer(cheek, artlib.mix(BELLY, CREAM, 0.45), F.d(0.020), 0.62), body))

    bd = artlib.inner_shadow(bd, blur=F.d(0.007), color=INK, opacity=0.36)
    bd = artlib.over(bd, directional_light(body, -F.d(0.0070), F.d(0.0060), F.d(0.0070), CREAM, 0.72))
    bd = artlib.over(bd, directional_light(body, F.d(0.0070), -F.d(0.0058), F.d(0.0085),
                                           artlib.mix(WARM, CREAM, 0.35), 0.26))
    bd = artlib.over(bd, occlusion(wing, body, offset=(F.d(0.007), F.d(0.015)),
                                   blur=F.d(0.018), opacity=0.50))
    layers.append(bd)

    # ---- eye ---------------------------------------------------------------
    eye = white(W, H)
    artlib.blob(eye, ex, ey, er, er, artlib.rgba(artlib.shade(INK, 0.05), 255))
    artlib.blob(eye, ex + er * 0.30, ey - er * 0.34, er * 0.34, er * 0.34, (255, 255, 255, 240))
    artlib.blob(eye, ex - er * 0.44, ey + er * 0.40, er * 0.15, er * 0.15, (255, 255, 255, 150))
    layers.append(eye)

    # ---- near wing ----------------------------------------------------------
    wg = artlib.apply_gradient(wing, wing_grad)
    # lighter covert band along the leading edge, then two quill separations
    cov = white(W, H)
    artlib.blob(cov, F.x(0.400), F.y(0.300), F.d(0.190), F.d(0.052), (255, 255, 255, 255), -122.4)
    wg = artlib.over(wg, artlib.clip_to(
        soft_mask_layer(cov, artlib.mix(WING, CREAM, 0.26), F.d(0.014), 0.55), wing))
    sep = white(W, H)
    for (a, b, w) in (((0.330, 0.214), (0.430, 0.382), 0.0095),
                      ((0.300, 0.266), (0.404, 0.428), 0.0085)):
        ribbon(sep, seg_spine(F.p(*a), F.p(*b), 0.05), F.d(w),
               artlib.rgba(artlib.shade(WING, -0.56), 140), "leaf")
    wg = artlib.over(wg, artlib.clip_to(sep, wing))
    wg = artlib.inner_shadow(wg, blur=F.d(0.006), color=INK, opacity=0.40)
    wg = artlib.over(wg, directional_light(wing, -F.d(0.0066), F.d(0.0056), F.d(0.0062),
                                           CREAM, 0.95))
    wg = artlib.over(wg, directional_light(wing, F.d(0.0065), -F.d(0.0054), F.d(0.0078), WARM, 0.30))
    root = white(W, H)
    artlib.blob(root, F.x(0.470), F.y(0.468), F.d(0.122), F.d(0.092), (255, 255, 255, 255), -40)
    wg = artlib.over(wg, artlib.clip_to(soft_mask_layer(root, INK, F.d(0.030), 0.44), wing))
    layers.append(wg)

    return artlib.over(artlib.canvas(W, H), *layers)


# Where the shed feathers are pulled to in `compact` mode, and how far. They
# are the ink that reaches furthest from the centre of the mark, so this is
# also what decides how large the bird can be inside the launcher's mask --
# and, because the mark is centred on its ink, how far off-centre the bird
# itself ends up. Tucked in behind the tail rather than dropped below it, they
# read as shed feathers and keep the silhouette compact.
COMPACT_TUCK = 0.52
COMPACT_ANCHOR = (0.268, 0.650)


def draw_trail(W: int, H: int, F: Frame, mono: bool = False, compact: bool = False):
    """Speed arcs and shed feathers.

    Three layers come back, because each is shaded differently downstream: the
    arcs are blurred into a motion smear, the quills take a gradient, and the
    rachis has to be painted on top of that gradient rather than through it.
    """
    arcs = white(W, H)
    quills = white(W, H)
    veins = white(W, H)
    # The arcs get screened onto the field, and screening lifts every channel
    # of the base: a cream streak over an indigo field comes back pink. Keeping
    # the streak's own blue low is what keeps the result warm.
    col = (255, 255, 255, 255) if mono else (255, 198, 118, 255)
    qcol = (255, 255, 255, 255) if mono else artlib.rgba(artlib.mix(BODY, CREAM, 0.30), 255)
    span = 0.64 if compact else 1.0

    for (cx, cy, r, a0, a1, hw) in (
            (0.55, -0.42, 1.00, 120.0, 95.0, 0.0155),
            (0.52, -0.30, 0.92, 122.0, 100.0, 0.0115),
            (0.50, -0.20, 0.80, 124.0, 104.0, 0.0085)):
        a0 = a1 + (a0 - a1) * span
        ribbon(arcs, arc_spine(F.x(cx), F.y(cy), F.d(r), a0, a1), F.d(hw), col, "comet")

    feathers = (
        ((0.300, 0.772), (0.182, 0.852), 0.040, 0.10),
        ((0.186, 0.700), (0.090, 0.764), 0.031, 0.12),
    )
    for (root, tip, hw, curve) in feathers:
        if compact:  # pull the shed feathers in toward the bird, keeping their shape
            k, (ax, ay) = COMPACT_TUCK, COMPACT_ANCHOR
            root = (ax + (root[0] - 0.300) * k, ay + (root[1] - 0.712) * k)
            tip = (ax + (tip[0] - 0.300) * k, ay + (tip[1] - 0.712) * k)
            hw *= 0.90
        feather(quills, F.p(*root), F.p(*tip), F.d(hw), qcol, curve=curve)
        if not mono:
            ribbon(veins, seg_spine(F.p(*root), F.p(*tip), curve), F.d(hw) * 0.11,
                   artlib.rgba(artlib.shade(BODY, -0.30), 215), "taper")
    return arcs, quills, veins


def shade_quills(quills: Image.Image) -> Image.Image:
    """Light the shed feathers like every other part of the mark.

    A flat fill is what makes a shed feather read as a petal (or, at launcher
    sizes, as screen dirt). The gradient is aimed from just above the quills'
    own ink, so it keeps the upper-left key light whether they sit splayed out
    behind the bird or tucked in under the tail.
    """
    bb = quills.getchannel("A").getbbox()
    if bb is None:
        return quills
    span = float(quills.width)
    qw, qh = max(bb[2] - bb[0], 1), max(bb[3] - bb[1], 1)
    center = ((bb[0] - qw * 0.22) / span, (bb[1] - qh * 0.34) / span)
    radius = 1.30 * math.hypot(qw, qh) / span
    return artlib.apply_gradient(quills, artlib.radial_gradient(
        192, 192,
        [(0.00, artlib.mix(BODY, CREAM, 0.55)),
         (0.50, BODY),
         (1.00, artlib.shade(BODY, -0.34))],
        center=center, radius=radius))


def render_mark(size: int, mono: bool = False, with_trail: bool = True,
                compact: bool = False, ss: int = 3, split: bool = False):
    """The full mark (trail + bird). Returns (RGBA square, bird ink bbox).

    With ``split=True`` the speed arcs come back as their own layer --
    ``(arcs, solid, bird ink bbox)`` -- so a caller that owns an opaque field
    can *screen* them onto it. Alpha-blending a pale streak over a dark field
    can only reach the midpoint of the two, which is mud; screening lifts.
    """
    with supersample(ss):
        W = H = int(size)
        BF = Frame(0.245 * W, 0.075 * W, 0.720 * W)   # bird sub-frame
        TF = Frame(0.0, 0.0, W)                       # trail uses the whole square

        arc_layer = artlib.canvas(W, H)
        stack = artlib.canvas(W, H)
        if with_trail:
            arcs, quills, veins = draw_trail(W, H, TF, mono=mono, compact=compact)
            if mono:
                stack = artlib.over(stack, arcs, quills)
            else:
                # A streak needs both halves to read as light rather than as
                # smoke: a wide bloom for the glow and a tighter core inside it
                # for the direction.
                bloom = arcs.filter(ImageFilter.GaussianBlur(W * 0.0090 * ss))
                core = arcs.filter(ImageFilter.GaussianBlur(W * 0.0022 * ss))
                arc_layer = artlib.over(arc_layer, fade(bloom, 0.46), fade(core, 0.95))
                quills = artlib.over(shade_quills(quills), artlib.clip_to(veins, quills))
                quills = artlib.over(
                    artlib.drop_shadow(quills, offset=(W * 0.003, W * 0.008),
                                       blur=W * 0.010, color=(16, 6, 28), opacity=0.45),
                    artlib.inner_shadow(quills, blur=W * 0.0025, color=INK, opacity=0.30))
                quills = artlib.over(quills, directional_light(
                    quills, -W * 0.0022, W * 0.0018, W * 0.0022, CREAM, 0.70))
                stack = artlib.over(stack, quills)

        bird = draw_bird(W, H, BF, mono=mono)
        if not mono:
            # contact shadow: guarantees the subject separates from any field
            bird = Image.alpha_composite(
                artlib.drop_shadow(bird, offset=(W * 0.005, W * 0.016), blur=W * 0.020,
                                   color=(16, 6, 28), opacity=0.42), bird)
        stack = artlib.over(stack, bird)

        solid = resolve_rgba(stack, W, H)
        bbox = resolve_rgba(bird, W, H).getchannel("A").point(
            lambda v: 255 if v > 40 else 0).getbbox()
        if split:
            return resolve_rgba(arc_layer, W, H), solid, bbox
        return Image.alpha_composite(resolve_rgba(arc_layer, W, H), solid), bbox


# --------------------------------------------------------------------------
# backgrounds
# --------------------------------------------------------------------------

def diagonal_gradient(w: int, h: int, stops, angle_deg: float = 48.0,
                      zoom: float = 1.0) -> Image.Image:
    a = math.radians(angle_deg)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    d = (xx / max(w - 1, 1)) * math.cos(a) + (yy / max(h - 1, 1)) * math.sin(a)
    d -= d.min()
    d /= max(d.max(), 1e-9)
    if zoom != 1.0:
        # `zoom` < 1 runs the whole ramp across the middle `zoom` of the canvas,
        # so the same gradient can be drawn into a larger frame unchanged.
        d = 0.5 + (d - 0.5) / zoom
    stops = sorted(stops, key=lambda it: it[0])
    pos = np.array([p for p, _ in stops], dtype=np.float64)
    cols = np.array([artlib.rgba(c) for _, c in stops], dtype=np.float64)
    out = np.empty((h, w, 4), dtype=np.float64)
    for ch in range(4):
        out[:, :, ch] = np.interp(d, pos, cols[:, ch])
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def vignette(w: int, h: int, strength: float = 0.5, radius: float = 0.78,
             color=(8, 5, 22)) -> Image.Image:
    return artlib.radial_gradient(
        w, h,
        [(0.00, artlib.rgba(color, 0)),
         (0.52, artlib.rgba(color, int(22 * strength))),
         (1.00, artlib.rgba(color, int(255 * strength)))],
        center=(0.5, 0.5), radius=radius)


def soft_disc(w: int, h: int, cx: float, cy: float, r: float, color,
              opacity: float) -> Image.Image:
    return artlib.radial_gradient(
        w, h,
        [(0.00, artlib.rgba(color, int(255 * opacity))),
         (0.50, artlib.rgba(color, int(255 * opacity * 0.46))),
         (1.00, artlib.rgba(color, 0))],
        center=(cx, cy), radius=r)


def brand_field(w: int, h: int, *, center=(0.33, 0.26), radius: float = 1.00,
                seed: int = 7, vig: float = 0.56, bokeh: bool = True,
                beam: bool = True, square: int | None = None) -> Image.Image:
    """The shared brand background: rose core falling to indigo, warm beam.

    `square` draws the field as though it were composed for a `square`-pixel
    icon and then extended outward to fill a bigger canvas. Android only shows
    the middle 72dp of a 108dp adaptive layer, so this is how the launcher
    background ends up being the same picture as the full-bleed store icon
    rather than a differently-framed one.
    """
    k = 1.0 if square is None else float(square) / float(w)

    def pt(p):
        return (0.5 + (p[0] - 0.5) * k, 0.5 + (p[1] - 0.5) * k)

    base = artlib.radial_gradient(
        w, h,
        [(0.00, FIELD_CORE), (0.26, FIELD_MID), (0.60, FIELD_DEEP), (1.00, FIELD_EDGE)],
        center=pt(center), radius=radius * k)
    layers = []
    if beam:
        layers.append(diagonal_gradient(w, h, [
            (0.00, artlib.rgba(WARM, 84)),
            (0.30, artlib.rgba(artlib.mix(WARM, FIELD_CORE, 0.6), 30)),
            (0.66, artlib.rgba(FIELD_DEEP, 0)),
            (1.00, artlib.rgba((8, 6, 30), 88)),
        ], angle_deg=52, zoom=k))
    if bokeh:
        rng = np.random.default_rng(seed)
        orbs = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for _ in range(10):
            cx, cy = float(rng.uniform(0.04, 0.96)), float(rng.uniform(0.04, 0.96))
            r = float(rng.uniform(0.05, 0.20))
            col = artlib.mix(WARM, CREAM, float(rng.uniform(0.0, 0.8)))
            cx, cy = pt((cx, cy))
            orbs = Image.alpha_composite(
                orbs, soft_disc(w, h, cx, cy, r * k, col, float(rng.uniform(0.04, 0.095))))
        layers.append(orbs)
    layers.append(vignette(w, h, strength=vig, radius=0.78 * k))
    out = artlib.over(base, *layers)
    return artlib.grain(out, strength=0.045, seed=seed, cells=max(24, w // 14))


# --------------------------------------------------------------------------
# typography
# --------------------------------------------------------------------------

def text_block(text: str, px: int, tracking: float = 0.055, K: int = 3):
    """Letterspaced white text as an alpha block. Returns (block, w, h, K)."""
    font = ImageFont.truetype(FONT_PATH, int(px * K))
    widths = [font.getlength(ch) for ch in text]
    tr = tracking * px * K
    total = sum(widths) + tr * max(len(text) - 1, 0)
    asc, desc = font.getmetrics()
    pad = int(px * K * 0.55)
    img = Image.new("RGBA", (int(total) + pad * 2, asc + desc + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = float(pad)
    for ch, cw in zip(text, widths):
        d.text((x, pad), ch, font=font, fill=(255, 255, 255, 255))
        x += cw + tr
    bbox = img.getchannel("A").getbbox() or (0, 0, img.width, img.height)
    grow = int(px * K * 0.26)
    img = img.crop((max(0, bbox[0] - grow), max(0, bbox[1] - grow),
                    min(img.width, bbox[2] + grow), min(img.height, bbox[3] + grow)))
    return img, max(1, img.width // K), max(1, img.height // K), K


def wordmark(px: int = 120, tracking: float = 0.07, text: str = WORDMARK_TEXT,
             top=CREAM, bot=(255, 182, 104), outline_px: float = 0.0,
             shadow: bool = True) -> Image.Image:
    """A finished, coloured wordmark at 1x with its shadow baked in."""
    block, w, h, K = text_block(text, px, tracking)
    with supersample(K):
        col = artlib.apply_gradient(block, artlib.vertical_gradient(
            8, 256, [(0.00, top), (0.40, artlib.mix(top, bot, 0.28)),
                     (0.80, bot), (1.00, artlib.shade(bot, -0.18))]))
        col = artlib.over(col, directional_light(block, -px * 0.012, px * 0.012,
                                                 px * 0.010, (255, 255, 255), 0.55))
        if outline_px > 0:
            col = artlib.outline(col, width=outline_px, color=artlib.shade(INK, 0.04),
                                 opacity=0.92)
        if shadow:
            sh = artlib.drop_shadow(col, offset=(0, max(1.5, px * 0.040)),
                                    blur=max(2.0, px * 0.060), color=INK, opacity=0.58)
            col = Image.alpha_composite(sh, col)
        return resolve_rgba(col, w, h)


# --------------------------------------------------------------------------
# placement helpers
# --------------------------------------------------------------------------

def blit(dst: Image.Image, src: Image.Image, x: int, y: int) -> None:
    """alpha_composite `src` onto `dst` at (x, y), clipping anything outside."""
    x, y = int(x), int(y)
    sx0, sy0 = max(0, -x), max(0, -y)
    sx1 = min(src.width, dst.width - x)
    sy1 = min(src.height, dst.height - y)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    piece = src.crop((sx0, sy0, sx1, sy1))
    dst.alpha_composite(piece, (x + sx0, y + sy0))


def ink_crop(img: Image.Image, thresh: int = 4) -> Image.Image:
    a = img.getchannel("A").point(lambda v: 255 if v > thresh else 0)
    bbox = a.getbbox()
    return img.crop(bbox) if bbox else img


def place(dst: Image.Image, src: Image.Image, cx: float, cy: float,
          width: float | None = None, height: float | None = None,
          ref_box=None, blend: str = "over") -> Image.Image:
    """Scale `src` so `ref_box` is `width` wide, then centre that box on (cx, cy).

    `ref_box` lets a sprite carry a soft trail or shadow outside the part that
    should actually be measured and centred. With no width or height the sprite
    is placed at its native size, centred on its own ink. `blend="screen"`
    lifts an opaque `dst` instead of interpolating toward `src`, which is what
    a speed streak or a glow needs; it leaves `dst`'s alpha alone.
    """
    if ref_box is None:
        src = ink_crop(src)
        ref_box = (0, 0, src.width, src.height)
    bw, bh = ref_box[2] - ref_box[0], ref_box[3] - ref_box[1]
    if width is not None:
        k = width / max(bw, 1)
    elif height is not None:
        k = height / max(bh, 1)
    else:
        k = 1.0
    nw, nh = max(1, int(round(src.width * k))), max(1, int(round(src.height * k)))
    if (nw, nh) != src.size:
        src = resize_rgba(src, (nw, nh))
    icx = (ref_box[0] + ref_box[2]) * 0.5 * k
    icy = (ref_box[1] + ref_box[3]) * 0.5 * k
    layer = Image.new("RGBA", dst.size, (0, 0, 0, 0))
    layer.paste(src, (int(round(cx - icx)), int(round(cy - icy))), src)
    if blend == "screen":
        return artlib.screen(dst, layer)
    return Image.alpha_composite(dst, layer)


def max_alpha_radius(img: Image.Image, cx: float, cy: float, thresh: int = 8) -> float:
    a = np.asarray(img.getchannel("A"))
    ys, xs = np.nonzero(a > thresh)
    if len(xs) == 0:
        return 0.0
    return float(np.max(np.hypot(xs + 0.5 - cx, ys + 0.5 - cy)))


# --------------------------------------------------------------------------
# landscape elements for the feature graphic
# --------------------------------------------------------------------------

def ridge_band(w: int, h: int, base_y: float, amp: float, peaks: int, seed: int,
               top_color, bot_color, roughness: float = 0.45,
               rim=None, rim_px: float = 3.0) -> Image.Image:
    prof = artlib.ridge_profile(w, peaks, seed, roughness)
    top = (base_y - amp * prof)[None, :]
    yy = np.arange(h, dtype=np.float64)[:, None]
    mask = np.clip((yy - top) / 1.6 + 0.5, 0.0, 1.0)
    depth = np.clip((yy - top) / max(h - (base_y - amp), 1.0), 0.0, 1.0)
    c0 = np.array(artlib.rgba(top_color)[:3], dtype=np.float64)
    c1 = np.array(artlib.rgba(bot_color)[:3], dtype=np.float64)
    rgb = c0[None, None, :] + (c1 - c0)[None, None, :] * depth[:, :, None]
    if rim is not None:
        cr = np.array(artlib.rgba(rim)[:3], dtype=np.float64)
        k = np.clip(1.0 - (yy - top) / rim_px, 0.0, 1.0) * 0.9
        rgb = rgb + (cr[None, None, :] - rgb) * k[:, :, None]
    out = np.concatenate([rgb, (mask * 255.0)[:, :, None]], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8), "RGBA")


def game_pillar(width: int, length: int, from_top: bool, theme: str = "dusk"):
    """Build a pillar from the game's own tiles, if that generator has run.

    Returns None when the sprites are not on disk yet, so the caller can fall
    back to the procedural pillar below.
    """
    # The upper pillar has its own cap sprite: flipping the lower one would
    # flip its lighting with it, which is the whole reason that sprite exists.
    cap_p = manifest.image_path(f"pillar_cap{'_up' if from_top else ''}_{theme}.png")
    body_p = manifest.image_path(f"pillar_body_{theme}.png")
    if not (os.path.exists(cap_p) and os.path.exists(body_p)):
        return None
    try:
        cap = Image.open(cap_p).convert("RGBA")
        body = Image.open(body_p).convert("RGBA")
    except Exception:
        return None
    if cap.size != tuple(manifest.PILLAR_CAP) or body.size != tuple(manifest.PILLAR_BODY):
        return None

    k = width / cap.width                      # the cap is the widest part
    cw, ch = max(1, round(cap.width * k)), max(1, round(cap.height * k))
    bw, bh = max(1, round(body.width * k)), max(1, round(body.height * k))
    cap = resize_rgba(cap, (cw, ch))
    body = resize_rgba(body, (bw, bh))

    H = max(length, ch + bh)
    out = Image.new("RGBA", (cw, H), (0, 0, 0, 0))
    bx = (cw - bw) // 2
    overlap = max(1, int(ch * 0.12))
    if from_top:
        y = 0
        while y < H - ch + overlap:
            blit(out, body, bx, y)
            y += bh
        blit(out, cap, 0, H - ch)
    else:
        y = ch - overlap
        while y < H:
            blit(out, body, bx, y)
            y += bh
        blit(out, cap, 0, 0)
    return out


def pillar(w: int, h: int, body_col, light_col, rim_col, *, from_top: bool,
           cap_h: float, shaft_w: float, cap_w: float, ss: int = 3,
           sun_on_left: bool = True) -> Image.Image:
    """A framing pillar: shaft plus flared cap.

    Everything is drawn lit from the left and the whole sprite is mirrored when
    the sun is on the other side, so the body gradient, the warm rim and the
    cool bounce can never end up disagreeing about where the light is.
    """
    with supersample(ss):
        img = white(w, h)
        r = shaft_w * 0.16
        if from_top:
            artlib.rounded(img, (w - shaft_w) / 2, -r * 2, shaft_w,
                           h - cap_h * 0.55 + r * 2, r, (255, 255, 255, 255))
            artlib.rounded(img, (w - cap_w) / 2, h - cap_h, cap_w, cap_h,
                           cap_h * 0.28, (255, 255, 255, 255))
        else:
            artlib.rounded(img, (w - shaft_w) / 2, cap_h * 0.55, shaft_w,
                           h - cap_h * 0.55 + r * 2, r, (255, 255, 255, 255))
            artlib.rounded(img, (w - cap_w) / 2, 0, cap_w, cap_h,
                           cap_h * 0.28, (255, 255, 255, 255))

        grad = artlib.horizontal_gradient(256, 8, [
            (0.00, artlib.shade(body_col, -0.22)),
            (0.14, light_col),
            (0.42, body_col),
            (1.00, artlib.shade(body_col, -0.42)),
        ])
        col = artlib.apply_gradient(img, grad)
        col = artlib.inner_shadow(col, blur=3.0, color=(6, 3, 14), opacity=0.46)
        # warm rim on the lit (left) edge, cool sky bounce on the shaded one
        col = artlib.over(col, directional_light(img, 2.6, 0.0, 1.6, rim_col, 1.0))
        col = artlib.over(col, directional_light(img, -2.8, 0.0, 2.6,
                                                 artlib.mix(rim_col, (120, 150, 220), 0.6), 0.22))
        out = artlib.grain(resolve_rgba(col, w, h), strength=0.04, seed=11, cells=20)
        return out if sun_on_left else out.transpose(Image.FLIP_LEFT_RIGHT)


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# the icon, composed once
# --------------------------------------------------------------------------
# A user taps Install on the Play listing icon and then looks at the launcher
# icon forever, so the two have to be the same picture. They are only drawn at
# different scales: Android masks the middle 72dp of the 108dp adaptive layer,
# which is `ADAPTIVE_VIEW` of `ADAPTIVE_LAYER` px, while the store icon is
# full-bleed square. Everything below is expressed in "icon square" units and
# rendered into whatever canvas the target needs, so there is exactly one
# composition and no second set of numbers to drift.
ADAPTIVE_VIEW = manifest.ADAPTIVE_LAYER * 72 // 108   # 288 px = the masked viewport

# Solved, not guessed. The mark is sized and placed so its *minimal enclosing
# circle* -- not the circle around its bounding box, which wastes all the room
# a 1.25:1 shape has in its corners -- sits on the centre of Android's 66dp
# guaranteed-visible circle and nearly fills it. That puts the ink at 90% of
# the safe circle's diameter (54% of the 108dp layer) against 43% before, then
# gives back half a percent of it to leave the beak some air in the square.
# Going further means giving up the 66dp guarantee, and the price of that is a
# beak clipped off by any launcher whose mask is tighter than the 72dp circle.
ICON_BIRD_W = 0.700        # bird ink width, as a fraction of the icon square
ICON_BIRD_C = (0.570, 0.424)   # bird ink centre, in icon-square coordinates


def icon_field(canvas_px: int, square_px: int) -> Image.Image:
    """The icon's background, composed for `square_px` and drawn into `canvas_px`."""
    k = float(square_px) / float(canvas_px)
    out = brand_field(canvas_px, canvas_px, center=(0.32, 0.25), radius=1.02,
                      seed=7, vig=0.60, square=square_px)
    # a very soft halo keeps the subject off the darkest part of the field,
    # mapped out into the larger canvas exactly the way the field itself is
    hx, hy, hr = 0.50, 0.48, 0.52
    return Image.alpha_composite(out, soft_disc(
        canvas_px, canvas_px, 0.5 + (hx - 0.5) * k, 0.5 + (hy - 0.5) * k, hr * k,
        artlib.mix(FIELD_CORE, WARM, 0.40), 0.18))


def icon_parts(canvas_px: int, square_px: int):
    """(background, foreground) for the icon at this scale.

    The speed streak lives in the *background* because it has to be screened
    onto the field to read as light, and an adaptive foreground is alpha
    composited by the launcher, which cannot screen. It is a soft smear, so the
    few pixels of launcher parallax between the two layers do not show.
    """
    cx = canvas_px * 0.5 + (ICON_BIRD_C[0] - 0.5) * square_px
    cy = canvas_px * 0.5 + (ICON_BIRD_C[1] - 0.5) * square_px
    bird_w = ICON_BIRD_W * square_px
    arcs, solid, box = render_mark(int(bird_w * 1.75), compact=True, ss=3, split=True)

    bg = place(icon_field(canvas_px, square_px), arcs, cx, cy,
               width=bird_w, ref_box=box, blend="screen")
    fg = place(Image.new("RGBA", (canvas_px, canvas_px), (0, 0, 0, 0)), solid,
               cx, cy, width=bird_w, ref_box=box)
    return bg, fg


def build_icon() -> Image.Image:
    N = manifest.APP_ICON
    bg, fg = icon_parts(N, N)
    return Image.alpha_composite(bg, fg)


def build_adaptive_bg() -> Image.Image:
    return icon_parts(manifest.ADAPTIVE_LAYER, ADAPTIVE_VIEW)[0]


def build_adaptive_fg() -> Image.Image:
    return icon_parts(manifest.ADAPTIVE_LAYER, ADAPTIVE_VIEW)[1]


def build_monochrome() -> Image.Image:
    N = manifest.ADAPTIVE_LAYER
    cx = N * 0.5 + (ICON_BIRD_C[0] - 0.5) * ADAPTIVE_VIEW
    cy = N * 0.5 + (ICON_BIRD_C[1] - 0.5) * ADAPTIVE_VIEW
    bird_w = ICON_BIRD_W * ADAPTIVE_VIEW
    mark, box = render_mark(int(bird_w * 1.75), mono=True, compact=True, ss=3)
    out = place(Image.new("RGBA", (N, N), (0, 0, 0, 0)), mark, cx, cy,
                width=bird_w, ref_box=box)
    # a themed icon is tinted by the launcher: every pixel must be pure white
    arr = np.asarray(out).copy()
    arr[:, :, 0:3] = 255
    return Image.fromarray(arr, "RGBA")


def load_logo(max_w: float, max_h: float, path: str | None = None):
    """Reuse the game's own wordmark if another generator has written it.

    Returns (image, is_full_lockup). A wide, short file is treated as a pure
    wordmark and gets the bird placed above it; anything squarer probably
    already contains a mark, so it is used on its own.
    """
    path = LOGO_PATH if path is None else path   # resolved per call, not at import
    if not os.path.exists(path):
        return None, False
    try:
        img = Image.open(path).convert("RGBA")
    except Exception:
        return None, False
    img = ink_crop(img)
    if img.width < 16 or img.height < 16:
        return None, False
    lockup = (img.width / img.height) < 2.4
    k = min(max_w / img.width, max_h / img.height)
    img = resize_rgba(img, (max(1, int(img.width * k)), max(1, int(img.height * k))))
    return img, lockup


def edge_lock(w: int, h: int, color, band: float) -> Image.Image:
    """A border ramp that forces every edge pixel to exactly `color`.

    p4a scales the presplash FIT_CENTER and paints whatever is left over in
    `android.presplash_color`, so unless the asset's own border *is* that
    colour there is a hard seam at the join -- the first thing every user sees
    on a cold start. One constant cannot match two different edges, so the fix
    belongs in the art: lock all four borders to one value and declare it.
    """
    xx = np.minimum(np.arange(w), np.arange(w)[::-1])[None, :]
    yy = np.minimum(np.arange(h), np.arange(h)[::-1])[:, None]
    t = np.clip(np.minimum(xx, yy).astype(np.float64) / max(band, 1.0), 0.0, 1.0)
    a = 1.0 - t * t * (3.0 - 2.0 * t)   # smoothstep: flat at both ends, no ring
    layer = np.zeros((h, w, 4), dtype=np.float64)
    layer[:, :, :3] = np.array(artlib.rgba(color)[:3], dtype=np.float64)
    layer[:, :, 3] = a * 255.0
    return Image.fromarray(layer.round().astype(np.uint8), "RGBA")


def build_presplash() -> Image.Image:
    W, H = manifest.PRESPLASH
    # Authored portrait, because the app is portrait-locked: a square asset on
    # a 20:9 phone fills 45% of the screen and letterboxes the rest.
    out = brand_field(W, H, center=(0.46, 0.30), radius=1.52, seed=21, vig=0.46)
    out = Image.alpha_composite(out, soft_disc(W, H, 0.50, 0.355, 0.46,
                                               artlib.mix(FIELD_CORE, WARM, 0.45), 0.20))

    logo, lockup = load_logo(W * 0.72, H * 0.17)
    if logo is not None and lockup:
        # the shipped logo already is a complete lockup -- centre it alone
        out = place(out, logo, W * 0.5, H * 0.5)
        return Image.alpha_composite(out, edge_lock(W, H, FIELD_EDGE, min(W, H) * 0.135))

    arcs, solid, bird_box = render_mark(int(W * 0.72), ss=3, compact=True, split=True)
    out = place(out, arcs, W * 0.515, H * 0.392, width=W * 0.470,
                ref_box=bird_box, blend="screen")
    out = place(out, solid, W * 0.515, H * 0.392, width=W * 0.470, ref_box=bird_box)

    if logo is None:
        logo = wordmark(120, tracking=0.075, shadow=True)
        k = min(W * 0.72 / logo.width, H * 0.11 / logo.height)
        logo = resize_rgba(logo, (int(logo.width * k), int(logo.height * k)))
    out = place(out, logo, W * 0.5, H * 0.588)
    return Image.alpha_composite(out, edge_lock(W, H, FIELD_EDGE, min(W, H) * 0.135))


# Where the sun sits in the feature graphic. Every lit edge in the frame is
# derived from this, so the two framing pillars cannot end up rim-lit on the
# same side as each other.
SUN_CX, SUN_CY = 0.290, 0.655


def build_feature() -> Image.Image:
    W, H = manifest.FEATURE_GRAPHIC
    pal = manifest.THEME_PALETTE["dusk"]

    out = artlib.vertical_gradient(W, H, [
        (0.00, (14, 16, 52)), (0.16, (40, 28, 86)), (0.34, (92, 40, 110)),
        (0.52, (158, 58, 112)), (0.68, (216, 92, 104)), (0.82, (246, 140, 96)),
        (0.93, (255, 186, 118)), (1.00, (255, 214, 150)),
    ])
    # sun just above the far ridge line
    out = Image.alpha_composite(out, soft_disc(W, H, SUN_CX, SUN_CY, 0.25, (255, 198, 144), 0.42))
    out = Image.alpha_composite(out, soft_disc(W, H, SUN_CX, SUN_CY, 0.062, (255, 248, 220), 0.88))

    rng = np.random.default_rng(5)
    with supersample(3):
        st = white(W, H)
        for _ in range(80):
            x, y = float(rng.uniform(0, W)), float(rng.uniform(0, H * 0.48))
            a = float(np.clip(rng.normal(104, 48), 16, 210)) * max(0.0, 1.0 - y / (H * 0.56))
            r = float(rng.uniform(0.7, 2.0))
            artlib.blob(st, x, y, r, r, (255, 242, 228, int(a)))
        out = Image.alpha_composite(out, resolve_rgba(st, W, H))

    # clouds: the game's own dusk sprites when they exist, soft bands otherwise
    clouds = []
    for i in range(manifest.CLOUD_VARIANTS):
        p = manifest.image_path(f"cloud_dusk_{i}.png")
        if os.path.exists(p):
            try:
                clouds.append(Image.open(p).convert("RGBA"))
            except Exception:
                pass
    if clouds:
        for i, (cx, cy, cw_, op) in enumerate((
                (0.14, 0.505, 0.21, 0.36), (0.72, 0.455, 0.17, 0.30),
                (0.47, 0.585, 0.24, 0.24), (0.92, 0.545, 0.14, 0.22))):
            c = clouds[i % len(clouds)]
            k = (W * cw_) / c.width
            c = resize_rgba(c, (max(1, int(c.width * k)), max(1, int(c.height * k))))
            out = place(out, fade(c, op), W * cx, H * cy)
    else:
        for (cx, cy, rx, ry, col, op) in (
                (0.20, 0.515, 0.26, 0.016, (255, 196, 170), 0.26),
                (0.63, 0.575, 0.30, 0.020, (255, 176, 150), 0.22),
                (0.40, 0.628, 0.34, 0.015, (255, 214, 176), 0.24)):
            band = artlib.radial_gradient(
                W, H, [(0.0, artlib.rgba(col, int(255 * op))), (1.0, artlib.rgba(col, 0))],
                center=(cx, cy), radius=rx)
            arr = np.asarray(band, dtype=np.float64).copy()
            yy = np.arange(H, dtype=np.float64)[:, None]
            arr[:, :, 3] *= np.clip(1.0 - np.abs(yy - cy * H) / (ry * H * 3.4), 0.0, 1.0)
            out = Image.alpha_composite(out, Image.fromarray(arr.round().astype(np.uint8), "RGBA"))

    # parallax ridges: desaturated toward the fog colour by distance
    far = artlib.mix(pal["far"], pal["fog"], 0.62)
    mid = artlib.mix(pal["mid"], pal["fog"], 0.30)
    near = pal["near"]
    base = artlib.shade(pal["ground_body"], -0.34)
    out = Image.alpha_composite(out, ridge_band(
        W, H, H * 0.775, H * 0.125, 3, 4, artlib.shade(far, 0.10), artlib.shade(far, -0.12),
        roughness=0.36, rim=artlib.mix(far, (255, 224, 190), 0.60), rim_px=2.2))
    out = Image.alpha_composite(out, ridge_band(
        W, H, H * 0.850, H * 0.100, 4, 9, artlib.shade(mid, 0.06), artlib.shade(mid, -0.22),
        roughness=0.32, rim=artlib.mix(mid, (255, 200, 158), 0.58), rim_px=2.2))

    near_band = ridge_band(
        W, H, H * 0.918, H * 0.062, 5, 2, artlib.shade(near, 0.10), artlib.shade(near, -0.34),
        roughness=0.30, rim=artlib.mix(near, (255, 164, 126), 0.50), rim_px=1.8)
    ground_band = ridge_band(
        W, H, H * 0.968, H * 0.012, 3, 6, artlib.shade(base, 0.06), artlib.shade(base, -0.30),
        roughness=0.30, rim=artlib.mix(base, (255, 150, 120), 0.40), rim_px=1.4)
    out = Image.alpha_composite(out, near_band)
    out = Image.alpha_composite(out, ground_band)

    # ---- framing pillars --------------------------------------------------
    # The sun disc sits at x = SUN_CX, so the two pillars face it from opposite
    # sides and nothing about their lighting can be shared: each is built into
    # its own layer and lit with its own sign. The in-game tiles are lit from
    # the left for daylight, so the left-hand pillar is mirrored to bring its
    # baked highlight round onto the side that actually faces the sun. Both run
    # off the frame edge: a framing silhouette that stops short of the edge
    # reads as a compositing mistake.
    pw = 150
    sun_x = W * SUN_CX
    pieces = []
    for (xpos, ypos, length, from_top) in ((-pw // 3, int(H * 0.400), int(H * 0.68), False),
                                           (W - pw + pw // 3, 0, int(H * 0.56), True)):
        sun_on_left = sun_x < xpos + pw * 0.5
        sprite = game_pillar(pw, length, from_top=from_top)
        if sprite is not None:
            if not sun_on_left:        # the tile's own highlight is on the left
                sprite = sprite.transpose(Image.FLIP_LEFT_RIGHT)
        else:
            sprite = pillar(pw, length, artlib.shade(pal["pillar"], -0.26),
                            artlib.shade(pal["pillar"], 0.04), (255, 186, 132),
                            from_top=from_top, cap_h=38, shaft_w=int(pw * 0.80),
                            cap_w=int(pw * 0.98), sun_on_left=sun_on_left)
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        blit(lay, sprite, xpos, ypos)
        # sink the daylight tiles into the dusk with a graded scrim ...
        scrim = artlib.vertical_gradient(W, H, [
            (0.00, artlib.rgba((24, 16, 52), 86)), (1.00, artlib.rgba((12, 6, 30), 128))])
        scrim.putalpha(ImageChops.multiply(scrim.getchannel("A"), lay.getchannel("A")))
        lay = Image.alpha_composite(lay, scrim)
        # ... then put a warm rim back on the edge that faces the sun
        with supersample(1):
            dx = 3 if sun_on_left else -3
            lay = Image.alpha_composite(
                lay, directional_light(lay, dx, 0, 1.8, (255, 180, 126), 0.62))
        pieces.append(lay)

    # Nothing else in this frame has an outline, so a hard silhouette cut makes
    # the pillars read as stickers laid over the painting. Dissolving the edge
    # into the dusk haze -- a wide atmospheric halo plus a tight one, both
    # under the sprite -- is what puts them back inside it.
    with supersample(1):
        for i, lay in enumerate(pieces):
            out = Image.alpha_composite(out, soft_mask_layer(lay, pal["fog"], 9.0, 0.16))
            out = Image.alpha_composite(out, soft_mask_layer(lay, pal["fog"], 2.2, 0.26))
            # a matching inward band, so the cut dissolves from both sides.
            # (A clip_to() of a blurred mask would wash the whole shape and
            # flatten the tile's brickwork, which is the thing worth keeping.)
            pieces[i] = lay = artlib.inner_shadow(lay, blur=2.0, color=pal["fog"],
                                                  opacity=0.34)
            out = Image.alpha_composite(out, lay)

    # The left pillar runs down through the ground: without a contact pass the
    # two are just overlapping flat planes and the corner goes dead.
    with supersample(1):
        receiver = Image.alpha_composite(near_band, ground_band)
        out = Image.alpha_composite(
            out, occlusion(pieces[0], receiver, offset=(11, 4), blur=13, opacity=0.50))
    out = Image.alpha_composite(
        out, soft_disc(W, H, 0.030, 0.985, 0.085, INK, 0.40))

    # a few coins along the flight line, so the banner reads as the game
    coin_p = manifest.image_path("coin_0.png")
    if os.path.exists(coin_p):
        try:
            coin = Image.open(coin_p).convert("RGBA")
            for (cx, cy, sz, op) in ((0.742, 0.500, 42, 1.00),
                                     (0.792, 0.455, 36, 0.86),
                                     (0.839, 0.421, 29, 0.66)):
                c = resize_rgba(coin, (sz, sz))
                out = place(out, fade(c, op), W * cx, H * cy)
        except Exception:
            pass

    # a few feathers drifting on the far side of the frame, to balance the
    # weight of the hero and sell the "feather" idea without adding clutter
    with supersample(3):
        drift = white(W, H)
        for (x, y, ln, hw, ang, a) in ((0.200, 0.420, 36, 7.0, -26, 108),
                                       (0.302, 0.322, 27, 5.4, 14, 80),
                                       (0.140, 0.556, 22, 4.4, -40, 68),
                                       (0.374, 0.512, 18, 3.6, 8, 54)):
            import math as _m
            rad = _m.radians(ang)
            root = (W * x, H * y)
            tip = (root[0] + ln * _m.cos(rad), root[1] + ln * _m.sin(rad))
            feather(drift, root, tip, hw, (255, 214, 176, a), curve=0.14)
        out = Image.alpha_composite(out, resolve_rgba(drift, W, H))

    # hero: bird mid-flight with its feather trail, lifted by a warm halo
    out = Image.alpha_composite(out, soft_disc(W, H, 0.636, 0.610, 0.17,
                                               (255, 190, 140), 0.26))
    arcs, solid, bird_box = render_mark(420, ss=3, split=True)
    out = place(out, arcs, W * 0.640, H * 0.630, width=204, ref_box=bird_box, blend="screen")
    out = place(out, solid, W * 0.640, H * 0.630, width=204, ref_box=bird_box)

    # wordmark: sit it in the deep part of the sky, over a soft scrim so the
    # type never has to fight the gradient
    # load_logo already classifies the file; recomputing the test here with a
    # different threshold is how the tagline went missing.
    logo, is_lockup = load_logo(W * 0.54, H * 0.30)
    if logo is None:
        logo = wordmark(120, tracking=0.075, outline_px=1.2, shadow=True)
        k = (W * 0.56) / logo.width
        logo = resize_rgba(logo, (int(logo.width * k), int(logo.height * k)))
        is_lockup = False
    ly = H * 0.245 if is_lockup else H * 0.255
    out = Image.alpha_composite(out, soft_disc(W, H, 0.5, ly / H, 0.40, (24, 12, 48), 0.30))
    out = place(out, logo, W * 0.5, ly)

    if not is_lockup:
        # "ONE TAP" is the phrase this genre is searched for, so the tagline has
        # to survive the Play-search thumbnail, not just the full-size banner.
        tag, tw, th, tk = text_block(TAGLINE_TEXT, 30, tracking=0.26)
        with supersample(tk):
            tc = tint(tag, artlib.mix(CREAM, WARM, 0.22))
            tc = Image.alpha_composite(
                artlib.drop_shadow(tc, offset=(0, 1.6), blur=2.6, color=INK, opacity=0.80), tc)
            tag = resolve_rgba(tc, tw, th)
        ty = ly + logo.height * 0.5 + 30
        out = Image.alpha_composite(out, soft_disc(W, H, 0.5, ty / H, 0.22, (24, 12, 48), 0.26))
        out = place(out, tag, W * 0.5, ty, width=W * 0.335)

    out = Image.alpha_composite(out, vignette(W, H, strength=0.46, radius=0.86))
    return artlib.grain(out, strength=0.035, seed=31, cells=64)


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def check(path: str, size, opaque: bool) -> bool:
    img = Image.open(path)
    arr = np.asarray(img.convert("RGBA"), dtype=np.int16)
    exp = (size, size) if isinstance(size, int) else tuple(size)
    a = arr[:, :, 3]
    corners = [int(a[0, 0]), int(a[0, -1]), int(a[-1, 0]), int(a[-1, -1])]
    rows = [("size", img.size == exp, f"{img.size} expected {exp}"),
            ("mode", img.mode == "RGBA", img.mode)]
    if opaque:
        # full bleed: alpha is meant to be uniformly 255, so only RGB can vary
        rows.append(("corners opaque", all(c == 255 for c in corners), str(corners)))
        rows.append(("fully opaque", int(a.min()) == 255, f"min alpha {int(a.min())}"))
    else:
        rows.append(("corners clear", all(c == 0 for c in corners), str(corners)))
        rows.append(("alpha range", int(a.max()) - int(a.min()) > 200,
                     f"{int(a.min())}..{int(a.max())}"))
        rows.append(("alpha variance", float(a.std()) > 1.0, f"std={float(a.std()):.1f}"))
    vis = arr[:, :, :3][a > 8]
    var = float(vis.std()) if len(vis) else 0.0
    # a themed icon is pure white by design, so its RGB variance is legitimately 0
    rows.append(("rgb variance", var > 10.0 or "monochrome" in path, f"std={var:.1f}"))
    name = os.path.basename(path)
    ok = True
    for label, passed, detail in rows:
        ok = ok and bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<22} {label:<16} {detail}")
    return ok


JOBS = [
    ("icon_1024.png", build_icon, manifest.APP_ICON, True),
    ("adaptive_bg.png", build_adaptive_bg, manifest.ADAPTIVE_LAYER, True),
    ("adaptive_fg.png", build_adaptive_fg, manifest.ADAPTIVE_LAYER, False),
    ("icon_monochrome.png", build_monochrome, manifest.ADAPTIVE_LAYER, False),
    ("presplash.png", build_presplash, manifest.PRESPLASH, True),
    ("feature_graphic.png", build_feature, manifest.FEATURE_GRAPHIC, True),
]


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, fn, size, _ in JOBS:
        print(f"-> {name}")
        img = fn().convert("RGBA")
        exp = (size, size) if isinstance(size, int) else tuple(size)
        if img.size != exp:
            img = resize_rgba(img, exp)
        img.save(os.path.join(OUT_DIR, name))

    print("\nverification")
    all_ok = True
    for name, _, size, opaque in JOBS:
        all_ok &= check(os.path.join(OUT_DIR, name), size, opaque)

    R = manifest.ADAPTIVE_SAFE / 2.0
    c = manifest.ADAPTIVE_LAYER / 2.0
    for name in ("adaptive_fg.png", "icon_monochrome.png"):
        img = Image.open(os.path.join(OUT_DIR, name)).convert("RGBA")
        r = max_alpha_radius(img, c, c, thresh=8)
        inside = r <= R
        all_ok &= inside
        print(f"  [{'PASS' if inside else 'FAIL'}] {name:<22} safe circle      "
              f"max radius {r:.2f} of {R:.1f}  (fill {200 * r / manifest.ADAPTIVE_SAFE:.0f}%)")
        assert inside, f"{name} escapes the adaptive safe circle ({r:.2f} > {R})"

    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED'} -- "
          f"{len(JOBS)} files in {OUT_DIR}")


if __name__ == "__main__":
    main()
