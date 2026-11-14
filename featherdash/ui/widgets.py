"""Pointer-driven widgets. All geometry is in world units.

Three decisions shape this module:

**Tap targets are enforced, not suggested.** A widget may *look* smaller than a
finger -- a tidy little mute icon in a corner -- but its hit box is always grown
about its centre to `metrics.min_tap`, the floor for the device the game is
actually running on: a thumb's worth on a touch screen, a mouse's much smaller
worth on a pointer one. Undersized targets are the commonest usability complaint
in mobile games and a Play Store accessibility flag, and the only way to be sure
is to make it impossible to opt out by accident. The floor is resolved at draw
time (the first moment a widget knows its viewport); until then `config.TOUCH_MIN`
stands in, because assuming a thumb is the safe way to be wrong.

**Internal proportions come from `metrics_for(vp)`, not from this file.** How big
a control *is* belongs to the screen that places it; how its contents sit inside
it belongs here, and the two meet at Metrics. The icon in a button is
`metrics.icon`, the space beside it is `metrics.gap`, the chip behind an icon
turns at a corner radius shared by every chip on the device, and a press sinks a
fraction of `metrics.button_h`. That is what makes a tablet button a tablet
button rather than a phone button someone stretched.

**Corner radius is a world-unit quantity.** `assets.NineSlice` keeps its inset
in *source pixels* for both the source and the destination, which pins the
corner radius to a fixed number of device pixels: the same button would have
fat corners on a small window and hairline corners on a tablet. The nine-slice
here reads the same art and the same `manifest.BUTTON_INSET`, but scales the
destination inset through the viewport, so a button looks identical everywhere.

**Missing art degrades to something usable.** Interface art may not be rendered
yet (and a packaging mistake could ship without it). `assets` already returns a
magenta placeholder and logs the miss; stretching that over every button makes
a build impossible to review, so frames and icons fall back to drawn vector
equivalents. The log line from `assets` is still the thing that tells you art
is missing -- these fallbacks only stop it being fatal.
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import pygame

from .. import assets, config, platform
from ..display import Viewport
from . import text as uitext
from .layout import Box, Column
from .metrics import Metrics, metrics_for
from .text import SurfaceCache

__all__ = [
    "Widget", "Button", "IconButton", "Toggle", "Panel", "Carousel",
    "ScoreCounter", "WidgetGroup", "draw_icon", "draw_frame", "invalidate",
    "missing_art", "cache_bytes",
    "set_haptics_hook", "spring_backend", "UI_ART_SOURCE_SCALE",
]

Color = Tuple[int, int, int]

# --------------------------------------------------------------------------
# shared constants
# --------------------------------------------------------------------------
# The insets come from config, not from tools/manifest.py: the tools tree is
# build-time only and is not packaged into the APK, so importing it here would
# work on a desktop and fail on a device. tests/test_contract.py asserts the two
# definitions never drift apart.
BUTTON_INSET = config.BUTTON_INSET
PANEL_INSET = config.PANEL_INSET

# Interface art is authored at roughly twice its on-screen size (see
# tools/manifest.py). This is the constant that turns a source-pixel inset into
# a world-unit one, and it is the whole reason corners stay round at any scale.
UI_ART_SOURCE_SCALE = 2.0

PRESS_SCALE = 0.055        # how far a button shrinks when held
# ...and how far it sinks, as a fraction of `metrics.button_h` rather than a
# fixed 5 world units: the travel has to stay in proportion to the control, or
# a press that reads as a firm click on a phone is a twitch on a tablet.
PRESS_SINK_FRAC = 0.042
# Underdamping on the press spring: the button overshoots slightly on the way
# back, which is what makes a tap feel acknowledged rather than absorbed.
PRESS_BOUNCE = 0.45
# An icon chip has no label to anchor it, so it takes the press a little harder
# than a full-width button does; this multiplies both the shrink and the sink.
ICON_PRESS_BOOST = 1.4

# Content inside a control, as fractions of the metric it is measured against.
# These are the only proportions this module owns -- every absolute size comes
# from Metrics.
ICON_IN_CHIP = 0.54        # of the chip's own span, when metrics.icon is smaller
ICON_IN_CHIP_MAX = 0.68    # ...and the most of it a glyph may ever occupy
ICON_ONLY_BOOST = 1.30     # a labelless button carries a larger glyph
CHIP_RADIUS_FRAC = 0.30    # of metrics.icon_button: one corner radius per device
TITLE_IN_BAND = 0.74       # of the band Panel.content_box reserves for a title
DISABLED_ALPHA = 110
ENTRANCE_TIME = 0.26
# Longest side, in pixels, of the offscreen surface a panel entrance is
# composed into. Resampling cost grows with the area of both the source and
# the destination, so an uncapped full-screen sheet is a multi-frame freeze on
# a mid-range phone; capping it trades a little softness on the few
# translucent, moving frames for a steady 60 Hz.
ENTRANCE_MAX_SIDE = 1400

# Press depth is quantised before it reaches any cache key. The spring sweeps
# a continuous range, so without this every frame of a press asks for a pixel
# size nothing has ever asked for: a guaranteed miss that rebuilds a
# nine-slice (nine smoothscales) or a supersampled rounded rect, and churns
# the cache hard enough to evict the steady-state entries behind it. Twenty
# steps across the whole spring, including the overshoot, is finer than a
# pixel on a phone-sized button and makes every press after the first free.
PRESS_STEPS = 12

_FRAME_CACHE_ENTRIES = 256
# A nine-sliced sheet covering a 1080x2400 phone is ~8 MB of RGBA on its own,
# and a screen can legitimately hold a panel, its dark variant and several
# button styles at once. Only rest sizes land here -- press frames are scaled
# on the fly and never cached -- so this is a ceiling, not a working set.
_FRAME_CACHE_BYTES = 20 * 1024 * 1024
# Icon art is asked for by pixel size and `assets._scaled` has no eviction at
# all, so a continuously-animated size would leak there permanently. Rounding
# to a small grid keeps the set of sizes we ever ask for finite.
_ICON_SIZE_GRID = 2


def _bound(cache: dict, limit: int) -> None:
    if len(cache) > limit:
        for key in list(cache)[: max(1, limit // 4)]:
            cache.pop(key, None)


def _quantise_press(press: float) -> float:
    """Snap a press spring's value onto the grid the frame caches are keyed on."""
    return round(press * PRESS_STEPS) / PRESS_STEPS


def _grid(value: int, step: int = _ICON_SIZE_GRID) -> int:
    return max(step, int(round(value / step)) * step)


# --------------------------------------------------------------------------
# haptics
# --------------------------------------------------------------------------
def _haptics_default() -> bool:
    """Haptics are on until the app installs a hook that consults the profile."""
    return True


_haptics_hook: Callable[[], bool] = _haptics_default


def set_haptics_hook(fn: Callable[[], bool]) -> None:
    """Bind widget haptics to the player's setting, e.g. `lambda: profile['haptics']`."""
    global _haptics_hook
    _haptics_hook = fn


def _haptic() -> None:
    try:
        if _haptics_hook():
            platform.vibrate(0.012)
    except Exception:
        # A buzz is never worth a crash, and the hook belongs to someone else.
        pass


# --------------------------------------------------------------------------
# animation
# --------------------------------------------------------------------------
# featherdash.fx is written in parallel, so the import is optional and this
# package works standalone. The probe below is not paranoia: a spring exposes
# several superficially similar verbs ("kick" adds velocity, "nudge" displaces)
# and picking the wrong one gives an animation that technically runs and is
# invisible. Everything is resolved once, at import, and verified by actually
# stepping a throwaway spring.
try:
    from ..fx import Spring as _FxSpring  # type: ignore
except Exception:  # ImportError normally, anything at all mid-development
    _FxSpring = None

# (construct mode, snap verb or None, displace verb or None)
_SPRING_RECIPE: Optional[Tuple[str, Optional[str], Optional[str]]] = None
_SPRING_PROBED = False


def _probe_spring() -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """Work out how to drive fx.Spring, or decide not to."""
    global _SPRING_PROBED, _SPRING_RECIPE
    if _SPRING_PROBED:
        return _SPRING_RECIPE
    _SPRING_PROBED = True
    if _FxSpring is None:
        return None

    for mode, kwargs in (("tuned", {"stiffness": 200.0, "damping": 18.0}),
                         ("stiff", {"stiffness": 200.0}),
                         ("plain", {}),
                         ("bare", None)):
        try:
            spring = _FxSpring() if kwargs is None else _FxSpring(0.0, **kwargs)
            if not callable(getattr(spring, "update", None)):
                continue
            # `target` must be assignable: that is how every widget here aims.
            spring.target = 1.0
            spring.update(1.0 / 60.0)
            if not isinstance(spring.value, (int, float)):
                continue
            snap = next((n for n in ("snap", "reset", "set") if callable(getattr(spring, n, None))), None)
            # Displacement, never velocity -- a velocity impulse's peak depends
            # on stiffness, so the same call would punch by a different amount
            # on every widget.
            push = next((n for n in ("nudge", "displace", "punch") if callable(getattr(spring, n, None))), None)
            _SPRING_RECIPE = (mode, snap, push)
            return _SPRING_RECIPE
        except Exception:
            continue
    return None


def spring_backend() -> str:
    """'fx' or 'lerp' -- which animator the widgets are currently using."""
    return "fx" if _probe_spring() is not None else "lerp"


class _Motion:
    """One animated scalar: press depth, entrance progress, carousel offset.

    Uses `fx.Spring` when available so the whole game shares one feel, and a
    frame-rate independent exponential approach otherwise. `rate` is the shared
    unit of speed -- roughly "e-foldings per second" -- and maps onto a spring
    as omega, so swapping backends does not retune every widget. `bounce` is
    only meaningful to the spring: 0 is critically damped, higher rings more.
    """

    __slots__ = ("_spring", "_snap_fn", "_push_fn", "_value", "_target", "_rate")

    def __init__(self, value: float = 0.0, rate: float = 18.0, bounce: float = 0.0) -> None:
        self._value = float(value)
        self._target = float(value)
        self._rate = max(0.1, float(rate))
        self._spring = None
        self._snap_fn: Optional[str] = None
        self._push_fn: Optional[str] = None

        recipe = _probe_spring()
        if recipe is None or _FxSpring is None:
            return
        mode, snap, push = recipe
        stiffness = self._rate * self._rate                   # omega == rate
        damping = 2.0 * self._rate * max(0.05, 1.0 - bounce)  # zeta == 1 - bounce
        try:
            if mode == "tuned":
                self._spring = _FxSpring(value, stiffness=stiffness, damping=damping)
            elif mode == "stiff":
                self._spring = _FxSpring(value, stiffness=stiffness)
            elif mode == "plain":
                self._spring = _FxSpring(value)
            else:
                self._spring = _FxSpring()
            self._spring.target = float(value)
            self._snap_fn, self._push_fn = snap, push
        except Exception:
            self._spring = None

    # -- state -------------------------------------------------------------
    @property
    def value(self) -> float:
        if self._spring is not None:
            try:
                return float(self._spring.value)
            except Exception:
                self._spring = None
        return self._value

    @property
    def target(self) -> float:
        return self._target

    # -- control -----------------------------------------------------------
    def set(self, target: float) -> None:
        """Animate towards `target`."""
        self._target = float(target)
        if self._spring is not None:
            try:
                self._spring.target = float(target)
            except Exception:
                self._spring = None

    def snap(self, value: float) -> None:
        """Teleport, killing any motion -- a drag following a finger exactly."""
        self._value = self._target = float(value)
        if self._spring is None:
            return
        try:
            self._spring.target = float(value)
            if self._snap_fn is not None:
                getattr(self._spring, self._snap_fn)(float(value))
            else:
                self._spring.value = float(value)
        except Exception:
            self._spring = None

    def punch(self, amount: float) -> None:
        """Displace away from the target so it springs back -- the pop."""
        if self._spring is not None:
            try:
                if self._push_fn is not None:
                    getattr(self._spring, self._push_fn)(float(amount))
                else:
                    self._spring.value = float(self._target + amount)
                return
            except Exception:
                # A missed pop is cosmetic; keep the spring driving the widget.
                return
        self._value = self._target + amount

    def update(self, dt: float) -> float:
        if self._spring is not None:
            try:
                self._spring.update(dt)
                return float(self._spring.value)
            except Exception:
                self._spring = None
        # 1 - exp(-rate*dt) is the frame-rate independent form; a bare
        # `v += (t - v) * k` would animate faster on a 144 Hz monitor.
        blend = 1.0 - math.exp(-self._rate * max(0.0, dt))
        self._value += (self._target - self._value) * blend
        if abs(self._target - self._value) < 1e-4:
            self._value = self._target
        return self._value


# --------------------------------------------------------------------------
# frames (scale-aware nine-slice, with a drawn fallback)
# --------------------------------------------------------------------------
# Bounded by bytes as well as entries: a nine-sliced panel at tablet
# resolution is several megabytes on its own, so an entry cap says nothing
# useful about how much memory this is holding.
_frames = SurfaceCache(_FRAME_CACHE_ENTRIES, _FRAME_CACHE_BYTES)
_warned_art: set = set()

# Bumped by `invalidate()`. Caches that live on a widget instance rather than
# in this module (the Panel entrance composite) carry it in their key, so a
# viewport change drops them without the module having to know they exist.
_generation = 0

_FRAME_STYLES: Dict[str, Dict[str, Any]] = {
    # fill top, fill bottom, border, border alpha
    "primary": {"top": (84, 112, 186), "bottom": (48, 66, 124), "border": (160, 196, 255), "ba": 90},
    "primary_down": {"top": (52, 70, 128), "bottom": (36, 48, 94), "border": (140, 172, 232), "ba": 70},
    "ghost": {"top": (255, 255, 255), "bottom": (235, 240, 255), "border": (255, 255, 255), "ba": 150, "fa": 34},
    "ghost_down": {"top": (255, 255, 255), "bottom": (235, 240, 255), "border": (255, 255, 255), "ba": 190, "fa": 72},
    "panel": {"top": (46, 56, 100), "bottom": (28, 34, 66), "border": (150, 176, 232), "ba": 70, "fa": 238},
    "panel_dark": {"top": (22, 26, 52), "bottom": (12, 15, 34), "border": (120, 142, 198), "ba": 60, "fa": 242},
    # The pale chip is the HUD's default backing, so it is judged over the sky,
    # not over a panel: at 10% fill the pause button was a ring of hairline
    # white on a bright day theme. 16% still reads as a piece of glass rather
    # than a plate, and the matching `_down` keeps a press inside the same
    # family instead of swapping to the ghost button's styling mid-tap.
    "chip": {"top": (255, 255, 255), "bottom": (236, 242, 255), "border": (255, 255, 255), "ba": 132, "fa": 40},
    "chip_down": {"top": (255, 255, 255), "bottom": (232, 238, 255), "border": (255, 255, 255), "ba": 170, "fa": 84},
    # A dark scrim, for controls that sit on top of arbitrary content: the
    # carousel arrows cross five differently-coloured skin cards, and a pale
    # chip vanishes over the light ones.
    "scrim": {"top": (24, 30, 58), "bottom": (12, 15, 34), "border": (255, 255, 255), "ba": 120, "fa": 150},
    "scrim_down": {"top": (12, 15, 34), "bottom": (6, 8, 20), "border": (255, 255, 255), "ba": 170, "fa": 200},
}


def _press_style(name: str) -> str:
    """The pressed counterpart of a backing style, falling back to a dim one."""
    down = f"{name}_down"
    return down if down in _FRAME_STYLES else "ghost_down"


def _supersample(w: int, h: int) -> int:
    """Draw big and shrink: pygame's shape drawing has no antialiasing."""
    if w <= 0 or h <= 0:
        return 1
    return max(1, min(3, int(1100 / max(w, h)) or 1))


def _rounded(w: int, h: int, radius: int, style: str) -> pygame.Surface:
    spec = _FRAME_STYLES.get(style, _FRAME_STYLES["panel"])
    ss = _supersample(w, h)
    sw, sh = max(1, w * ss), max(1, h * ss)
    rad = max(0, min(int(radius * ss), min(sw, sh) // 2))
    surf = pygame.Surface((sw, sh), pygame.SRCALPHA)

    fill_alpha = int(spec.get("fa", 255))
    top, bottom = spec["top"], spec["bottom"]
    # A one-pixel-tall gradient strip stretched vertically is far cheaper than
    # drawing sh rounded rects, and smoothscale gives a clean ramp.
    strip = pygame.Surface((1, max(2, sh // 8)), pygame.SRCALPHA)
    steps = strip.get_height()
    for i in range(steps):
        t = i / max(1, steps - 1)
        strip.set_at((0, i), (int(top[0] + (bottom[0] - top[0]) * t),
                              int(top[1] + (bottom[1] - top[1]) * t),
                              int(top[2] + (bottom[2] - top[2]) * t), fill_alpha))
    gradient = pygame.transform.smoothscale(strip, (sw, sh))

    mask = pygame.Surface((sw, sh), pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), pygame.Rect(0, 0, sw, sh), border_radius=rad)
    gradient.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    surf.blit(gradient, (0, 0))

    border_w = max(1, int(round(min(sw, sh) * 0.018)))
    pygame.draw.rect(surf, (*spec["border"], int(spec.get("ba", 90))),
                     pygame.Rect(0, 0, sw, sh), width=border_w, border_radius=rad)
    if ss > 1:
        surf = pygame.transform.smoothscale(surf, (max(1, w), max(1, h)))
    return surf


def _nine_slice(name: str, w: int, h: int, src_inset: int, dst_inset: int) -> pygame.Surface:
    """Stretch `name` to w x h, scaling the corner inset instead of pinning it."""
    src = assets.original(name)
    sw, sh = src.get_size()
    si = max(0, min(int(src_inset), sw // 2, sh // 2))
    di = max(0, min(int(dst_inset), w // 2, h // 2))
    out = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
    if si <= 0 or di <= 0:
        return pygame.transform.smoothscale(src, (max(1, w), max(1, h)))

    mid_sw, mid_sh = sw - si * 2, sh - si * 2
    mid_dw, mid_dh = w - di * 2, h - di * 2

    def piece(x: int, y: int, pw: int, ph: int) -> pygame.Surface:
        return src.subsurface(pygame.Rect(x, y, pw, ph))

    def put(src_piece: pygame.Surface, dx: int, dy: int, dw: int, dh: int) -> None:
        if dw <= 0 or dh <= 0:
            return
        out.blit(pygame.transform.smoothscale(src_piece, (dw, dh)), (dx, dy))

    put(piece(0, 0, si, si), 0, 0, di, di)
    put(piece(sw - si, 0, si, si), w - di, 0, di, di)
    put(piece(0, sh - si, si, si), 0, h - di, di, di)
    put(piece(sw - si, sh - si, si, si), w - di, h - di, di, di)
    if mid_sw > 0:
        put(piece(si, 0, mid_sw, si), di, 0, mid_dw, di)
        put(piece(si, sh - si, mid_sw, si), di, h - di, mid_dw, di)
    if mid_sh > 0:
        put(piece(0, si, si, mid_sh), 0, di, di, mid_dh)
        put(piece(sw - si, si, si, mid_sh), w - di, di, di, mid_dh)
    if mid_sw > 0 and mid_sh > 0:
        put(piece(si, si, mid_sw, mid_sh), di, di, mid_dw, mid_dh)
    return out


def _frame_surface(name: str, w: int, h: int, src_inset: int, dst_inset: int,
                   style: str) -> pygame.Surface:
    key = ("art", name, w, h, dst_inset, style)
    cached = _frames.get(key)
    if cached is not None:
        return cached
    if assets.exists(name):
        surf = _nine_slice(name, w, h, src_inset, dst_inset)
    else:
        if name not in _warned_art:
            _warned_art.add(name)
            print(f"[ui] {name} not rendered yet -- drawing a vector stand-in")
        surf = _rounded(w, h, dst_inset, style)
    return _frames.put(key, surf)


def _press_scaled(base: pygame.Surface, w: int, h: int) -> pygame.Surface:
    """Resample an already-built frame to the size a press has it at.

    Rebuilding the nine-slice at the pressed size is nine smoothscales from
    the source PNG (or a supersampled vector redraw) for what is a uniform 5%
    shrink; scaling the surface we already have is one. It is also what a
    press *means* -- the whole control gets smaller, corners included.

    Deliberately not cached. A press sweeps a dozen sizes and a phone-sized
    button is half a megabyte each, so keeping them would cost more memory
    per button than the entire glyph cache, to save a third of a millisecond
    on a control that is pressed one at a time. The cache that matters is the
    rest-size one behind it, which this never misses.
    """
    return pygame.transform.smoothscale(base, (max(1, w), max(1, h)))


def _backing(w: int, h: int, radius: int, style: str) -> pygame.Surface:
    """A drawn pill/circle behind an icon.

    Deliberately not nine-sliced art: an icon chip is a flat tinted shape that
    costs nothing to draw and would otherwise need its own PNG at every size.
    """
    key = ("drawn", w, h, radius, style)
    cached = _frames.get(key)
    if cached is not None:
        return cached
    return _frames.put(key, _rounded(w, h, radius, style))


def _icon_backing(rect: pygame.Rect, base: pygame.Rect, circle: bool,
                  style: str, radius: Optional[int] = None) -> pygame.Surface:
    """The chip behind an icon, built at the rest size and scaled when pressed.

    A circle is a circle, but a rounded square takes its corner from the
    device rather than from its own size (see `IconButton.draw`), so the big
    dash control and a small settings chip turn through the same radius
    instead of looking like two unrelated families of control.
    """
    half = min(base.w, base.h) // 2
    turn = half if circle else max(1, min(half, int(radius if radius is not None
                                                   else min(base.w, base.h) * 0.30)))
    surf = _backing(base.w, base.h, turn, style)
    if (base.w, base.h) != (rect.w, rect.h):
        surf = _press_scaled(surf, rect.w, rect.h)
    return surf


def draw_frame(surface: pygame.Surface, vp: Viewport, box: Box, name: str,
               src_inset: int, style: str = "panel", alpha: int = 255,
               *, base: Optional[Box] = None) -> pygame.Rect:
    """Blit a nine-sliced frame over a world-space box.

    `base` is the box's rest size. When a press animation is shrinking the
    control, the frame is built once at the rest size and scaled, instead of
    nine-slicing a size that will never be asked for again.
    """
    rect = box.to_rect(vp)
    if rect.w <= 0 or rect.h <= 0:
        return rect
    dst_inset = max(1, int(round(src_inset / UI_ART_SOURCE_SCALE * vp.scale)))
    if base is None:
        surf = _frame_surface(name, rect.w, rect.h, src_inset, dst_inset, style)
    else:
        brect = base.to_rect(vp)
        surf = _frame_surface(name, brect.w, brect.h, src_inset, dst_inset, style)
        if (brect.w, brect.h) != (rect.w, rect.h):
            surf = _press_scaled(surf, rect.w, rect.h)
    surf.set_alpha(255 if alpha >= 255 else max(0, min(255, int(alpha))))
    surface.blit(surf, rect.topleft)
    return rect


# --------------------------------------------------------------------------
# icons (real art, or drawn stand-ins)
# --------------------------------------------------------------------------
def _star_points(cx: float, cy: float, outer: float, inner: float, n: int = 5) -> List[Tuple[float, float]]:
    pts = []
    for i in range(n * 2):
        r = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + math.pi * i / n
        pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
    return pts


def _gear_ops() -> List[tuple]:
    ops: List[tuple] = [("ring", (0.5, 0.5, 0.30), 0.11)]
    for i in range(6):
        a = math.pi * i / 3.0
        ops.append(("circ", (0.5 + math.cos(a) * 0.40, 0.5 + math.sin(a) * 0.40, 0.085)))
    return ops


_T = 0.085  # default stroke thickness, normalised to the icon box

_GLYPHS: Dict[str, List[tuple]] = {
    "play": [("poly", [(0.33, 0.19), (0.81, 0.50), (0.33, 0.81)])],
    "pause": [("rect", (0.30, 0.22, 0.13, 0.56), 0.05), ("rect", (0.57, 0.22, 0.13, 0.56), 0.05)],
    "close": [("plines", [(0.28, 0.28), (0.72, 0.72)], _T), ("plines", [(0.72, 0.28), (0.28, 0.72)], _T)],
    "check": [("plines", [(0.22, 0.52), (0.42, 0.73), (0.79, 0.27)], _T)],
    "left": [("plines", [(0.61, 0.22), (0.36, 0.50), (0.61, 0.78)], _T)],
    "right": [("plines", [(0.39, 0.22), (0.64, 0.50), (0.39, 0.78)], _T)],
    "home": [("poly", [(0.50, 0.15), (0.88, 0.49), (0.77, 0.49), (0.77, 0.84), (0.58, 0.84),
                       (0.58, 0.63), (0.42, 0.63), (0.42, 0.84), (0.23, 0.84), (0.23, 0.49), (0.12, 0.49)])],
    "restart": [("arc", (0.5, 0.52, 0.29), (-0.35, 4.35), 0.10),
                ("poly", [(0.62, 0.08), (0.90, 0.22), (0.63, 0.37)])],
    "settings": _gear_ops(),
    "shop": [("rect", (0.20, 0.36, 0.60, 0.46), 0.09), ("arc", (0.50, 0.37, 0.16), (0.15, 2.99), 0.075)],
    "trophy": [("poly", [(0.30, 0.16), (0.70, 0.16), (0.67, 0.44), (0.58, 0.56), (0.42, 0.56), (0.33, 0.44)]),
               ("rect", (0.45, 0.54, 0.10, 0.16), 0.02), ("rect", (0.30, 0.70, 0.40, 0.11), 0.04),
               ("arc", (0.28, 0.30, 0.13), (1.4, 4.9), 0.065), ("arc", (0.72, 0.30, 0.13), (-1.75, 1.75), 0.065)],
    "star": [("poly", _star_points(0.5, 0.52, 0.38, 0.165))],
    "lock": [("arc", (0.50, 0.42, 0.17), (0.1, 3.04), 0.085), ("rect", (0.26, 0.42, 0.48, 0.38), 0.08)],
    "feather": [("poly", [(0.74, 0.18), (0.78, 0.44), (0.62, 0.62), (0.38, 0.68), (0.30, 0.56),
                          (0.42, 0.36), (0.58, 0.24)]),
                ("plines", [(0.74, 0.18), (0.22, 0.84)], 0.065)],
    "share": [("circ", (0.74, 0.22, 0.125)), ("circ", (0.26, 0.50, 0.125)), ("circ", (0.74, 0.78, 0.125)),
              ("plines", [(0.33, 0.44), (0.67, 0.27)], 0.055), ("plines", [(0.33, 0.56), (0.67, 0.73)], 0.055)],
    "info": [("ring", (0.5, 0.5, 0.36), 0.085), ("circ", (0.5, 0.30, 0.055)),
             ("rect", (0.455, 0.42, 0.09, 0.28), 0.04)],
    "sound_on": [("poly", [(0.10, 0.37), (0.26, 0.37), (0.44, 0.19), (0.44, 0.81), (0.26, 0.63), (0.10, 0.63)]),
                 ("arc", (0.46, 0.50, 0.20), (-0.95, 0.95), 0.07),
                 ("arc", (0.46, 0.50, 0.34), (-0.85, 0.85), 0.07)],
    "sound_off": [("poly", [(0.10, 0.37), (0.26, 0.37), (0.44, 0.19), (0.44, 0.81), (0.26, 0.63), (0.10, 0.63)]),
                  ("plines", [(0.58, 0.36), (0.88, 0.66)], 0.075),
                  ("plines", [(0.88, 0.36), (0.58, 0.66)], 0.075)],
    "music_on": [("circ", (0.30, 0.73, 0.125)), ("circ", (0.70, 0.64, 0.125)),
                 ("plines", [(0.41, 0.73), (0.41, 0.26), (0.81, 0.17), (0.81, 0.64)], 0.062)],
    "music_off": [("circ", (0.30, 0.73, 0.125)),
                  ("plines", [(0.41, 0.73), (0.41, 0.26), (0.81, 0.17), (0.81, 0.40)], 0.062),
                  ("plines", [(0.18, 0.86), (0.86, 0.14)], 0.07)],
    "haptics_on": [("rect", (0.37, 0.20, 0.26, 0.60), 0.07),
                   ("arc", (0.37, 0.50, 0.24), (2.3, 3.98), 0.065),
                   ("arc", (0.63, 0.50, 0.24), (-0.84, 0.84), 0.065)],
    "haptics_off": [("rect", (0.37, 0.20, 0.26, 0.60), 0.07),
                    ("plines", [(0.16, 0.86), (0.84, 0.14)], 0.07)],
    "medal": [("circ", (0.50, 0.63, 0.27)), ("circ", (0.50, 0.63, 0.145)),
              ("poly", [(0.30, 0.10), (0.44, 0.10), (0.50, 0.40), (0.36, 0.40)]),
              ("poly", [(0.70, 0.10), (0.56, 0.10), (0.50, 0.40), (0.64, 0.40)])],
}

_icons = SurfaceCache(192, 6 * 1024 * 1024)


def _draw_ops(surf: pygame.Surface, ops: Sequence[tuple], size: int, color: Color) -> None:
    rgba = (*color, 255)

    def p(v: float) -> int:
        return int(round(v * size))

    for op in ops:
        kind = op[0]
        if kind == "poly":
            pygame.draw.polygon(surf, rgba, [(p(x), p(y)) for x, y in op[1]])
        elif kind == "rect":
            x, y, w, h = op[1]
            pygame.draw.rect(surf, rgba, pygame.Rect(p(x), p(y), max(1, p(w)), max(1, p(h))),
                             border_radius=p(op[2]) if len(op) > 2 else 0)
        elif kind == "circ":
            cx, cy, r = op[1]
            pygame.draw.circle(surf, rgba, (p(cx), p(cy)), max(1, p(r)))
        elif kind == "ring":
            cx, cy, r = op[1]
            pygame.draw.circle(surf, rgba, (p(cx), p(cy)), max(1, p(r)), max(1, p(op[2])))
        elif kind == "arc":
            cx, cy, r = op[1]
            a0, a1 = op[2]
            rect = pygame.Rect(p(cx - r), p(cy - r), max(2, p(r * 2)), max(2, p(r * 2)))
            pygame.draw.arc(surf, rgba, rect, a0, a1, max(1, p(op[3])))
        elif kind == "plines":
            pts = [(p(x), p(y)) for x, y in op[1]]
            width = max(1, p(op[2]))
            pygame.draw.lines(surf, rgba, False, pts, width)
            # Round the joints and caps; pygame draws neither.
            for pt in pts:
                pygame.draw.circle(surf, rgba, pt, max(1, width // 2))


def _procedural_icon(name: str, px: int, color: Color) -> pygame.Surface:
    key = (name, px, color)
    cached = _icons.get(key)
    if cached is not None:
        return cached
    ss = _supersample(px, px)
    size = max(8, px * ss)
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    ops = _GLYPHS.get(name)
    if ops is None:
        # Unknown icon: a labelled chip is at least identifiable on screen.
        pygame.draw.circle(surf, (*color, 90), (size // 2, size // 2), size // 2 - max(1, size // 20),
                           max(1, size // 18))
        glyph = uitext.font(max(8, int(size * 0.46))).render(name[:1].upper(), True, color)
        surf.blit(glyph, glyph.get_rect(center=(size // 2, size // 2)))
    else:
        _draw_ops(surf, ops, size, color)
    if ss > 1:
        surf = pygame.transform.smoothscale(surf, (max(1, px), max(1, px)))
    return _icons.put(key, surf)


_dots = SurfaceCache(128, 1024 * 1024)


def _dot(radius: int, color: Color, alpha: int) -> pygame.Surface:
    """A small antialiased filled circle, cached -- page dots and bullets."""
    key = (radius, color, alpha)
    cached = _dots.get(key)
    if cached is not None:
        return cached
    ss = 3
    size = max(2, radius * 2) * ss
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(surf, (*color, max(0, min(255, alpha))), (size // 2, size // 2), size // 2)
    surf = pygame.transform.smoothscale(surf, (max(2, radius * 2), max(2, radius * 2)))
    return _dots.put(key, surf)


def draw_icon(surface: pygame.Surface, vp: Viewport, name: str, box: Box,
              alpha: int = 255, tint: Optional[Color] = None) -> pygame.Rect:
    """Draw `icon_<name>.png` centred in a world-space box, square and aspect-safe.

    The pixel size is snapped onto a small grid before it reaches a cache.
    `assets._scaled` never evicts, so asking it for the continuously-changing
    size of a press animation would leak a surface per frame, permanently.
    """
    rect = box.to_rect(vp)
    size = _grid(max(1, min(rect.w, rect.h)))
    filename = f"icon_{name}.png"
    if assets.exists(filename):
        surf = assets.scaled(filename, size, size)
        if tint is not None:
            surf = assets.tinted(surf, tint, 255)
    else:
        if filename not in _warned_art:
            _warned_art.add(filename)
            print(f"[ui] {filename} not rendered yet -- drawing a vector stand-in")
        surf = _procedural_icon(name, size, tint if tint is not None else config.COLOR_TEXT)
    surf.set_alpha(255 if alpha >= 255 else max(0, min(255, int(alpha))))
    out = surf.get_rect(center=rect.center)
    surface.blit(surf, out.topleft)
    # The surface belongs to the shared asset cache, so leave its alpha as we
    # found it rather than letting a faded button bleed into the next caller.
    surf.set_alpha(255)
    return out


# --------------------------------------------------------------------------
# base
# --------------------------------------------------------------------------
def _glyph_box(chip: Box, m: Metrics) -> Box:
    """The icon inside a chip: `metrics.icon`, unless the chip is an odd size.

    A plain fraction of the chip was wrong at both ends -- it made the 132-unit
    dash control's glyph as small as a settings chip's relative to its backing,
    and shrank a deliberately tiny chip's glyph until it was unreadable. So the
    device's icon size leads, the chip's own span sets a floor, and a ceiling
    keeps air around it whatever happens.
    """
    span = min(chip.w, chip.h)
    size = min(max(span * ICON_IN_CHIP, m.icon), span * ICON_IN_CHIP_MAX)
    return Box.from_center(chip.cx, chip.cy, size, size)


def _type_size(size: Any, m: Metrics) -> Any:
    """Resolve a widget's text size for this device.

    A `config.FONT_SIZES` key is an *intent* -- "the large step of the type
    scale" -- so it follows `metrics.type_scale` and grows on a roomier screen.
    A caller that has already worked out a world-unit size (usually by asking
    Metrics for it) gets that number back untouched, so the scale can never be
    applied twice. An unknown key passes through to `ui.text`, which owns the
    warning and the fallback.
    """
    if isinstance(size, str):
        base = config.FONT_SIZES.get(size)
        return size if base is None else m.scaled(base)
    return size


class Widget:
    """Base class: a world-space box that can be drawn and pointed at.

    `hit()` deliberately ignores `enabled`. A greyed-out button is still a solid
    object on screen, so it must swallow the tap rather than let it fall through
    to whatever is behind the dialog; `interactive` is the flag that decides
    whether anything actually happens.
    """

    def __init__(self, rect: Box, *, visible: bool = True, enabled: bool = True,
                 min_tap: Optional[float] = None) -> None:
        self.rect = rect
        self.visible = visible
        self.enabled = enabled
        # None means "whatever this device needs", which only `metrics_for` can
        # answer and only once a viewport is in hand. config.TOUCH_MIN stands in
        # until the first draw: erring towards a thumb is the harmless direction.
        self._tap_auto = min_tap is None
        self._tap_floor = float(config.TOUCH_MIN if min_tap is None else min_tap)

    @property
    def interactive(self) -> bool:
        return self.visible and self.enabled

    @property
    def min_tap(self) -> float:
        """The hit-target floor in world units, as last resolved for the device."""
        return self._tap_floor

    @min_tap.setter
    def min_tap(self, value: float) -> None:
        # An explicit assignment is a deliberate override (a scene pinning a
        # decorative widget to 0), so it stops tracking the device.
        self._tap_auto = False
        self._tap_floor = float(value)

    @property
    def hit_box(self) -> Box:
        """The box a finger is tested against -- never smaller than `min_tap`."""
        if self._tap_floor <= 0:
            return self.rect
        return self.rect.expanded_to(self._tap_floor, self._tap_floor)

    def hit(self, wx: float, wy: float) -> bool:
        return self.visible and self.hit_box.contains(wx, wy)

    def metrics(self, vp: Viewport) -> Metrics:
        """This device's component table, and the moment the tap floor adopts it.

        Every `draw` starts here. Drawing is the one call that is guaranteed to
        happen before a pointer can reach a widget, and it is the only one that
        carries a viewport, so it is where a widget learns what device it is on.
        """
        m = metrics_for(vp)
        if self._tap_auto:
            self._tap_floor = m.min_tap
        return m

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        pass

    def on_pointer_down(self, wx: float, wy: float) -> bool:
        return False

    def on_pointer_move(self, wx: float, wy: float) -> bool:
        return False

    def on_pointer_up(self, wx: float, wy: float) -> bool:
        return False


# --------------------------------------------------------------------------
# button
# --------------------------------------------------------------------------
class Button(Widget):
    """A nine-sliced button with an optional icon and label."""

    def __init__(self, rect: Box, label: str = "", icon: Optional[str] = None,
                 on_click: Optional[Callable[[], None]] = None, variant: str = "primary",
                 *, text_size: Any = "large", text_color: Optional[Color] = None,
                 icon_tint: Optional[Color] = None, visible: bool = True, enabled: bool = True,
                 min_tap: Optional[float] = None, haptic: bool = True) -> None:
        super().__init__(rect, visible=visible, enabled=enabled, min_tap=min_tap)
        self.label = label
        self.icon = icon
        self.on_click = on_click
        self.variant = variant
        self.text_size = text_size
        self.text_color = text_color
        self.icon_tint = icon_tint
        self.haptic = haptic
        self._press = _Motion(0.0, rate=26.0, bounce=PRESS_BOUNCE)
        self._held = False
        self._inside = False

    # -- behaviour ---------------------------------------------------------
    @property
    def pressed(self) -> bool:
        """True while this widget is holding a pointer down.

        A control that is layered over something else needs this to decide
        whether to keep routing events to a button that has become disabled
        mid-gesture -- see `Carousel._arrows`.
        """
        return self._held

    def fire(self) -> None:
        """Activate without a pointer (keyboard, gamepad, autoplay demo)."""
        if self.interactive and self.on_click is not None:
            self.on_click()

    def on_pointer_down(self, wx: float, wy: float) -> bool:
        if not self.hit(wx, wy):
            return False
        if not self.enabled:
            return True  # consumed, but inert
        self._held = True
        self._inside = True
        self._press.set(1.0)
        if self.haptic:
            _haptic()
        return True

    def on_pointer_move(self, wx: float, wy: float) -> bool:
        if not self._held:
            return False
        # Sliding off a held button cancels it, and the art must say so before
        # the finger lifts or the cancel feels like a dropped input.
        self._inside = self.hit(wx, wy)
        self._press.set(1.0 if self._inside else 0.0)
        return True

    def on_pointer_up(self, wx: float, wy: float) -> bool:
        if not self._held:
            return False
        self._held = False
        self._press.set(0.0)
        if self.hit(wx, wy) and self.interactive:
            self._press.punch(-0.35)
            if self.on_click is not None:
                self.on_click()
        return True

    def update(self, dt: float) -> None:
        self._press.update(dt)

    # -- drawing -----------------------------------------------------------
    @property
    def _pressed_look(self) -> bool:
        return self._held and self._inside

    def _frame_name(self) -> Tuple[str, str]:
        if self.variant == "ghost":
            # The secondary has a down state of its own now. Handing back the
            # same PNG for both left it the only button in the kit whose art
            # did not acknowledge the press.
            if self._pressed_look:
                return ("button_ghost_down.png", "ghost_down")
            return ("button_ghost.png", "ghost")
        if self._pressed_look:
            return ("button_down.png", "primary_down")
        return ("button.png", "primary")

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        m = self.metrics(vp)
        # The lower bound is negative on purpose: releasing kicks the spring
        # past rest, and letting that through is what makes the button pop
        # back out rather than merely stop being pressed.
        press = _quantise_press(max(-0.45, min(1.2, self._press.value)))
        # The sink follows the device's standard button, so every button on a
        # screen travels the same distance -- but never more than its own
        # height's worth, or a short chip would sink out from under its label.
        sink = min(m.button_h, self.rect.h) * PRESS_SINK_FRAC
        box = self.rect.scaled(1.0 - PRESS_SCALE * press).moved(0.0, sink * press)
        alpha = 255 if self.enabled else DISABLED_ALPHA

        name, style = self._frame_name()
        draw_frame(surface, vp, box, name, BUTTON_INSET, style, alpha, base=self.rect)

        # The nine-slice turns its corner over a fixed ~34 world units whatever
        # size the button is drawn at (BUTTON_INSET / UI_ART_SOURCE_SCALE of
        # authored radius), so the label has to stand off by that order however
        # small the control gets -- which is exactly what the spacing rhythm is
        # for. The box-relative cap is only there for a control narrower than
        # two gaps, where any fixed inset would leave nothing to draw into.
        pad = min(m.gap, box.w * 0.22)
        inner = box.inset(pad, min(m.gap * 0.5, box.h * 0.14))
        if inner.w <= 0 or inner.h <= 0:
            return
        color = self.text_color if self.text_color is not None else (
            config.COLOR_TEXT if self.enabled else config.COLOR_TEXT_DIM)
        tint = self.icon_tint if self.icon_tint is not None else color
        size = _type_size(self.text_size, m)

        if self.icon and self.label:
            icon_size = min(m.icon, inner.h, box.h * 0.56)
            gap = min(m.gap, inner.w * 0.2)
            label_max = max(1.0, inner.w - icon_size - gap)
            lw, _lh = uitext.measure(self.label, size, vp, max_width=label_max)
            total = icon_size + gap + lw
            x = box.cx - total * 0.5
            draw_icon(surface, vp, self.icon,
                      Box(x, box.cy - icon_size * 0.5, icon_size, icon_size), alpha, tint)
            uitext.draw_text(surface, vp, self.label, (x + icon_size + gap, box.cy),
                             size, color, align="left", valign="middle",
                             alpha=alpha, max_width=label_max)
        elif self.icon:
            # Nothing to share the room with, so the glyph may run larger than
            # the icon beside a label -- the control still has to read as that
            # one action from across the screen.
            glyph = min(inner.w, inner.h, m.icon * ICON_ONLY_BOOST)
            draw_icon(surface, vp, self.icon,
                      Box.from_center(box.cx, box.cy, glyph, glyph), alpha, tint)
        elif self.label:
            uitext.draw_text(surface, vp, self.label, (box.cx, box.cy), size, color,
                             align="center", valign="middle", alpha=alpha, max_width=inner.w)


class IconButton(Widget):
    """Icon-only button on a soft circular (or rounded-square) backing."""

    def __init__(self, rect: Box, icon: str, on_click: Optional[Callable[[], None]] = None,
                 *, style: str = "circle", tint: Optional[Color] = None,
                 backing: bool = True, backing_style: str = "chip",
                 visible: bool = True, enabled: bool = True,
                 min_tap: Optional[float] = None, haptic: bool = True) -> None:
        super().__init__(rect, visible=visible, enabled=enabled, min_tap=min_tap)
        self.icon = icon
        self.on_click = on_click
        self.style = style
        self.tint = tint
        self.backing = backing
        self.backing_style = backing_style
        self.haptic = haptic
        self._press = _Motion(0.0, rate=26.0, bounce=PRESS_BOUNCE)
        self._held = False
        self._inside = False

    # Identical press semantics to Button; sharing the methods keeps the two
    # from drifting apart, which is how a tap target quietly stops working.
    pressed = Button.pressed
    fire = Button.fire
    on_pointer_down = Button.on_pointer_down
    on_pointer_move = Button.on_pointer_move
    on_pointer_up = Button.on_pointer_up

    def update(self, dt: float) -> None:
        self._press.update(dt)

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        m = self.metrics(vp)
        press = _quantise_press(max(-0.45, min(1.2, self._press.value)))
        box = self.rect.scaled(1.0 - PRESS_SCALE * ICON_PRESS_BOOST * press)
        alpha = 255 if self.enabled else DISABLED_ALPHA
        held = self._held and self._inside

        if self.backing:
            rect = box.to_rect(vp)
            if rect.w > 0 and rect.h > 0:
                style = _press_style(self.backing_style) if held else self.backing_style
                surf = _icon_backing(rect, self.rect.to_rect(vp), self.style == "circle", style,
                                     vp.ipx(m.icon_button * CHIP_RADIUS_FRAC))
                surf.set_alpha(255 if alpha >= 255 else alpha)
                surface.blit(surf, rect.topleft)
        draw_icon(surface, vp, self.icon, _glyph_box(box, m), alpha,
                  self.tint if self.tint is not None else config.COLOR_TEXT)


# --------------------------------------------------------------------------
# toggle
# --------------------------------------------------------------------------
class Toggle(Widget):
    """An on/off icon pair bound to a `Profile` key.

    Takes anything that supports `p[key]` and `p.toggle(key)`, so a scene can
    hand it the real profile and a test can hand it a stub -- and it still works
    with `profile=None` for previews.
    """

    def __init__(self, rect: Box, profile: Any, key: str,
                 icon_on: str = "sound_on", icon_off: str = "sound_off",
                 on_change: Optional[Callable[[bool], None]] = None,
                 *, style: str = "circle", visible: bool = True, enabled: bool = True,
                 min_tap: Optional[float] = None, haptic: bool = True) -> None:
        super().__init__(rect, visible=visible, enabled=enabled, min_tap=min_tap)
        self.profile = profile
        self.key = key
        self.icon_on = icon_on
        self.icon_off = icon_off
        self.on_change = on_change
        self.style = style
        self.haptic = haptic
        self._value = bool(profile[key]) if profile is not None else False
        self._anim = _Motion(1.0 if self._value else 0.0, rate=20.0, bounce=0.35)
        self._press = _Motion(0.0, rate=26.0, bounce=PRESS_BOUNCE)
        self._held = False
        self._inside = False

    @property
    def value(self) -> bool:
        return self._value

    def sync(self) -> None:
        """Re-read the profile, for when something else changed the setting."""
        if self.profile is not None:
            self._value = bool(self.profile[self.key])
            self._anim.set(1.0 if self._value else 0.0)

    def toggle(self) -> bool:
        if self.profile is not None:
            self._value = bool(self.profile.toggle(self.key))
        else:
            self._value = not self._value
        self._anim.set(1.0 if self._value else 0.0)
        self._press.punch(0.4)
        if self.on_change is not None:
            self.on_change(self._value)
        return self._value

    def on_pointer_down(self, wx: float, wy: float) -> bool:
        if not self.hit(wx, wy):
            return False
        if not self.enabled:
            return True
        self._held = True
        self._inside = True
        self._press.set(1.0)
        if self.haptic:
            _haptic()
        return True

    pressed = Button.pressed
    on_pointer_move = Button.on_pointer_move

    def on_pointer_up(self, wx: float, wy: float) -> bool:
        if not self._held:
            return False
        self._held = False
        self._press.set(0.0)
        if self.hit(wx, wy) and self.interactive:
            self.toggle()
        return True

    def update(self, dt: float) -> None:
        self._press.update(dt)
        self._anim.update(dt)

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        m = self.metrics(vp)
        t = max(0.0, min(1.0, self._anim.value))
        press = _quantise_press(max(-0.45, min(1.2, self._press.value)))
        box = self.rect.scaled(1.0 - PRESS_SCALE * ICON_PRESS_BOOST * press)
        alpha = 255 if self.enabled else DISABLED_ALPHA

        rect = box.to_rect(vp)
        if rect.w > 0 and rect.h > 0:
            surf = _icon_backing(rect, self.rect.to_rect(vp), self.style == "circle",
                                 "ghost_down" if t > 0.5 else "chip",
                                 vp.ipx(m.icon_button * CHIP_RADIUS_FRAC))
            surf.set_alpha(255 if alpha >= 255 else alpha)
            surface.blit(surf, rect.topleft)

        # Cross-fade rather than hard-swap: the eye reads the change as one
        # object changing state instead of two icons flickering.
        inner = _glyph_box(box, m)
        on_color = config.COLOR_TEXT if self._value else config.COLOR_TEXT_DIM
        if t < 0.999:
            draw_icon(surface, vp, self.icon_off, inner,
                      int(alpha * (1.0 - t)), config.COLOR_TEXT_DIM)
        if t > 0.001:
            draw_icon(surface, vp, self.icon_on, inner, int(alpha * t), on_color)


# --------------------------------------------------------------------------
# panel
# --------------------------------------------------------------------------
class Panel(Widget):
    """A nine-sliced container with an optional title and an entrance animation.

    The entrance renders the panel and its children to an offscreen surface and
    scales that, so labels, icons and children all grow together. Nothing in
    the composite changes while it runs -- the children's press springs are at
    rest -- so it is built once when the entrance starts and only the scale is
    redone per frame. Building it per frame costs megabytes of allocation and
    a full redraw sixteen times over, which is a visible freeze on a phone
    every time the game-over dialog opens.

    `open()` and `close()` are symmetric: a panel that has finished closing
    turns itself invisible and fires `on_closed`. Without that the dialog goes
    on consuming every tap inside its old rectangle for the rest of the run --
    the player taps PLAY AGAIN and then cannot flap in the middle of the
    screen, with nothing on screen to explain why.
    """

    def __init__(self, rect: Box, title: Optional[str] = None, *, dark: bool = False,
                 children: Sequence[Widget] = (), pad: Optional[float] = None,
                 title_size: Any = "title", block_pointer: bool = True,
                 open_now: bool = True, visible: bool = True,
                 on_closed: Optional[Callable[[], None]] = None) -> None:
        super().__init__(rect, visible=visible, min_tap=0.0)
        self.title = title
        self.dark = dark
        self.children: List[Widget] = list(children)
        self.pad = float(pad) if pad is not None else max(26.0, rect.h * 0.075)
        self.title_size = title_size
        self.block_pointer = block_pointer
        self.on_closed = on_closed
        self._anim = _Motion(1.0 if open_now else 0.0,
                             rate=1.0 / max(0.01, ENTRANCE_TIME) * 4.0, bounce=0.2)
        self._open = open_now
        self._entrance: Optional[Tuple[tuple, pygame.Surface]] = None

    # -- composition -------------------------------------------------------
    def add(self, *widgets: Widget) -> "Panel":
        self.children.extend(widgets)
        return self

    def remove(self, widget: Widget) -> None:
        if widget in self.children:
            self.children.remove(widget)

    @property
    def content_box(self) -> Box:
        """The area inside the frame, below the title if there is one."""
        inner = self.rect.inset(self.pad)
        if self.title:
            drop = self.rect.h * 0.20
            return Box(inner.x, inner.y + drop, inner.w, max(0.0, inner.h - drop))
        return inner

    def layout_column(self, sizes: Sequence[Tuple[float, float]], spacing: float = 0.0,
                      align: str = "center", at: str = "center") -> List[Box]:
        """Stack boxes of the given sizes inside the content area."""
        col = Column(spacing, align)
        for w, h in sizes:
            col.add(w, h)
        area = self.content_box
        total_w, total_h = col.size
        fx = {"left": 0.0, "center": 0.5, "right": 1.0}.get(align, 0.5)
        fy = {"top": 0.0, "center": 0.5, "bottom": 1.0}.get(at, 0.5)
        return col.place(area.x + (area.w - total_w) * fx, area.y + (area.h - total_h) * fy)

    # -- state -------------------------------------------------------------
    def open(self) -> None:
        self._open = True
        self.visible = True
        self._anim.set(1.0)

    def close(self) -> None:
        """Start the exit. `update()` finishes the job -- see `_finish_close`."""
        self._open = False
        self._anim.set(0.0)

    def snap_open(self) -> None:
        self._open = True
        self.visible = True
        self._anim.snap(1.0)
        self._entrance = None

    def snap_closed(self) -> None:
        """Dismiss with no animation -- a scene change under an open dialog."""
        self._open = False
        self._anim.snap(0.0)
        self._finish_close()

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def progress(self) -> float:
        return max(0.0, min(1.0, self._anim.value))

    # -- loop --------------------------------------------------------------
    def update(self, dt: float) -> None:
        self._anim.update(dt)
        if not self._open and self.visible and self.progress <= 0.002:
            self._finish_close()
        if self._open and self._entrance is not None and self._settled():
            # The composite is only needed while the thing is growing, and a
            # full-screen panel's is tens of megabytes. Let it go.
            self._entrance = None
        for child in self.children:
            child.update(dt)

    def _finish_close(self) -> None:
        self.visible = False
        self._entrance = None
        if self.on_closed is not None:
            self.on_closed()

    def _settled(self) -> bool:
        return self.progress > 0.995

    def _blocks(self) -> bool:
        """Whether a tap on the panel's body stops here.

        Only while the panel is on its way in or already in. A dialog that has
        been told to close has handed the screen back, so it must stop
        claiming taps immediately rather than at the end of its animation --
        and once it is gone it is `visible = False` and never asked again.
        """
        return bool(self.visible and self._open and self.block_pointer)

    def hit(self, wx: float, wy: float) -> bool:
        return self.visible and self._open and self.rect.contains(wx, wy)

    def _dispatch(self, method: str, wx: float, wy: float) -> bool:
        # Topmost first: the last child added is drawn last, so it is on top.
        for child in reversed(self.children):
            if getattr(child, method)(wx, wy):
                return True
        # A dialog eats taps on its own body; letting them through to the game
        # behind it is how players accidentally flap while reading their score.
        return bool(self._blocks() and self.rect.contains(wx, wy))

    def on_pointer_down(self, wx: float, wy: float) -> bool:
        if not self.visible or not self._settled():
            # Ignore taps mid-entrance: the player is reacting to where the
            # buttons were a moment ago, not where they are now.
            return bool(self._blocks() and self.rect.contains(wx, wy))
        return self._dispatch("on_pointer_down", wx, wy)

    def on_pointer_move(self, wx: float, wy: float) -> bool:
        if not self.visible:
            return False
        # Moves and releases always reach the children, settled or not: a
        # child holding a press needs its release even if the dialog started
        # closing underneath it, or it stays stuck down forever.
        return self._dispatch("on_pointer_move", wx, wy)

    def on_pointer_up(self, wx: float, wy: float) -> bool:
        if not self.visible:
            return False
        return self._dispatch("on_pointer_up", wx, wy)

    # -- drawing -----------------------------------------------------------
    def _draw_contents(self, surface: pygame.Surface, vp: Viewport, alpha: int) -> None:
        m = self.metrics(vp)
        name = "panel_dark.png" if self.dark else "panel.png"
        draw_frame(surface, vp, self.rect, name, PANEL_INSET,
                   "panel_dark" if self.dark else "panel", alpha)
        if self.title:
            # `content_box` reserves a band at the top for the title, so the
            # title is set to fit that band and centred in it. Placing it at a
            # flat fraction of the panel's height instead -- which is what this
            # did -- put a 64-unit title 32 units below the inset: it hung over
            # the top of a tall panel and collided with the first child of a
            # short one, and neither case had anything to do with the band the
            # layout had already set aside for it.
            inner = self.rect.inset(self.pad)
            band = self.rect.h * 0.20
            size = _type_size(self.title_size, m)
            if isinstance(size, (int, float)):   # an unknown key passes through
                size = min(size, band * TITLE_IN_BAND)
            uitext.draw_text(surface, vp, self.title, (self.rect.cx, inner.y + band * 0.5),
                             size, config.COLOR_TEXT, align="center", valign="middle",
                             alpha=alpha, max_width=inner.w)
        for child in self.children:
            child.draw(surface, vp)

    def _entrance_bounds(self) -> Box:
        """The world box the composite has to cover.

        The panel plus whatever its children stick out by, plus a little for
        the title's shadow and outline. Derived rather than a flat fraction of
        the height: the offscreen surface costs the square of this, so a
        generous guess on a full-screen panel is tens of megabytes of nothing.
        """
        area = self.rect
        for child in self.children:
            if child.visible:
                area = area.union(child.rect)
        return area.inflated(max(8.0, self.rect.h * 0.05))

    def _entrance_surface(self, vp: Viewport) -> Optional[Tuple[pygame.Rect, pygame.Surface]]:
        """The panel, its title and its children, drawn once per entrance.

        Nothing in here moves while the entrance runs: the children's press
        springs are at rest and the content is whatever the scene set before
        it opened the dialog. Rebuilding it every frame was ~4.5 MB of
        allocation and a full redraw per frame for a modest dialog, and 17 MB
        per frame for one filling the safe area.

        A large panel is composed at reduced resolution. `smoothscale` costs
        roughly in proportion to the source as well as the destination, so a
        sheet covering the whole screen is a 10 ms resample per frame at full
        resolution and a 4 ms one at half. The softness only ever shows while
        the thing is translucent and moving: `draw()` hands over to the direct,
        crisp path as soon as the scale is within half a pixel of its final
        size, which on this curve is most of the second half of the animation.
        """
        rect = self._entrance_bounds().to_rect(vp)
        if rect.w <= 0 or rect.h <= 0:
            return None
        shrink = min(1.0, ENTRANCE_MAX_SIDE / max(rect.w, rect.h))
        cw = max(1, int(round(rect.w * shrink)))
        ch = max(1, int(round(rect.h * shrink)))
        key = (_generation, rect.x, rect.y, cw, ch, vp.scale, self.title,
               self.dark, len(self.children))
        if self._entrance is not None and self._entrance[0] == key:
            return rect, self._entrance[1]

        scratch = pygame.Surface((cw, ch), pygame.SRCALPHA)
        # A viewport whose origin is shifted (and, for a big panel, shrunk)
        # into the scratch surface lets the children draw themselves normally,
        # with no knowledge of any of this.
        sub = replace(vp, screen_w=cw, screen_h=ch, scale=vp.scale * shrink,
                      origin_x=(vp.origin_x - rect.x) * shrink,
                      origin_y=(vp.origin_y - rect.y) * shrink)
        self._draw_contents(scratch, sub, 255)
        self._entrance = (key, scratch)
        return rect, scratch

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        t = self.progress
        if t <= 0.002:
            return
        # Ease-out back: the growth decelerates hard near the end so the
        # dialog feels like it lands rather than drifting into place.
        scale = 0.86 + 0.14 * t + 0.045 * math.sin(math.pi * min(1.0, t))
        alpha = min(1.0, t * 1.6)
        # Once the panel is opaque and within half a pixel of its final size
        # there is nothing left for the offscreen path to express, and it is
        # the expensive one. On this curve that covers roughly the last third
        # of the entrance -- the frames that would otherwise be resampling a
        # full-size composite while the game is also restarting.
        if t > 0.995 or (alpha >= 1.0 and abs(scale - 1.0) * self.rect.h * vp.scale < 0.5):
            self._draw_contents(surface, vp, 255)
            return

        built = self._entrance_surface(vp)
        if built is None:
            return
        rect, scratch = built
        sw = max(1, int(round(rect.w * scale)))
        sh = max(1, int(round(rect.h * scale)))
        scaled = pygame.transform.smoothscale(scratch, (sw, sh))
        scaled.set_alpha(max(0, min(255, int(255 * alpha))))
        surface.blit(scaled, (rect.centerx - sw // 2, rect.centery - sh // 2))


# --------------------------------------------------------------------------
# carousel
# --------------------------------------------------------------------------
ItemRenderer = Callable[[pygame.Surface, Viewport, Any, Box, float], None]


class Carousel(Widget):
    """A horizontally pageable row: drag with momentum, or use the arrows.

    Both input routes land in the same place -- `set_index` -- so the shop
    cannot get into a state where a drag and an arrow disagree about which skin
    is selected. A flick advances exactly one page however hard it is thrown,
    which is what makes a pager feel controlled rather than slippery.
    """

    DRAG_THRESHOLD = 10.0    # world units before a tap becomes a drag
    # Momentum is measured in world units per *second*, sampled in update()
    # rather than per pointer event. Event-rate sampling makes the same
    # physical flick behave differently at 60 and 120 Hz, and a platform that
    # coalesces touch moves into one fat event would read as a huge throw.
    FLICK_SECONDS = 0.12     # how far ahead a release is projected
    # Smoothing on that sample has to be frame-rate independent too, or the
    # whole exercise is pointless: a fixed per-frame EMA weight reaches only
    # 1 - (1 - w)^n of the true speed in n frames, so the same 0.1 s flick
    # reads 9% slow at 30 Hz and pages one card short of where it does at 60.
    # FLICK_SMOOTHING stays the tuning knob -- it is the weight this used to
    # apply once per frame -- and FLICK_RATE is the continuous rate that
    # reproduces it at exactly 60 Hz.
    FLICK_SMOOTHING = 0.55
    FLICK_RATE = -math.log(1.0 - 0.55) * 60.0

    def __init__(self, rect: Box, items: Sequence[Any], render_item: ItemRenderer,
                 *, item_w: Optional[float] = None, gap: float = 28.0, index: int = 0,
                 on_change: Optional[Callable[[int], None]] = None,
                 on_select: Optional[Callable[[int], None]] = None,
                 show_arrows: bool = True, show_dots: bool = True,
                 visible: bool = True, enabled: bool = True) -> None:
        super().__init__(rect, visible=visible, enabled=enabled, min_tap=0.0)
        self.items = list(items)
        self.render_item = render_item
        self.gap = float(gap)
        self.item_w = float(item_w) if item_w is not None else rect.w * 0.70
        self.on_change = on_change
        self.on_select = on_select
        self.show_arrows = show_arrows
        self.show_dots = show_dots

        self._index = max(0, min(index, max(0, len(self.items) - 1)))
        self._scroll = _Motion(self._index * self.stride, rate=16.0, bounce=0.15)
        self._dragging = False
        self._moved = False
        self._grab_x = 0.0
        self._grab_scroll = 0.0
        self._prev_scroll = self._index * self.stride
        self._velocity = 0.0  # world units per second
        # Set at draw time: `_relayout` runs every frame from update(), which
        # has no viewport of its own to resolve the arrow size against.
        self._m: Optional[Metrics] = None

        # Scrim backing: the arrows sit over whatever the item renderer draws,
        # which in the shop is five different pastel cards.
        self.prev_button = IconButton(Box(), "left", self.prev, style="circle",
                                      backing_style="scrim")
        self.next_button = IconButton(Box(), "right", self.next, style="circle",
                                      backing_style="scrim")
        self._relayout()

    # -- geometry ----------------------------------------------------------
    @property
    def stride(self) -> float:
        return self.item_w + self.gap

    @property
    def dots_h(self) -> float:
        return self.rect.h * 0.13 if self.show_dots and len(self.items) > 1 else 0.0

    @property
    def items_box(self) -> Box:
        return Box(self.rect.x, self.rect.y, self.rect.w, max(0.0, self.rect.h - self.dots_h))

    def item_box(self, i: int) -> Box:
        area = self.items_box
        cx = area.cx - self._scroll.value + i * self.stride
        return Box.from_center(cx, area.cy, self.item_w, area.h)

    def _relayout(self) -> None:
        area = self.items_box
        # The arrows are ordinary icon buttons, so they take the device's icon
        # button size like every other one -- clamped by the carousel's own box
        # so a short pager cannot grow arrows taller than the cards they sit
        # on. Before the first draw there is no viewport to ask, and the old
        # proportional rule stands in.
        if self._m is not None:
            size = min(self._m.icon_button, area.h * 0.40, self.rect.w * 0.22)
        else:
            size = min(area.h * 0.34, self.rect.w * 0.16)
        # Arrows overlap the card edges: on a phone there is no room beside a
        # card wide enough to read, and an overlapping arrow is the convention.
        self.prev_button.rect = Box.from_center(area.x + size * 0.62, area.cy, size, size)
        self.next_button.rect = Box.from_center(area.right - size * 0.62, area.cy, size, size)
        for btn in (self.prev_button, self.next_button):
            btn.visible = self.show_arrows and len(self.items) > 1
        self.prev_button.enabled = self._index > 0
        self.next_button.enabled = self._index < len(self.items) - 1

    # -- paging ------------------------------------------------------------
    @property
    def index(self) -> int:
        return self._index

    @property
    def is_settled(self) -> bool:
        return not self._dragging and abs(self._scroll.value - self._index * self.stride) < 0.5

    def set_index(self, index: int, animate: bool = True) -> None:
        index = max(0, min(int(index), max(0, len(self.items) - 1)))
        changed = index != self._index
        self._index = index
        if animate:
            self._scroll.set(index * self.stride)
        else:
            self._scroll.snap(index * self.stride)
        self._relayout()
        if changed and self.on_change is not None:
            self.on_change(index)

    def next(self) -> None:
        self.set_index(self._index + 1)

    def prev(self) -> None:
        self.set_index(self._index - 1)

    # -- pointer -----------------------------------------------------------
    def _arrows(self, method: str, wx: float, wy: float) -> bool:
        """Give the arrows first refusal, but only the ones that can act.

        `Widget.hit()` ignores `enabled` on purpose -- a greyed-out dialog
        button must still swallow the tap rather than let it reach whatever is
        behind the dialog. An arrow is the opposite case: it sits *on top of a
        draggable surface*, and its hit box is grown to TOUCH_MIN, so a
        disabled one consuming the press turns a 132-unit strip -- a quarter
        of the control -- into a place where swiping does nothing at all.
        That is exactly where a player on the first skin starts their swipe.

        A pressed arrow keeps receiving moves and releases even if it has
        since been disabled, or it would stay stuck down.
        """
        for btn in (self.next_button, self.prev_button):
            if btn.visible and (btn.enabled or btn.pressed) and getattr(btn, method)(wx, wy):
                return True
        return False

    def on_pointer_down(self, wx: float, wy: float) -> bool:
        if not self.interactive:
            return False
        if self._arrows("on_pointer_down", wx, wy):
            return True
        if not self.items_box.contains(wx, wy):
            return False
        self._dragging = True
        self._moved = False
        self._grab_x = wx
        self._grab_scroll = self._prev_scroll = self._scroll.value
        self._velocity = 0.0
        return True

    def on_pointer_move(self, wx: float, wy: float) -> bool:
        if self._arrows("on_pointer_move", wx, wy):
            return True
        if not self._dragging:
            return False
        if abs(wx - self._grab_x) > self.DRAG_THRESHOLD:
            self._moved = True
        delta = self._grab_x - wx
        target = self._grab_scroll + delta
        limit = max(0, len(self.items) - 1) * self.stride
        # Rubber-band past the ends rather than stopping dead; the resistance
        # is what tells a player they have reached the last skin.
        if target < 0:
            target *= 0.35
        elif target > limit:
            target = limit + (target - limit) * 0.35
        self._scroll.snap(target)
        return True

    def on_pointer_up(self, wx: float, wy: float) -> bool:
        if self._arrows("on_pointer_up", wx, wy):
            return True
        if not self._dragging:
            return False
        self._dragging = False
        if not self._moved:
            if self.on_select is not None and self.items_box.contains(wx, wy):
                self.on_select(self._index)
            self.set_index(self._index)
            return True
        stride = max(1e-6, self.stride)
        # Where the content actually sits now, which is where a long deliberate
        # drag should land; momentum may then carry it one page further, but no
        # more -- a hard flick must not fly past five skins.
        settled = int(round(self._scroll.value / stride))
        projected = int(round((self._scroll.value + self._velocity * self.FLICK_SECONDS) / stride))
        self.set_index(max(settled - 1, min(settled + 1, projected)))
        return True

    # -- loop --------------------------------------------------------------
    def update(self, dt: float) -> None:
        if self._dragging:
            instant = (self._scroll.value - self._prev_scroll) / max(1e-6, dt)
            blend = 1.0 - math.exp(-self.FLICK_RATE * max(0.0, dt))
            self._velocity += (instant - self._velocity) * blend
            self._prev_scroll = self._scroll.value
        else:
            self._scroll.update(dt)
        self.prev_button.update(dt)
        self.next_button.update(dt)
        self._relayout()

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        if self._m is None:
            # First sight of a viewport: the arrows were laid out against the
            # fallback, so re-place them before anything is drawn.
            self._m = self.metrics(vp)
            self._relayout()
        else:
            self._m = self.metrics(vp)
        area = self.items_box
        clip_rect = area.to_rect(vp)
        previous = surface.get_clip()
        surface.set_clip(clip_rect)
        for i, item in enumerate(self.items):
            box = self.item_box(i)
            if not box.intersects(area.inflated(self.gap, 0.0)):
                continue
            focus = max(0.0, 1.0 - abs(box.cx - area.cx) / max(1e-6, self.stride))
            self.render_item(surface, vp, item, box, focus)
        surface.set_clip(previous)

        if self.show_arrows:
            self.prev_button.draw(surface, vp)
            self.next_button.draw(surface, vp)

        if self.dots_h > 0:
            # Page dots are a fixed small thing, not a scaled-up version of
            # the pager: capping them against the spacing rhythm stops a tall
            # carousel growing dots the size of its own arrows.
            r = min(self.dots_h * 0.17, self._m.gap * 0.55)
            spacing = r * 4.0
            total = spacing * (len(self.items) - 1)
            y = self.rect.bottom - self.dots_h * 0.5
            live = self._scroll.value / max(1e-6, self.stride)
            for i in range(len(self.items)):
                cx = self.rect.cx - total * 0.5 + i * spacing
                near = max(0.0, 1.0 - abs(live - i))
                px, py = vp.to_screen_i(cx, y)
                colour = config.COLOR_TEXT if near > 0.5 else config.COLOR_TEXT_DIM
                # Quantised so a dragging dot reuses a handful of cached
                # surfaces instead of allocating one per dot per frame.
                radius = max(1, vp.ipx(r * (0.7 + 0.45 * round(near, 1))))
                dot = _dot(radius, colour, int(90 + 165 * near) // 16 * 16)
                surface.blit(dot, (px - dot.get_width() // 2, py - dot.get_height() // 2))


# --------------------------------------------------------------------------
# score counter
# --------------------------------------------------------------------------
class ScoreCounter(Widget):
    """A number that tallies up to a target with a punch when it lands.

    Counting is linear in time rather than exponential so the tally always
    finishes -- an eased counter technically never arrives, and the end-of-run
    screen cannot wait for it.
    """

    def __init__(self, rect: Box, target: int = 0, size: Any = "huge",
                 *, color: Color = config.COLOR_TEXT, duration: float = 0.85,
                 prefix: str = "", suffix: str = "", outline: float = 0.0,
                 shadow: bool = True, on_complete: Optional[Callable[[], None]] = None,
                 on_tick: Optional[Callable[[int], None]] = None,
                 visible: bool = True) -> None:
        super().__init__(rect, visible=visible, min_tap=0.0)
        self.size = size
        self.color = color
        self.duration = max(0.01, float(duration))
        self.prefix = prefix
        self.suffix = suffix
        self.outline = outline
        self.shadow = shadow
        self.on_complete = on_complete
        self.on_tick = on_tick
        self._target = int(target)
        self._value = float(target)
        self._shown = int(target)
        self._rate = 0.0
        self._punch = _Motion(0.0, rate=14.0, bounce=0.5)
        self._done = True

    @property
    def value(self) -> int:
        return int(self._value)

    @property
    def target(self) -> int:
        return self._target

    @property
    def is_done(self) -> bool:
        return self._done

    def set_target(self, target: int, *, from_value: Optional[int] = None) -> None:
        self._target = int(target)
        if from_value is not None:
            self._value = float(from_value)
        self._shown = int(self._value)
        self._rate = abs(self._target - self._value) / self.duration
        self._done = abs(self._target - self._value) < 0.5
        if self._done:
            self._value = float(self._target)

    def finish(self) -> None:
        """Jump to the total -- players tap to skip a tally they have read."""
        if self._done:
            return
        self._value = float(self._target)
        self._shown = self._target
        self._done = True
        self._punch.punch(1.0)
        if self.on_complete is not None:
            self.on_complete()

    def update(self, dt: float) -> None:
        self._punch.update(dt)
        if self._done:
            return
        step = self._rate * dt
        if abs(self._target - self._value) <= step or step <= 0:
            self._value = float(self._target)
            self._done = True
            self._punch.punch(1.0)
            if self.on_complete is not None:
                self.on_complete()
        else:
            self._value += step if self._target > self._value else -step
        shown = int(self._value)
        if shown != self._shown:
            self._shown = shown
            self._punch.punch(0.28)
            if self.on_tick is not None:
                self.on_tick(shown)

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        if not self.visible:
            return
        size = _type_size(self.size, self.metrics(vp))
        punch = max(-0.5, min(1.4, self._punch.value))
        uitext.draw_number(surface, vp, int(self._value), self.rect.center, size,
                           self.color, align="center", valign="middle", shadow=self.shadow,
                           outline=self.outline, prefix=self.prefix, suffix=self.suffix,
                           scale=1.0 + 0.16 * punch)


# --------------------------------------------------------------------------
# group
# --------------------------------------------------------------------------
class WidgetGroup:
    """Holds widgets, draws them in order and routes pointers topmost-first.

    Capture is per pointer id: once a widget takes a press it keeps every move
    and the release, even if the finger slides off. Without that, dragging a
    carousel past a button would hand the gesture to the button mid-drag, and a
    second finger landing elsewhere would cancel the first.
    """

    def __init__(self, widgets: Sequence[Widget] = ()) -> None:
        self.widgets: List[Widget] = list(widgets)
        self._captured: Dict[int, Widget] = {}

    # -- composition -------------------------------------------------------
    def add(self, *widgets: Widget) -> Widget:
        self.widgets.extend(widgets)
        return widgets[-1] if widgets else None  # type: ignore[return-value]

    def remove(self, widget: Widget) -> None:
        if widget in self.widgets:
            self.widgets.remove(widget)
        for pid, held in list(self._captured.items()):
            if held is widget:
                del self._captured[pid]

    def clear(self) -> None:
        self.widgets.clear()
        self._captured.clear()

    def __iter__(self):
        return iter(self.widgets)

    def __len__(self) -> int:
        return len(self.widgets)

    # -- loop --------------------------------------------------------------
    def update(self, dt: float) -> None:
        for widget in self.widgets:
            widget.update(dt)

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        for widget in self.widgets:
            if widget.visible:
                widget.draw(surface, vp)

    # -- pointer -----------------------------------------------------------
    def on_pointer_down(self, wx: float, wy: float, pointer_id: int = 0) -> bool:
        held = list(self._captured.values())
        for widget in reversed(self.widgets):
            if not widget.visible:
                continue
            if any(widget is h for h in held):
                # One capture per widget: a second finger on a widget that is
                # already holding a press must stop at it, not slide behind
                # it. Falling through is how a tap on a small front button
                # fires the big one underneath -- the player touched one
                # control and a different one activated.
                if widget.hit(wx, wy):
                    return True
                continue
            if widget.on_pointer_down(wx, wy):
                self._captured[pointer_id] = widget
                return True
        return False

    def on_pointer_move(self, wx: float, wy: float, pointer_id: int = 0) -> bool:
        widget = self._captured.get(pointer_id)
        if widget is not None:
            widget.on_pointer_move(wx, wy)
            return True
        for other in reversed(self.widgets):
            if other.visible and other.on_pointer_move(wx, wy):
                return True
        return False

    def on_pointer_up(self, wx: float, wy: float, pointer_id: int = 0) -> bool:
        widget = self._captured.pop(pointer_id, None)
        if widget is not None:
            widget.on_pointer_up(wx, wy)
            return True
        for other in reversed(self.widgets):
            if other.visible and other.on_pointer_up(wx, wy):
                return True
        return False

    def cancel(self) -> None:
        """Drop every capture -- call when a scene changes under a held finger."""
        for pid, widget in list(self._captured.items()):
            widget.on_pointer_up(-1e6, -1e6)
            del self._captured[pid]

    def handle_event(self, event: pygame.event.Event, vp: Viewport) -> bool:
        """Route a pygame event. Returns True when a widget consumed it."""
        kind = event.type
        if kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            # SDL synthesises mouse events from touches; handling both would
            # fire every button twice on a phone.
            if getattr(event, "touch", False):
                return False
            if kind != pygame.MOUSEMOTION and getattr(event, "button", 1) != 1:
                return False
            wx, wy = vp.to_world(*event.pos)
            if kind == pygame.MOUSEBUTTONDOWN:
                return self.on_pointer_down(wx, wy, 0)
            if kind == pygame.MOUSEBUTTONUP:
                return self.on_pointer_up(wx, wy, 0)
            return self.on_pointer_move(wx, wy, 0)

        if kind in (pygame.FINGERDOWN, pygame.FINGERUP, pygame.FINGERMOTION):
            # Finger coordinates are normalised 0..1 over the whole window.
            wx, wy = vp.to_world(event.x * vp.screen_w, event.y * vp.screen_h)
            pid = int(getattr(event, "finger_id", 0)) & 0xFFFF
            if kind == pygame.FINGERDOWN:
                return self.on_pointer_down(wx, wy, pid)
            if kind == pygame.FINGERUP:
                return self.on_pointer_up(wx, wy, pid)
            return self.on_pointer_move(wx, wy, pid)
        return False


def missing_art() -> set:
    """Interface PNGs that were asked for and drawn as vector stand-ins.

    The fallbacks mean a missing button never reaches `assets.original()`, so
    it never shows up in `assets.missing()` either. A packaging check that only
    consults `assets` would therefore pass a build with no interface art at
    all; this is the set that closes that hole.
    """
    return set(_warned_art)


def invalidate() -> None:
    """Drop every size-dependent cache in the kit. Call on a viewport change."""
    global _generation
    _generation += 1
    _frames.clear()
    _icons.clear()
    _dots.clear()
    uitext.invalidate()


def cache_bytes() -> int:
    """Live bytes held by every surface cache in the kit -- text included.

    The caches are bounded by this, not by an entry count: a nine-sliced panel
    or an outlined `mega` score is megabytes on its own at phone resolution,
    so "384 entries" is a number that says nothing about whether Android is
    about to kill the process.
    """
    return _frames.nbytes + _icons.nbytes + _dots.nbytes + uitext.cache_bytes()
