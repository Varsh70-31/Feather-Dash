"""Text rendering in world units, with memory-bounded surface caches.

Every string the game draws goes through here. Four things matter:

* **Resolution independence.** A caller asks for a size in *world units* (or a
  key from `config.FONT_SIZES`); the pixel size is derived from the viewport at
  draw time. A 46-unit label is the same fraction of the screen on a 540px phone
  and a 4K monitor.
* **Legibility over a moving sky.** The drop shadow is not decoration. White
  text crossing a bright cloud disappears without it, so `shadow=True` is the
  default and switching it off should be a deliberate choice.
* **Cost is paid once, never per frame.** `Font.render` allocates and
  rasterises; doing it every frame for the score, the HUD and every button
  label is the classic pygame framerate leak. Everything a draw needs is baked
  into *one* cached surface per state: the outline, the five shadow taps and
  any punch scale are all resolved at build time, so a shadowed, outlined,
  scaled string costs two blits however elaborate it looks.
* **The bound is bytes, not entries.** A cached block's area grows with the
  square of the viewport scale and with the length of the string, so "384
  entries" means a few hundred KB of `tiny` labels and the better part of a
  gigabyte of outlined `mega` numbers at phone resolution. Android kills the
  process long before an entry counter notices, so `SurfaceCache` evicts on a
  real memory budget and keeps the entry cap only as a secondary guard.

Caches are dropped by `invalidate()`, which the game calls on resize -- the one
moment every pixel size changes at once.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Tuple, Union

import pygame

from .. import assets, config
from ..display import Viewport

Color = Tuple[int, int, int]
SizeSpec = Union[str, float, int]
Placement = Tuple[str, int, int]

__all__ = [
    "draw_text", "draw_number", "measure", "measure_px", "px_for", "number_width",
    "font", "invalidate", "cache_size", "cache_bytes", "cache_stats",
    "MAX_CACHE_ENTRIES", "MAX_CACHE_BYTES",
]

# --------------------------------------------------------------------------
# tuning
# --------------------------------------------------------------------------
# Entries are the secondary guard; bytes are the real bound. 16 MB is generous
# for the dozen or so live strings a screen actually holds and still leaves a
# 1 GB Android device plenty of room for the atlas, the audio and the heap.
MAX_CACHE_ENTRIES = 384
MAX_CACHE_BYTES = 16 * 1024 * 1024
# One pool, not three. The glyph composites, their baked shadows and their
# punch-scaled copies have wildly different sizes from screen to screen -- a
# results dialog is all composites, a HUD score mid-punch is almost all
# scaled copies -- so fixed sub-budgets would starve whichever one the
# current screen actually uses. A shared pool with least-recently-used
# eviction gives the live set whatever it needs and ages out the rest.

_MIN_PX = 8                 # below this the font is unreadable anyway
_MAX_FIT_STEPS = 16         # iterations allowed when shrinking to `max_width`

# Scale is quantised before it reaches a cache key: a punch sweeps a continuous
# range, and without this every frame of it is a unique surface. Hundredths are
# finer than a pixel on any glyph the game draws, so the animation is unchanged
# while a whole punch collapses to ~20 surfaces that every later punch reuses.
_SCALE_STEPS = 100
# The shadow is a soft blob behind the glyph, so it is quantised far more
# coarsely than the glyph it sits under: a 5% error in its size is invisible,
# and it halves both the resampling work and what the scaled cache holds.
_SHADOW_SCALE_STEPS = 20
_SCALE_EPS = 0.005

# Shadow geometry is expressed as a fraction of the font's pixel size so it
# scales with the text instead of vanishing on a tablet.
_SHADOW_OFFSET = 0.062
_SHADOW_SPREAD = 0.030
_SHADOW_ALPHA = 190
# A soft shadow is five cheap taps of the glyph rather than a blur -- but they
# are composited into one cached surface at build time, not blitted separately
# at draw time. "over" is associative and every tap is the same colour, so the
# pre-composed result is pixel-identical to five blits against the backdrop,
# at a sixth of the per-frame cost.
_SHADOW_TAPS: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0), (-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0),
)
_SHADOW_TAP_ALPHA = 0.42

_DIGITS = "0123456789"


# --------------------------------------------------------------------------
# caches
# --------------------------------------------------------------------------
class SurfaceCache:
    """Insertion-ordered surface cache bounded by bytes first, entries second.

    Shared with `widgets.py`, which has exactly the same problem with its
    nine-slice frames. Not part of the kit's public API.

    Eviction drops the oldest entries in a batch rather than one per miss, so
    a full cache costs amortised nothing instead of an eviction on every
    insert; it keeps going until the budget is met, because one `mega` entry
    can be larger than a hundred `tiny` ones.
    """

    __slots__ = ("_items", "_bytes", "max_entries", "max_bytes", "_stats")

    def __init__(self, max_entries: int, max_bytes: int,
                 stats: Optional[Dict[str, int]] = None) -> None:
        self._items: Dict[tuple, pygame.Surface] = {}
        self._bytes = 0
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self._stats = stats

    @staticmethod
    def cost(surf: pygame.Surface) -> int:
        return surf.get_width() * surf.get_height() * 4

    def get(self, key: tuple) -> Optional[pygame.Surface]:
        surf = self._items.pop(key, None)
        if surf is not None:
            # Re-insert to make eviction least-recently-*used* rather than
            # oldest-inserted. The caches are shared between things with very
            # different lifetimes -- a score that is redrawn every frame and a
            # label drawn once -- and plain FIFO throws away the hot one.
            self._items[key] = surf
        if self._stats is not None:
            self._stats["hits" if surf is not None else "misses"] += 1
        return surf

    def put(self, key: tuple, surf: pygame.Surface) -> pygame.Surface:
        existing = self._items.pop(key, None)
        if existing is not None:
            self._bytes -= self.cost(existing)
        self._items[key] = surf
        self._bytes += self.cost(surf)
        self._trim()
        return surf

    def _trim(self) -> None:
        if len(self._items) <= self.max_entries and self._bytes <= self.max_bytes:
            return
        batch = max(1, self.max_entries // 4)
        while self._items and (len(self._items) > self.max_entries - batch
                               or self._bytes > self.max_bytes):
            key = next(iter(self._items))
            self._bytes -= self.cost(self._items.pop(key))
            if self._stats is not None:
                self._stats["evictions"] += 1

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0

    def values(self) -> Iterable[pygame.Surface]:
        return self._items.values()

    @property
    def nbytes(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: tuple) -> bool:
        return key in self._items


_stats = {"hits": 0, "misses": 0, "evictions": 0}

_surfaces = SurfaceCache(MAX_CACHE_ENTRIES * 4, MAX_CACHE_BYTES, _stats)
# Three views onto that one pool, kept as separate names because the keys are
# built in three different places and a stray collision between them would be
# a very confusing bug. The leading tag in each key is what keeps them apart.
_glyphs = _shadows = _scales = _surfaces

# Metadata, not pixels: these hold tuples of ints and are bounded by count.
_blocks: Dict[tuple, Tuple[int, int, Tuple[Placement, ...]]] = {}
_advances: Dict[int, int] = {}
_fits: Dict[tuple, int] = {}
_warned_sizes: set = set()


def _bound(cache: dict, limit: int) -> None:
    """Drop the oldest quarter of a *metadata* dict once `limit` is passed."""
    if len(cache) <= limit:
        return
    for key in list(cache)[: max(1, limit // 4)]:
        cache.pop(key, None)
        _stats["evictions"] += 1


def invalidate() -> None:
    """Drop every cached surface. The game calls this on a viewport change."""
    _surfaces.clear()
    _blocks.clear()
    _advances.clear()
    _fits.clear()


def cache_size() -> int:
    """Number of cached surfaces -- the secondary bound."""
    return len(_surfaces)


def cache_bytes() -> int:
    """Live bytes held by this module's surface cache -- the real bound."""
    return _surfaces.nbytes


def cache_stats() -> Dict[str, int]:
    out = dict(_stats)
    out["entries"] = len(_surfaces)
    out["bytes"] = _surfaces.nbytes
    out["budget"] = _surfaces.max_bytes
    return out


# --------------------------------------------------------------------------
# sizing
# --------------------------------------------------------------------------
def font(px: int) -> pygame.font.Font:
    """The game font at a pixel size, initialising pygame.font if nobody has.

    A tool, a test or an early-boot screen that only needs metrics should not
    have to know about pygame's init order.
    """
    if not pygame.font.get_init():
        pygame.font.init()
    return assets.font(px)


_font = font  # internal shorthand, kept so the hot paths read cleanly


def _units(size: SizeSpec) -> float:
    """Resolve a size spec to world units."""
    if isinstance(size, str):
        value = config.FONT_SIZES.get(size)
        if value is None:
            # A typo in a rarely-visited screen must not take the game down in
            # a player's hands; fall back loudly instead.
            if size not in _warned_sizes:
                _warned_sizes.add(size)
                print(f"[ui.text] unknown font size {size!r}, using 'body'")
            value = config.FONT_SIZES["body"]
        return float(value)
    return float(size)


def px_for(size: SizeSpec, vp: Viewport) -> int:
    """World-unit (or named) size to a concrete pixel size for this viewport."""
    return max(_MIN_PX, int(round(_units(size) * vp.scale)))


def _outline_px(outline: float, vp: Viewport) -> int:
    if outline <= 0:
        return 0
    return max(1, int(round(outline * vp.scale)))


def _quantise_scale(scale: float) -> float:
    return round(float(scale) * _SCALE_STEPS) / _SCALE_STEPS


def _block(px: int, text: str) -> Tuple[int, int, Tuple[Placement, ...]]:
    """Measure a (possibly multi-line) string and place its lines.

    Returns (width, height, placements) in pixels, excluding outline padding.
    `_composite` builds its surface from exactly this, so `measure()` can never
    disagree with what was actually drawn.
    """
    key = (px, text)
    cached = _blocks.get(key)
    if cached is not None:
        return cached

    font = _font(px)
    lines = text.split("\n")
    sizes = [font.size(line) for line in lines]
    width = max((s[0] for s in sizes), default=0)
    line_h = font.get_linesize()
    if len(lines) == 1:
        height = sizes[0][1]
    else:
        height = line_h * (len(lines) - 1) + sizes[-1][1]
    placements = tuple(
        (line, int(round((width - sizes[i][0]) * 0.5)), i * line_h)
        for i, line in enumerate(lines)
    )
    out = (width, height, placements)
    _blocks[key] = out
    _bound(_blocks, MAX_CACHE_ENTRIES * 2)
    return out


def _fit_px(text: str, px: int, max_px: int, pad: int) -> int:
    """Largest pixel size <= `px` whose rendered width fits in `max_px`."""
    key = (text, px, max_px, pad)
    cached = _fits.get(key)
    if cached is not None:
        return cached

    width = _block(px, text)[0] + pad * 2
    fitted = px
    if width > max_px and width > 0:
        # Advance widths are very nearly linear in point size, so one division
        # lands within a pixel or two; the walk below closes the gap.
        fitted = max(_MIN_PX, int(px * max_px / width))
        for _ in range(_MAX_FIT_STEPS):
            if fitted <= _MIN_PX:
                break
            if _block(fitted, text)[0] + pad * 2 <= max_px:
                break
            fitted -= 1
    _fits[key] = fitted
    _bound(_fits, MAX_CACHE_ENTRIES)
    return fitted


# --------------------------------------------------------------------------
# rasterising
# --------------------------------------------------------------------------
def _composite(text: str, px: int, color: Color, pad: int, outline_color: Color) -> pygame.Surface:
    """A cached surface holding the glyphs with their outline already baked in.

    Baking the outline means a frame costs one blit instead of nine, which is
    the difference between an outlined score being free and being a hitch.
    """
    key = ("glyph", text, px, color, pad, outline_color if pad else None)
    surf = _glyphs.get(key)
    if surf is not None:
        return surf

    font = _font(px)
    width, height, placements = _block(px, text)
    out = pygame.Surface((max(1, width + pad * 2), max(1, height + pad * 2)), pygame.SRCALPHA)

    if pad > 0:
        # Sampling the glyph around a circle gives an even outline; a plain
        # 8-way box offset leaves the diagonals visibly thin.
        steps = max(8, min(24, pad * 6))
        for line, lx, ly in placements:
            if not line:
                continue
            stamp = font.render(line, True, outline_color)
            for i in range(steps):
                angle = math.tau * i / steps
                out.blit(stamp, (lx + pad + round(math.cos(angle) * pad),
                                 ly + pad + round(math.sin(angle) * pad)))

    for line, lx, ly in placements:
        if not line:
            continue
        out.blit(font.render(line, True, color), (lx + pad, ly + pad))

    return _glyphs.put(key, out)


def _shadow_offset(px: int) -> float:
    return max(1.0, px * _SHADOW_OFFSET)


def _shadow_spread(px: int) -> int:
    """Tap separation, in whole pixels.

    Whole pixels so that baking is exact: `round(x + offset + dx*spread)`
    equals `round(x + offset) + dx*spread` only when the spread is an integer,
    and that identity is what lets the taps be composed once at build time and
    positioned once at draw time without drifting a pixel.
    """
    return max(1, int(round(px * _SHADOW_SPREAD)))


def _shadow_margin(px: int) -> int:
    """Transparent border the baked shadow needs for its outermost taps."""
    return _shadow_spread(px) + 1


def _shadow(text: str, px: int, pad: int) -> pygame.Surface:
    """The whole five-tap drop shadow, pre-composed into one cached surface.

    The taps are a function of `px` alone and the per-call alpha is a uniform
    multiply, so there is nothing per-frame about any of this -- which is the
    same argument that put the outline inside `_composite`.
    """
    key = ("shade", text, px, pad)
    surf = _shadows.get(key)
    if surf is not None:
        return surf

    glyph = _composite(text, px, config.COLOR_SHADOW, pad, config.COLOR_SHADOW)
    spread = _shadow_spread(px)
    margin = _shadow_margin(px)
    gw, gh = glyph.get_size()
    out = pygame.Surface((gw + margin * 2, gh + margin * 2), pygame.SRCALPHA)
    # Seed the scratch with the shadow colour at zero alpha. pygame blends
    # non-premultiplied, so compositing onto transparent *black* would drag
    # every partially covered pixel towards black instead of leaving it the
    # shadow colour; seeding the colour makes the bake exact.
    out.fill((*config.COLOR_SHADOW, 0))

    # Each tap carries its full final opacity, so the five of them accumulate
    # against each other exactly as they used to accumulate against the
    # backdrop. Baking the opacity in (rather than applying it to the composed
    # surface afterwards) also lets a full-alpha draw take pygame's fast
    # per-pixel-alpha blit instead of the slower surface-alpha path.
    glyph.set_alpha(max(1, int(round(_SHADOW_ALPHA * _SHADOW_TAP_ALPHA))))
    for dx, dy in _SHADOW_TAPS:
        out.blit(glyph, (margin + int(dx * spread), margin + int(dy * spread)))
    # The composite is shared with the colour-glyph cache: leave its alpha as
    # we found it rather than letting a shadow bake bleed into the next caller.
    glyph.set_alpha(255)
    return _shadows.put(key, out)


def _scaled(base: pygame.Surface, qscale: float, key: tuple) -> pygame.Surface:
    """A cached smoothscale of `base`. `key` must already carry `qscale`."""
    surf = _scales.get(key)
    if surf is not None:
        return surf
    w = max(1, int(round(base.get_width() * qscale)))
    h = max(1, int(round(base.get_height() * qscale)))
    return _scales.put(key, pygame.transform.smoothscale(base, (w, h)))


def _anchor_px(sx: float, sy: float, w: int, h: int, align: str, valign: str) -> Tuple[int, int]:
    if align == "left":
        x = sx
    elif align == "right":
        x = sx - w
    else:
        x = sx - w * 0.5
    if valign == "top":
        y = sy
    elif valign == "bottom":
        y = sy - h
    else:
        y = sy - h * 0.5
    return int(round(x)), int(round(y))


def _blit_glyph(surface: pygame.Surface, text: str, px: int, color: Color, pad: int,
                ocolor: Color, shadow: bool, x: int, y: int, alpha: int,
                qscale: float) -> Tuple[int, int]:
    """Draw one cached glyph block (with its shadow) and return its pixel size.

    Two blits at most: the shadow composite and the glyph composite, both fully
    baked. Nothing here rasterises, allocates or scales once the caches are warm.
    """
    main = _composite(text, px, color, pad, ocolor)
    shade = _shadow(text, px, pad) if shadow else None
    offset = _shadow_offset(px)
    margin = _shadow_margin(px)

    if qscale != 1.0:
        main = _scaled(main, qscale, ("m", text, px, color, pad, ocolor, qscale))
        if shade is not None:
            sscale = round(qscale * _SHADOW_SCALE_STEPS) / _SHADOW_SCALE_STEPS
            shade = _scaled(shade, sscale, ("s", text, px, pad, sscale))
            margin = int(round(margin * sscale))
        offset *= qscale

    surf_alpha = 255 if alpha >= 255 else alpha
    if shade is not None:
        shade.set_alpha(surf_alpha)
        surface.blit(shade, (int(round(x + offset)) - margin, int(round(y + offset)) - margin))
    main.set_alpha(surf_alpha)
    surface.blit(main, (x, y))
    return main.get_size()


# --------------------------------------------------------------------------
# public drawing
# --------------------------------------------------------------------------
def draw_text(surface: pygame.Surface, vp: Viewport, text: str,
              pos: Tuple[float, float], size: SizeSpec,
              color: Color = config.COLOR_TEXT,
              align: str = "center", valign: str = "middle",
              shadow: bool = True, outline: float = 0, alpha: int = 255,
              max_width: Optional[float] = None,
              *, outline_color: Optional[Color] = None, scale: float = 1.0) -> pygame.Rect:
    """Draw `text` at a world-space `pos` and return the pixel box it occupied.

    `size` is a `config.FONT_SIZES` key or a world-unit height. `outline` and
    `max_width` are world units too. The returned rect matches `measure()` for
    the same arguments, so callers can lay out around text without guessing.
    """
    text = text if isinstance(text, str) else str(text)
    alpha = max(0, min(255, int(alpha)))
    sx, sy = vp.to_screen(pos[0], pos[1])
    if not text or alpha <= 0:
        return pygame.Rect(int(round(sx)), int(round(sy)), 0, 0)

    px = px_for(size, vp)
    pad = _outline_px(outline, vp)
    if max_width is not None:
        px = _fit_px(text, px, max(1, int(round(max_width * vp.scale))), pad)
        pad = min(pad, max(0, px // 6))  # keep the outline from swallowing a shrunken glyph

    ocolor = outline_color if outline_color is not None else config.COLOR_SHADOW
    qscale = 1.0 if abs(scale - 1.0) <= _SCALE_EPS else _quantise_scale(scale)

    # Anchor from the measurement rather than from the cached surface: it is
    # the same number by construction and it keeps the hit/miss counters
    # honest about how many times a frame actually consulted the cache.
    block_w, block_h, _ = _block(px, text)
    bw, bh = max(1, block_w + pad * 2), max(1, block_h + pad * 2)
    if qscale != 1.0:
        bw = max(1, int(round(bw * qscale)))
        bh = max(1, int(round(bh * qscale)))
    x, y = _anchor_px(sx, sy, bw, bh, align, valign)
    w, h = _blit_glyph(surface, text, px, color, pad, ocolor, shadow, x, y, alpha, qscale)
    return pygame.Rect(x, y, w, h)


def measure(text: str, size: SizeSpec, vp: Viewport, outline: float = 0,
            max_width: Optional[float] = None) -> Tuple[float, float]:
    """Size of `text` in WORLD UNITS, matching what `draw_text` will occupy."""
    text = text if isinstance(text, str) else str(text)
    if not text:
        return (0.0, 0.0)
    w, h = measure_px(text, size, vp, outline, max_width)
    return (w / vp.scale, h / vp.scale)


def measure_px(text: str, size: SizeSpec, vp: Viewport, outline: float = 0,
               max_width: Optional[float] = None) -> Tuple[int, int]:
    """Same as `measure` but in pixels, for code already working in pixels."""
    text = text if isinstance(text, str) else str(text)
    if not text:
        return (0, 0)
    px = px_for(size, vp)
    pad = _outline_px(outline, vp)
    if max_width is not None:
        px = _fit_px(text, px, max(1, int(round(max_width * vp.scale))), pad)
        pad = min(pad, max(0, px // 6))
    w, h, _ = _block(px, text)
    return (w + pad * 2, h + pad * 2)


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------
def _advance(px: int) -> int:
    """Widest digit at this size -- the fixed cell every digit is centred in."""
    cached = _advances.get(px)
    if cached is not None:
        return cached
    font = _font(px)
    adv = max(font.size(d)[0] for d in _DIGITS)
    _advances[px] = adv
    _bound(_advances, 128)
    return adv


def _number_cells(s: str, px: int, pad: int) -> Tuple[int, int, List[Tuple[str, int]]]:
    """Lay a numeric string out on a fixed digit grid. Returns (w, h, cells)."""
    adv = _advance(px)
    cells: List[Tuple[str, int]] = []
    x = 0
    height = 0
    for ch in s:
        if ch in _DIGITS:
            cw = adv
        else:
            cw = _block(px, ch)[0]
        height = max(height, _block(px, ch)[1])
        cells.append((ch, x))
        x += cw
    return (x + pad * 2, height + pad * 2, cells)


def _draw_number_px(surface: pygame.Surface, s: str, x: int, y: int, px: int, pad: int,
                    color: Color, ocolor: Color, shadow: bool, alpha: int,
                    qscale: float = 1.0) -> None:
    """Blit a numeric string cell by cell, optionally at a cached punch scale.

    Scaling each *digit* rather than a composed block is what makes the punch
    free: ten digits times a hundred scale steps is a small, bounded key space
    that every later punch of every later score hits, whereas a composed block
    is a fresh surface for every value a tally passes through.
    """
    adv = _advance(px)
    for ch, cx in _number_cells(s, px, 0)[2]:
        glyph = _composite(ch, px, color, pad, ocolor)
        # Centre each digit in its cell so a 1 sits where an 8 would.
        cw = adv if ch in _DIGITS else _block(px, ch)[0]
        gx = cx + (cw - (glyph.get_width() - pad * 2)) * 0.5
        _blit_glyph(surface, ch, px, color, pad, ocolor, shadow,
                    x + int(round(gx * qscale)), y, alpha, qscale)


def draw_number(surface: pygame.Surface, vp: Viewport, value: Union[int, float, str],
                pos: Tuple[float, float], size: SizeSpec,
                color: Color = config.COLOR_TEXT,
                align: str = "center", valign: str = "middle",
                shadow: bool = True, outline: float = 0, alpha: int = 255,
                *, prefix: str = "", suffix: str = "", pad_to: int = 0,
                outline_color: Optional[Color] = None, scale: float = 1.0) -> pygame.Rect:
    """Draw a number on a fixed digit grid and return its pixel box.

    Proportional digits make a ticking counter jitter sideways -- `1` is much
    narrower than `8`, so a score rolling 18 -> 19 -> 20 visibly twitches. Every
    digit here is centred in a cell as wide as the widest digit, so only the
    glyphs change and the number stays put.
    """
    if isinstance(value, float):
        body = str(int(value))
    elif isinstance(value, int):
        body = str(value)
    else:
        body = str(value)
    if pad_to > 0:
        negative = body.startswith("-")
        digits = body[1:] if negative else body
        body = ("-" if negative else "") + digits.rjust(pad_to, "0")
    s = f"{prefix}{body}{suffix}"

    alpha = max(0, min(255, int(alpha)))
    sx, sy = vp.to_screen(pos[0], pos[1])
    if not s or alpha <= 0:
        return pygame.Rect(int(round(sx)), int(round(sy)), 0, 0)

    px = px_for(size, vp)
    pad = _outline_px(outline, vp)
    ocolor = outline_color if outline_color is not None else config.COLOR_SHADOW
    qscale = 1.0 if abs(scale - 1.0) <= _SCALE_EPS else _quantise_scale(scale)
    w, h, _ = _number_cells(s, px, pad)
    if qscale != 1.0:
        w = max(1, int(round(w * qscale)))
        h = max(1, int(round(h * qscale)))

    x, y = _anchor_px(sx, sy, w, h, align, valign)
    _draw_number_px(surface, s, x, y, px, pad, color, ocolor, shadow, alpha, qscale)
    return pygame.Rect(x, y, w, h)


def number_width(value: Union[int, float, str], size: SizeSpec, vp: Viewport,
                 outline: float = 0, prefix: str = "", suffix: str = "",
                 pad_to: int = 0) -> float:
    """World-unit width of a fixed-grid number, for laying out around it."""
    body = str(int(value)) if isinstance(value, float) else str(value)
    if pad_to > 0:
        negative = body.startswith("-")
        digits = body[1:] if negative else body
        body = ("-" if negative else "") + digits.rjust(pad_to, "0")
    px = px_for(size, vp)
    pad = _outline_px(outline, vp)
    return _number_cells(f"{prefix}{body}{suffix}", px, pad)[0] / vp.scale
