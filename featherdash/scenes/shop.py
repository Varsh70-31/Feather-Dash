"""Bird selection and unlocks.

Feathers are earned only by playing, so the shop is a progression screen rather
than a storefront: there is no currency purchase and no network call anywhere in
this game.

The screen is one dialog read top to bottom, and the reading order is the layout
order: the bird, its name, what it costs, and the single button that acts on it.
Everything else -- the collection header, the unlock progress, the balance -- is
small and dim, because each answers a question the player asks once.

Two things carry the selection so the falloff never has to shout: a fixed plate
the cards slide through, and the fact that only the centred bird is *alive* (it
flaps and bobs, driven by the same `preview` Bird the screen has always kept).
The name belongs to the plate rather than to a card, so a swipe can never put
two legible names in the same slot or let the carousel's clip cut one in half.

The one control that acts is the only one with a filled face. A disabled
"Selected" or an unaffordable "Unlock" is the resting state of this screen, and
a 43%-alpha orange slab is both the loudest object in the frame and the least
readable; those states are ghosts, and the orange is reserved for a button that
does something.

Every absolute size comes from `ui.metrics` or `vp.safe_*()`. The only numbers
here are proportions, because Metrics has no entry for a carousel cell and a
cell is meaningless except as a share of the panel it sits in.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional, Tuple

import pygame

from .. import assets, config
from ..display import Viewport
from ..entities import Bird
from ..input import Frame
from ..ui import text as ui_text
from ..ui.layout import Box, Column, safe_box
from ..ui.metrics import Metrics, metrics_for
from ..ui.widgets import (
    UI_ART_SOURCE_SCALE,
    Button,
    Carousel,
    IconButton,
    Panel,
    Widget,
    WidgetGroup,
    draw_frame,
    draw_icon,
)
from .base import Scene

Color = Tuple[int, int, int]

# --------------------------------------------------------------------------
# proportions
# --------------------------------------------------------------------------
# How much of the panel's inner width the focused card takes. The smaller the
# share, the more of the neighbouring birds survives the clip -- which is the
# whole point of the split: a tablet has room to show that there *is* a
# collection, a phone has room for one bird and a hint of the next.
_CELL_FRAC: Dict[str, float] = {"compact": 0.56, "regular": 0.52, "expanded": 0.48}

# The bird's share of its cell, and how far an off-centre bird shrinks. The
# falloff is deliberately gentle: the plate and the animation mark the
# selection, so a neighbour stays big enough to be recognised and tapped past.
_BIRD_FRAC = 0.78
_FOCUS_SCALE = 0.66
# Quantising the focus scale caps how many sprite sizes a drag can push into the
# asset cache; 1/16 of a bird is well under a pixel of visible stepping.
_SCALE_STEPS = 16.0

# Where the dialog sits in the band below the header: above centre, because the
# ground fills the bottom of the frame and the eye starts high.
_PANEL_BIAS = 0.40

_PLATE_ALPHA = 74
# The halo is added rather than blended. Alpha-blending a warm accent over the
# dark panel lands on a neutral grey-khaki -- the hue the theme is supposed to
# be carrying is exactly what the blend averages away -- so it goes on
# additively, which can only ever push the panel towards the accent. The number
# is higher than the blended one it replaces because it means something else:
# adding the accent at 49% of itself, at the centre of a ramp that is nearly
# nothing by the time it reaches the plate's edge.
_GLOW_ALPHA = 124
# The sprite sheet carries transparent padding under each bird, so a halo
# centred on the sprite box pools below the bird instead of sitting behind it.
_GLOW_LIFT = 0.08
# How far an off-centre cell is pulled back towards the middle.
_PARALLAX = 0.20
# Where a cell starts fading out and how long the fade runs, as fractions of
# the card's width from its centre. Nothing is drawn at the carousel's clip
# line, so without this a neighbour is sliced by an invisible vertical edge and
# reads as a rendering fault rather than as the next bird coming into view.
_EDGE_START = 0.34
_EDGE_RUN = 0.30
# Dimming the whole cell is not enough on its own -- the surviving part still
# ends on a straight vertical line through the bird's beak. The outer share of
# a departing sprite therefore dissolves per pixel as well, which is what turns
# the cut into a reveal.
_FADE_SPAN = 0.55
# A locked bird is a silhouette rather than a tinted sprite: at a glance the
# shape still says which bird it is, but nothing says "ready to fly".
_LOCKED_TINT: Color = (48, 54, 88)
# Light enough to stay a disc over the near-black Shadow silhouette, which the
# old near-black fill merged into until only the padlock glyph floated free.
_BADGE_FILL: Color = (34, 40, 72)
_BADGE_OF_BIRD = 0.26     # badge diameter as a share of the sprite it marks

# The name dips through zero at the handover rather than cross-fading two
# strings on top of each other: two different words at half alpha in one slot
# is a mush, and the dip is what reads as the change.
_LABEL_FADE = 0.75

_HEADER_SCRIM = 90        # the close button's own backing alpha, shared

# The dialog's edge. Both the dark panel and the ghost plate measure within a
# hair of the night sky, so without a stroke the screen stops reading as a
# dialog at all after score 36.
_STROKE_W = 1.5
_STROKE_ALPHA = 40
# The nine-slice turns the panel's corner over `config.PANEL_INSET` source
# pixels; inside that block the painted shape starts a little in from the edge
# and turns through a smaller radius. Both are measured off panel_dark.png as
# fractions of the destination slice, so the stroke follows the art at any size.
_PANEL_SLICE = config.PANEL_INSET / UI_ART_SOURCE_SCALE
_PANEL_EDGE = 0.10
_PANEL_RADIUS = 0.38

# Legibility floors. World units are device-independent; eyes are not. On the
# smallest window the game allows, the type scale puts "tiny" at ten pixels and
# a progress dot at three, which is a grey smear and a speck.
_MIN_TYPE_PX = 13.0
_MIN_DOT_R_PX = 4.0

_FLASH_FADE = 0.6         # seconds of flash message left when it starts fading


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if value < lo else (hi if value > hi else value)


# --------------------------------------------------------------------------
# small cached shapes
# --------------------------------------------------------------------------
# pygame draws no antialiasing, and all of these are small enough that the
# jaggies would show. Draw big, shrink, and cache by the pixel size actually
# asked for so a drag reuses a handful of surfaces instead of allocating.
_dots: Dict[tuple, pygame.Surface] = {}
_glows: Dict[tuple, pygame.Surface] = {}
_edges: Dict[tuple, pygame.Surface] = {}


def _bound(cache: dict, limit: int) -> None:
    if len(cache) > limit:
        cache.clear()


def _disc(radius_px: int, color: Color, alpha: int) -> pygame.Surface:
    radius_px = max(1, int(radius_px))
    key = (radius_px, color, alpha)
    surf = _dots.get(key)
    if surf is not None:
        return surf
    size = radius_px * 2
    big = pygame.Surface((size * 3, size * 3), pygame.SRCALPHA)
    pygame.draw.circle(big, (*color, max(0, min(255, alpha))),
                       (size * 3 // 2, size * 3 // 2), size * 3 // 2)
    surf = pygame.transform.smoothscale(big, (size, size))
    _bound(_dots, 96)
    _dots[key] = surf
    return surf


def _ring(radius_px: int, width_px: int, color: Color, alpha: int) -> pygame.Surface:
    """A circle outline, the same diameter as `_disc` at the same radius."""
    radius_px = max(1, int(radius_px))
    width_px = max(1, min(int(width_px), radius_px))
    key = ("ring", radius_px, width_px, color, alpha)
    surf = _dots.get(key)
    if surf is not None:
        return surf
    size = radius_px * 2
    ss = 3
    big = pygame.Surface((size * ss, size * ss), pygame.SRCALPHA)
    pygame.draw.circle(big, (*color, max(0, min(255, alpha))),
                       (size * ss // 2, size * ss // 2), size * ss // 2, width=width_px * ss)
    surf = pygame.transform.smoothscale(big, (size, size))
    _bound(_dots, 96)
    _dots[key] = surf
    return surf


def _glow(w_px: int, h_px: int, color: Color, alpha: int) -> pygame.Surface:
    """A soft halo in the current theme accent, added behind the focused bird.

    The ramp is baked into the RGB channels rather than into alpha, because
    BLEND_RGB_ADD ignores the source's alpha: a halo carrying its falloff in
    alpha would add a flat rectangle of accent over the whole box.
    """
    w_px, h_px = max(2, int(w_px)), max(2, int(h_px))
    key = (w_px, h_px, color, alpha)
    surf = _glows.get(key)
    if surf is not None:
        return surf
    # Concentric circles, outermost first: pygame's draw overwrites rather than
    # blends on an SRCALPHA surface, so the ramp has to be painted inwards. It is
    # painted into a small square and scaled *up*, which turns the steps into a
    # smooth ramp and keeps the cost the same however big the bird is.
    src, rings = 48, 12
    peak = max(0, min(255, alpha)) / 255.0
    small = pygame.Surface((src, src), pygame.SRCALPHA)
    for i in range(rings):
        k = 1.0 - i / rings
        f = (1.0 - k) ** 1.6 * peak
        pygame.draw.circle(small, (int(color[0] * f), int(color[1] * f), int(color[2] * f), 255),
                           (src // 2, src // 2), max(1, int(src * 0.5 * k)))
    surf = pygame.transform.smoothscale(small, (w_px, h_px))
    _bound(_glows, 32)
    _glows[key] = surf
    return surf


def _edge_fade(w_px: int, h_px: int, to_right: bool) -> pygame.Surface:
    """A horizontal alpha ramp, multiplied into a cell leaving the carousel.

    The carousel cuts on a hard vertical line and draws nothing past it, so a
    bird that still has weight when it reaches that line is sliced dead
    straight -- which reads as a rendering fault rather than as a reveal.
    Dimming the whole cell does not help: whatever survives still ends on that
    same straight edge. Only the pixels nearest the cut can soften it.

    `to_right` means the cell is leaving past the right-hand edge, so the ramp
    dies away to the right; otherwise it is mirrored. The surface is white with
    a shaped alpha, which is what makes it correct under BLEND_RGBA_MULT: the
    colour multiplies by 1 and only the alpha is scaled.

    Cached by size and direction -- there are two directions and a handful of
    thumbnail sizes, so the cache stays tiny and nothing is rebuilt per frame.
    """
    w_px, h_px = max(1, int(w_px)), max(1, int(h_px))
    key = ("edge_fade", w_px, h_px, to_right)
    surf = _edges.get(key)
    if surf is not None:
        return surf

    surf = pygame.Surface((w_px, h_px), pygame.SRCALPHA)
    surf.fill((255, 255, 255, 255))
    # Only the outer _FADE_SPAN of the cell ramps; the inner part stays solid so
    # the bird's face is never dimmed just for being off-centre.
    span = max(1.0, w_px * _FADE_SPAN)
    for i in range(int(span)):
        # Distance from the outward edge, 0 at the cut.
        t = _clamp(i / span)
        shade = int(255 * t ** 1.4)
        x = (w_px - 1 - i) if to_right else i
        surf.fill((255, 255, 255, shade), pygame.Rect(x, 0, 1, h_px))

    _edges[key] = surf
    _bound(_edges, 64)
    return surf


def _plate(w_px: int, h_px: int, color: Color, alpha: int) -> pygame.Surface:
    """A flat translucent rectangle -- one straight run of the dialog's edge."""
    w_px, h_px = max(1, int(w_px)), max(1, int(h_px))
    key = ("plate", w_px, h_px, color, alpha)
    surf = _edges.get(key)
    if surf is not None:
        return surf
    surf = pygame.Surface((w_px, h_px), pygame.SRCALPHA)
    surf.fill((*color, max(0, min(255, alpha))))
    _bound(_edges, 24)
    _edges[key] = surf
    return surf


def _stroke_box(surface: pygame.Surface, vp: Viewport, box: Box, radius: float,
                width: float, color: Color, alpha: int) -> None:
    """Outline a rounded box in world units, translucently.

    `pygame.draw` writes pixels rather than blending them, so a translucent
    outline has to arrive as a blit. It arrives as four thin strips and four
    corner arcs rather than as one panel-sized SRCALPHA surface, because the
    surface would be five to nine megabytes of almost entirely nothing -- more
    than the whole frame cache -- for a one-pixel line.
    """
    rect = box.to_rect(vp)
    w = max(1, vp.ipx(width))
    if rect.w <= w * 2 or rect.h <= w * 2:
        return
    r = max(w, min(vp.ipx(radius), rect.w // 2, rect.h // 2))

    ring = _ring(r, w, color, alpha)
    previous = surface.get_clip()
    for cx, cy in ((rect.left, rect.top), (rect.right - r * 2, rect.top),
                   (rect.left, rect.bottom - r * 2), (rect.right - r * 2, rect.bottom - r * 2)):
        # Clip to the quadrant so the three unwanted quarters of the circle
        # never land on the panel's face.
        quad = pygame.Rect(cx if cx <= rect.centerx else cx + r,
                           cy if cy <= rect.centery else cy + r, r, r)
        surface.set_clip(previous.clip(quad))
        surface.blit(ring, (cx, cy))
    surface.set_clip(previous)

    span_x, span_y = rect.w - r * 2, rect.h - r * 2
    if span_x > 0:
        bar = _plate(span_x, w, color, alpha)
        surface.blit(bar, (rect.left + r, rect.top))
        surface.blit(bar, (rect.left + r, rect.bottom - w))
    if span_y > 0:
        bar = _plate(w, span_y, color, alpha)
        surface.blit(bar, (rect.left, rect.top + r))
        surface.blit(bar, (rect.right - w, rect.top + r))


def _quantise(color: Color, step: int = 24) -> Color:
    """Snap the accent so a theme cross-fade cannot thrash the glow cache."""
    return (color[0] // step * step, color[1] // step * step, color[2] // step * step)


def _mix(a: Color, b: Color, t: float) -> Color:
    t = _clamp(t)
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


class _Painter(Widget):
    """Lets the scene paint inside the panel's entrance animation.

    `Panel` renders itself and its children to an offscreen surface while it
    opens. Text drawn next to the panel instead of inside it would therefore
    land at full size a frame before the panel arrives, and the dialog would
    stop reading as one object.
    """

    def __init__(self, rect: Box, paint: Callable[[pygame.Surface, Viewport], None]) -> None:
        super().__init__(rect, min_tap=0.0)
        self._paint = paint

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        self._paint(surface, vp)


# --------------------------------------------------------------------------
# the screen
# --------------------------------------------------------------------------
class ShopScene(Scene):
    name = "shop"

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.group = WidgetGroup()
        self.carousel: Carousel | None = None
        self.panel: Panel | None = None
        self.action: Button | None = None
        self.prev_button: IconButton | None = None
        self.next_button: IconButton | None = None
        self.preview = Bird("ember", 0.0)
        self.index = 0
        self.message = ""
        self.message_time = 0.0
        self.message_color: Color = config.COLOR_GOOD
        self._built_for: tuple[float, float] | None = None

        # resolved by _build; every draw reads these rather than recomputing
        self.m: Metrics = metrics_for(self.vp)
        self.type_boost = 1.0
        self.header_box = Box()
        self.card_box = Box()
        self.strip_box = Box()
        self.status_box = Box()
        self.balance_box = Box()
        self.cell_w = 0.0
        self.bird_w = 0.0
        self.bird_h = 0.0
        self.name_h = 0.0
        self.name_size = 0.0
        self.dot_r = 0.0
        self.dot_gap = 0.0

    # -- lifecycle ---------------------------------------------------------
    def enter(self, **kwargs: Any) -> None:
        current = str(self.app.profile["skin"])
        self.index = max(0, config.SKIN_KEYS.index(current) if current in config.SKIN_KEYS else 0)
        self.message = ""
        self.message_time = 0.0
        self._built_for = None
        self._build(animate=True)
        self._sync_preview()

    def on_resize(self, vp: Viewport) -> None:
        # A resize is not an entrance: replaying it would make every window drag
        # flash the dialog.
        self._built_for = None
        self._build(animate=False)

    # -- type --------------------------------------------------------------
    def _size(self, key: str) -> float:
        """A step of the type scale, in world units, floored against pixels.

        `metrics.type_scale` answers "how much room is there", which is the
        right question on a phone and a tablet and the wrong one in a 320x480
        window, where it resolves the small end of the ramp to ten pixels. The
        whole ramp is lifted together rather than the bottom of it clamped, so
        the hierarchy survives instead of collapsing into one size.
        """
        return self.m.scaled(config.FONT_SIZES[key]) * self.type_boost

    # -- layout ------------------------------------------------------------
    def _build(self, animate: bool = False) -> None:
        vp = self.vp
        key = (vp.world_w, vp.scale)
        if self._built_for == key:
            return
        self._built_for = key

        m = self.m = metrics_for(vp)
        area = safe_box(vp)
        self.type_boost = max(1.0, _MIN_TYPE_PX / max(1e-6, vp.px(config.FONT_SIZES["tiny"])))

        # The dialog's width is known before anything else is placed, because
        # the header is anchored to the dialog rather than to the window: a
        # title hung off the safe area floats in open sky on a wide display,
        # 158 units adrift of the panel it is supposed to be labelling.
        panel_w = min(m.panel_w, area.w)
        panel_x = area.x + (area.w - panel_w) * 0.5
        inner_w = max(m.icon_button, panel_w - m.panel_pad * 2)

        # -- screen chrome: title block left, close right ------------------
        self.header_box = Box(panel_x, area.y, panel_w, m.icon_button)
        close_box = Box(self.header_box.right - m.icon_button, area.y,
                        m.icon_button, m.icon_button)

        # The dialog's own padding already separates it from the header, so a
        # section gap on top of that would double-space the top of the screen.
        body_top = self.header_box.bottom + m.gap
        # ...and the band stops at the horizon. The panel is translucent, so a
        # button overhanging the ground has grass and stones showing through
        # the single busiest, brightest control on the screen.
        band_bottom = min(area.bottom, vp.ground_y - m.gap)
        body_h = max(m.row_h, band_bottom - body_top)

        self.cell_w = inner_w * _CELL_FRAC.get(m.size_class, _CELL_FRAC["compact"])
        self.bird_w = self.cell_w * _BIRD_FRAC
        self.bird_h = self.bird_w * config.BIRD_H / config.BIRD_W

        # Measure the type that will actually be drawn, so the dialog can never
        # clip its own contents on a font the designer has not seen.
        line_h = ui_text.measure("Ag", self._size("small"), vp)[1]
        status_h = ui_text.measure("Ag", self._size("body"), vp)[1]
        name_h = ui_text.measure("Ag", self._size("title"), vp)[1]

        # The pager row is as tall as the dots in it. Sizing it by the arrows
        # instead buried the dots in a third of an icon button of slack above
        # and below, which is why the spacing rhythm did not read: the arrows
        # keep their size and their tap targets, but they are centred on the
        # row the eye actually sees rather than defining it.
        self.dot_r = max(m.gap * 0.30, _MIN_DOT_R_PX / max(1e-6, vp.scale))
        self.dot_gap = max(m.gap * 1.35, self.dot_r * 3.2)
        strip_h = self.dot_r * 2.4

        fixed = (m.panel_pad * 2 + m.gap + strip_h + m.section_gap
                 + status_h + m.gap + line_h + m.gap + m.button_h)
        # An expanded window is wide but no taller, and it spends more of that
        # height on type and padding. When the composition will not fit the band
        # the card compresses -- but the bird and its name give together, in
        # proportion. Taking the whole deficit out of the bird leaves a card
        # where the label is twice the height of the thing it is labelling.
        padding = m.gap * 3.0
        natural = self.bird_h + name_h + padding
        card_h = min(natural, max(m.icon * 2.0 + padding, body_h - fixed))
        squeeze = _clamp((card_h - padding) / max(1e-6, self.bird_h + name_h), 0.35, 1.0)
        self.bird_h *= squeeze
        self.bird_w = self.bird_h * config.BIRD_W / config.BIRD_H
        self.name_size = self._size("title") * squeeze
        self.name_h = name_h * squeeze

        col = Column(0.0, align="center")
        col.add(inner_w, card_h, "card")
        col.add(0.0, m.gap)
        col.add(inner_w, strip_h, "strip")
        col.add(0.0, m.section_gap)
        col.add(inner_w, status_h, "status")
        col.add(0.0, m.gap)
        col.add(inner_w, line_h, "balance")
        col.add(0.0, m.gap)
        col.add(min(m.button_w, inner_w), m.button_h, "action")

        panel_h = min(col.size[1] + m.panel_pad * 2, body_h)
        panel_box = Box(panel_x, body_top + (body_h - panel_h) * _PANEL_BIAS, panel_w, panel_h)
        col.place(panel_box.x + m.panel_pad, panel_box.y + m.panel_pad)

        self.card_box = col["card"]
        self.strip_box = col["strip"]
        self.status_box = col["status"]
        self.balance_box = col["balance"]

        # -- widgets -------------------------------------------------------
        self.group.clear()
        carousel = Carousel(
            self.card_box, items=list(config.SKINS), render_item=self._render_skin,
            index=self.index, on_change=self._on_change,
            item_w=self.cell_w, gap=m.gap,
            # The carousel's own arrows hug the card and its dots are purely
            # positional; this screen places both itself so they can also carry
            # the unlock state.
            show_arrows=False, show_dots=False,
        )
        self.carousel = carousel
        self.action = Button(col["action"], "", on_click=self._activate,
                             text_size=self._size("large"), min_tap=m.min_tap)

        # The arrows flank the dots instead of floating over the card. Sitting
        # them beside the art is the usual convention, but the art is exactly
        # what the margin is now showing: the whole point of a smaller cell on a
        # bigger screen is that the next bird is visible, and an arrow parked on
        # top of it throws that away.
        span = self.dot_gap * max(0, len(config.SKINS) - 1)
        reach = min(span * 0.5 + m.gap + m.icon_button * 0.5,
                    inner_w * 0.5 - m.icon_button * 0.5)
        self.prev_button = IconButton(
            Box.from_center(self.strip_box.cx - reach, self.strip_box.cy,
                            m.icon_button, m.icon_button),
            "left", carousel.prev, backing_style="scrim", min_tap=m.min_tap)
        self.next_button = IconButton(
            Box.from_center(self.strip_box.cx + reach, self.strip_box.cy,
                            m.icon_button, m.icon_button),
            "right", carousel.next, backing_style="scrim", min_tap=m.min_tap)

        # Arrows before the carousel, so a finger landing where their enlarged
        # tap targets reach up into the card still starts a drag. The label
        # paints last of the three so it sits on the plate rather than under a
        # bird drifting through the bottom of the card.
        self.panel = Panel(
            panel_box, dark=True, pad=m.panel_pad, open_now=not animate,
            children=[_Painter(panel_box, self._draw_panel_content),
                      self.prev_button, self.next_button, carousel,
                      _Painter(self.card_box, self._draw_card_label), self.action])
        self.group.add(self.panel)
        self.group.add(IconButton(close_box, "close", on_click=self._back, min_tap=m.min_tap))
        if animate:
            self.panel.open()

        self.preview.x = vp.cx
        self._refresh_button()

    # -- carousel cell -----------------------------------------------------
    def _render_skin(self, surface: pygame.Surface, vp: Viewport, item: Any,
                     box: Box, focus: float) -> None:
        """Draw one carousel cell. `focus` is 1.0 for the centred item, fading outward."""
        key, _name, _cost = item
        m = self.m
        focus = _clamp(focus)
        unlocked = self.app.profile.is_unlocked(key)
        # Past the halfway point the incoming bird takes over the animation, so
        # a drag hands the "alive" state across rather than snapping on release.
        live = focus > 0.5

        scale = round((_FOCUS_SCALE + (1.0 - _FOCUS_SCALE) * focus) * _SCALE_STEPS) / _SCALE_STEPS
        w, h = self.bird_w * scale, self.bird_h * scale

        # Off-centre cells are drawn pulled back towards the middle. Without it
        # a neighbour's own centre falls outside the clip and all that survives
        # is a sliver of wing; with it, the player can see what is coming next.
        cx = box.cx - (box.cx - self.card_box.cx) * _PARALLAX * (1.0 - focus)
        cy = box.y + m.gap + self.bird_h * 0.5

        # Fade towards the clip. The carousel cuts on a hard vertical line and
        # draws nothing there, so a bird that still has weight when it reaches
        # that line is sliced dead straight -- which looks like a fault, not a
        # reveal.
        edge = 1.0 - _clamp((abs(cx - self.card_box.cx) - self.card_box.w * _EDGE_START)
                            / max(1e-6, self.card_box.w * _EDGE_RUN))
        alpha = int((72 + 183 * focus) * edge)
        if alpha <= 2:
            return

        frame = self.preview.frame_index() if live else 0
        sprite = assets.scaled(f"bird_{key}_{frame}.png", vp.px(w), vp.px(h))
        if not unlocked:
            sprite = assets.tinted(sprite, _LOCKED_TINT, 255)
        if live:
            # The idle bob is authored against the bird's real size; rescale it
            # so a shop preview bobs by the same fraction of itself.
            cy += (self.preview.y - config.BIRD_START_Y) * (h / config.BIRD_H) * focus
            angle = self.preview.angle * focus
            if abs(angle) > 0.5:
                sprite = pygame.transform.rotate(sprite, -angle)
        if edge < 0.999:
            # The copy is the price of not mutating the shared asset cache; it
            # is one memcpy of a thumbnail, and only for a cell on its way out.
            sprite = sprite.copy()
            sprite.blit(_edge_fade(*sprite.get_size(), cx > self.card_box.cx),
                        (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        sprite.set_alpha(alpha)
        surface.blit(sprite, sprite.get_rect(center=vp.to_screen(cx, cy)))
        sprite.set_alpha(255)   # the asset cache owns this surface; leave it clean

        # The badge is sized from the sprite, not from the metrics table: a
        # fixed world size grows relative to its subject as the card compresses,
        # until the disc covers the bird it is asking the player to pay for. It
        # is dropped entirely off-centre, so a padlock can never outlive the
        # bird it belongs to and float on the panel as debris.
        if not unlocked and live:
            badge = max(m.hud_icon * 0.55, w * _BADGE_OF_BIRD)
            bx, by = cx - w * 0.40, cy + h * 0.34
            disc = _disc(vp.ipx(badge * 0.5), _BADGE_FILL, int(alpha * 0.92))
            surface.blit(disc, disc.get_rect(center=vp.to_screen_i(bx, by)))
            draw_icon(surface, vp, "lock",
                      Box.from_center(bx, by, badge * 0.56, badge * 0.56),
                      alpha, config.COLOR_TEXT_DIM)

    # -- state -------------------------------------------------------------
    def _on_change(self, index: int) -> None:
        self.index = index % len(config.SKINS)
        self.app.audio.play("ui_tap")
        self._sync_preview()
        self._refresh_button()

    def _sync_preview(self) -> None:
        self.preview.skin = config.SKIN_KEYS[self.index]
        self.preview.reset(self.vp.cx, config.BIRD_START_Y)

    def _current(self) -> tuple[str, str, int]:
        return config.SKINS[self.index]

    def _refresh_button(self) -> None:
        key, _, cost = self._current()
        last = len(config.SKINS) - 1
        if self.prev_button is not None:
            self.prev_button.enabled = self.index > 0
        if self.next_button is not None:
            self.next_button.enabled = self.index < last

        button = self.action
        if button is None:
            return
        # The filled orange face is reserved for a button that acts. An inert
        # one takes the ghost variant instead: a primary at 43% alpha is a
        # muddy tan slab with a grey label on it -- the biggest, loudest object
        # on the screen, doing nothing, and unreadable while it does it.
        if self.app.profile.is_unlocked(key):
            selected = str(self.app.profile["skin"]) == key
            button.label = "Selected" if selected else "Select"
            button.icon = "check" if selected else None
            button.enabled = not selected
            button.variant = "ghost" if selected else "primary"
            button.text_color = config.COLOR_TEXT if selected else None
        else:
            # The price sits above the button in gold; repeating it on the
            # button would be the loudest redundancy on the screen.
            affordable = int(self.app.profile["feathers"]) >= cost
            button.label = "Unlock"
            button.icon = "feather" if affordable else "lock"
            button.enabled = affordable
            button.variant = "primary" if affordable else "ghost"
            button.text_color = None if affordable else config.COLOR_TEXT_DIM

    def _activate(self) -> None:
        key, name, cost = self._current()
        profile = self.app.profile
        if profile.is_unlocked(key):
            profile["skin"] = key
            profile.save()
            self.app.audio.play("ui_tap")
            self._flash(f"{name} selected")
        elif profile.spend(cost):
            profile.unlock(key)
            profile["skin"] = key
            profile.save()
            self.app.audio.play("unlock")
            if self.app.particles is not None:
                self.app.particles.emit_preset("unlock", self.vp.cx, config.BIRD_START_Y)
            self._flash(f"{name} unlocked!")
        else:
            self.app.audio.play("ui_back")
            self._flash("Not enough feathers", config.COLOR_DANGER)
        self._refresh_button()

    def _flash(self, message: str, color: Optional[Color] = None) -> None:
        self.message = message
        self.message_color = config.COLOR_GOOD if color is None else color
        self.message_time = 2.2

    def _back(self) -> None:
        self.app.audio.play("ui_back")
        self.app.go("menu")

    # -- frame -------------------------------------------------------------
    def handle(self, frame: Frame) -> None:
        if frame.back:
            self._back()
            return
        for ptr in frame.down:
            self.group.on_pointer_down(ptr.x, ptr.y)
        for ptr in frame.moved:
            self.group.on_pointer_move(ptr.x, ptr.y)
        for ptr in frame.up:
            self.group.on_pointer_up(ptr.x, ptr.y)

    def step(self, dt: float) -> None:
        self.app.world.step(dt, config.SPEED_START * 0.35)
        self.preview.step(dt)
        if self.message_time > 0:
            self.message_time = max(0.0, self.message_time - dt)

    def update(self, dt: float) -> None:
        self.group.update(dt)

    # -- the slot ----------------------------------------------------------
    def _slot(self) -> tuple[int, float]:
        """Which bird owns the plate right now, and how settled the slot is.

        The live scroll is recoverable from the geometry the carousel exposes,
        which is what lets the name and the dots glide with a drag rather than
        jumping on settle. `settled` is 1.0 on a parked card and 0.0 at the
        exact midpoint of a swipe, where ownership of the slot changes hands.
        """
        carousel = self.carousel
        if carousel is None or not carousel.items:
            return (self.index, 1.0)
        count = len(carousel.items)
        stride = max(1e-6, carousel.stride)
        live = _clamp((carousel.items_box.cx - carousel.item_box(0).cx) / stride,
                      0.0, count - 1.0)
        lo = min(count - 1, int(math.floor(live)))
        frac = live - lo
        index = lo if frac < 0.5 else min(lo + 1, count - 1)
        return (index, abs(frac - 0.5) * 2.0)

    # -- drawing -----------------------------------------------------------
    def _draw_panel_content(self, surface: pygame.Surface, vp: Viewport) -> None:
        """Everything inside the dialog that is not a widget.

        Drawn as the panel's first child, so it is behind the carousel and
        animates in with the rest of the dialog.
        """
        m = self.m
        key, _name, cost = self._current()
        unlocked = self.app.profile.is_unlocked(key)
        feathers = int(self.app.profile["feathers"])

        # -- an edge, on every theme ---------------------------------------
        # The dark panel measures 1.02:1 against the night sky. Without a line
        # around it the bird, the price and the button float loose in the sky
        # and the screen stops reading as a dialog at all.
        _stroke_box(surface, vp, self.panel.rect.inset(_PANEL_SLICE * _PANEL_EDGE),
                    _PANEL_SLICE * _PANEL_RADIUS, _STROKE_W,
                    config.COLOR_TEXT, _STROKE_ALPHA)

        # -- the slot the cards slide through ------------------------------
        # A whisper of a plate, not a card: it has to say "this one" without
        # becoming the brightest object on a screen whose subject is a bird.
        plate = Box.from_center(self.card_box.cx, self.card_box.cy, self.cell_w, self.card_box.h)
        draw_frame(surface, vp, plate, "button_ghost.png", config.BUTTON_INSET,
                   "ghost", _PLATE_ALPHA)

        # The halo belongs to whichever bird owns the plate, so it thins out at
        # the handover rather than smearing across an empty slot. Quantised, or
        # a drag would ask the cache for a new halo every frame.
        _index, settled = self._slot()
        glow_alpha = int(_GLOW_ALPHA * (0.18 + 0.82 * settled)) // 8 * 8
        halo = _glow(vp.ipx(self.bird_w * 1.5), vp.ipx(self.bird_h * 1.5),
                     _quantise(self.app.world.accent), glow_alpha)
        surface.blit(halo, halo.get_rect(center=vp.to_screen_i(
            plate.cx, self.card_box.y + m.gap + self.bird_h * (0.5 - _GLOW_LIFT))),
            special_flags=pygame.BLEND_RGB_ADD)

        self._draw_progress_dots(surface, vp, 1.0)

        # -- the bottom cluster --------------------------------------------
        # A flash message answers the same question as the price line -- "what
        # does this bird cost me?" -- so it takes the same row and cross-fades,
        # and nothing below it ever moves.
        fade = min(1.0, self.message_time / _FLASH_FADE) if self.message_time > 0 else 0.0
        if fade < 1.0:
            self._draw_status(surface, vp, key, cost, unlocked, feathers, 1.0 - fade)
        if fade > 0.0:
            ui_text.draw_text(surface, vp, self.message, self.status_box.center,
                              self._size("body"), color=self.message_color,
                              alpha=int(255 * fade), outline=m.gap * 0.06,
                              max_width=self.status_box.w)

        ui_text.draw_text(surface, vp, f"You have {feathers} feathers",
                          self.balance_box.center, self._size("small"),
                          color=config.COLOR_TEXT_DIM, max_width=self.balance_box.w)

    def _draw_card_label(self, surface: pygame.Surface, vp: Viewport) -> None:
        """The bird's name -- one slot on the plate, not one label per card.

        A label per cell put two fully legible names on the plate at the middle
        of a swipe and let the carousel's clip cut the trailing one mid-letter.
        One slot that dips to nothing at the handover can only ever show one
        name, and the dip is what reads as the change.
        """
        carousel = self.carousel
        if carousel is None or not carousel.items:
            return
        index, settled = self._slot()
        alpha = int(255 * settled ** _LABEL_FADE)
        if alpha <= 2:
            return
        m = self.m
        key, name, _cost = carousel.items[index]
        ui_text.draw_text(
            surface, vp, name,
            (self.card_box.cx, self.card_box.bottom - m.gap - self.name_h * 0.5),
            self.name_size,
            color=config.COLOR_TEXT if self.app.profile.is_unlocked(key)
            else config.COLOR_TEXT_DIM,
            alpha=alpha, outline=m.gap * 0.07, max_width=self.cell_w - m.gap)

    def _draw_status(self, surface: pygame.Surface, vp: Viewport, key: str, cost: int,
                     unlocked: bool, feathers: int, alpha: float) -> None:
        """The one medium-weight line: what this bird costs, or that it is yours."""
        m = self.m
        shade = int(255 * alpha)
        size = self._size("body")
        if unlocked:
            selected = str(self.app.profile["skin"]) == key
            ui_text.draw_text(surface, vp, "Equipped" if selected else "Owned",
                              self.status_box.center, size, alpha=shade,
                              color=config.COLOR_GOOD if selected else config.COLOR_TEXT_DIM,
                              max_width=self.status_box.w)
            return
        # Icon and number share one colour, so affordability is a single signal
        # rather than a gold feather arguing with a red price.
        color = config.COLOR_GOLD if feathers >= cost else config.COLOR_DANGER
        # The feather tracks the number it is attached to, so the pair stays one
        # object when the type is lifted off its pixel floor.
        icon = min(m.hud_icon * self.type_boost, self.status_box.h)
        pad = m.gap * 0.5
        # Plain text, not draw_number: a price never ticks, and the fixed digit
        # grid that stops a counter jittering opens a gap after a narrow digit
        # that reads as a thousands separator once the window gets small.
        price = str(int(cost))
        left = self.status_box.cx - (icon + pad + ui_text.measure(price, size, vp)[0]) * 0.5
        draw_icon(surface, vp, "feather",
                  Box(left, self.status_box.cy - icon * 0.5, icon, icon), shade, color)
        ui_text.draw_text(surface, vp, price, (left + icon + pad, self.status_box.cy),
                          size, color=color, align="left", alpha=shade)

    def _draw_progress_dots(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        """One dot per bird: owned ones are filled, locked ones are rings.

        The row has to exist anyway to say where the player is in the list, so
        it carries the collection's progress as well instead of repeating it in
        a second line of text that would compete with the price. Ownership and
        proximity get a channel each -- fill and colour -- because sharing one
        made three locked dots read as "far away", which is what a smaller dot
        means in every pager ever built, and made the even spacing look uneven.
        """
        carousel = self.carousel
        if carousel is None or not carousel.items:
            return
        count = len(carousel.items)
        stride = max(1e-6, carousel.stride)
        live = (carousel.items_box.cx - carousel.item_box(0).cx) / stride

        radius = vp.ipx(self.dot_r)
        width = max(1, vp.ipx(self.dot_r * 0.34))
        start = self.strip_box.cx - self.dot_gap * (count - 1) * 0.5
        accent = self.app.world.accent
        shade = int(230 * alpha)
        if shade <= 0:
            return
        for i, item in enumerate(carousel.items):
            # Quantised so a dragging row reuses a few cached shapes rather
            # than allocating one per dot per frame.
            near = round(max(0.0, 1.0 - abs(live - i)) * 8.0) / 8.0
            color = _mix(config.COLOR_TEXT_DIM, _quantise(accent, 8), near)
            shape = (_disc(radius, color, shade) if self.app.profile.is_unlocked(item[0])
                     else _ring(radius, width, color, shade))
            surface.blit(shape, shape.get_rect(
                center=vp.to_screen_i(start + i * self.dot_gap, self.strip_box.cy)))

    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        self.app.world.draw_background(surface, vp)
        self.app.world.draw_ground(surface, vp)

        m = self.m
        # The header is screen chrome rather than part of the dialog, so it only
        # borrows the dialog's fade -- it does not scale in with it.
        progress = self.panel.progress if self.panel is not None else 1.0
        head_alpha = int(255 * progress)
        owned = len(self.app.profile["unlocked"])
        title_size, sub_size = self._size("small"), self._size("tiny")
        title_h = ui_text.measure("Ag", title_size, vp)[1]
        sub_h = ui_text.measure("Ag", sub_size, vp)[1]

        # The header is the one block of text on the screen with nothing behind
        # it, and it sits over a sky that moves: the sun and the clouds drift
        # under it, so the contrast changes frame to frame. It gets the same
        # scrim the close button beside it already carries, which also ties the
        # row to the dialog it labels.
        draw_frame(surface, vp, self.header_box.inflated(m.gap * 0.5, m.gap * 0.25),
                   "button_ghost.png", config.BUTTON_INSET, "ghost",
                   int(_HEADER_SCRIM * progress))

        text_w = self.header_box.w - m.icon_button - m.gap * 2.0
        top = self.header_box.cy - (title_h + m.gap * 0.3 + sub_h) * 0.5
        left = self.header_box.x + m.gap * 0.5
        ui_text.draw_text(surface, vp, "CHOOSE YOUR BIRD", (left, top + title_h * 0.5),
                          title_size, color=config.COLOR_TEXT, align="left", alpha=head_alpha,
                          max_width=text_w)
        ui_text.draw_text(surface, vp, f"{owned} of {len(config.SKINS)} unlocked",
                          (left, top + title_h + m.gap * 0.3 + sub_h * 0.5),
                          sub_size, color=config.COLOR_TEXT_DIM, align="left",
                          alpha=head_alpha, max_width=text_w)

        self.group.draw(surface, vp)
