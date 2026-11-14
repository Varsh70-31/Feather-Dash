"""Generate Feather Dash's complete interface kit.

Everything here is computed from primitives -- no sampled, traced or downloaded
imagery is involved -- so the output is an original work the project owns.

Three rendering strategies are used, each chosen for a reason:

* **Panels and buttons** are rendered from an analytic rounded-rectangle signed
  distance field, evaluated on a 4x sub-grid and box-reduced.  The shading is
  deliberately *separable*: every vertical variation comes from a 1-D column
  profile, every horizontal variation from a 1-D row profile, and the only 2-D
  term is the SDF itself, which is provably invariant along the stretch axis
  inside each 9-slice edge strip.  That is what makes the 9-slice stretch
  artifact-free, and ``verify_nine_slice`` proves it on the finished PNG.
* **Icons** are drawn with Pillow on an ``artlib`` supersampled canvas.  Each
  glyph is rendered twice: once to measure its natural extent and ink centroid,
  then again through a fitted transform, so the whole set shares one optical
  size, one optical centre and one stroke weight.
* **Medals and the logo** are shaded in floating point with analytic surface
  normals, so the bevelled ring and the recessed field get real diffuse +
  specular response rather than a painted-on gradient.

Run with the project venv::

    .venv/bin/python tools/gen_ui.py

The script is idempotent: it rewrites every file it owns on each run.
"""
from __future__ import annotations

import functools
import math
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artlib  # noqa: E402
import manifest  # noqa: E402

SS = artlib.SS  # 4


def _find_font(name: str = "BaiJamjuree-Bold.ttf") -> str:
    """The project font, wherever it currently lives in the tree."""
    for d in (manifest.FONTS, os.path.join(manifest.ROOT, "Fonts"),
              os.path.join(manifest.ROOT, "assets", "fonts")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"{name} not found under {manifest.ROOT}")


FONT_PATH = _find_font()

# The kit is theme-neutral: a deep indigo slate for surfaces and a warm amber
# for the primary action, which sits comfortably on all four sky themes (whose
# own `accent` colours are warm oranges and ambers).
LIGHT_DIR = (-0.46, -1.0)  # surfaces facing this way catch the key light


def _norm2(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


LKEY = _norm2(LIGHT_DIR)


# ---------------------------------------------------------------------------
# float compositing helpers
# ---------------------------------------------------------------------------

def c(*rgba_255) -> np.ndarray:
    """Colour literal in 0..255 (alpha optional) -> float array in 0..1."""
    if len(rgba_255) == 3:
        rgba_255 = rgba_255 + (255,)
    return np.array(rgba_255, dtype=np.float64) / 255.0


def grid(w: int, h: int, ss: int = SS):
    """Pixel-centre coordinates, in *target* space, on an ss x ss sub-grid."""
    xs = (np.arange(w * ss) + 0.5) / ss
    ys = (np.arange(h * ss) + 0.5) / ss
    return xs, ys


def blank(w: int, h: int, ss: int = SS) -> np.ndarray:
    return np.zeros((h * ss, w * ss, 4), dtype=np.float64)


def over(dst: np.ndarray, src: np.ndarray) -> np.ndarray:
    """Straight-alpha source-over in float."""
    sa, da = src[..., 3:4], dst[..., 3:4]
    oa = sa + da * (1.0 - sa)
    safe = np.where(oa > 1e-9, oa, 1.0)
    rgb = (src[..., :3] * sa + dst[..., :3] * da * (1.0 - sa)) / safe
    return np.concatenate([rgb, oa], axis=-1)


def layer(alpha: np.ndarray, rgb) -> np.ndarray:
    """Build an RGBA layer from a coverage map and a colour (scalar or field)."""
    a = np.clip(alpha, 0.0, 1.0)[..., None]
    col = rgb[..., :3] if (isinstance(rgb, np.ndarray) and rgb.ndim >= 3) \
        else np.asarray(rgb, dtype=np.float64)[:3]
    return np.concatenate([np.broadcast_to(col, a.shape[:-1] + (3,)), a], axis=-1)


def reduce_ss(arr: np.ndarray, ss: int = SS) -> np.ndarray:
    """Box-reduce a supersampled float RGBA buffer, premultiplying correctly."""
    h, w = arr.shape[0] // ss, arr.shape[1] // ss
    a = arr[..., 3:4]
    pm = np.concatenate([arr[..., :3] * a, a], axis=-1)
    pm = pm.reshape(h, ss, w, ss, 4).mean(axis=(1, 3))
    oa = pm[..., 3:4]
    rgb = np.where(oa > 1e-9, pm[..., :3] / np.maximum(oa, 1e-9), 0.0)
    return np.concatenate([np.clip(rgb, 0, 1), np.clip(oa, 0, 1)], axis=-1)


def to_image(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def blur_a(a: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian-blur a float 0..1 single-channel array."""
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L")
    return np.asarray(im.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float64) / 255.0


def shift_a(a: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Translate a 2-D array without wrapping."""
    h, w = a.shape
    r = np.zeros_like(a)
    ys_, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs_, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    r[yd, xd] = a[ys_, xs_]
    return r


# ---------------------------------------------------------------------------
# rounded-rectangle SDF, with an analytic normal
# ---------------------------------------------------------------------------

def rr_sdf(X, Y, x0, y0, w, h, r):
    cx, cy = x0 + w * 0.5, y0 + h * 0.5
    bx, by = w * 0.5 - r, h * 0.5 - r
    dx = np.abs(X - cx) - bx
    dy = np.abs(Y - cy) - by
    ax, ay = np.maximum(dx, 0.0), np.maximum(dy, 0.0)
    return np.hypot(ax, ay) + np.minimum(np.maximum(dx, dy), 0.0) - r


def rr_normal(X, Y, x0, y0, w, h, r):
    """Outward unit normal of the rounded rect, evaluated analytically.

    A numerical gradient would sample neighbouring columns and so would leak
    corner detail one pixel into the stretchable edge strips; this does not.
    """
    cx, cy = x0 + w * 0.5, y0 + h * 0.5
    bx, by = w * 0.5 - r, h * 0.5 - r
    sx, sy = np.sign(X - cx), np.sign(Y - cy)
    dx = np.abs(X - cx) - bx
    dy = np.abs(Y - cy) - by
    ax, ay = np.maximum(dx, 0.0), np.maximum(dy, 0.0)
    corner = (dx > 0.0) & (dy > 0.0)
    ln = np.maximum(np.hypot(ax, ay), 1e-9)
    flat_x = dx >= dy
    nx = np.where(corner, sx * ax / ln, np.where(flat_x, sx, 0.0))
    ny = np.where(corner, sy * ay / ln, np.where(flat_x, 0.0, sy))
    return nx, ny


def coverage(d, ss: int = SS):
    """Analytic antialiased coverage from a distance field (target-space px)."""
    return np.clip(0.5 - d * ss, 0.0, 1.0)


def band_in(d, depth):
    """1 at the silhouette, falling to exactly 0 `depth` px inside."""
    return smoothstep(1.0 + d / depth)


def band_out(d, dist):
    """1 at the silhouette, falling to exactly 0 `dist` px outside."""
    return smoothstep(1.0 - d / dist)


def lit(nx, ny, direction=LKEY):
    return np.clip(nx * direction[0] + ny * direction[1], 0.0, 1.0)


# ---------------------------------------------------------------------------
# separable profiles (the 9-slice contract lives here)
# ---------------------------------------------------------------------------

def vprofile(ys, size, inset, stops):
    """Colour as a function of y only, forced flat across the stretch band.

    `stops` must contain the two anchor stops at `inset` and `size - inset`
    with *identical* colours and nothing between them, which makes np.interp
    return that exact value everywhere in the middle -- bit for bit.
    """
    pos = [p for p, _ in stops]
    mids = [i for i, p in enumerate(pos) if inset <= p <= size - inset]
    assert len(mids) == 2, "need exactly two anchor stops inside the stretch band"
    a, b = stops[mids[0]], stops[mids[1]]
    assert abs(a[0] - inset) < 1e-9 and abs(b[0] - (size - inset)) < 1e-9
    assert np.allclose(a[1], b[1]), "the stretch band must be a single colour"
    arr = np.array([s[1] for s in stops], dtype=np.float64)
    out = np.empty((len(ys), 4))
    for ch in range(4):
        out[:, ch] = np.interp(ys, pos, arr[:, ch])
    return out


def vscalar(ys, size, inset, stops):
    """Scalar version of `vprofile` (same flat-band contract)."""
    pos = [p for p, _ in stops]
    mids = [i for i, p in enumerate(pos) if inset <= p <= size - inset]
    assert len(mids) == 2
    assert abs(stops[mids[0]][1] - stops[mids[1]][1]) < 1e-12
    return np.interp(ys, pos, [v for _, v in stops])


def hshade(xs, size, inset, margin, amount, power=1.0):
    """Multiplier that is exactly 1.0 across the stretch band and dips at the
    left/right inner edges, giving the surface a gentle cylindrical roll."""
    span = max(inset - margin, 1e-9)
    left = np.clip((inset - xs) / span, 0.0, 1.0)
    right = np.clip((xs - (size - inset)) / span, 0.0, 1.0)
    return 1.0 - amount * smoothstep(np.maximum(left, right)) ** power


# ---------------------------------------------------------------------------
# 9-slice: panels
# ---------------------------------------------------------------------------

# The kit's shapes were first drawn on a 320 px panel; the canvas grew to 400
# so the 9-slice keeps a usable share of the shading at the sizes the game
# actually draws (see the note in manifest.py).  Scaling the shape language by
# the same factor is what keeps the corner radius reading identically.
PANEL_SCALE = manifest.PANEL_SIZE[0] / 320.0

def ps(v: float) -> float:
    """An authored-at-320px measurement, in shipped canvas pixels.

    Every length in the panel spec is still quoted in the space the kit was
    first drawn in, so an edge treatment keeps its *on-screen* size when the
    canvas changes -- a 2.6 px rim authored at 320 is a 1.3 world-unit
    hairline, and it has to grow with the canvas or the extra inset buys
    gradient without buying bevel.
    """
    return v * PANEL_SCALE


PANEL_GEO = dict(margin=ps(14.0), radius=ps(40.0))


def edge_ramp(edge, mid, n: int = 10):
    """Weights running from the silhouette (t=0) to the stretch anchor (t=1).

    Two things have to be true at once.  At t=0 the ramp must be *steep*, or
    the lit lip is a wash instead of an edge.  At t=1 its slope must be exactly
    zero, or the join into the 9-slice's flat centre shows a Mach band -- the
    value is continuous there either way, but the eye reads the kink in the
    derivative as a line.  A sum of two powers (a tight lip plus a broad body)
    satisfies both, and reaches `mid` exactly, which is what `vprofile`'s
    flat-band assertion requires of the anchor stop.
    """
    return [(i / n, mid + (edge - mid) *
             (0.72 * (1.0 - i / n) ** 3.2 + 0.28 * (1.0 - i / n) ** 1.35))
            for i in range(n + 1)]


# A panel is a *surface*, not an object with a bezel: the quieter its edge
# treatment, the more of the player's attention is left for the one button it
# is holding.  So the rim, the weighted base and the inner ambient are all
# roughly a third of what they were, and what remains is the body gradient and
# a soft lift shadow -- the two cues that actually say "this floats above the
# game".  The dark variant also lost 11% of its opacity: at 224 the sky behind
# a results panel was a flat charcoal rectangle, and at 200 it is still a dark
# surface but the theme's colour comes through it as atmosphere.
PANEL_SPECS = {
    # light / frosted: a cool off-white card with a lit top lip and a weighted
    # base, so it still reads as a *card* when it sits on a pale background.
    "panel": dict(
        mid=c(234, 232, 243, 230),
        top_edge=c(255, 255, 255, 236),
        bot_edge=c(186, 180, 209, 238),
        side=0.050,
        rim=(c(255, 255, 255), 0.58, 2.6),        # colour, opacity, depth
        base_edge=(c(134, 126, 162), 0.40, 5.5),
        ambient_inner=(c(124, 116, 154), 0.13, 2.6),
        shadows=[(c(24, 20, 44), 0.20, 13.0, 24.0), (c(24, 20, 44), 0.14, 3.0, 5.0)],
        halo=None,
    ),
    # deep translucent slate for modal overlays
    "panel_dark": dict(
        mid=c(37, 42, 65, 200),
        top_edge=c(76, 86, 124, 208),
        bot_edge=c(12, 14, 27, 212),
        side=0.058,
        rim=(c(190, 206, 242), 0.40, 2.6),
        base_edge=(c(4, 6, 12), 0.40, 5.5),
        ambient_inner=(c(8, 10, 22), 0.14, 2.6),
        shadows=[(c(4, 6, 16), 0.36, 14.0, 28.0), (c(4, 6, 16), 0.22, 4.0, 7.0)],
        halo=(c(104, 126, 182), 0.045, 30.0),
    ),
}


def build_panel(name: str) -> Image.Image:
    W, H = manifest.PANEL_SIZE
    INS = manifest.PANEL_INSET
    sp = PANEL_SPECS[name]
    m, r = PANEL_GEO["margin"], PANEL_GEO["radius"]
    assert m + r <= INS, "the straight edge must reach the stretch band"

    xs, ys = grid(W, H)
    X, Y = xs[None, :], ys[:, None]

    d = rr_sdf(X, Y, m, m, W - 2 * m, H - 2 * m, r)
    nx, ny = rr_normal(X, Y, m, m, W - 2 * m, H - 2 * m, r)
    cov = coverage(d)
    out = blank(W, H)

    # --- outer glow / lift shadow (knocked out under the card) -------------
    if sp["halo"]:
        hc, ho, hr = sp["halo"]
        out = over(out, layer(band_out(d, ps(hr)) ** 1.6 * ho * (1.0 - cov), hc))
    for col, op, dy, blur in sp["shadows"]:
        ds = rr_sdf(X, Y - ps(dy), m, m, W - 2 * m, H - 2 * m, r)
        sa = np.where(ds <= 0.0, 1.0, band_out(ds, ps(blur)) ** 1.5)
        out = over(out, layer(sa * op * (1.0 - cov), col))

    # --- body ---------------------------------------------------------------
    # The whole inset band carries the gradient, not just its first third:
    # everything inside the inset is flattened by the stretch, so an authored
    # ramp that dies early simply throws away shading the game could have shown.
    ramp = edge_ramp(sp["top_edge"], sp["mid"])
    stops = [(t * INS, col) for t, col in ramp] + \
            [(H - t * INS, col) for t, col in reversed(edge_ramp(sp["bot_edge"], sp["mid"]))]
    vcol = vprofile(ys, H, INS, stops)[:, None, :]
    hs = hshade(xs, W, INS, m, sp["side"])[None, :, None]
    body_rgb = np.broadcast_to(np.clip(vcol[..., :3] * hs, 0.0, 1.0), (H * SS, W * SS, 3))
    body_a = np.broadcast_to(vcol[..., 3:4] * cov[..., None], (H * SS, W * SS, 1))
    out = over(out, np.concatenate([body_rgb, body_a], axis=-1))

    # --- edge treatment -----------------------------------------------------
    key = lit(nx, ny)
    anti = lit(nx, ny, (-LKEY[0], -LKEY[1]))

    acol, aop, adep = sp["ambient_inner"]
    out = over(out, layer(band_in(d, ps(adep)) * cov * aop, acol))
    bcol, bop, bdep = sp["base_edge"]
    out = over(out, layer(band_in(d, ps(bdep)) * cov * anti * bop, bcol))
    rcol, rop, rdep = sp["rim"]
    out = over(out, layer(band_in(d, ps(rdep)) * cov * key * rop, rcol))
    # bounce: a whisper of light returning onto the very bottom lip
    out = over(out, layer(band_in(d, ps(1.8)) * cov * anti * 0.20, c(255, 246, 236)))
    return to_image(reduce_ss(out))


# ---------------------------------------------------------------------------
# 9-slice: buttons
# ---------------------------------------------------------------------------

# The button was first drawn on a 420x148 canvas and every measurement below
# is still quoted in that space; `bs()` maps it onto the shipped canvas, which
# grew so the 9-slice keeps the shading (see the note in manifest.py).
BTN_SCALE = manifest.BUTTON_SIZE[1] / 148.0


def bs(v: float) -> float:
    """An authored-at-148px measurement, in shipped canvas pixels."""
    return v * BTN_SCALE


BTN_M = bs(6.0)        # margin kept for the contact shadow
# The lip was 12, which is 8% of the button's own height: a plastic toy key.
# At 6.5 the slab still shows -- the control is clearly a *thing you push*,
# and the press still has somewhere to travel -- but the button reads as a
# surface with an edge rather than as an extrusion with a face on top.
BTN_LIP = bs(6.5)      # how far the base slab is extruded below the face
BTN_R = bs(46.0)       # corner radius; the asserts below are why this is the max
# ...and the press travels 80% of that lip, so the pressed state is still
# unmistakable at a glance even though the whole control is shallower.
BTN_PRESS = bs(5.2)    # how far the face travels when pressed


def _button_geometry(press: float = 0.0):
    W, H = manifest.BUTTON_SIZE
    base = (BTN_M, BTN_M, W - 2 * BTN_M, H - 2 * BTN_M)
    face = (BTN_M, BTN_M + press, W - 2 * BTN_M, H - 2 * BTN_M - BTN_LIP)
    return base, face


def build_button(variant: str) -> Image.Image:
    W, H = manifest.BUTTON_SIZE
    INS = manifest.BUTTON_INSET
    xs, ys = grid(W, H)
    X, Y = xs[None, :], ys[:, None]

    down = variant.endswith("down")
    press = BTN_PRESS if down else 0.0
    base, face = _button_geometry(press)

    # Both silhouettes must present a *straight* side across the whole stretch
    # band, horizontally and vertically, or the edge strips stop being
    # invariant and the 9-slice shows a bulge at the joins.
    for (x0, y0, w, h) in (base, face):
        assert x0 + BTN_R <= INS and x0 + w - BTN_R >= W - INS
        assert y0 + BTN_R <= INS and y0 + h - BTN_R >= H - INS

    # The ghost is built on the *face* rect, never the base.  The base carries
    # the primary's extruded lip, so a ghost built on it stands 10% taller than
    # the primary beside it in the same layout box and drops its optical centre
    # by 4% of that box -- the two secondaries in the results panel would not
    # share a baseline with the primary above them.  Same rect, same silhouette.
    sil = face if variant.startswith("ghost") else base
    db = rr_sdf(X, Y, *sil, BTN_R)
    nbx, nby = rr_normal(X, Y, *sil, BTN_R)
    cb = coverage(db)

    out = blank(W, H)
    # A softer, wider contact shadow does the lifting the extrusion used to do.
    drops = ((0.26, bs(1.8), bs(3.2)), (0.18, bs(0.8), bs(1.6))) if down else \
            ((0.32, bs(3.0), bs(5.2)), (0.20, bs(1.2), bs(2.2)))
    for op, dy, blur in drops:
        ds = rr_sdf(X, Y - dy, *sil, BTN_R)
        sa = np.where(ds <= 0.0, 1.0, band_out(ds, blur) ** 1.4)
        out = over(out, layer(sa * op * (1.0 - cb), c(16, 8, 4)))

    if variant.startswith("ghost"):
        return _finish_ghost(out, xs, ys, db, nbx, nby, cb, W, H, INS, down)

    db = rr_sdf(X, Y, *base, BTN_R)
    nbx, nby = rr_normal(X, Y, *base, BTN_R)
    cb = coverage(db)

    # ---- extruded base slab (the lip, and the socket when pressed) --------
    # Only the bottom `BTN_LIP` of the base is ever visible (plus the socket
    # walls when pressed), so these stops are tuned for that strip: one deep,
    # desaturated amber that reads as the face's own shadowed edge rather than
    # as a second colour stacked under it.
    if variant == "down":
        base_stops = [(0, c(118, 48, 20)), (bs(16), c(126, 52, 22)), (bs(34), c(138, 58, 25)),
                      (INS, c(144, 62, 27)), (H - INS, c(144, 62, 27)),
                      (bs(114), c(140, 59, 26)), (bs(130), c(130, 53, 23)),
                      (bs(142), c(123, 49, 21)), (H, c(120, 47, 20))]
    else:
        base_stops = [(0, c(188, 94, 42)), (bs(30), c(184, 90, 40)),
                      (INS, c(180, 86, 38)), (H - INS, c(180, 86, 38)),
                      (bs(112), c(173, 79, 34)), (bs(128), c(160, 69, 29)),
                      (bs(140), c(150, 62, 26)), (H, c(146, 59, 25))]
    bcol = vprofile(ys, H, INS, base_stops)[:, None, :]
    bh = hshade(xs, W, INS, BTN_M, 0.13)[None, :, None]
    out = over(out, layer(cb, np.broadcast_to(np.clip(bcol[..., :3] * bh, 0, 1),
                                             (H * SS, W * SS, 3))))

    anti_b = lit(nbx, nby, (-LKEY[0], -LKEY[1]))
    # bounce light returning onto the bottom lip, and a dark outer turn
    out = over(out, layer(band_in(db, bs(3.4)) * cb * anti_b * 0.28, c(255, 184, 124)))
    out = over(out, layer(band_in(db, bs(2.0)) * cb * lit(nbx, nby) * 0.26, c(110, 40, 14)))

    # ---- face -------------------------------------------------------------
    df = rr_sdf(X, Y, *face, BTN_R)
    nfx, nfy = rr_normal(X, Y, *face, BTN_R)
    cf = coverage(df)

    # occlusion the face casts onto the lip: tight and dark, so the step reads
    occl = band_out(df, bs(3.6)) ** 1.1 * np.clip(nfy, 0.0, 1.0) * (1.0 - cf) * cb
    out = over(out, layer(occl * 0.60, c(74, 24, 8)))

    # The face carries about half the vertical contrast it used to (a 42-point
    # mean-RGB fall instead of 71) and the gloss is a quarter of its old
    # height.  A loud top-to-bottom ramp plus a glass crown is how a 2012
    # button looked; one confident colour with a breath of shading and a
    # hairline of light on the top edge is how a modern one does, and it holds
    # up better over a busy sky because the eye reads the shape, not the sheen.
    if variant == "down":
        face_stops = [(0, c(232, 150, 76)), (bs(14), c(230, 146, 72)),
                      (bs(30), c(227, 140, 66)), (bs(46), c(225, 135, 62)),
                      (INS, c(224, 132, 60)), (H - INS, c(224, 132, 60)),
                      (bs(110), c(220, 126, 55)), (bs(130), c(214, 118, 49)),
                      (bs(140), c(211, 114, 47)), (H, c(210, 112, 46))]
        gloss = [(0, 0.0), (bs(10), 0.11), (bs(20), 0.045), (bs(32), 0.008),
                 (INS, 0.0), (H - INS, 0.0), (H, 0.0)]
    else:
        face_stops = [(0, c(255, 198, 122)), (bs(6), c(254, 193, 114)),
                      (bs(16), c(252, 182, 100)), (bs(26), c(251, 173, 90)),
                      (bs(38), c(250, 166, 82)), (bs(48), c(249, 162, 77)),
                      (INS, c(248, 160, 75)), (H - INS, c(248, 160, 75)),
                      (bs(104), c(244, 151, 67)), (bs(122), c(239, 141, 60)),
                      (bs(130), c(237, 137, 57)), (H, c(236, 135, 56))]
        # a tight crown of light on the top edge, gone within a sixth of the
        # button's height -- a highlight, not a reflection
        gloss = [(0, 0.19), (bs(5), 0.175), (bs(11), 0.115), (bs(18), 0.055),
                 (bs(26), 0.018), (bs(34), 0.004),
                 (INS, 0.0), (H - INS, 0.0), (H, 0.0)]

    fcol = vprofile(ys, H, INS, face_stops)[:, None, :]
    fh = hshade(xs, W, INS, BTN_M, 0.085)[None, :, None]
    out = over(out, layer(cf, np.broadcast_to(np.clip(fcol[..., :3] * fh, 0, 1),
                                             (H * SS, W * SS, 3))))
    out = over(out, layer(vscalar(ys, H, INS, gloss)[:, None] * cf, c(255, 251, 242)))

    keyf = lit(nfx, nfy)
    antif = lit(nfx, nfy, (-LKEY[0], -LKEY[1]))
    # the face's own bottom edge rolls into shadow where it meets the lip
    out = over(out, layer(band_in(df, bs(3.6)) * cf * antif * 0.30, c(166, 66, 22)))
    # ...and a hairline of key light along the top: one pixel of edge at the
    # size this ships at, which is all a flat face needs to lift off the panel
    out = over(out, layer(band_in(df, bs(1.7)) * cf * keyf * (0.40 if variant == "down" else 0.72),
                          c(255, 246, 228)))
    # a faint dark turn around the whole silhouette, so it reads on light panels
    out = over(out, layer(band_in(db, bs(1.5)) * cb * 0.24, c(124, 46, 16)))

    if variant == "down":
        # pressed: the socket walls above and beside the face fall into shadow
        sock = band_out(df, bs(8.0)) * (1.0 - cf) * cb
        out = over(out, layer(sock * 0.46 * np.clip(-nfy * 0.85 + 0.35, 0, 1), c(80, 28, 9)))

    return to_image(reduce_ss(out))


def _finish_ghost(out, xs, ys, df, nfx, nfy, cf, W, H, INS, down=False):
    """Outlined/translucent secondary button, legible on light *and* dark.

    Two things this has to get right.  The fill has to read as a *surface*: at
    10% alpha the near-layer tree silhouettes and the ground edge of the day
    theme show straight through it and it looks like a hole punched in the
    scene, so the mid band sits at 25% with a shaded top lip under the rim.
    And the stroke has to be *keyed*, from the same LKEY as every other asset
    in the kit -- an outline of one flat alpha all the way round is a sticker,
    not an edge catching light.
    """
    a0 = 104 if down else 80
    fill_stops = [(0, c(255, 252, 248, a0 + 14)), (bs(20), c(255, 250, 244, a0 + 6)),
                  (INS, c(255, 248, 242, a0)), (H - INS, c(255, 248, 242, a0)),
                  (bs(124), c(244, 236, 232, a0 - 8)), (H, c(240, 232, 228, a0 - 12))]
    fcol = vprofile(ys, H, INS, fill_stops)[:, None, :]
    out = over(out, np.concatenate([
        np.broadcast_to(fcol[..., :3], (H * SS, W * SS, 3)),
        np.broadcast_to(fcol[..., 3:4] * cf[..., None], (H * SS, W * SS, 1))], axis=-1))

    # the plate's own thickness, shading the fill just under the lit rim
    out = over(out, layer(band_in(df, bs(11.0) if down else bs(7.0)) * cf
                          * np.clip(-nfy, 0, 1) * (0.34 if down else 0.22),
                          c(14, 16, 34)))

    # a dark halo just outside the stroke -> the shape survives a light surface
    out = over(out, layer((band_out(df, bs(4.2)) ** 1.5) * (1.0 - cf) * 0.38, c(10, 9, 20)))

    key = lit(nfx, nfy)
    anti = lit(nfx, nfy, (-LKEY[0], -LKEY[1]))
    # tightened to match the primary's hairline crown: a broad inner glow is
    # what made the two secondaries look softer than the button above them
    out = over(out, layer(band_in(df, bs(2.3)) * cf * (0.32 if down else 0.36),
                          c(255, 241, 226)))
    out = over(out, layer(band_in(df, bs(1.8)) * cf * key * (0.62 if down else 0.85),
                          c(255, 255, 255)))
    out = over(out, layer(band_in(df, bs(2.4)) * cf * anti * (0.44 if down else 0.35),
                          c(40, 30, 46)))
    gl = vscalar(ys, H, INS, [(0, 0.030 if down else 0.055), (bs(8), 0.028 if down else 0.050),
                              (bs(28), 0.014 if down else 0.022),
                              (bs(50), 0.005 if down else 0.002),
                              (INS, 0.0), (H - INS, 0.0), (H, 0.0)])[:, None]
    out = over(out, layer(gl * cf, c(255, 255, 255)))
    return to_image(reduce_ss(out))


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def round_poly(pts, r, steps=9):
    """Replace every vertex of a polygon with a tangent arc of radius `r`.

    Handles convex and reflex vertices alike and clamps the tangent length to
    the adjacent edges, so sharp spikes (a play triangle, a star point) degrade
    gracefully instead of exploding the way a naive miter offset does.
    """
    n = len(pts)
    out = []
    for i in range(n):
        pp, p, pn = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        ux, uy = p[0] - pp[0], p[1] - pp[1]
        vx, vy = pn[0] - p[0], pn[1] - p[1]
        lu, lv = math.hypot(ux, uy) or 1e-9, math.hypot(vx, vy) or 1e-9
        ux, uy, vx, vy = ux / lu, uy / lu, vx / lv, vy / lv
        cosA = max(-1.0, min(1.0, (-ux) * vx + (-uy) * vy))
        A = math.acos(cosA)
        if A < 1e-3 or math.pi - 1e-3 < A:
            out.append(p)
            continue
        t = min(r / math.tan(A / 2.0), lu * 0.49, lv * 0.49)
        reff = t * math.tan(A / 2.0)
        pin = (p[0] - ux * t, p[1] - uy * t)
        pout = (p[0] + vx * t, p[1] + vy * t)
        bx, by = -ux + vx, -uy + vy
        lb = math.hypot(bx, by) or 1e-9
        dist = reff / math.sin(A / 2.0)
        cen = (p[0] + bx / lb * dist, p[1] + by / lb * dist)
        a0 = math.atan2(pin[1] - cen[1], pin[0] - cen[0])
        a1 = math.atan2(pout[1] - cen[1], pout[0] - cen[0])
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        for s in range(steps + 1):
            a = a0 + da * s / steps
            out.append((cen[0] + reff * math.cos(a), cen[1] + reff * math.sin(a)))
    return out


def arc_pts(cx, cy, r, a0, a1, steps=64):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps)))
            for i in range(steps + 1)]


# ---------------------------------------------------------------------------
# the feather: one parametric mark, reused by the icon, the medals and the logo
# ---------------------------------------------------------------------------

def _bez(p0, p1, p2, t):
    u = 1.0 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def _bez_tan(p0, p1, p2, t):
    u = 1.0 - t
    return (2 * u * (p1[0] - p0[0]) + 2 * t * (p2[0] - p1[0]),
            2 * u * (p1[1] - p0[1]) + 2 * t * (p2[1] - p1[1]))


def feather_shapes(n_barbs: int = 4, wmax: float = 0.250, inner: float = 0.66,
                   cut_depth: float = 0.32, rachis_start: float = 0.13):
    """The brand feather in a 0..1 unit box (y down), tip at the upper right.

    Returns (blade_outline, [barb_cuts], rachis_quad) as unit-space polygons.
    One definition drives icon_feather, the medal emblem and the logo mark, so
    the brand shape is literally the same curve everywhere.
    """
    P0, P1, P2 = (0.10, 0.94), (0.17, 0.34), (0.95, 0.08)
    N = 80

    def frame(t):
        px, py = _bez(P0, P1, P2, t)
        tx, ty = _bez_tan(P0, P1, P2, t)
        ln = math.hypot(tx, ty) or 1e-9
        tx, ty = tx / ln, ty / ln
        return (px, py), (-ty, tx)  # normal points down-left (the wide side)

    def width(t):
        return wmax * math.sin(math.pi * (t ** 0.62)) ** 0.85

    outer, inner_pts = [], []
    for i in range(N + 1):
        t = i / N
        (px, py), (nxx, nyy) = frame(t)
        w = width(t)
        outer.append((px + nxx * w, py + nyy * w))
        inner_pts.append((px - nxx * w * inner, py - nyy * w * inner))
    blade = inner_pts + outer[::-1]

    cuts = []
    for k in range(n_barbs):
        t1 = 0.155 + 0.135 * k
        t0 = t1 - 0.085
        (p1x, p1y), (n1x, n1y) = frame(t1)
        (p0x, p0y), (n0x, n0y) = frame(t0)
        w1, w0 = width(t1), width(t0)
        (ax, ay), (anx, any_) = frame(t0 + 0.015)
        cuts.append([
            (p1x + n1x * w1 * 1.10, p1y + n1y * w1 * 1.10),
            (p0x + n0x * w0 * 1.10, p0y + n0y * w0 * 1.10),
            (ax + anx * w0 * cut_depth, ay + any_ * w0 * cut_depth),
        ])

    rach_a, rach_b = [], []
    for i in range(N + 1):
        t = rachis_start + (0.90 - rachis_start) * i / N
        (px, py), (nxx, nyy) = frame(t)
        hw = 0.023 * (1.0 - 0.74 * t)
        rach_a.append((px + nxx * hw, py + nyy * hw))
        rach_b.append((px - nxx * hw, py - nyy * hw))
    return blade, cuts, rach_a + rach_b[::-1]


def place(pts, cx, cy, size, rot_deg):
    """Map unit-box polygon points to target space: scale, rotate, centre."""
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + ((ux - 0.5) * size) * ca - ((uy - 0.5) * size) * sa,
             cy + ((ux - 0.5) * size) * sa + ((uy - 0.5) * size) * ca) for ux, uy in pts]


# ---------------------------------------------------------------------------
# icon system
# ---------------------------------------------------------------------------

WHITE = (255, 255, 255, 255)
IW, IH = manifest.ICON_SIZE
ICX, ICY = IW / 2.0, IH / 2.0
PAD = 0.12
BOXE = IW * (1.0 - 2 * PAD)       # 110.0 -- the content box the whole set shares
HALF = BOXE / 2.0                 # 55.0 nominal half-extent
SW = 12.6                         # the set's nominal stroke weight
# Every stroked glyph multiplies SW by this and nothing else.  Hand-picked
# per-glyph multipliers had drifted to a 1.56x spread (haptics 10.0 px, check
# 15.6 px measured perpendicular at 144), which at the shipped 24 px is 1.7
# vs 2.6 device px -- share, restart and haptics looked like a lighter icon
# family than close and the chevrons.  `main()` now measures the true ridge
# width per icon and fails anything more than 12% off the set median.
SWM = 1.15                        # -> 14.5 px, the set's one stroke weight
STROKE_TARGET = SW * SWM


class Glyph:
    """Additive/subtractive silhouette builder with a fitted transform.

    Builders are written against the nominal box (centre ICX/ICY, half-extent
    HALF); the transform set here scales and recentres them so the whole set
    lands on one optical size.  Stroke widths scale by k**0.3 rather than k, so
    fitting an icon up or down barely moves its line weight -- that is what
    keeps the set's optical weight consistent while its extents are equalised.
    """

    def __init__(self, k=1.0, ox=0.0, oy=0.0, mul=1):
        self.k, self.ox, self.oy, self.mul = k, ox, oy, mul
        self.widths = []          # every width asked of stroke(), for the audit
        self.kw = k ** 0.30
        self.px, self.py = IW * (mul - 1) / 2.0, IH * (mul - 1) / 2.0
        self.ink = artlib.canvas(IW * mul, IH * mul)
        self.hole = artlib.canvas(IW * mul, IH * mul)
        self._t = self.ink

    def _p(self, x, y):
        return (self.px + ICX + (x - ICX) * self.k + self.ox,
                self.py + ICY + (y - ICY) * self.k + self.oy)

    def _ps(self, pts):
        return [self._p(x, y) for x, y in pts]

    def add(self):
        self._t = self.ink
        return self

    def sub(self):
        self._t = self.hole
        return self

    # -- primitives ------------------------------------------------------
    def circle(self, cx, cy, r):
        x, y = self._p(cx, cy)
        artlib.blob(self._t, x, y, r * self.k, r * self.k, WHITE)
        return self

    def ellipse(self, cx, cy, rx, ry, rot=0.0):
        x, y = self._p(cx, cy)
        artlib.blob(self._t, x, y, rx * self.k, ry * self.k, WHITE, rotation=rot)
        return self

    def rrect(self, x, y, w, h, r):
        px, py = self._p(x, y)
        artlib.rounded(self._t, px, py, w * self.k, h * self.k, r * self.k, WHITE)
        return self

    def poly(self, pts):
        artlib.poly(self._t, self._ps(pts), WHITE)
        return self

    def rpoly(self, pts, r):
        artlib.poly(self._t, self._ps(round_poly(pts, r)), WHITE)
        return self

    def stroke(self, pts, width, caps=True):
        if self._t is self.ink:          # knockouts are cuts, not strokes
            self.widths.append(width)
        d = artlib.draw_on(self._t)
        p = [(artlib.s(x), artlib.s(y)) for x, y in self._ps(pts)]
        wpx = max(1, artlib.s(width * self.kw))
        if len(p) >= 2:
            d.line(p, fill=WHITE, width=wpx, joint="curve")
        if caps:
            for (x, y) in (p[0], p[-1]):
                d.ellipse((x - wpx // 2, y - wpx // 2, x + wpx // 2, y + wpx // 2), fill=WHITE)
        return self

    def arc(self, cx, cy, r, a0, a1, width, caps=True, steps=64):
        return self.stroke(arc_pts(cx, cy, r, a0, a1, steps), width, caps=caps)

    def star(self, cx, cy, r_out, r_in, points=5, rot=-90.0, round_r=6.0):
        pts = []
        for i in range(points * 2):
            a = math.radians(rot + i * 180.0 / points)
            rr = r_out if i % 2 == 0 else r_in
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        return self.rpoly(pts, round_r) if round_r > 0 else self.poly(pts)

    def mask(self):
        a = np.asarray(self.ink.getchannel("A"), dtype=np.float64) / 255.0
        b = np.asarray(self.hole.getchannel("A"), dtype=np.float64) / 255.0
        return np.clip(a * (1.0 - b), 0.0, 1.0)


# --- individual glyphs -----------------------------------------------------

def ico_play(g):
    h = HALF * 1.17
    wt, ht = h * 1.76, h * 1.92
    x0 = ICX - wt * 0.46
    g.rpoly([(x0, ICY - ht / 2), (x0 + wt, ICY), (x0, ICY + ht / 2)], 10.0)


def ico_pause(g):
    h, bw, gp = HALF, 28.0, 22.0
    for sgn in (-1, 1):
        g.rrect(ICX + (gp / 2 if sgn > 0 else -gp / 2 - bw), ICY - h, bw, h * 2, 12.0)


def ico_home(g):
    h = HALF
    eave = ICY - h * 0.08
    rw, ww, bot = h * 1.00, h * 0.74, ICY + h * 0.92
    g.rpoly([(ICX, ICY - h * 1.02), (ICX + rw, eave), (ICX + ww, eave), (ICX + ww, bot),
             (ICX - ww, bot), (ICX - ww, eave), (ICX - rw, eave)], 7.0)
    g.sub().rrect(ICX - h * 0.25, ICY + h * 0.16, h * 0.50, h * 0.78, 11.0)
    g.add()


def ico_restart(g):
    r = HALF * 0.86
    a0, a1 = 300.0, 603.0          # gap at the top, sweeping clockwise
    g.arc(ICX, ICY, r, a0, a1, SW * SWM)
    a = math.radians(a1)
    px, py = ICX + r * math.cos(a), ICY + r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)        # tangent = direction of travel
    nx2, ny2 = math.cos(a), math.sin(a)       # radial
    L, Wd, back = 26.0, 18.5, 7.0
    g.rpoly([(px + tx * L, py + ty * L),
             (px + nx2 * Wd - tx * back, py + ny2 * Wd - ty * back),
             (px - nx2 * Wd - tx * back, py - ny2 * Wd - ty * back)], 3.0)


def ico_settings(g):
    teeth, r_tip, r_root = 8, HALF * 1.00, HALF * 0.745
    step = 360.0 / teeth
    pts = []
    for i in range(teeth):
        cdeg = i * step - 90.0
        for rad, off in ((r_root, -13.0), (r_tip, -8.4), (r_tip, 8.4), (r_root, 13.0)):
            a = math.radians(cdeg + off)
            pts.append((ICX + rad * math.cos(a), ICY + rad * math.sin(a)))
        for j in range(1, 4):      # keep the root circular between the teeth
            a = math.radians(cdeg + 13.0 + (step - 26.0) * j / 4.0)
            pts.append((ICX + r_root * math.cos(a), ICY + r_root * math.sin(a)))
    g.rpoly(pts, 4.2)
    g.sub().circle(ICX, ICY, HALF * 0.335)
    g.add()


def ico_shop(g):
    # A shopping bag is portrait with a taper toward the base, about 0.9:1.
    # The old body was h*1.88 x h*1.22 -- 1.54:1 landscape, which is a
    # briefcase at any size and a purse at 22 px -- and its handle was a low,
    # heavy arc that read as a case hinge rather than as two straps.
    h = HALF
    top = ICY - h * 0.44
    g.rpoly([(ICX - h * 0.70, top), (ICX + h * 0.70, top),
             (ICX + h * 0.60, top + h * 1.46), (ICX - h * 0.60, top + h * 1.46)], 15.0)
    # the strap sits wholly above the mouth and is open underneath, so it reads
    # as a handle rather than as the hinge of a case
    # tall enough that the 4 px contour under the glyph cannot close the arch:
    # at h*0.40 the opening was 14.7 px, of which the contour ate 8
    g.arc(ICX, top - 1.0, h * 0.46, 186, 354, SW * SWM)


def ico_trophy(g):
    h = HALF
    top, bot, bw = ICY - h * 0.94, ICY + h * 0.20, h * 0.66
    for sgn in (-1, 1):
        g.arc(ICX + sgn * bw * 0.96, top + h * 0.22, h * 0.30,
              -84 if sgn > 0 else 96, 84 if sgn > 0 else 264, SW * SWM)
    cup = [(ICX - bw, top), (ICX + bw, top)]
    for side in (1, -1):
        rng = range(1, 7) if side == 1 else range(6, 0, -1)
        for i in rng:
            t = i / 6.0
            cup.append((ICX + side * bw * (1.0 - 0.56 * t ** 1.7), top + (bot - top) * t))
    g.rpoly(cup, 7.0)
    g.rrect(ICX - h * 0.13, bot - 3.0, h * 0.26, h * 0.34, 4.0)
    g.rrect(ICX - h * 0.34, ICY + h * 0.46, h * 0.68, h * 0.15, 5.0)
    g.rrect(ICX - h * 0.60, ICY + h * 0.62, h * 1.20, h * 0.24, 8.0)


def ico_close(g):
    e = HALF * 0.84
    g.stroke([(ICX - e, ICY - e), (ICX + e, ICY + e)], SW * SWM)
    g.stroke([(ICX - e, ICY + e), (ICX + e, ICY - e)], SW * SWM)


def _speaker(g):
    h = HALF * 1.17
    g.rpoly([(ICX - h * 0.88, ICY - h * 0.27), (ICX - h * 0.46, ICY - h * 0.27),
             (ICX - h * 0.04, ICY - h * 0.78), (ICX - h * 0.04, ICY + h * 0.78),
             (ICX - h * 0.46, ICY + h * 0.27), (ICX - h * 0.88, ICY + h * 0.27)], 7.0)
    return h


def ico_sound_on(g):
    h = _speaker(g)
    g.arc(ICX - h * 0.06, ICY, h * 0.42, -52, 52, SW * SWM)
    g.arc(ICX - h * 0.06, ICY, h * 0.78, -48, 48, SW * SWM)


def ico_sound_off(g):
    # one wave instead of two: the slash needs the room, and the pair of them
    # under a diagonal bar turns to mush at 24 px
    h = _speaker(g)
    g.arc(ICX - h * 0.06, ICY, h * 0.42, -52, 52, SW * SWM)


def _note_single(g):
    """One eighth note: the reduced glyph the slash needs.

    A slashed icon has to carry *less* detail than its unslashed partner, not
    the same.  The two-note beamed figure loses its right stem and notehead to
    the knockout and what survives reads as a flag on a pole; this one is laid
    out across the slash's quiet corners -- head in the lower left, flag in the
    upper right -- so each half still reads as part of a note.
    """
    h = HALF * 1.14
    hx, hy = ICX - h * 0.42, ICY + h * 0.56          # notehead centre
    sx, sy = ICX + h * 0.26, ICY - h * 0.92          # stem top
    g.stroke([(hx + h * 0.19, hy - h * 0.06), (sx, sy)], SW * SWM, caps=False)
    g.ellipse(hx, hy, h * 0.36, h * 0.27, rot=-20)
    # the flag is a swept band between two quadratics, not a polygon: a
    # straight-sided pennant with a bulge on its outer edge reads as a hatchet
    n = 10
    outer = [_bez((sx, sy), (sx + h * 0.66, sy + h * 0.12), (sx + h * 0.30, sy + h * 0.80),
                  i / n) for i in range(n + 1)]
    inner = [_bez((sx, sy + h * 0.30), (sx + h * 0.32, sy + h * 0.34),
                  (sx + h * 0.12, sy + h * 0.60), i / n) for i in range(n + 1)]
    g.poly(outer + inner[::-1])


def _note(g):
    h = HALF * 1.14
    hx1, hy1 = ICX - h * 0.42, ICY + h * 0.62
    hx2, hy2 = ICX + h * 0.56, ICY + h * 0.42
    top1, top2 = ICY - h * 0.72, ICY - h * 0.92
    sw2 = SW * SWM / 2.0
    g.rrect(hx1 - sw2, top1, SW * SWM, hy1 - top1, 5.0)
    g.rrect(hx2 - sw2, top2, SW * SWM, hy2 - top2, 5.0)
    g.poly([(hx1 - sw2, top1), (hx2 + sw2, top2),
            (hx2 + sw2, top2 + 26.0), (hx1 - sw2, top1 + 26.0)])
    g.ellipse(hx1 - 9.5, hy1 - 2.5, 17.5, 13.5, rot=-18)
    g.ellipse(hx2 - 9.5, hy2 - 2.5, 17.5, 13.5, rot=-18)


def ico_music_on(g):
    _note(g)


def ico_music_off(g):
    _note_single(g)


def _device(g, rings=(0.60, 0.90)):
    h = HALF
    # A solid slab, not an outline.  The outlined phone's wall measured 7.5 px
    # against the set's 14.5, so the device carried a weight lighter than the
    # arcs beside it -- and at 24 px that wall closed up into a grey blob.
    g.rrect(ICX - h * 0.38, ICY - h * 0.90, h * 0.76, h * 1.80, 13.0)
    g.sub().rrect(ICX - h * 0.16, ICY - h * 0.74, h * 0.32, 9.0, 4.5)
    g.add()
    for sgn in (-1, 1):
        base = 0.0 if sgn > 0 else 180.0
        for rr in rings:
            g.arc(ICX, ICY, rr * h, base - 26, base + 26, SW * SWM)


def ico_haptics_on(g):
    _device(g)


def ico_haptics_off(g):
    # one arc per side, not two: the slash already crosses the device and all
    # four arcs, and four of them under a diagonal bar is noise below 30 px
    _device(g, rings=(0.90,))


def ico_left(g):
    e = HALF
    g.stroke([(ICX + e * 0.34, ICY - e * 0.72), (ICX - e * 0.30, ICY),
              (ICX + e * 0.34, ICY + e * 0.72)], SW * SWM)


def ico_right(g):
    e = HALF
    g.stroke([(ICX - e * 0.34, ICY - e * 0.72), (ICX + e * 0.30, ICY),
              (ICX - e * 0.34, ICY + e * 0.72)], SW * SWM)


def ico_check(g):
    e = HALF
    g.stroke([(ICX - e * 0.80, ICY + e * 0.04), (ICX - e * 0.24, ICY + e * 0.56),
              (ICX + e * 0.80, ICY - e * 0.56)], SW * SWM)


def ico_lock(g):
    h = HALF * 1.25
    body_t = ICY - h * 0.08
    g.arc(ICX, body_t + 2.0, h * 0.44, 180, 360, SW * SWM)
    g.rrect(ICX - h * 0.72, body_t, h * 1.44, h * 1.00, 14.0)
    g.sub().circle(ICX, body_t + h * 0.38, 9.0)
    g.sub().poly([(ICX - 5.4, body_t + h * 0.36), (ICX + 5.4, body_t + h * 0.36),
                  (ICX + 3.2, body_t + h * 0.78), (ICX - 3.2, body_t + h * 0.78)])
    g.add()


def ico_feather(g):
    blade, cuts, rachis = feather_shapes()
    size = HALF * 2.00
    g.poly(place(blade, ICX, ICY, size, 0.0))
    g.sub()
    for cut in cuts:
        g.poly(place(cut, ICX, ICY, size, 0.0))
    g.poly(place(rachis, ICX, ICY, size, 0.0))
    g.add()


def ico_bird(g):
    """A bird in side view, for the bird-selection button.

    It exists because the shopping-bag "shop" glyph and the padlock "lock" glyph
    reduce to nearly the same silhouette at icon size, so the menu's Birds
    button looked locked. A bird also says what the button does, since nothing
    is bought in this game.

    Four shapes only -- body, beak, tail, eye. Earlier attempts added a wing,
    but at 22 px the wing and the body merged into an unreadable blob; the eye
    knockout does the same job of breaking up the mass and costs one circle.
    """
    h = HALF
    cx, cy = ICX - h * 0.08, ICY + h * 0.06

    # Body: one plump teardrop, head end to the right and slightly raised.
    g.rpoly([(cx - h * 0.40, cy - h * 0.12),
             (cx + h * 0.10, cy - h * 0.62),
             (cx + h * 0.56, cy - h * 0.30),
             (cx + h * 0.52, cy + h * 0.26),
             (cx + h * 0.02, cy + h * 0.58),
             (cx - h * 0.42, cy + h * 0.30)], 22.0)

    # Beak: a wedge off the head, angled down-right so it cannot be mistaken
    # for a tail on the other side.
    g.rpoly([(cx + h * 0.46, cy - h * 0.40),
             (cx + h * 1.00, cy - h * 0.14),
             (cx + h * 0.46, cy + h * 0.02)], 2.0)

    # Tail: swept up and back, which is what gives the silhouette direction.
    g.rpoly([(cx - h * 0.26, cy - h * 0.22),
             (cx - h * 0.96, cy - h * 0.56),
             (cx - h * 0.84, cy + h * 0.06),
             (cx - h * 0.28, cy + h * 0.18)], 7.0)

    # Eye: a knockout near the head, which reads as an eye and keeps the body
    # from being a featureless lump at small sizes.
    g.sub()
    g.circle(cx + h * 0.26, cy - h * 0.26, h * 0.145)
    g.add()


def ico_share(g):
    e = HALF
    a = (ICX + e * 0.54, ICY - e * 0.72)
    b = (ICX - e * 0.60, ICY)
    cpt = (ICX + e * 0.54, ICY + e * 0.72)
    g.stroke([b, a], SW * SWM, caps=False)
    g.stroke([b, cpt], SW * SWM, caps=False)
    for p in (a, b, cpt):
        g.circle(p[0], p[1], 18.0)


def ico_star(g):
    g.star(ICX, ICY + 2.0, HALF * 1.26, HALF * 0.560, 5, -90.0, 5.5)


def ico_medal(g):
    """Disc on top, two swallowtail tails below -- the same object as medal_*.png.

    The previous build inverted that: a small low disc under a *crossed* pair
    of ribbons (the fourth vertex of each quad sat on the far side of centre,
    so the two tails swapped sides and formed an X).  It read as a pair of
    scissors at every size, collided with icon_close, and contradicted the
    medal art the icon is supposed to stand for.  The knocked-out star is gone
    too: it was 18 px at 144, which is 3 px where the game draws icons.
    """
    h = HALF
    disc_cy, rr = ICY - h * 0.18, h * 0.78
    for sgn in (-1, 1):
        g.poly([(ICX + sgn * h * 0.10, disc_cy + rr * 0.50),   # top, inner
                (ICX + sgn * h * 0.56, disc_cy + rr * 0.12),   # top, outer
                (ICX + sgn * h * 0.80, ICY + h * 1.10),        # bottom, outer
                (ICX + sgn * h * 0.52, ICY + h * 0.80),        # swallowtail
                (ICX + sgn * h * 0.24, ICY + h * 1.16)])       # bottom, inner
    g.circle(ICX, disc_cy, rr)
    # one struck ring for the recessed field, echoing the medal's inner wall;
    # it fills in gracefully at 20 px instead of turning to noise the way a
    # five-pointed knockout does
    g.sub().arc(ICX, disc_cy, rr * 0.60, 0, 360, 7.0, caps=False)
    g.add()


def ico_dash(g):
    """The forward burst: a chevron with three motion streaks trailing it.

    Built entirely from strokes at the set's one weight, so it reads as a
    member of the family rather than as a pasted-in mark, and so the audit
    below can actually measure it (a filled bolt has no stroke width to
    compare).  The streaks are what separate it from `right`: a lone chevron
    is navigation, a chevron with a wake is speed.  They are graded -- the
    middle one longest and closest -- which is what gives the glyph a
    direction at the ~20 px it is drawn at in a settings row.
    """
    h = HALF
    g.stroke([(ICX + h * 0.20, ICY - h * 0.84), (ICX + h * 0.86, ICY),
              (ICX + h * 0.20, ICY + h * 0.84)], SW * SWM)
    for dy, x0, x1 in ((0.00, -1.00, -0.12), (-0.54, -0.82, -0.26), (0.54, -0.82, -0.26)):
        g.stroke([(ICX + h * x0, ICY + h * dy), (ICX + h * x1, ICY + h * dy)], SW * SWM)


def ico_speed(g):
    """Game speed: a dial, a needle swung to the fast end, and a hub.

    The dial is deliberately heavy for its size.  A speedometer is mostly air,
    and the first attempt measured 142 luminance over a white panel (the set's
    limit is 140) because three thin strokes cannot hold a 24 px square down.
    A longer sweep, a wedge of a needle and a hub that reads as mass fix that
    without adding any detail that dies when the icon shrinks.
    """
    h = HALF
    r = h * 0.94
    cy = ICY + h * 0.10
    g.arc(ICX, cy, r, 150, 390, SW * SWM)
    # The needle has to cross most of the dial's radius or the glyph reads as
    # a dot inside a horseshoe; a hub wider than the needle is what made the
    # first pass look like a face rather than an instrument.
    a = math.radians(-58.0)
    nx, ny = -math.sin(a), math.cos(a)
    bw = h * 0.15
    g.rpoly([(ICX + nx * bw, cy + ny * bw),
             (ICX + math.cos(a) * r * 0.88, cy + math.sin(a) * r * 0.88),
             (ICX - nx * bw, cy - ny * bw)], 4.0)
    g.circle(ICX, cy, h * 0.19)


def ico_info(g):
    r = HALF
    g.circle(ICX, ICY, r)
    g.sub().circle(ICX, ICY - r * 0.46, 8.0)
    g.sub().rrect(ICX - 7.4, ICY - r * 0.14, 14.8, r * 0.82, 7.4)
    g.add()


ICON_BUILDERS = {
    "play": ico_play, "pause": ico_pause, "home": ico_home, "restart": ico_restart,
    "settings": ico_settings, "shop": ico_shop, "trophy": ico_trophy, "close": ico_close,
    "sound_on": ico_sound_on, "sound_off": ico_sound_off,
    "music_on": ico_music_on, "music_off": ico_music_off,
    "haptics_on": ico_haptics_on, "haptics_off": ico_haptics_off,
    "left": ico_left, "right": ico_right, "check": ico_check, "lock": ico_lock,
    "feather": ico_feather, "share": ico_share, "star": ico_star,
    "medal": ico_medal, "info": ico_info, "dash": ico_dash, "speed": ico_speed,
    "bird": ico_bird,
}

# Optical size relative to the 110 px content box.  A disc, a star and a
# triangle with the same bounding box do not read as the same size, so each
# family gets a correction: solid round shapes shrink, spiky ones grow.
ICON_FIT = {
    "play": 0.99, "pause": 0.97, "home": 0.97, "restart": 1.00, "settings": 1.00,
    "shop": 0.95, "trophy": 1.00, "close": 0.99, "sound_on": 1.00, "sound_off": 1.00,
    "music_on": 0.99, "music_off": 0.96, "haptics_on": 1.00, "haptics_off": 1.00,
    "left": 0.90, "right": 0.90, "check": 0.98, "lock": 0.95, "feather": 1.00,
    "share": 0.98, "star": 1.03, "medal": 0.99, "info": 0.90,
    "dash": 1.00, "speed": 0.99, "bird": 1.00,
}
SLASHED = {"sound_off", "music_off", "haptics_off"}
# `_off` variants fitted from their `_on` partner stay pixel-matched through a
# toggle.  music_off is deliberately *not* in here: it is a reduced glyph now,
# not the same one with a bar across it, so it has to be fitted on its own.
FIT_WITH = {"sound_off": "sound_on", "haptics_off": "haptics_on"}


def _measure(mask):
    """Bounding box and ink centroid of a mask, in target-space pixels."""
    colsum, rowsum = mask.sum(axis=0), mask.sum(axis=1)
    cols = np.where(colsum > 0.10 * SS)[0]
    rows = np.where(rowsum > 0.10 * SS)[0]
    tot = mask.sum() or 1.0
    return dict(
        x0=cols[0] / SS, x1=(cols[-1] + 1) / SS,
        y0=rows[0] / SS, y1=(rows[-1] + 1) / SS,
        cx=float((colsum * np.arange(mask.shape[1])).sum() / tot) / SS,
        cy=float((rowsum * np.arange(mask.shape[0])).sum() / tot) / SS,
        ink=float(tot) / (SS * SS))


def _slash_masks():
    """The 'off' bar, built in final icon space so all three match exactly."""
    # The knockout is SW + 2*gap wide.  At gap 5.2 that gap was 0.87 px once
    # the icon reached the 24 px the game draws it at, so it antialiased shut
    # and the bar fused with the glyph underneath; 9.0 keeps it at 1.5 px.
    e, gap = 38.0, 9.0
    p0, p1 = (ICX - e, ICY - e), (ICX + e, ICY + e)
    ink = Glyph()
    ink.stroke([p0, p1], SW * SWM)
    knock = Glyph()
    knock.stroke([p0, p1], SW * SWM + 2 * gap)
    return ink.mask(), knock.mask()


CONTOUR_PX = 4.0   # icon-space px of solid dark the whole set is cut out against
CONTOUR_A = 0.72


def _contour(m, px):
    """A hard dilation ring under the glyph: `px` of solid dark all the way round.

    Built by thresholding a blur rather than by a rank filter, which is both
    faster and gives the ring a rounded structuring element.  A step edge
    blurred by sigma crosses 0.22 at 0.7726*sigma outside the silhouette, which
    is the constant that turns a wanted dilation into a blur radius.
    """
    d = blur_a(m, px * SS / 0.7726)
    return np.clip(np.clip((d - 0.22) * 15.0 + 0.5, 0.0, 1.0) - m, 0.0, 1.0)


def _flat_layer(alpha, rgb, size):
    im = Image.new("RGBA", size, tuple(rgb) + (255,))
    im.putalpha(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), "L"))
    return im


def _edt_sq(f):
    """Exact squared Euclidean distance transform (Felzenszwalb-Huttenlocher).

    `f` is a 2-D cost field; the result is min_q (f[q] + |p-q|^2).  Used to
    measure true *perpendicular* stroke width, which is the only way to compare
    an arc's weight with a chevron's -- a bounding box cannot see it.
    """
    out = f.astype(np.float64)
    for axis in (0, 1):
        out = np.moveaxis(out, axis, -1)
        n = out.shape[-1]
        rows = out.reshape(-1, n)
        res = np.empty_like(rows)
        for i in range(rows.shape[0]):
            g = rows[i]
            v = np.zeros(n, dtype=np.intp)
            z = np.empty(n + 1)
            z[0], z[1], k = -np.inf, np.inf, 0
            for q in range(1, n):
                while True:
                    p = v[k]
                    sxx = ((g[q] + q * q) - (g[p] + p * p)) / (2.0 * q - 2.0 * p)
                    if sxx <= z[k] and k > 0:
                        k -= 1
                        continue
                    break
                k += 1
                v[k], z[k], z[k + 1] = q, sxx, np.inf
            k = 0
            for q in range(n):
                while z[k + 1] < q:
                    k += 1
                res[i, q] = (q - v[k]) ** 2 + g[v[k]]
        out = np.moveaxis(res.reshape(out.shape), -1, axis)
    return out


def dominant_width(m, thresh: float = 0.5):
    """The glyph's most-repeated perpendicular width, and how much of it that is.

    The distance transform's local maxima lie on the medial axis, so twice the
    distance there is the true width wherever the shape is stroke-like.  What
    the audit needs is not the median of that -- a play triangle's medial width
    is 73 px and nothing is wrong with it, and a tapered glyph like the bolt
    has no single width at all -- but whether some *sustained* run sits below
    the set's stroke weight.  So: find the width that the largest share of the
    ridge agrees on, and report that share with it.  The device drawn as a
    7.5 px outline put half its ridge at 7.5; the bolt's taper puts under a
    tenth of its ridge at any one width, and is correctly left alone.
    """
    # half resolution: a 14.5 px stroke is still 29 samples across there, and
    # the exact transform is a Python scan per row
    half = m[::2, ::2]
    ink = half > thresh
    if not ink.any():
        return 0.0, 0.0
    dist = np.sqrt(_edt_sq(np.where(ink, 1e12, 0.0)))
    ridge = dist.copy()
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        ridge = np.where((dist >= np.roll(dist, (dy, dx), (0, 1)) - 1e-9) &
                         (dist >= np.roll(dist, (-dy, -dx), (0, 1)) - 1e-9), ridge, 0.0)
    vals = dist[(ridge > 0) & (dist > 1.0)] * 2.0 / (SS / 2.0)
    if vals.size == 0:
        return 0.0, 0.0
    best = (0.0, 0.0)
    for w in np.unique(np.round(vals, 1)):
        mass = float(np.mean(np.abs(vals - w) <= 0.08 * w))
        if mass > best[1]:
            best = (float(w), mass)
    return best


def build_icon(name: str):
    # Pass 1: draw at true scale into a double-size canvas (so nothing can
    # clip) and measure.  The `_off` variants are fitted from their `_on`
    # partner, so a pair differs only by the slash -- never by size.
    ref = FIT_WITH.get(name, name)
    probe = Glyph(mul=2)
    ICON_BUILDERS[ref](probe)
    mp = _measure(probe.mask())
    for key, pad in (("x0", probe.px), ("x1", probe.px), ("cx", probe.px),
                     ("y0", probe.py), ("y1", probe.py), ("cy", probe.py)):
        mp[key] -= pad

    nat_w, nat_h = mp["x1"] - mp["x0"], mp["y1"] - mp["y0"]
    k = BOXE * ICON_FIT[ref] / max(nat_w, nat_h)
    # optical centre: halfway between the bounding-box centre and the ink
    # centroid -- the classic answer for asymmetric glyphs like a play triangle
    ocx = 0.55 * (mp["x0"] + mp["x1"]) / 2 + 0.45 * mp["cx"]
    ocy = 0.55 * (mp["y0"] + mp["y1"]) / 2 + 0.45 * mp["cy"]

    g = Glyph(k=k, ox=-(ocx - ICX) * k, oy=-(ocy - ICY) * k)
    ICON_BUILDERS[name](g)
    m = g.mask()

    if name in SLASHED:
        sink, sknock = _slash_masks()
        m = np.clip(m * (1.0 - sknock) + sink, 0.0, 1.0)

    big = Image.fromarray((m * 255).astype(np.uint8), "L")
    glyph = Image.new("RGBA", big.size, (255, 255, 255, 255))
    glyph.putalpha(big)
    # The glyphs are white-only and every caller tints them with COLOR_TEXT or
    # COLOR_TEXT_DIM, so the art itself has to carry the value separation: a
    # white glyph is 1.24:1 against panel.png's centre, 1.12:1 against the day
    # sky and 1.00:1 against a white cloud, and game.py draws icon_feather and
    # icon_play straight over the sky.  A *blur* cannot do that job -- measured
    # on the previous build, the contact shadow's peak alpha fell from 0.53 at
    # 144 px to 0.31 at 20 px and its coverage from 1.3% of the box to 0.4%,
    # because a Gaussian scales down into sub-pixel averaging.  A solid 3.5 px
    # contour is 0.5 px at 20 px and still reads as an edge; the ambient on top
    # of it is only there for lift.
    out = artlib.over(
        Image.new("RGBA", big.size, (0, 0, 0, 0)),
        _flat_layer(_contour(m, CONTOUR_PX) * CONTOUR_A, (8, 9, 20), big.size),
        artlib.drop_shadow(glyph, offset=(0, 2.4), blur=3.2,
                           color=(8, 9, 20), opacity=0.22),
        glyph)

    st = _measure(m)
    st["k"] = k
    st["sw"], st["sw_mass"] = dominant_width(m)
    st["widths"] = list(g.widths)
    return artlib.resolve(out, IW, IH), st


# ---------------------------------------------------------------------------
# medals
# ---------------------------------------------------------------------------
# Five tiers that have to be told apart at 40 px.  Hue does that work: warm
# brown, neutral grey, yellow, cyan-white, violet-white -- reinforced by five
# clearly different ribbon colours, which survive even when the relief does not.

MEDAL_TIERS = {
    "bronze": dict(
        metal=c(198, 122, 64), hi=c(255, 216, 162), lo=c(88, 40, 14),
        field=c(150, 82, 38), spec=0.62, spec_pow=24.0,
        ribbon=c(164, 68, 44), ribbon_lo=c(84, 26, 20), tint=c(255, 186, 120, 36)),
    "silver": dict(
        metal=c(188, 198, 216), hi=c(255, 255, 255), lo=c(58, 68, 92),
        field=c(126, 139, 166), spec=0.74, spec_pow=34.0,
        ribbon=c(50, 74, 124), ribbon_lo=c(20, 32, 64), tint=c(214, 230, 255, 30)),
    "gold": dict(
        metal=c(248, 198, 64), hi=c(255, 250, 200), lo=c(130, 76, 4),
        field=c(206, 146, 28), spec=0.82, spec_pow=28.0,
        ribbon=c(190, 48, 54), ribbon_lo=c(100, 18, 26), tint=c(255, 226, 132, 40)),
    "platinum": dict(
        metal=c(208, 243, 251), hi=c(255, 255, 255), lo=c(48, 118, 148),
        field=c(142, 206, 230), spec=0.98, spec_pow=56.0,
        ribbon=c(22, 152, 166), ribbon_lo=c(8, 72, 90), tint=c(188, 236, 255, 40),
        ring2=True),
    # Diamond is the 200-point tier and used to be the dullest thing on screen:
    # measured mean crown saturation 0.084 against bronze 0.564, gold 0.579,
    # platinum 0.171 and silver 0.108, with a crown luminance (166) that was
    # indistinguishable from platinum's (168).  The metal is cooled toward
    # violet-white to pull it off platinum, and the prism below now *adds*
    # chroma instead of washing it out.
    "diamond": dict(
        metal=c(206, 178, 255), hi=c(255, 255, 255), lo=c(88, 52, 196),
        field=c(168, 124, 250), spec=1.00, spec_pow=70.0,
        ribbon=c(140, 74, 228), ribbon_lo=c(60, 24, 126), tint=c(200, 170, 255, 60),
        facets=True, prism=True, sparkle=True),
}


def _poly_mask(w, h, polys, ss=SS):
    img = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(img)
    for p in polys:
        d.polygon([(x * ss, y * ss) for x, y in p], fill=255)
    return np.asarray(img, dtype=np.float64) / 255.0


def build_medal(tier: str) -> Image.Image:
    W, H = manifest.MEDAL_SIZE
    T = MEDAL_TIERS[tier]
    xs, ys = grid(W, H)
    XX = np.broadcast_to(xs[None, :], (H * SS, W * SS))
    YY = np.broadcast_to(ys[:, None], (H * SS, W * SS))

    DCX, DCY = W / 2.0, H * 0.378
    R_OUT, R_IN = W * 0.302, W * 0.222

    rad = np.hypot(XX - DCX, YY - DCY)
    theta = np.arctan2(YY - DCY, XX - DCX)
    ct, st = np.cos(theta), np.sin(theta)

    L3 = np.array([-0.50, -0.70, 0.51])
    L3 /= np.linalg.norm(L3)
    HV = L3 + np.array([0.0, 0.0, 1.0])
    HV /= np.linalg.norm(HV)

    def shade(nr, nz, base, spec_amt, spec_pow, rim=0.0):
        ln = np.sqrt(nr * nr + nz * nz)
        nx3, ny3, nz3 = nr * ct / ln, nr * st / ln, nz / ln
        diff = np.clip(nx3 * L3[0] + ny3 * L3[1] + nz3 * L3[2], 0.0, 1.0)
        spec = np.clip(nx3 * HV[0] + ny3 * HV[1] + nz3 * HV[2], 0.0, 1.0) ** spec_pow
        col = base[:3][None, None, :] * (0.32 + 0.68 * diff)[..., None]
        col = col + (T["hi"][:3][None, None, :] - col) * (spec * spec_amt)[..., None]
        if rim:
            col = col + (T["hi"][:3][None, None, :] - col) * \
                (np.clip(1.0 - diff, 0, 1) * rim)[..., None]
        return np.clip(col, 0, 1)

    out = blank(W, H)

    # ---- two ribbon tails, hanging from behind the disc -------------------
    def edge_dist(P, Q):
        """Perpendicular distance from every pixel to the line PQ."""
        ex, ey = Q[0] - P[0], Q[1] - P[1]
        ln = math.hypot(ex, ey) or 1e-9
        return np.abs((XX - P[0]) * ey - (YY - P[1]) * ex) / ln

    for sgn in (-1, 1):
        a = (DCX + sgn * 8.0, DCY + R_OUT * 0.44)       # top, inner
        b = (DCX + sgn * 64.0, DCY + R_OUT * 0.08)      # top, outer
        cc = (DCX + sgn * 116.0, H * 0.930)             # bottom, outer
        notch = (DCX + sgn * 86.0, H * 0.818)           # swallowtail
        dd = (DCX + sgn * 52.0, H * 0.972)              # bottom, inner
        mk = _poly_mask(W, H, [[a, b, cc, notch, dd]])
        # fold runs across the tail's own width, so the slant does not skew it
        di, do = edge_dist(a, dd), edge_dist(b, cc)
        u = np.clip(di / np.maximum(di + do, 1e-6), 0, 1)
        fold = 0.34 + 0.86 * np.sin(np.clip(u * 1.04, 0, 1) * math.pi) ** 1.05
        drop = np.clip((YY - DCY) / (H * 0.60), 0, 1)
        col = T["ribbon_lo"][:3] + (T["ribbon"][:3] - T["ribbon_lo"][:3]) * \
            np.clip(fold * (1.0 - 0.26 * drop), 0, 1)[..., None]
        gn = (artlib.value_noise(W * SS, H * SS, 10, 17 + sgn, 3) - 0.5) * 0.05
        out = over(out, layer(mk, np.clip(col * (1.0 + gn[..., None]), 0, 1)))
        # a lit crease along the fold's crown, and a turned dark edge
        crease = np.clip(1.0 - np.abs(u - 0.30) / 0.11, 0, 1) ** 1.5
        out = over(out, layer(crease * mk * 0.30, np.clip(T["ribbon"] * 1.5, 0, 1)))
        inner = mk * shift_a(mk, 3, 0) * shift_a(mk, -3, 0) * \
            shift_a(mk, 0, 3) * shift_a(mk, 0, -3)
        out = over(out, layer(blur_a(np.clip(mk - inner, 0, 1), 1.6 * SS) * 0.60,
                              T["ribbon_lo"] * 0.5))

    # the disc throws an occlusion shadow down onto the ribbons
    dsh = np.where(rad < R_OUT, 1.0, np.clip(1.0 - (rad - R_OUT) / 26.0, 0, 1) ** 1.5)
    out = over(out, layer(dsh * np.clip((YY - DCY) / 42.0, 0, 1) * 0.55, c(20, 10, 4)))

    # ---- disc: bevelled crown + recessed field ----------------------------
    cov_out = np.clip(0.5 - (rad - R_OUT) * SS, 0, 1)
    cov_in = np.clip(0.5 - (rad - R_IN) * SS, 0, 1)
    ring = np.clip(cov_out - cov_in, 0, 1)

    u = np.clip((rad - R_IN) / (R_OUT - R_IN), 0, 1)
    out = over(out, layer(ring * cov_out,
                          shade(-np.cos(math.pi * u) * 1.25, 1.0, T["metal"],
                                T["spec"], T["spec_pow"], rim=0.12)))

    if T.get("ring2"):   # platinum: a second concentric step on the crown
        g2 = np.clip(1.0 - np.abs(rad - (R_IN + (R_OUT - R_IN) * 0.70)) / 3.6, 0, 1)
        out = over(out, layer(g2 * ring * 0.42, T["lo"]))
        g3 = np.clip(1.0 - np.abs(rad - (R_IN + (R_OUT - R_IN) * 0.80)) / 2.6, 0, 1)
        out = over(out, layer(g3 * ring * 0.42, T["hi"]))

    wall = np.clip((rad - (R_IN - 15.0)) / 15.0, 0, 1) ** 1.4 * 1.45
    field_col = shade(wall, 1.0, T["field"], T["spec"] * 0.30, T["spec_pow"] * 0.6)
    field_col = np.clip(field_col * (1.0 - 0.22 * np.clip(rad / R_IN, 0, 1) ** 2)[..., None], 0, 1)
    fg = (artlib.value_noise(W * SS, H * SS, 14, 91, 4) - 0.5) * 0.05
    out = over(out, layer(cov_in, np.clip(field_col * (1.0 + fg[..., None]), 0, 1)))

    if T.get("facets"):
        # alternating ground facets, each with a hard edge where it meets its
        # neighbour -- that edge is what makes it read as cut stone
        wedge = ((theta - math.pi / 12.0) % (math.tau / 6.0)) / (math.tau / 6.0)
        across = np.clip((rad / R_IN) * 1.15, 0, 1)
        bright = np.clip(0.5 - np.abs(wedge - 0.26) * 3.4, 0, 1)
        dark = np.clip(0.5 - np.abs(wedge - 0.74) * 3.4, 0, 1)
        out = over(out, layer(cov_in * 0.46 * bright * across, c(255, 255, 255)))
        out = over(out, layer(cov_in * 0.30 * dark * across, c(88, 52, 196)))
        seam = np.clip(1.0 - np.abs(wedge - 0.5) * 22.0, 0, 1)
        out = over(out, layer(cov_in * 0.22 * seam * across, c(255, 255, 255)))
    if T.get("prism"):
        # Added, not composited over.  Three ramps all clipped around 0.5 laid
        # over the field at 20% alpha is a 20% mid-grey wash: it dragged the
        # spec'd lavender field (saturation 0.27) down to a rendered 0.175 and
        # the whole medal to 0.084, which reads as dirty silver, not cut stone.
        # Offsetting the ramps away from each other is what makes the gradient
        # refract across the face instead of greying it.
        pr = np.stack([np.clip(0.30 + (XX - DCX) / (R_IN * 1.5), 0, 1),
                       np.clip(0.10 + (DCY - YY) / (R_IN * 2.0), 0, 1),
                       np.clip(0.45 - (XX - DCX) / (R_IN * 1.8), 0, 1)], axis=-1)
        out[..., :3] = np.clip(out[..., :3] + pr * cov_in[..., None] * 0.22, 0, 1)

    # ---- embossed feather emblem ------------------------------------------
    blade, cuts, rachis = feather_shapes()
    # A struck emblem sits *inside* its field with air around it.  At 1.92 the
    # blade's farthest point was 75.6 px from the disc centre against an R_IN
    # of 63.9 -- an 18% overshoot -- and the `* cov_in` clamp then sliced 6.6%
    # of its ink flat along the circle, cutting the feather's tip and the quill
    # end off against the inner wall with a hard arc, on all five medals.
    esz = R_IN * 1.42
    # the -14 degree rotation and the feather's own asymmetry (a wide blade, a
    # thin quill) both pull its optical centre left of the geometric one
    ecx, ecy = DCX + 5.0, DCY - 1.0
    em = _poly_mask(W, H, [place(blade, ecx, ecy, esz, -14.0)])
    cutm = _poly_mask(W, H, [place(cc, ecx, ecy, esz, -14.0) for cc in cuts] +
                      [place(rachis, ecx, ecy, esz, -14.0)])
    em = np.clip(em - cutm, 0, 1) * cov_in

    k = int(3.0 * SS)
    emb_hi = np.clip(em - shift_a(em, k, k), 0, 1)
    emb_lo = np.clip(em - shift_a(em, -k, -k), 0, 1)
    emb_sh = blur_a(shift_a(em, int(1.1 * SS), int(1.5 * SS)), 1.7 * SS)
    out = over(out, layer(np.clip(emb_sh - em, 0, 1) * 0.34 * cov_in, c(26, 14, 8)))
    # struck proud of the recessed field, so the relief reads a tone lighter
    out = over(out, layer(em * 0.94, np.clip(T["metal"][:3] * 1.02, 0, 1)))
    # the two bevel walls: away from the key light, then into it
    out = over(out, layer(blur_a(emb_lo, 0.45 * SS) * 0.72, T["lo"]))
    out = over(out, layer(blur_a(emb_hi, 0.45 * SS) * 0.90, T["hi"]))

    # ---- specular sweep ----------------------------------------------------
    sweep = (XX - DCX) * math.cos(math.radians(-32)) + (YY - DCY) * math.sin(math.radians(-32))
    broad = np.clip(1.0 - np.abs(sweep + R_OUT * 0.30) / (R_OUT * 0.75), 0, 1) ** 1.6
    tight = np.clip(1.0 - np.abs(sweep + R_OUT * 0.54) / (R_OUT * 0.13), 0, 1) ** 1.4
    out = over(out, layer(cov_out * broad * 0.14 * T["spec"], c(255, 255, 255)))
    out = over(out, layer(cov_out * tight * 0.34 * T["spec"], c(255, 255, 255)))
    out = over(out, layer(cov_out * 0.10, T["tint"]))

    if T.get("sparkle"):
        for (sx, sy, sr) in ((DCX - R_OUT * 0.52, DCY - R_OUT * 0.56, 17.0),
                             (DCX + R_OUT * 0.60, DCY + R_OUT * 0.30, 11.0)):
            dx, dy = XX - sx, YY - sy
            star = (np.clip(1 - np.abs(dy) / 2.6, 0, 1) * np.clip(1 - np.abs(dx) / sr, 0, 1) +
                    np.clip(1 - np.abs(dx) / 2.6, 0, 1) * np.clip(1 - np.abs(dy) / sr, 0, 1))
            out = over(out, layer(np.clip(star, 0, 1) * cov_out * 0.85, c(255, 255, 255)))

    # outer turn + rim light on the crown
    edge_out = np.clip(1.0 - np.abs(rad - R_OUT) / 3.4, 0, 1) * cov_out
    face_key = ct * L3[0] + st * L3[1]
    out = over(out, layer(edge_out * 0.42, T["lo"] * 0.7))
    out = over(out, layer(edge_out * np.clip(face_key, 0, 1) * 0.72, T["hi"]))
    out = over(out, layer(edge_out * np.clip(-face_key, 0, 1) * 0.28,
                          np.clip(T["metal"] * 1.3, 0, 1)))

    # ---- whole-medal contact shadow ---------------------------------------
    sil = np.clip(out[..., 3], 0, 1)
    sm = np.roll(blur_a(sil, 3.4 * SS), int(5 * SS), axis=0)
    out = over(layer(np.clip(sm * 0.44 * (1.0 - sil), 0, 1), c(10, 6, 14)), out)
    return to_image(reduce_ss(out))


# ---------------------------------------------------------------------------
# logo
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=256)
def _font(size):
    # cached: the binary search below asks for a hundred-odd sizes, and each
    # uncached truetype() call holds an open file handle
    return ImageFont.truetype(FONT_PATH, int(round(size * SS)))


def _text_width(text, size, tracking=0.0):
    f = _font(size)
    probe = ImageDraw.Draw(Image.new("L", (8, 8)))
    return (sum(probe.textlength(ch, font=f) for ch in text)
            + tracking * SS * max(0, len(text) - 1)) / SS


def _fit_size(text, target_w, tracking_ratio=0.0, lo=40.0, hi=420.0):
    for _ in range(44):
        mid = (lo + hi) / 2.0
        if _text_width(text, mid, tracking_ratio * mid) > target_w:
            hi = mid
        else:
            lo = mid
    return lo


def _draw_text(draw, text, size, ox, oy, tracking):
    f = _font(size)
    x = ox * SS
    for ch in text:
        draw.text((x, oy * SS), ch, font=f, fill=255, anchor="ls")
        x += draw.textlength(ch, font=f) + tracking * SS


def build_logo() -> Image.Image:
    W, H = manifest.LOGO_SIZE
    BLOCK_W, TRACK = 758.0, 0.035

    # the two words are set to the same measure, which stacks them into one
    # solid block -- the usual way a short title is locked up
    s1 = _fit_size("FEATHER", BLOCK_W, TRACK)
    s2 = _fit_size("DASH", BLOCK_W, TRACK)
    f1, f2 = _font(s1), _font(s2)
    cap1 = (f1.getbbox("FEATHER")[3] - f1.getbbox("FEATHER")[1]) / SS
    cap2 = (f2.getbbox("DASH")[3] - f2.getbbox("DASH")[1]) / SS

    gap, extr = 20.0, 17.0
    total = cap1 + gap + cap2
    top = (H - total - extr) / 2.0 + 1.0
    block_x = W - 84.0 - BLOCK_W

    text = Image.new("L", (W * SS, H * SS), 0)
    td = ImageDraw.Draw(text)
    _draw_text(td, "FEATHER", s1, block_x, top + cap1, TRACK * s1)
    _draw_text(td, "DASH", s2, block_x, top + cap1 + gap + cap2, TRACK * s2)

    # --- the feather mark, locked to the left of the stack -----------------
    mark = Image.new("L", (W * SS, H * SS), 0)
    md = ImageDraw.Draw(mark)
    cut = Image.new("L", (W * SS, H * SS), 0)
    cd = ImageDraw.Draw(cut)
    blade, cuts, rachis = feather_shapes(cut_depth=0.40, rachis_start=0.11)
    mcx, mcy, msz = block_x - 142.0, top + total * 0.46, 286.0
    md.polygon([(x * SS, y * SS) for x, y in place(blade, mcx, mcy, msz, -10.0)], fill=255)
    for cc in cuts:
        cd.polygon([(x * SS, y * SS) for x, y in place(cc, mcx, mcy, msz, -10.0)], fill=255)
    cd.polygon([(x * SS, y * SS) for x, y in place(rachis, mcx, mcy, msz, -10.0)], fill=255)
    mark = ImageChops.subtract(mark, cut)

    fa = np.asarray(ImageChops.lighter(text, mark), dtype=np.float64) / 255.0
    hpx, wpx = fa.shape
    out = blank(W, H)

    # --- warm outer glow ----------------------------------------------------
    out = over(out, layer(np.clip(blur_a(fa, 13.0 * SS) * 1.7, 0, 1) * 0.44, c(255, 140, 54)))
    out = over(out, layer(np.clip(blur_a(fa, 36.0 * SS) * 2.1, 0, 1) * 0.22, c(255, 92, 38)))

    # --- extrusion: a solid swept slab, not a single offset copy -----------
    ext = fa.copy()
    for i in range(1, int(extr * SS) + 1):
        ext = np.maximum(ext, shift_a(fa, 0, i))
    ext = np.clip(ext - fa, 0, 1)
    yt = np.clip(np.arange(hpx) / SS / H, 0, 1)[:, None, None]
    ext_rgb = np.clip(c(132, 46, 22)[:3] + (c(74, 22, 14)[:3] - c(132, 46, 22)[:3]) * yt, 0, 1)
    out = over(out, layer(ext, np.broadcast_to(ext_rgb, (hpx, wpx, 3))))

    # --- dark contour around the whole lockup ------------------------------
    sil = np.maximum(fa, ext)
    grown = np.asarray(Image.fromarray((sil * 255).astype(np.uint8), "L")
                       .filter(ImageFilter.MaxFilter(int(4 * SS) | 1)),
                       dtype=np.float64) / 255.0
    out = over(out, layer(np.clip(grown - sil, 0, 1) * 0.95, c(52, 16, 10)))

    # --- the face: warm vertical gradient ----------------------------------
    y0, y1 = top - 24.0, top + total + 6.0
    tt = np.clip((np.arange(hpx) / SS - y0) / max(y1 - y0, 1.0), 0, 1)
    stops = [(0.00, c(255, 240, 192)), (0.15, c(255, 208, 110)),
             (0.40, c(253, 167, 64)), (0.66, c(246, 130, 48)),
             (0.88, c(236, 102, 44)), (1.00, c(224, 88, 44))]
    pos = np.array([p for p, _ in stops])
    cols = np.array([col for _, col in stops])
    frgb = np.stack([np.interp(tt, pos, cols[:, ch]) for ch in range(3)], axis=-1)
    out = over(out, layer(fa, np.broadcast_to(frgb[:, None, :], (hpx, wpx, 3))))

    # --- top-edge highlight, bottom-edge shade -----------------------------
    hi = np.clip(fa - shift_a(fa, 0, int(6.0 * SS)), 0, 1)
    out = over(out, layer(blur_a(hi, 1.2 * SS) * fa, c(255, 248, 212)))
    hi2 = np.clip(fa - shift_a(fa, 0, int(2.4 * SS)), 0, 1)
    out = over(out, layer(hi2 * fa * 0.80, c(255, 255, 244)))
    lo = np.clip(fa - shift_a(fa, 0, -int(5.0 * SS)), 0, 1)
    out = over(out, layer(blur_a(lo, 1.7 * SS) * fa * 0.46, c(184, 58, 30)))
    return to_image(reduce_ss(out))


# ---------------------------------------------------------------------------
# 9-slice reference implementation (also used by the proof sheets)
# ---------------------------------------------------------------------------

# The runtime maps a source inset onto `inset / UI_ART_SOURCE_SCALE` world
# units (featherdash/ui/widgets.py), so the *destination* corner is half the
# source corner in art pixels.  A proof sheet that slices at ci = inset instead
# renders panel corners 2.1-2.9x and button corners 1.9x larger than they ever
# ship, and so certifies shading the game cannot show.
UI_ART_SOURCE_SCALE = 2.0
VP_SCALE = 2340.0 / manifest.WORLD_H      # a 1080x2340 phone

# (file, inset, world width, world height, label) -- what the scenes ask for:
# game.py:341 and shop.py/settings.py for the panels, menu/game/settings for
# the buttons.
SHIPPED_FRAMES = [
    ("panel_dark.png", manifest.PANEL_INSET, 438.0, 430.0, "results panel"),
    ("panel_dark.png", manifest.PANEL_INSET, 458.0, 605.0, "shop panel"),
    ("panel.png", manifest.PANEL_INSET, 438.0, 430.0, "light panel"),
    ("button.png", manifest.BUTTON_INSET, 330.0, 126.0, "primary"),
    ("button_down.png", manifest.BUTTON_INSET, 330.0, 126.0, "primary pressed"),
    ("button_ghost.png", manifest.BUTTON_INSET, 330.0, 126.0, "ghost"),
    ("button_ghost_down.png", manifest.BUTTON_INSET, 330.0, 126.0, "ghost pressed"),
    ("button.png", manifest.BUTTON_INSET, 340.0, 118.0, "primary, short"),
    ("button_ghost.png", manifest.BUTTON_INSET, 320.0, 96.0, "ghost, short"),
]


def nine_slice(img: Image.Image, inset: int, w: int, h: int,
               dst_inset: int | None = None) -> Image.Image:
    """Stretch `img` to w x h the way featherdash/ui/widgets._nine_slice does."""
    W, H = img.size
    si = max(1, min(int(inset), W // 2, H // 2))
    if dst_inset is None:
        dst_inset = int(round(si / UI_ART_SOURCE_SCALE))
    ci = max(1, min(int(dst_inset), w // 2, h // 2))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    def piece(bx0, by0, bx1, by1, dx0, dy0, dw, dh):
        if dw <= 0 or dh <= 0:
            return
        out.alpha_composite(
            img.crop((bx0, by0, bx1, by1)).resize((dw, dh), Image.BICUBIC), (dx0, dy0))

    mx, my = w - 2 * ci, h - 2 * ci
    piece(0, 0, si, si, 0, 0, ci, ci)
    piece(W - si, 0, W, si, w - ci, 0, ci, ci)
    piece(0, H - si, si, H, 0, h - ci, ci, ci)
    piece(W - si, H - si, W, H, w - ci, h - ci, ci, ci)
    piece(si, 0, W - si, si, ci, 0, mx, ci)
    piece(si, H - si, W - si, H, ci, h - ci, mx, ci)
    piece(0, si, si, H - si, 0, ci, ci, my)
    piece(W - si, si, W, H - si, w - ci, ci, ci, my)
    piece(si, si, W - si, H - si, ci, ci, mx, my)
    return out


def shipped_frame(name: str, inset: int, world_w: float, world_h: float,
                  scale: float = VP_SCALE) -> Image.Image:
    """Exactly what the player sees: the game's sizes, the game's corner rule."""
    img = Image.open(manifest.image_path(name)).convert("RGBA")
    w, h = max(1, int(round(world_w * scale))), max(1, int(round(world_h * scale)))
    di = max(1, int(round(inset / UI_ART_SOURCE_SCALE * scale)))
    return nine_slice(img, inset, w, h, di)


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_nine_slice(path, inset, label):
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.int16)
    H, W = a.shape[:2]
    problems = []
    centre = a[inset:H - inset, inset:W - inset]
    if centre.size:
        dev = int(np.abs(centre - centre[0, 0]).max())
        if dev:
            problems.append(f"centre not uniform (max dev {dev})")
    for nm, blk, axis in (("top", a[0:inset, inset:W - inset], 1),
                          ("bottom", a[H - inset:H, inset:W - inset], 1),
                          ("left", a[inset:H - inset, 0:inset], 0),
                          ("right", a[inset:H - inset, W - inset:W], 0)):
        if blk.size == 0:
            continue
        ref = blk[:, :1] if axis == 1 else blk[:1, :]
        dev = int(np.abs(blk - ref).max())
        if dev:
            problems.append(f"{nm} edge varies along stretch axis (max dev {dev})")
    print(f"  [{'OK ' if not problems else 'FAIL'}] 9-slice {label}: "
          + ("; ".join(problems) or "stretch-invariant"))
    return not problems


def verify_shipped_frames():
    """Every frame must still have depth once the game's 9-slice has stretched it.

    This is the check the kit was missing.  A 320 px panel with a 72 px inset
    passed everything else here -- stretch bands invariant, joins seamless --
    and still shipped a featureless dark rectangle, because only 8% of a
    430-unit panel's height carried any shading and the other 84% was one flat
    colour.  Seam-free is necessary; it is not sufficient.
    """
    problems = []
    for name, inset, ww, wh, label in SHIPPED_FRAMES:
        a = np.asarray(shipped_frame(name, inset, ww, wh), dtype=np.float64)
        limit = 0.58 if "panel" in label else 0.42
        h, w = a.shape[:2]
        col = a[:, w // 2, :3].mean(axis=1)[a[:, w // 2, 3] > 8]
        if col.size < 8:
            problems.append(f"{label}: nothing drawn")
            continue
        # Area, not a column: the 9-slice flattens the centre patch in *both*
        # axes, so the honest number is the share of the surface that carries
        # no shading at all.  It was 72% of the results panel and 77% of the
        # shop panel; the budget below is what 150 px of inset buys back.
        ctr = a[h // 2, w // 2, :3]
        flat = float(np.mean((np.abs(a[..., :3] - ctr).max(axis=-1) < 1.0) & (a[..., 3] > 8)))
        rng = float(col.max() - col.min())
        bad = []
        if flat > limit:
            bad.append(f"{flat:.0%} of its area is one flat colour")
        if rng < 26.0:
            bad.append(f"only {rng:.0f}/255 of vertical range")
        print(f"  [{'OK ' if not bad else 'FAIL'}] shipped {label:16s}"
              f" {int(ww)}x{int(wh)}u: flat area {flat:3.0%} (max {limit:.0%}),"
              f" range {rng:5.1f}" + ("  <-- " + "; ".join(bad) if bad else ""))
        if bad:
            problems.append(label)
    return not problems


def darkest_over(img: Image.Image, bg, size: int = 24) -> float:
    """Luminance of the darkest pixel once `img` is composited onto `bg`.

    Measured at the size the game draws icons, not at the authored 144: a
    baked Gaussian shadow looks fine at source resolution and averages itself
    out of existence on the way down.
    """
    small = img.resize((size, size), Image.LANCZOS)
    a = np.asarray(small.convert("RGBA"), dtype=np.float64) / 255.0
    rgb = a[..., :3] * a[..., 3:4] + \
        (np.asarray(bg, dtype=np.float64) / 255.0) * (1.0 - a[..., 3:4])
    return float((rgb @ np.array([0.2126, 0.7152, 0.0722])).min() * 255.0)


def verify_image(path, size, label):
    im = Image.open(path)
    a = np.asarray(im)
    problems = []
    if im.mode != "RGBA":
        problems.append(f"mode {im.mode}")
    if im.size != tuple(size):
        problems.append(f"size {im.size} != {tuple(size)}")
    al = a[..., 3]
    if int(al.max()) - int(al.min()) < 32:
        problems.append(f"alpha has no range ({al.min()}..{al.max()})")
    vis = a[..., :3][al > 16]
    if vis.size == 0:
        problems.append("fully transparent")
    elif float(vis.astype(np.float64).std()) < 3.0:
        problems.append(f"rgb variance too low ({float(vis.std()):.2f})")
    corners = [int(al[0, 0]), int(al[0, -1]), int(al[-1, 0]), int(al[-1, -1])]
    if max(corners) > 0:
        problems.append(f"corners not transparent {corners}")
    if problems:
        print(f"  [FAIL] {label}: " + "; ".join(problems))
    return not problems


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    manifest.ensure_dirs()
    written = []

    def save(img, name):
        img.save(manifest.image_path(name))
        written.append(name)

    print("panels...")
    for nm in ("panel", "panel_dark"):
        save(build_panel(nm), nm + ".png")

    print("buttons...")
    save(build_button("up"), "button.png")
    save(build_button("down"), "button_down.png")
    save(build_button("ghost"), "button_ghost.png")
    # the secondary needs a down state of its own: widgets._frame_name used to
    # hand back button_ghost.png for both, so a held ghost button never moved
    save(build_button("ghost_down"), "button_ghost_down.png")

    print("icons...")
    stats = {}
    for nm in manifest.ICONS:
        img, st = build_icon(nm)
        save(img, f"icon_{nm}.png")
        st["dark"] = darkest_over(img, (240, 240, 245))
        stats[nm] = st

    print("medals...")
    for t in manifest.MEDALS:
        save(build_medal(t), f"medal_{t}.png")

    print("logo...")
    save(build_logo(), "logo.png")

    med = float(np.median([s["ink"] for s in stats.values()]))
    print("\n  icon optical audit (fit scale, extent, centre offset, ink, stroke, contrast):")
    icon_ok = True
    for nm in manifest.ICONS:
        s = stats[nm]
        bw, bh = s["x1"] - s["x0"], s["y1"] - s["y0"]
        dx, dy = (s["x0"] + s["x1"]) / 2 - ICX, (s["y0"] + s["y1"]) / 2 - ICY
        flags = []
        if not 0.80 <= s["k"] <= 1.25:
            flags.append("fit scale far from 1 -> stroke drift")
        if max(bw, bh) > BOXE * ICON_FIT[FIT_WITH.get(nm, nm)] + 0.75 \
                and nm not in SLASHED:
            flags.append(f"breaks the {PAD:.0%} padding box")
        # One stroke weight.  Measuring the ridge median and comparing it with
        # the *set* median, as first proposed, flags every solid glyph -- a
        # play triangle's medial width is 73 px and nothing is wrong with it.
        # Two checks that do not have that false positive: the exact width
        # every stroked glyph asks for (this is where the 0.76-1.26 hand-picked
        # multipliers lived), and a floor on the measured ridge, which catches
        # a thin feature built out of rrects instead of strokes -- the device's
        # 7.5 px outline, for one.
        off = [w for w in s["widths"] if abs(w / STROKE_TARGET - 1.0) > 0.02]
        if off:
            flags.append(f"stroke width {off[0]:.1f} != the set's {STROKE_TARGET:.1f}")
        if s["sw_mass"] >= 0.25 and 0 < s["sw"] < 0.82 * STROKE_TARGET:
            flags.append(f"{s['sw_mass']:.0%} of its ridge sits at {s['sw']:.1f},"
                         f" lighter than the set stroke")
        # a white glyph has to separate from a light surface on its own
        if s["dark"] > 140:
            flags.append(f"no contrast on a light surface (darkest {s['dark']:.0f})")
        if flags:
            icon_ok = False
        print(f"    {nm:12s} k={s['k']:.2f}  box {bw:5.1f}x{bh:5.1f}"
              f"  off ({dx:+5.1f},{dy:+5.1f})  ink {s['ink'] / med:4.2f}x"
              f"  sw {s['sw']:4.1f}@{s['sw_mass']:3.0%}  dark {s['dark']:5.1f}"
              + ("   <-- " + "; ".join(flags) if flags else ""))

    print("\n  verification:")
    exp = manifest.expected_images()
    ok = icon_ok
    for nm in written:
        ok &= verify_image(manifest.image_path(nm), exp[nm], nm)
    ok &= verify_nine_slice(manifest.image_path("panel.png"), manifest.PANEL_INSET, "panel")
    ok &= verify_nine_slice(manifest.image_path("panel_dark.png"), manifest.PANEL_INSET,
                            "panel_dark")
    for b in ("button", "button_down", "button_ghost", "button_ghost_down"):
        ok &= verify_nine_slice(manifest.image_path(b + ".png"), manifest.BUTTON_INSET, b)
    ok &= verify_shipped_frames()
    print(f"\n  {len(written)} files written -> {manifest.IMAGES}")
    print("  ALL CHECKS PASSED" if ok else "  *** CHECKS FAILED ***")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
