"""Drawing primitives for Feather Dash's procedural art pipeline.

Everything is authored here from scratch: no sampled, traced or downloaded
imagery is involved, so the generated assets are original works owned by the
project. Shapes are rasterised at `SS` times the target size and reduced with a
Lanczos filter, which gives clean antialiasing without shipping vector runtimes.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 4  # supersampling factor

RGBA = tuple[int, int, int, int]
Color = Sequence[int]


# --------------------------------------------------------------------------
# canvas helpers
# --------------------------------------------------------------------------

def canvas(w: int, h: int, fill: RGBA = (0, 0, 0, 0)) -> Image.Image:
    """Create a supersampled transparent canvas for a `w` x `h` target."""
    return Image.new("RGBA", (w * SS, h * SS), tuple(fill))


def draw_on(img: Image.Image) -> ImageDraw.ImageDraw:
    return ImageDraw.Draw(img, "RGBA")


def resolve(img: Image.Image, w: int, h: int) -> Image.Image:
    """Reduce a supersampled canvas to its final size."""
    return img.resize((w, h), Image.LANCZOS)


def s(v: float) -> int:
    """Scale a target-space measurement into supersampled space."""
    return int(round(v * SS))


def box(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    """Supersampled bounding box from target-space x/y/width/height."""
    return (s(x), s(y), s(x + w), s(y + h))


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------

def rgba(c: Color, a: int | None = None) -> RGBA:
    if len(c) == 4:
        r, g, b, ca = c
    else:
        r, g, b = c
        ca = 255
    return (int(r), int(g), int(b), int(ca if a is None else a))


def mix(c1: Color, c2: Color, t: float) -> RGBA:
    a, b = rgba(c1), rgba(c2)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))  # type: ignore[return-value]


def shade(c: Color, amount: float) -> RGBA:
    """Darken (amount < 0) or lighten (amount > 0) a colour, keeping alpha."""
    r, g, b, a = rgba(c)
    if amount >= 0:
        return (
            int(r + (255 - r) * amount),
            int(g + (255 - g) * amount),
            int(b + (255 - b) * amount),
            a,
        )
    k = 1.0 + amount
    return (int(r * k), int(g * k), int(b * k), a)


def saturate(c: Color, amount: float) -> RGBA:
    r, g, b, a = rgba(c)
    grey = 0.299 * r + 0.587 * g + 0.114 * b
    return (
        max(0, min(255, int(grey + (r - grey) * amount))),
        max(0, min(255, int(grey + (g - grey) * amount))),
        max(0, min(255, int(grey + (b - grey) * amount))),
        a,
    )


# --------------------------------------------------------------------------
# gradients
# --------------------------------------------------------------------------

def vertical_gradient(w: int, h: int, stops: Sequence[tuple[float, Color]]) -> Image.Image:
    """Linear top-to-bottom gradient. `stops` are (position 0..1, colour)."""
    stops = sorted(stops, key=lambda it: it[0])
    pos = np.array([p for p, _ in stops], dtype=np.float64)
    cols = np.array([rgba(c) for _, c in stops], dtype=np.float64)
    t = np.linspace(0.0, 1.0, h)
    column = np.empty((h, 4), dtype=np.float64)
    for ch in range(4):
        column[:, ch] = np.interp(t, pos, cols[:, ch])
    data = np.repeat(column[:, None, :], w, axis=1)
    return Image.fromarray(data.round().astype(np.uint8), "RGBA")


def horizontal_gradient(w: int, h: int, stops: Sequence[tuple[float, Color]]) -> Image.Image:
    return vertical_gradient(h, w, stops).transpose(Image.ROTATE_270)


def radial_gradient(
    w: int,
    h: int,
    stops: Sequence[tuple[float, Color]],
    center: tuple[float, float] = (0.5, 0.5),
    radius: float = 0.5,
) -> Image.Image:
    """Radial gradient; `center` is fractional, `radius` is a fraction of width."""
    stops = sorted(stops, key=lambda it: it[0])
    pos = np.array([p for p, _ in stops], dtype=np.float64)
    cols = np.array([rgba(c) for _, c in stops], dtype=np.float64)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    cx, cy = center[0] * w, center[1] * h
    r = max(radius * w, 1e-6)
    dist = np.clip(np.hypot(xx - cx, yy - cy) / r, 0.0, 1.0)
    out = np.empty((h, w, 4), dtype=np.float64)
    for ch in range(4):
        out[:, :, ch] = np.interp(dist, pos, cols[:, ch])
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def apply_gradient(img: Image.Image, gradient: Image.Image) -> Image.Image:
    """Paint `gradient` through the alpha channel of `img`."""
    grad = gradient.resize(img.size, Image.BILINEAR).convert("RGBA")
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(grad, (0, 0), img)
    return out


# --------------------------------------------------------------------------
# shading effects
# --------------------------------------------------------------------------

def alpha_mask(img: Image.Image) -> Image.Image:
    return img.getchannel("A")


def drop_shadow(
    img: Image.Image,
    offset: tuple[float, float] = (0, 6),
    blur: float = 8,
    color: Color = (0, 0, 0),
    opacity: float = 0.35,
    spread: float = 0.0,
) -> Image.Image:
    """Return a shadow layer sized like `img`, to be composited underneath it."""
    mask = alpha_mask(img)
    if spread:
        mask = mask.filter(ImageFilter.MaxFilter(int(spread) * 2 + 1))
    mask = mask.filter(ImageFilter.GaussianBlur(blur * SS))
    mask = mask.point(lambda v: int(v * opacity))
    shadow = Image.new("RGBA", img.size, rgba(color, 255))
    shadow.putalpha(mask)
    return ImageChops.offset(shadow, s(offset[0]), s(offset[1]))


def inner_shadow(img: Image.Image, blur: float = 6, color: Color = (0, 0, 0), opacity: float = 0.4) -> Image.Image:
    """Darken the inside edge of a shape, following its own silhouette."""
    mask = alpha_mask(img)
    eroded = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(blur * SS))
    edge = ImageChops.subtract(mask, eroded)
    edge = edge.point(lambda v: int(v * opacity))
    layer = Image.new("RGBA", img.size, rgba(color, 255))
    layer.putalpha(ImageChops.multiply(edge, mask))
    return Image.alpha_composite(img, layer)


def outline(img: Image.Image, width: float = 2, color: Color = (0, 0, 0), opacity: float = 1.0) -> Image.Image:
    """Add a soft outline around the silhouette of `img`."""
    k = max(1, s(width))
    mask = alpha_mask(img)
    grown = mask.filter(ImageFilter.MaxFilter(k * 2 + 1)).filter(ImageFilter.GaussianBlur(k * 0.4))
    grown = grown.point(lambda v: int(min(255, v * 1.6) * opacity))
    layer = Image.new("RGBA", img.size, rgba(color, 255))
    layer.putalpha(grown)
    return Image.alpha_composite(layer, img)


def glow(img: Image.Image, radius: float = 10, color: Color = (255, 255, 255), opacity: float = 0.6) -> Image.Image:
    mask = alpha_mask(img).filter(ImageFilter.GaussianBlur(radius * SS))
    mask = mask.point(lambda v: int(v * opacity))
    layer = Image.new("RGBA", img.size, rgba(color, 255))
    layer.putalpha(mask)
    return Image.alpha_composite(layer, img)


def clip_to(layer: Image.Image, mask_src: Image.Image) -> Image.Image:
    """Keep only the part of `layer` that overlaps the silhouette of `mask_src`."""
    out = layer.copy()
    out.putalpha(ImageChops.multiply(alpha_mask(layer), alpha_mask(mask_src)))
    return out


def over(base: Image.Image, *layers: Image.Image) -> Image.Image:
    out = base
    for layer in layers:
        out = Image.alpha_composite(out, layer)
    return out


def screen(base: Image.Image, layer: Image.Image) -> Image.Image:
    """Screen `layer` onto `base`, keeping `base`'s alpha.

    Normal alpha compositing can only interpolate between the two colours, so a
    pale streak laid over a dark field lands on their muddy midpoint and can
    never read as *light*. Screening lifts instead of interpolating, which is
    what a glow, a speed smear or a lens flare needs. `base` must be opaque
    where it matters: screening onto transparency has nothing to lift.
    """
    if layer.size != base.size:
        layer = layer.resize(base.size, Image.BILINEAR)
    b = np.asarray(base.convert("RGBA"), dtype=np.float32) / 255.0
    lay = np.asarray(layer.convert("RGBA"), dtype=np.float32) / 255.0
    a = lay[:, :, 3:4]
    rgb = 1.0 - (1.0 - b[:, :, :3]) * (1.0 - lay[:, :, :3] * a)
    out = np.concatenate([rgb, b[:, :, 3:4]], axis=2)
    return Image.fromarray((out * 255.0).round().astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------
# texture
# --------------------------------------------------------------------------

def value_noise(w: int, h: int, cells: int, seed: int, octaves: int = 4) -> np.ndarray:
    """Tileable fractal value noise in 0..1, shaped (h, w)."""
    rng = np.random.default_rng(seed)
    total = np.zeros((h, w), dtype=np.float64)
    amplitude, norm = 1.0, 0.0
    for octave in range(octaves):
        n = max(2, cells * (2**octave))
        grid = rng.random((n, n))
        grid = np.pad(grid, ((0, 1), (0, 1)), mode="wrap")
        ys = np.linspace(0, n, h, endpoint=False)
        xs = np.linspace(0, n, w, endpoint=False)
        y0, x0 = ys.astype(int), xs.astype(int)
        fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
        # smoothstep keeps octave seams invisible
        fy, fx = fy * fy * (3 - 2 * fy), fx * fx * (3 - 2 * fx)
        g00 = grid[np.ix_(y0, x0)]
        g10 = grid[np.ix_(y0 + 1, x0)]
        g01 = grid[np.ix_(y0, x0 + 1)]
        g11 = grid[np.ix_(y0 + 1, x0 + 1)]
        top = g00 + (g01 - g00) * fx
        bot = g10 + (g11 - g10) * fx
        total += amplitude * (top + (bot - top) * fy)
        norm += amplitude
        amplitude *= 0.5
    return total / norm


def grain(img: Image.Image, strength: float = 0.05, seed: int = 0, cells: int = 8) -> Image.Image:
    """Overlay subtle fractal noise so flat fills do not look plastic."""
    w, h = img.size
    n = value_noise(w, h, cells, seed)
    arr = np.asarray(img, dtype=np.float64)
    factor = 1.0 + (n - 0.5) * 2.0 * strength
    arr[:, :, :3] = np.clip(arr[:, :, :3] * factor[:, :, None], 0, 255)
    return Image.fromarray(arr.round().astype(np.uint8), "RGBA")


# --------------------------------------------------------------------------
# shape helpers
# --------------------------------------------------------------------------

def blob(
    img: Image.Image,
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    color: Color,
    rotation: float = 0.0,
) -> None:
    """Draw a rotated filled ellipse directly onto a supersampled canvas."""
    d = draw_on(img)
    if not rotation:
        d.ellipse((s(cx - rx), s(cy - ry), s(cx + rx), s(cy + ry)), fill=rgba(color))
        return
    pts = []
    for i in range(96):
        a = i / 96 * math.tau
        x, y = rx * math.cos(a), ry * math.sin(a)
        ca, sa = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
        pts.append((s(cx + x * ca - y * sa), s(cy + x * sa + y * ca)))
    d.polygon(pts, fill=rgba(color))


def rounded(img: Image.Image, x: float, y: float, w: float, h: float, r: float, color: Color) -> None:
    draw_on(img).rounded_rectangle(box(x, y, w, h), radius=s(r), fill=rgba(color))


def poly(img: Image.Image, points: Iterable[tuple[float, float]], color: Color) -> None:
    draw_on(img).polygon([(s(x), s(y)) for x, y in points], fill=rgba(color))


def ridge_profile(width: int, peaks: int, seed: int, roughness: float = 0.45) -> np.ndarray:
    """A seamless 0..1 skyline silhouette, used for distant hills and ridges."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, math.tau, width, endpoint=False)
    out = np.zeros(width)
    amp, freq = 1.0, float(peaks)
    for _ in range(5):
        out += amp * np.sin(freq * x + rng.random() * math.tau)
        amp *= roughness
        freq *= 2
    out -= out.min()
    return out / max(out.max(), 1e-9)
