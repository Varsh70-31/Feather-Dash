"""Generate Feather Dash's player character: 6 skins x 4 wing-cycle frames.

Everything here is computed from primitives in `artlib` -- no sampled, traced or
downloaded imagery is involved, so the sprites are an original work.

Construction order per frame (back to front):

    far wing -> tail -> crest -> body -> belly -> head shading -> cheek -> eye
    -> beak -> wing occlusion on the body -> near wing -> rim light
    -> bounce light -> outline -> (glow) -> contact shadow

The tail/crest/body/eye/beak stack is rendered ONCE per skin and re-used by all
four frames, so the animation can only ever move the wings: the character never
jitters. `--check` proves it by comparing the head rectangle across frames.

Run:  .venv/bin/python tools/gen_bird.py          (regenerates every sprite)
      .venv/bin/python tools/gen_bird.py --check  (verify only)
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import artlib
import manifest
import numpy as np
from PIL import Image, ImageChops, ImageFilter

SS = artlib.SS
W, H = manifest.BIRD_SIZE
SIZE = (W * SS, H * SS)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def catmull(pts, samples: int = 18, closed: bool = True):
    """Dense point list through `pts` -- keeps organic shapes from going lumpy."""
    p = [(float(x), float(y)) for x, y in pts]
    n = len(p)
    out: list[tuple[float, float]] = []
    span = n if closed else n - 1
    for i in range(span):
        if closed:
            p0, p1, p2, p3 = p[(i - 1) % n], p[i], p[(i + 1) % n], p[(i + 2) % n]
        else:
            p0 = p[i - 1] if i >= 1 else p[0]
            p1, p2 = p[i], p[i + 1]
            p3 = p[i + 2] if i + 2 < n else p[n - 1]
        for j in range(samples):
            t = j / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    if not closed:
        out.append(p[-1])
    return out


def expand(pts, d: float):
    """Push a closed point ring outward from its centroid by `d` px."""
    cx = sum(x for x, _ in pts) / len(pts)
    cy = sum(y for _, y in pts) / len(pts)
    out = []
    for x, y in pts:
        dx, dy = x - cx, y - cy
        n = math.hypot(dx, dy) or 1.0
        out.append((x + dx / n * d, y + dy / n * d))
    return out


def leaf(root, tip, half_w, bow=0.0, fat=0.46, blunt=0.0):
    """A feather / wing-lobe from `root` to `tip`; `blunt` rounds off the tip."""
    rx, ry = root
    tx, ty = tip
    dx, dy = tx - rx, ty - ry
    ln = math.hypot(dx, dy) or 1.0
    ux, uy = dx / ln, dy / ln
    nx, ny = -uy, ux
    mx = rx + dx * fat + nx * bow
    my = ry + dy * fat + ny * bow
    pts = [(rx, ry), (mx + nx * half_w, my + ny * half_w)]
    if blunt > 0.0:
        b = half_w * blunt
        pts.append((tx - ux * b * 1.15 + nx * b, ty - uy * b * 1.15 + ny * b))
        pts.append((tx, ty))
        pts.append((tx - ux * b * 1.15 - nx * b, ty - uy * b * 1.15 - ny * b))
    else:
        pts.append((tx, ty))
    pts.append((mx - nx * half_w, my - ny * half_w))
    return catmull(pts, samples=16)


# --------------------------------------------------------------------------
# raster helpers
# --------------------------------------------------------------------------

def new_layer() -> Image.Image:
    return artlib.canvas(W, H)


def solid(pts, color) -> Image.Image:
    lay = new_layer()
    artlib.poly(lay, pts, color)
    return lay


def blur(layer: Image.Image, radius: float) -> Image.Image:
    return layer.filter(ImageFilter.GaussianBlur(radius * SS)) if radius else layer


def fade(layer: Image.Image, amount: float) -> Image.Image:
    layer.putalpha(artlib.alpha_mask(layer).point(lambda v: int(v * amount)))
    return layer


def soft_ellipse(cx, cy, rx, ry, color, feather=3.0, rot=0.0) -> Image.Image:
    lay = new_layer()
    artlib.blob(lay, cx, cy, rx, ry, color, rotation=rot)
    return blur(lay, feather)


def band_gradient(y0: float, y1: float, stops) -> Image.Image:
    """Vertical gradient whose local 0..1 maps onto canvas rows y0..y1."""
    out = [(0.0, stops[0][1])]
    for p, c in stops:
        out.append((min(1.0, max(0.0, (y0 + (y1 - y0) * p) / H)), c))
    out.append((1.0, stops[-1][1]))
    return artlib.vertical_gradient(6, H * SS, out)


def shaded(pts, y0, y1, stops) -> Image.Image:
    return artlib.apply_gradient(solid(pts, (255, 255, 255, 255)), band_gradient(y0, y1, stops))


def vramp(y0: float, y1: float) -> Image.Image:
    """0 above `y0`, 255 below `y1`: used to dissolve an edge into its host."""
    col = np.clip((np.arange(H * SS) / SS - y0) / max(y1 - y0, 1e-6), 0.0, 1.0)
    arr = np.repeat((col * 255)[:, None], W * SS, axis=1)
    return Image.fromarray(arr.round().astype(np.uint8), "L")


_FALLOFF: dict[tuple, Image.Image] = {}


def directional_falloff(dx: float, dy: float, bias: float = 0.0) -> Image.Image:
    """0..255 ramp that fades a rim light away from the light direction."""
    key = (dx, dy, bias)
    if key not in _FALLOFF:
        yy, xx = np.mgrid[0:H * SS, 0:W * SS].astype(np.float64)
        u = (xx / (W * SS) - 0.5) * dx + (yy / (H * SS) - 0.5) * dy
        u = (u - u.min()) / max(u.max() - u.min(), 1e-9)
        u = np.clip(u + bias, 0.0, 1.0) ** 1.2
        _FALLOFF[key] = Image.fromarray((u * 255).round().astype(np.uint8), "L")
    return _FALLOFF[key]


def edge_light(shape: Image.Image, dx: float, dy: float, thickness: float,
               color, opacity: float, falloff: Image.Image | None = None,
               soften: float = 0.6) -> Image.Image:
    """A crescent of light hugging the edge of `shape` that faces (-dx, -dy)."""
    a = artlib.alpha_mask(shape)
    shifted = ImageChops.offset(a, artlib.s(dx * thickness), artlib.s(dy * thickness))
    edge = ImageChops.subtract(a, shifted)
    edge = blur(edge, soften) if soften else edge
    edge = ImageChops.multiply(edge, a)
    if falloff is not None:
        edge = ImageChops.multiply(edge, falloff)
    edge = edge.point(lambda v: int(v * opacity))
    lay = Image.new("RGBA", SIZE, artlib.rgba(color, 255))
    lay.putalpha(edge)
    return lay


def occlusion(caster: Image.Image, receiver: Image.Image, offset=(2.5, 4.0),
              radius: float = 3.0, color=(0, 0, 0), opacity: float = 0.38) -> Image.Image:
    """Soft contact shadow cast by `caster` onto `receiver`."""
    m = artlib.alpha_mask(caster)
    m = ImageChops.offset(m, artlib.s(offset[0]), artlib.s(offset[1]))
    m = blur(m, radius)
    m = ImageChops.multiply(m, artlib.alpha_mask(receiver))
    m = m.point(lambda v: int(v * opacity))
    lay = Image.new("RGBA", SIZE, artlib.rgba(color, 255))
    lay.putalpha(m)
    return lay


def silhouette_of(layer: Image.Image) -> Image.Image:
    """Hard-edged white stand-in for a layer, used as a clip / shadow caster."""
    m = artlib.alpha_mask(layer).point(lambda v: 255 if v > 110 else 0)
    out = Image.new("RGBA", SIZE, (255, 255, 255, 255))
    out.putalpha(m)
    return out


# --------------------------------------------------------------------------
# palette derivation
# --------------------------------------------------------------------------

INK_FLOOR = 18  # outlines are a very dark tint of the skin, never pure black


def palette(key, body, belly, wing, beak):
    body = artlib.rgba(body)
    belly = artlib.rgba(belly)
    wing = artlib.rgba(wing)
    beak = artlib.rgba(beak)

    ink = artlib.shade(artlib.saturate(body, 0.6), -0.68 if key == "shadow" else -0.80)
    ink = tuple(max(c, INK_FLOOR) for c in ink[:3]) + (255,)
    head = artlib.shade(body, 0.16)

    p = {
        "body": body,
        "body_top": artlib.shade(body, 0.18),
        "body_mid": body,
        "body_bot": artlib.shade(artlib.saturate(body, 1.22), -0.40),
        "head": head,
        "belly_hi": artlib.shade(belly, 0.12),
        "belly": belly,
        # the bib's shaded bottom. Pulled only a little way back towards the
        # body: at 0.62 the "pale" chest ended up darker than the body behind
        # it and the counter-shading vanished.
        "belly_lo": artlib.mix(belly, artlib.shade(artlib.saturate(body, 1.12), -0.22), 0.28),
        "wing_top": artlib.shade(artlib.saturate(wing, 1.08), 0.13),
        "wing": wing,
        "wing_bot": artlib.shade(artlib.saturate(wing, 1.16), -0.46),
        "wing_ink": artlib.mix(artlib.shade(artlib.saturate(wing, 0.95), -0.64), ink, 0.40),
        "beak_top": artlib.shade(beak, 0.20),
        "beak": beak,
        "beak_bot": artlib.shade(artlib.saturate(beak, 1.1), -0.26),
        "beak_low": artlib.shade(artlib.saturate(beak, 1.12), -0.46),
        "ink": ink,
        "tail": artlib.mix(wing, ink, 0.18),
        "tail_lo": artlib.mix(wing, ink, 0.38),
        # The far wing is a whole plane behind the tail, so it has to be read
        # as one. At 0.42/0.58 it landed on top of tail_lo (dE76 ~2.5) and the
        # left half of the bird fused into a single thicket of leaf shapes.
        "far": artlib.mix(wing, ink, 0.62),
        "far_lo": artlib.mix(wing, ink, 0.76),
        # ...which in turn needs the far wing's own feather edges pushed past
        # far_lo, or the behind-plane loses its internal structure.
        "far_ink": artlib.mix(wing, ink, 0.90),
        "iris": artlib.mix(artlib.saturate(wing, 0.8), (30, 32, 44, 255), 0.52),
        "pupil": artlib.mix(ink, (10, 11, 18, 255), 0.6),
        "sclera": (253, 252, 249, 255),
        "sclera_lo": artlib.mix((253, 252, 249, 255), artlib.saturate(body, 0.4), 0.42),
    }

    if key == "shadow":
        p["rim"] = (208, 230, 255, 255)
        p["rim_op"] = 0.95
        p["bounce"] = (150, 180, 232, 255)
        p["bounce_op"] = 0.55
        p["blush"] = (122, 164, 234, 255)
        p["glow"] = ((152, 192, 255), 4.6, 0.62)
    else:
        p["rim"] = (255, 243, 218, 255)
        p["rim_op"] = 0.85
        p["bounce"] = artlib.mix(belly, (255, 255, 255, 255), 0.38)
        p["bounce_op"] = 0.40
        p["blush"] = artlib.mix(belly, (255, 120, 112, 255), 0.58)
        p["glow"] = None
    return p


# --------------------------------------------------------------------------
# character geometry (target space, canvas is 200 x 168, centre 100, 84)
# --------------------------------------------------------------------------
# One continuous teardrop: narrow head up-right, plump body down-left, with a
# shallow concave nape so the head still reads as its own mass.
BODY_CTRL = [
    (110, 27),   # crown
    (137, 41),   # forehead
    (146, 66),   # cheek / beak root
    (143, 90),   # jaw
    (140, 112),  # chest
    (121, 133),  # lower chest
    (91, 141),   # belly bottom
    (59, 132),   # rump
    (42, 107),   # back of body
    (41, 80),    # back
    (60, 57),    # nape (pulled in)
    (82, 34),    # back of head
]
BODY_Y0, BODY_Y1 = 25.0, 142.0

# pale throat -> chest -> belly, the classic bird counter-shading
BELLY_CTRL = [
    (127, 86), (132, 104), (127, 121), (108, 132),
    (86, 135), (69, 126), (66, 110), (80, 97), (105, 89),
]
# The bib's shading band is DERIVED from the shape, never typed by hand: a
# hand-typed band once started 14 px above the bib, which parked the pale
# `belly_hi` stop on rows the shape does not occupy and left only the dark end
# of the ramp visible. Deriving it means the two can never drift apart again.
BELLY_Y0 = min(y for _, y in BELLY_CTRL)   # 86
BELLY_Y1 = max(y for _, y in BELLY_CTRL)   # 135
# Counter-shading has no hard upper border, so the bib dissolves into the
# chest -- but only across its top few rows. A wider window eats the pale zone.
BIB_FADE = (84.0, 93.0)

EYE_C = (117.0, 53.0)
EYE_RX, EYE_RY = 16.2, 17.2

# the character is authored where it draws most naturally; this recentres the
# finished sprite in the frame so rotation in game has even padding all round.
# The wing cycle now reaches further above the back than the crest does, so the
# whole character sits slightly lower than it used to.
SHIFT = (5, 2)

# Up and forward of the old (86, 82). A pivot buried in the middle of the body
# lets the wing sweep *inside* its own outline; from here the upstroke clears
# the crown and the downstroke clears the belly.
SHOULDER = (86.5, 77.5)
FAR_SHOULDER = (78.5, 73.5)

CREST = [
    ((105, 32), (84, 19), 6.2, 2.4),
    ((96, 34), (73, 28), 5.4, 1.8),
]

TAIL = [
    ((62, 92), (20, 77), 10.4, 2.2),
    ((63, 102), (15, 97), 12.0, 0.0),
    ((62, 110), (25, 116), 10.4, -2.2),
]

BEAK_UPPER = [(130, 59), (150, 60.5), (166, 66), (175, 72), (157, 78), (134, 79)]
BEAK_LOWER = [(133, 80.5), (157, 79.5), (171, 75.5), (158, 88.5), (138, 91)]


# --------------------------------------------------------------------------
# wings
# --------------------------------------------------------------------------
#  Local wing frame: +x points backwards (towards the tail), +y points down.
#  `angle` > 0 swings the tip down, < 0 swings it up.

def wing_parts(length, width):
    """Three fanned primaries, three greater coverts over their roots, and a
    lesser-covert lobe on top: feather groups, the way a real wing stacks.

    The coverts used to be a single closed ring, which at the 96 px draw width
    collapsed into a bald rounded lobe -- a mitten, not a wing. They are built
    as overlapping leaves now so the group boundaries are real drawn edges.
    """
    L, Wd = length, width
    primaries = [
        ((L * 0.24, -Wd * 0.22), (L * 1.00, -Wd * 0.30), Wd * 0.165, 1.7),
        ((L * 0.24, -Wd * 0.06), (L * 1.00, Wd * 0.02), Wd * 0.175, 1.1),
        ((L * 0.22, Wd * 0.12), (L * 0.91, Wd * 0.36), Wd * 0.170, 0.4),
    ]
    # Trailing group first; each later one overlaps it towards the leading
    # edge. They start a little way out from the joint rather than on top of
    # it, because the lesser-covert cap is what covers the shoulder itself --
    # which also keeps the mass off the cheek when the wing swings up.
    coverts = [
        ((L * 0.09, Wd * 0.14), (L * 0.56, Wd * 0.24), Wd * 0.210, 0.4),
        ((L * 0.09, Wd * 0.00), (L * 0.64, -Wd * 0.04), Wd * 0.225, 0.9),
        ((L * 0.11, -Wd * 0.18), (L * 0.58, -Wd * 0.40), Wd * 0.215, 1.5),
    ]
    lesser = [
        (0.0, -Wd * 0.26), (L * 0.14, -Wd * 0.47), (L * 0.37, -Wd * 0.36),
        (L * 0.33, -Wd * 0.09), (L * 0.05, Wd * 0.08),
    ]
    return primaries, coverts, lesser


# each covert group catches the upper-left key light a little differently
COVERT_TINT = (-0.09, -0.02, 0.06)


def build_wing(p, angle, length, width, near=True):
    """Shaded wing layer plus its hard silhouette (for shadows and rim light)."""
    pivot = SHOULDER if near else FAR_SHOULDER
    ca = math.cos(math.radians(angle))
    sa = math.sin(math.radians(angle))
    px, py = pivot

    def to_world(pts):
        return [(px - x * ca + y * sa, py + x * sa + y * ca) for x, y in pts]

    primaries, coverts, lesser = wing_parts(length, width)
    if near:
        ink = p["wing_ink"]
        g_cov = [(0.0, p["wing_top"]), (0.55, p["wing"]), (1.0, p["wing_bot"])]
        g_pri = [(0.0, artlib.mix(p["wing"], p["wing_bot"], 0.25)), (1.0, p["wing_bot"])]
    else:
        ink = p["far_ink"]
        g_cov = [(0.0, p["far"]), (1.0, p["far_lo"])]
        g_pri = [(0.0, p["far_lo"]), (1.0, artlib.mix(p["far_lo"], p["far_ink"], 0.5))]

    grow = 2.1 if near else 1.5
    lay = new_layer()
    sil = new_layer()

    def rings_of(local_pts):
        """World-space feather outline plus the grown ring that inks its edge."""
        world = to_world(local_pts)
        return (catmull(world, samples=16),
                catmull(expand(world, grow), samples=16))

    def shade_fill(ring, halo, grad_stops):
        ys = [y for _, y in halo]
        return artlib.apply_gradient(solid(ring, (255, 255, 255, 255)),
                                     band_gradient(min(ys), max(ys), grad_stops))

    # -- primaries: a fan, inked as one group ------------------------------
    fills = []
    for i, (root, tip, hw, bow) in enumerate(reversed(primaries)):
        ring, halo = rings_of(leaf(root, tip, hw, bow, fat=0.54, blunt=0.8))
        artlib.poly(lay, halo, ink)
        artlib.poly(sil, halo, (255, 255, 255, 255))
        tint = 0.16 * (len(primaries) - 1 - i) / max(1, len(primaries) - 1)
        fills.append(shade_fill(ring, halo, [(q, artlib.shade(c, -tint)) for q, c in g_pri]))
    lay = artlib.over(lay, *fills)

    # -- greater coverts: laid down one at a time, trailing group first, so
    #    each one's ink edge and contact shadow land ON TOP of the group
    #    behind it. Those two interior edges are the whole difference between
    #    a wing and a mitten once the sprite is down at its drawn size.
    cov_shape = None
    for (root, tip, hw, bow), tint in zip(coverts, COVERT_TINT):
        ring, halo = rings_of(leaf(root, tip, hw, bow, fat=0.50, blunt=0.75))
        artlib.poly(sil, halo, (255, 255, 255, 255))
        edge = solid(halo, ink)
        fill = shade_fill(ring, halo, [(q, artlib.shade(c, tint)) for q, c in g_cov])
        lay = artlib.over(lay, occlusion(edge, silhouette_of(lay), offset=(1.8, 3.0),
                                         radius=1.8, color=ink, opacity=0.55))
        lay = artlib.over(lay, edge, fill)
        shp = silhouette_of(fill)
        cov_shape = shp if cov_shape is None else artlib.over(cov_shape, shp)

    # -- lesser covert: the shoulder cap, on top of the lot -----------------
    ring, halo = rings_of(catmull(lesser, samples=16))
    artlib.poly(sil, halo, (255, 255, 255, 255))
    edge = solid(halo, ink)
    les_fill = shade_fill(ring, halo, [(q, artlib.shade(c, 0.05)) for q, c in g_cov])
    les_shape = silhouette_of(les_fill)
    lay = artlib.over(lay, occlusion(edge, cov_shape, offset=(1.6, 2.6),
                                     radius=1.6, color=ink, opacity=0.50))
    lay = artlib.over(lay, edge, les_fill)

    if near:
        # shoulder root sinks into the body
        root_sh = soft_ellipse(px + 2.0 * sa, py + 2.0 * ca, width * 0.34, width * 0.42,
                               artlib.rgba(ink, 255), 3.0, rot=-angle)
        lay = artlib.over(lay, fade(artlib.clip_to(root_sh, les_shape), 0.44))

    shape = silhouette_of(sil)
    fall = directional_falloff(-1.0, -1.0, bias=0.06)
    lay = artlib.over(lay, edge_light(les_shape, 1.0, 1.0, 1.7, p["rim"],
                                      (0.52 if near else 0.22) * p["rim_op"], fall, 0.5))
    lay = artlib.over(lay, edge_light(cov_shape, 1.0, 1.0, 1.9, p["rim"],
                                      (0.66 if near else 0.26) * p["rim_op"], fall, 0.5))
    lay = artlib.over(lay, edge_light(shape, 1.0, 1.0, 1.6, p["rim"],
                                      (0.45 if near else 0.20) * p["rim_op"], fall, 0.5))
    return lay, shape


# --------------------------------------------------------------------------
# tail + crest
# --------------------------------------------------------------------------

def build_plumes(p, specs, base, low, grow=1.7):
    lay = new_layer()
    sil = new_layer()
    order = [1, 0, 2] if len(specs) == 3 else list(range(len(specs)))
    fills = []
    for i in order:
        root, tip, hw, bow = specs[i]
        ring = leaf(root, tip, hw, bow, blunt=0.45)
        artlib.poly(lay, expand(ring, grow), artlib.mix(low, p["ink"], 0.45))
        artlib.poly(sil, expand(ring, grow), (255, 255, 255, 255))
        ys = [y for _, y in ring]
        col = base if i == (1 if len(specs) == 3 else 0) else low
        fills.append(artlib.apply_gradient(
            solid(ring, (255, 255, 255, 255)),
            band_gradient(min(ys), max(ys),
                          [(0.0, artlib.shade(col, 0.16)), (1.0, artlib.shade(col, -0.18))])))
    lay = artlib.over(lay, *fills)

    quill = new_layer()
    qd = artlib.draw_on(quill)
    for root, tip, _hw, _bow in specs:
        qd.line([(artlib.s(root[0]), artlib.s(root[1])), (artlib.s(tip[0]), artlib.s(tip[1]))],
                fill=artlib.rgba(p["ink"], 80), width=artlib.s(1.2))
    lay = artlib.over(lay, artlib.clip_to(blur(quill, 0.4), silhouette_of(lay)))
    fall = directional_falloff(-1.0, -1.0, bias=0.06)
    lay = artlib.over(lay, edge_light(silhouette_of(sil), 1.0, 1.0, 1.5,
                                      p["rim"], 0.40 * p["rim_op"], fall, 0.5))
    return lay


# --------------------------------------------------------------------------
# head furniture
# --------------------------------------------------------------------------

def build_eye(p) -> Image.Image:
    cx, cy = EYE_C
    lay = new_layer()

    # dark socket ring doubles as the eye's own outline
    artlib.blob(lay, cx, cy, EYE_RX + 1.5, EYE_RY + 1.5,
                artlib.mix(p["ink"], p["body_bot"], 0.20))

    sclera = new_layer()
    artlib.blob(sclera, cx, cy, EYE_RX, EYE_RY, (255, 255, 255, 255))
    lay = artlib.over(lay, artlib.apply_gradient(sclera, band_gradient(
        cy - EYE_RY, cy + EYE_RY,
        [(0.0, artlib.mix(p["sclera_lo"], p["ink"], 0.16)), (0.32, p["sclera"]),
         (0.80, p["sclera"]), (1.0, p["sclera_lo"])])))

    # iris sits forward and a little low -- the bird is looking where it flies
    ix, iy = cx + 2.6, cy + 1.9
    iris = new_layer()
    artlib.blob(iris, ix, iy, 11.8, 12.2, p["iris"])
    artlib.blob(iris, ix, iy + 2.8, 10.4, 9.2, artlib.shade(p["iris"], 0.18))
    lay = artlib.over(lay, artlib.clip_to(iris, sclera))

    pup = new_layer()
    artlib.blob(pup, ix + 0.3, iy + 0.2, 7.8, 8.2, p["pupil"])
    lay = artlib.over(lay, artlib.clip_to(pup, sclera))

    # exactly one bright specular, up-left, plus a whisper of bounce
    spec = new_layer()
    artlib.blob(spec, ix - 4.8, iy - 5.6, 4.4, 4.0, (255, 255, 255, 255), rotation=-30)
    lay = artlib.over(lay, artlib.clip_to(spec, sclera))
    tiny = new_layer()
    artlib.blob(tiny, ix - 9.2, iy + 1.4, 1.8, 2.5, (255, 255, 255, 210), rotation=-30)
    lay = artlib.over(lay, artlib.clip_to(tiny, sclera))
    bnc = new_layer()
    artlib.blob(bnc, ix + 4.6, iy + 5.6, 3.0, 2.3, (255, 255, 255, 56))
    lay = artlib.over(lay, artlib.clip_to(blur(bnc, 0.5), sclera))

    # upper eyelid: a wide, shallow slice of head over the top of the eye, so
    # the lid line crosses the eye almost straight instead of pinching at the
    # corners. Tilted up at the front -> alert rather than sleepy.
    def lid_plate(dy, color):
        plate = new_layer()
        artlib.blob(plate, cx - 1.4, cy - EYE_RY * 1.62 + dy,
                    EYE_RX * 1.62, EYE_RY * 0.96, color, rotation=-8)
        return artlib.clip_to(plate, sclera)

    lid = lid_plate(0.0, p["head"])
    lay = artlib.over(lay, lid)

    lidline = lid_plate(1.5, artlib.mix(p["ink"], p["body_bot"], 0.28))
    lidline.putalpha(ImageChops.subtract(artlib.alpha_mask(lidline), artlib.alpha_mask(lid)))
    lay = artlib.over(lay, lidline)

    # the lid throws a short shadow down the sclera
    ao = lid_plate(4.6, artlib.rgba(p["ink"], 255))
    ao = blur(ao, 1.4)
    ao.putalpha(ImageChops.multiply(
        ImageChops.subtract(artlib.alpha_mask(ao), artlib.alpha_mask(lid)),
        artlib.alpha_mask(sclera)))
    lay = artlib.over(lay, fade(ao, 0.20))
    return lay


def build_beak(p) -> Image.Image:
    low = shaded(catmull(BEAK_LOWER, samples=18), 76, 91,
                 [(0.0, p["beak_bot"]), (1.0, p["beak_low"])])
    low = artlib.inner_shadow(low, blur=1.0, color=artlib.shade(p["beak_low"], -0.45),
                              opacity=0.5)

    up = shaded(catmull(BEAK_UPPER, samples=18), 58, 80,
                [(0.0, p["beak_top"]), (0.52, p["beak"]), (1.0, p["beak_bot"])])
    upshape = solid(catmull(BEAK_UPPER, samples=18), (255, 255, 255, 255))
    nostril = new_layer()
    artlib.blob(nostril, 145.0, 68.0, 2.3, 1.8,
                artlib.rgba(artlib.shade(p["beak_low"], -0.4), 185), rotation=12)
    up = artlib.over(up, artlib.clip_to(blur(nostril, 0.35), upshape))
    up = artlib.over(up, edge_light(upshape, 1.0, 1.0, 1.5, p["rim"], 0.60 * p["rim_op"],
                                    directional_falloff(-1.0, -1.0, 0.10), soften=0.4))

    seam = new_layer()
    artlib.draw_on(seam).line(
        [(artlib.s(x), artlib.s(y)) for x, y in
         catmull([(135, 78.8), (156, 78.6), (172, 74.8)], samples=14, closed=False)],
        fill=artlib.rgba(artlib.shade(p["beak_low"], -0.5), 165), width=artlib.s(1.4))
    seam = blur(seam, 0.35)

    both = artlib.over(low, up)
    return artlib.over(low, up, artlib.clip_to(seam, both))


# --------------------------------------------------------------------------
# the constant part of the character
# --------------------------------------------------------------------------

def build_midstack(p):
    ring = catmull(BODY_CTRL, samples=20)
    sil = solid(ring, (255, 255, 255, 255))

    body = artlib.apply_gradient(sil, band_gradient(
        BODY_Y0, BODY_Y1,
        [(0.0, p["body_top"]), (0.26, p["head"]), (0.52, p["body_mid"]), (1.0, p["body_bot"])]))

    # pale counter-shaded belly with a crisp edge, graded by its own form
    belly_ring = catmull(BELLY_CTRL, samples=20)
    belly = artlib.apply_gradient(solid(belly_ring, (255, 255, 255, 255)), band_gradient(
        BELLY_Y0, BELLY_Y1,
        [(0.0, p["belly_hi"]), (0.42, p["belly"]), (1.0, p["belly_lo"])]))
    belly = artlib.clip_to(blur(belly, 1.15), sil)
    belly.putalpha(ImageChops.multiply(artlib.alpha_mask(belly), vramp(*BIB_FADE)))
    body = artlib.over(body, fade(belly, 0.94))
    belly_sil = silhouette_of(belly)

    # the body overhangs the belly patch very slightly
    body = artlib.over(body, fade(occlusion(sil, belly_sil, offset=(-1.0, -2.2), radius=2.4,
                                            color=p["body_bot"], opacity=1.0), 0.20))

    # jaw / head occlusion onto the chest (light is upper-left)
    jaw = artlib.clip_to(soft_ellipse(124, 96, 26, 11, p["body_bot"], feather=3.2, rot=-16), sil)
    body = artlib.over(body, fade(jaw, 0.34))

    # nape crease
    nape = artlib.clip_to(soft_ellipse(69, 58, 17, 6.5, p["body_bot"], feather=2.4, rot=-44), sil)
    body = artlib.over(body, fade(nape, 0.34))

    body = artlib.inner_shadow(body, blur=1.9, color=artlib.mix(p["ink"], p["body_bot"], 0.4),
                               opacity=0.46)

    blush = artlib.clip_to(soft_ellipse(100, 76, 13.0, 8.4, p["blush"], feather=3.4, rot=-8), sil)
    body = artlib.over(body, fade(blush, 0.40))

    tail = build_plumes(p, TAIL, p["tail"], p["tail_lo"])
    crest = build_plumes(p, CREST, p["tail"], p["tail_lo"], grow=1.5)
    plumes = artlib.over(tail, crest)
    # the body shades the roots of the plumes that emerge from behind it
    plumes = artlib.over(plumes, occlusion(sil, silhouette_of(plumes), offset=(-1.5, 0.5),
                                           radius=3.2, color=p["ink"], opacity=0.50))

    beak = build_beak(p)
    # ...and the beak drops a short shadow onto the throat
    body = artlib.over(body, occlusion(silhouette_of(beak), sil, offset=(0.5, 3.0),
                                       radius=2.4,
                                       color=artlib.mix(p["ink"], p["body_bot"], 0.3),
                                       opacity=0.34))

    stack = artlib.over(plumes, body, build_eye(p), beak)
    return stack, sil


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------
# A flap is only an animation if it changes the OUTLINE. The previous cycle
# swung the wing backwards along the body, where it stayed inside the existing
# silhouette (frame 0 broke it by 0.0%) and the beat survived only as a dark
# smear sliding about inside the bird -- the first thing to be lost against a
# moving parallax. These poses arc the wing above the back and below the belly
# instead; `check()` holds the resulting silhouette change to MIN_FRAME_XOR.
#                angle   len   width
WING_POSES = [
    (-24.0, 78.0, 42.0),   # 0 level, extended -- just over the top, coming down
    (48.0, 86.0, 48.0),    # 1 downstroke: longest, swept down past the belly
    (-14.0, 74.0, 46.0),   # 2 level again, broader, on the way up
    (-70.0, 60.0, 32.0),   # 3 upstroke: folded narrow, raised clear of the back
]
# The far wing lags about 22 degrees behind the near one and is ~3/4 its size,
# so the two never stack into one shape; it reads as the opposite wing seen
# past the body rather than a dark echo of the near one.
FAR_POSES = [
    (-46.0, 59.0, 32.0),
    (26.0, 65.0, 36.0),
    (-36.0, 56.0, 35.0),
    (-86.0, 46.0, 24.0),
]


def build_frame(p, midstack, sil, frame):
    far_wing, _far_shape = build_wing(p, *FAR_POSES[frame], near=False)
    near_wing, near_shape = build_wing(p, *WING_POSES[frame], near=True)

    art = artlib.over(far_wing, midstack)
    art = artlib.over(art, occlusion(near_shape, sil, offset=(3.0, 4.5), radius=3.2,
                                     color=artlib.mix(p["ink"], p["body_bot"], 0.35),
                                     opacity=0.52))
    art = artlib.over(art, near_wing)

    art = artlib.over(art, edge_light(art, 1.0, 1.0, 2.4, p["rim"], p["rim_op"],
                                      directional_falloff(-1.0, -1.0, 0.04), 0.6))
    art = artlib.over(art, edge_light(art, -1.0, -1.0, 1.8, p["bounce"], p["bounce_op"],
                                      directional_falloff(0.35, 1.0, -0.06), 0.8))

    art = artlib.outline(art, width=1.4, color=p["ink"])

    # Rotation-invariant on purpose: the game spins this sprite from -26 to
    # +104 degrees, so a directional cast shadow baked in here would point
    # up-left at the terminal dive while the sun and every world layer still
    # light the scene from the upper left. A centred ambient-occlusion halo
    # still lifts the bird off the background but cannot contradict it.
    shadow = artlib.drop_shadow(art, offset=(0.0, 0.0), blur=3.6,
                                color=artlib.mix(p["ink"], (0, 0, 0, 255), 0.55), opacity=0.30)
    art = Image.alpha_composite(shadow, art)

    if p["glow"]:
        col, rad, op = p["glow"]
        art = artlib.glow(art, radius=rad, color=col, opacity=op)

    centred = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    centred.paste(art, (artlib.s(SHIFT[0]), artlib.s(SHIFT[1])))
    return centred


def generate() -> list[str]:
    manifest.ensure_dirs()
    written = []
    for idx, (key, _name, body, belly, wing, beak, _cost) in enumerate(manifest.BIRD_SKINS):
        p = palette(key, body, belly, wing, beak)
        midstack, sil = build_midstack(p)
        for frame in range(manifest.BIRD_FRAMES):
            art = build_frame(p, midstack, sil, frame)
            out = artlib.resolve(art, W, H)
            # one grain seed per skin -> the texture cannot shimmer between frames
            out = artlib.grain(out, strength=0.045, seed=1000 + idx, cells=7)
            path = manifest.image_path(f"bird_{key}_{frame}.png")
            out.save(path)
            written.append(path)
    return written


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

# Head + eye + beak: must be bit-identical in every frame. The left edge stops
# at 121 because the near wing's contact shadow on the body legitimately moves
# with the wing and feathers a couple of levels onto the body as far as column
# 120; everything that makes the character a character -- iris, pupil, both
# speculars, lid, lid line, lid AO, the whole beak -- is inside.
CONST_BOX = (121, 16, 197, 90)
MIN_PAD = 8          # visible pixels must stay this far inside the frame
MIN_FRAME_XOR = 0.12  # silhouette change between consecutive wing frames
MIN_BIB_WCAG = 1.5    # the counter-shaded bib against the body behind it
MIN_SKY_DE = 20.0     # body colour vs every theme's own sky, CIELAB dE76
MIN_SKY_DE_GLOW = 14.0  # ...relaxed for a skin that ships its own glow + rim
FLIGHT_BAND = (100, 900)  # the rows of the 1080-tall sky the bird actually flies in


# -- colour metrics ---------------------------------------------------------

def _lab(c):
    v = np.asarray(artlib.rgba(c)[:3], dtype=np.float64) / 255.0
    v = np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = (m @ v) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def de76(a, b) -> float:
    return float(np.linalg.norm(_lab(a) - _lab(b)))


def wcag(a, b) -> float:
    def lum(c):
        v = np.asarray(artlib.rgba(c)[:3], dtype=np.float64) / 255.0
        v = np.where(v <= 0.03928, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)
        return float(0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2])
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ramp(stops, t):
    """Colour of a (position, colour) ramp at `t` in 0..1."""
    stops = sorted(stops, key=lambda it: it[0])
    pos = [q for q, _ in stops]
    cols = [artlib.rgba(c) for _, c in stops]
    return tuple(float(np.interp(t, pos, [c[i] for c in cols])) for i in range(3))


def bib_contrast(p, y: float = (BELLY_Y0 + BELLY_Y1) / 2.0) -> tuple:
    """What the belly actually reads as against the body, at height `y`.

    Models the three things that between them once erased the bib: which rows
    of the shading band the shape occupies, how far `belly_lo` is pulled back
    towards the body, and how much of the pale zone the top-edge dissolve eats.
    """
    body = ramp([(0.0, p["body_top"]), (0.26, p["head"]),
                 (0.52, p["body_mid"]), (1.0, p["body_bot"])],
                (y - BODY_Y0) / (BODY_Y1 - BODY_Y0))
    bib = ramp([(0.0, p["belly_hi"]), (0.42, p["belly"]), (1.0, p["belly_lo"])],
               (y - BELLY_Y0) / (BELLY_Y1 - BELLY_Y0))
    a = min(1.0, max(0.0, (y - BIB_FADE[0]) / (BIB_FADE[1] - BIB_FADE[0]))) * 0.94
    seen = tuple(body[i] + (bib[i] - body[i]) * a for i in range(3))
    return wcag(seen, body), seen, body


def check_palettes() -> int:
    """Colour relationships that no amount of good drawing can rescue."""
    bad = 0
    for key, _name, body, belly, wing, beak, _cost in manifest.BIRD_SKINS:
        p = palette(key, body, belly, wing, beak)

        c, seen, behind = bib_contrast(p)
        if c < MIN_BIB_WCAG:
            print(f"BIB-FLAT {key}: {tuple(round(v) for v in seen)} on "
                  f"{tuple(round(v) for v in behind)} = WCAG {c:.2f} < {MIN_BIB_WCAG}")
            bad += 1

        # The behind-plane has to separate from the tail. `far` sits a fixed
        # 0.24 further down the same wing->ink ramp than `tail_lo` does, which
        # buys ~0.23x the ramp's own dE76 -- so 12 is the readability goal and
        # the proportional term is all a compressed, near-monochrome skin like
        # Shadow physically has to give.
        floor = min(12.0, 0.22 * de76(p["wing"], p["ink"]))
        d = de76(p["far"], p["tail_lo"])
        if d < floor:
            print(f"FAR-FLAT {key}: far vs tail_lo dE76 {d:.1f} < {floor:.1f}")
            bad += 1

        # a bird the colour of the sky it flies in is carried only by its
        # outline, which is the first thing a busy background eats
        limit = MIN_SKY_DE_GLOW if p["glow"] else MIN_SKY_DE
        for theme in manifest.THEMES:
            stops = manifest.THEME_PALETTE[theme]["sky"]
            worst, worst_y = 1e9, 0
            for y in range(FLIGHT_BAND[0], FLIGHT_BAND[1] + 1, 4):
                d = de76(p["body_mid"], ramp(stops, y / manifest.WORLD_H))
                if d < worst:
                    worst, worst_y = d, y
            if worst < limit:
                print(f"SKY-CAMOUFLAGE {key} on {theme}: min dE76 {worst:.1f} "
                      f"at y={worst_y} < {limit}")
                bad += 1
    return bad


def check() -> int:
    bad = check_palettes()
    for key, *_ in manifest.BIRD_SKINS:
        crops = []
        alphas = []
        for frame in range(manifest.BIRD_FRAMES):
            path = manifest.image_path(f"bird_{key}_{frame}.png")
            name = os.path.basename(path)
            if not os.path.exists(path):
                print(f"MISSING {path}")
                bad += 1
                continue
            im = Image.open(path)
            if im.mode != "RGBA":
                print(f"MODE {name} = {im.mode}")
                bad += 1
            if im.size != tuple(manifest.BIRD_SIZE):
                print(f"SIZE {name} = {im.size} != {manifest.BIRD_SIZE}")
                bad += 1
            a = np.asarray(im.convert("RGBA"))
            al = a[:, :, 3]
            corners = [int(al[0, 0]), int(al[0, -1]), int(al[-1, 0]), int(al[-1, -1])]
            if max(corners) != 0:
                print(f"CORNER-ALPHA {name} = {corners}")
                bad += 1
            if al.max() < 250 or al.min() != 0:
                print(f"ALPHA-RANGE {name} = {al.min()}..{al.max()}")
                bad += 1
            opaque = a[al > 200][:, :3].astype(np.float64)
            cov = opaque.shape[0] / float(al.size)
            if opaque.shape[0] < 500 or opaque.std() < 12:
                print(f"FLAT {name} std={opaque.std():.1f} n={opaque.shape[0]}")
                bad += 1
            if not (0.10 < cov < 0.70):
                print(f"COVERAGE {name} = {cov:.3f}")
                bad += 1
            # the sprite is rotated in game, so it must not reach the frame edge
            if max(int(al[0].max()), int(al[-1].max()),
                   int(al[:, 0].max()), int(al[:, -1].max())) > 2:
                print(f"EDGE-BLEED {name}")
                bad += 1
            ys, xs = np.nonzero(al >= 24)
            pads = (int(xs.min()), W - 1 - int(xs.max()),
                    int(ys.min()), H - 1 - int(ys.max()))
            if min(pads) < MIN_PAD:
                print(f"PADDING {name} l/r/t/b = {pads} (min {MIN_PAD})")
                bad += 1
            crops.append(np.asarray(im.crop(CONST_BOX)))
            alphas.append(al > 110)
        for frame in range(1, len(crops)):
            if not np.array_equal(crops[0], crops[frame]):
                diff = int(np.abs(crops[0].astype(int) - crops[frame].astype(int)).max())
                print(f"HEAD-JITTER {key} frame {frame}: max delta {diff}")
                bad += 1
        # ...and the exact opposite guarantee for the wings: the head may not
        # move at all, but the silhouette MUST. Without this a future re-pose
        # can quietly flatten the flap back into a smear inside the outline.
        for a, b in zip(range(len(alphas)), list(range(1, len(alphas))) + [0]):
            area = (alphas[a].sum() + alphas[b].sum()) / 2.0
            x = float(np.logical_xor(alphas[a], alphas[b]).sum()) / max(area, 1.0)
            if x < MIN_FRAME_XOR:
                print(f"FLAT-FLAP {key} frames {a}->{b}: silhouette XOR "
                      f"{x * 100:.1f}% < {MIN_FRAME_XOR * 100:.0f}%")
                bad += 1
    print("FAIL" if bad else f"OK  {len(manifest.BIRD_SKINS) * manifest.BIRD_FRAMES} "
                             f"sprites verified ({W}x{H} RGBA)")
    return bad


if __name__ == "__main__":
    if "--check" not in sys.argv:
        files = generate()
        print(f"wrote {len(files)} sprites to {manifest.IMAGES}")
    raise SystemExit(1 if check() else 0)
