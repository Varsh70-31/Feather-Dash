"""Title screen.

The world keeps scrolling behind the menu and the bird idles in frame, so the
game is already alive before the player presses anything -- the cheapest way to
make a title screen feel like part of the product rather than a form.

The screen is deliberately four things and nothing else: the logo, one dominant
action, one quiet one, and a single line of help -- plus a status strip once the
player has earned something to put in it. It re-forms per device class rather
than scaling one phone layout up:

* **compact** -- a single centred column with the actions parked low, where a
  thumb already rests.
* **regular** -- the same column, capped at `metrics.button_w` so a tablet reads
  as a tablet (wider margins, larger type) instead of a stretched phone.
* **expanded and genuinely wide** -- a tall stack would crowd the logo on a
  landscape board, so the two actions sit side by side, weighted so the primary
  keeps its full width, and the middle band is left to the bird and the scenery.

Three rules hold the composition together on every one of those:

* The interface is anchored to the *screen*, not to the clamped play area --
  see `_content_box`.
* Nothing interactive crosses the ground seam. A button that straddles it picks
  up the ragged grass silhouette through its fill and reads as unfinished.
* The mark, the bird and the actions are one descending column, so the air ends
  up between them rather than pooling in the middle of the screen.

Every dimension here comes from `metrics_for(vp)` or `vp.safe_*()`; the only
exceptions are the proportions below, which Metrics has no value for and which
are each pinned to something that does scale per device.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, List, Optional, Tuple

import pygame

from .. import assets, config
from ..display import Viewport
from ..entities import Bird
from ..fx import easing
from ..input import Frame
from ..ui import text as ui_text
from ..ui.layout import Box, safe_box
from ..ui.metrics import Metrics, metrics_for
from ..ui.widgets import Button, IconButton, Widget, WidgetGroup, draw_icon
from .base import Scene

_LOGO = "logo.png"

# Entrance: one short lift. Long enough to read as motion, short enough that a
# player who taps the moment the screen appears never waits for it. Deliberately
# the same duration as `widgets.ENTRANCE_TIME`, so a panel opening elsewhere in
# the game moves at the speed this screen taught the player to expect.
_INTRO = 0.26
# How far that lift travels, as a multiple of the section gap. A single `gap`
# was 35px on a phone -- an entrance nobody could see.
_RISE_OF_SECTION = 1.5

# The logo breathes rather than bounces. The rate is in radians per second; the
# amplitude rides on the spacing rhythm so it stays proportional per device.
_SWAY_RATE = 1.1
_SWAY_OF_GAP = 0.34

# Outline weight as a fraction of the glyph height. Metrics carries no stroke
# value, and text crossing a bright cloud is illegible without one, so it is
# pinned to the type size instead -- that way it still scales per device.
_OUTLINE_OF_TYPE = 0.07

# Where the idle bird hovers on the menu, as a fraction of the world height.
# Gameplay starts it at `config.BIRD_START_Y` (0.44) because that is the height
# the first pillar is tuned around; the menu has no pillars and does have a
# brand mark, so the bird drops into the band between the logo and the actions
# instead of floating in the middle of nothing. A fraction rather than a
# constant because it is a composition anchor, like `config.CLOUD_BAND`.
_BIRD_Y_FRAC = 0.52

# Where the mark sits inside the band it is given: 0 is hard under the status
# strip, 1 is hard above the bird. A touch below centre, so the mark leans
# towards the bird it belongs with rather than towards the strip, and the air
# ends up under a thin bar instead of between the logo and the primary action.
_LOGO_IN_BAND = 0.6

# The secondary action's width on a wide board, as a multiple of `row_h` -- the
# height it is already drawn at. Keeping the pair out of a 50/50 split is what
# leaves the primary dominant on the one shape with room to spare.
_SECONDARY_OF_ROW_H = 2.6


def _type(m: Metrics, key: str) -> float:
    """A `config.FONT_SIZES` key resolved through this device's type scale."""
    return m.scaled(config.FONT_SIZES[key])


def _outline(size: float) -> float:
    return size * _OUTLINE_OF_TYPE


def _content_box(vp: Viewport) -> Box:
    """The rectangle the interface may occupy, in world units.

    `safe_box` measures from the *play area*, and the play area is clamped: a
    20:9 phone shows 82 world units of sky above it and as much ground below,
    and a 21:9 monitor 650 units of scenery on each side. All of that surplus is
    already painted, so anchoring to the play area leaves the strip and the
    settings chip floating a tenth of the display in from the real corners, with
    nothing to read them against.

    So each edge is pushed out to the screen the player is actually looking at.
    That is also where the device insets belong: Android reports a cutout from
    the *screen* edge, and `vp.safe_*()` then measures it from the play area,
    which is only the same thing when nothing is overscanning.

    The bottom edge is the exception and stays put. The inset query reports
    display cutouts but not the gesture handle, so the band below the play area
    is the one piece of surplus that interface must keep out of.
    """
    area = safe_box(vp)
    left = area.x - max(0.0, -vp.view_left)
    top = area.y - max(0.0, -vp.view_top)
    right = area.right + max(0.0, vp.view_right - vp.world_w)
    return Box(left, top, max(1.0, right - left), max(1.0, area.bottom - top))


@dataclass(frozen=True)
class _Layout:
    """Everything `draw` needs, resolved once per viewport shape."""

    cx: float
    logo: Box
    sway: float             # logo breathing amplitude, world units
    strip_x: float          # left edge of the status strip
    strip_y: float          # its centre line
    strip_w: float          # room it has before the settings button
    hint_y: float
    stat_size: float
    hint_size: float
    rise: float             # how far content travels during the entrance
    bird_dy: float          # menu hover height, as an offset from gameplay's


class MenuScene(Scene):
    name = "menu"

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.bird = Bird(str(app.profile["skin"]), 0.0)
        self.group = WidgetGroup()
        self.time = 0.0
        self.intro = 0.0
        self._built_for: Optional[Tuple[Any, ...]] = None
        self._metrics: Optional[Metrics] = None
        self._layout: Optional[_Layout] = None
        # Resting y per widget, so the entrance can slide them without the
        # layout code having to know an animation is happening.
        self._rest: List[Tuple[Widget, float]] = []

    def enter(self, **kwargs: Any) -> None:
        self.time = 0.0
        self.intro = 0.0
        self.bird.skin = str(self.app.profile["skin"])
        # The hover is anchored to `BIRD_START_Y` inside `Bird.step`; the menu's
        # own height is applied as a draw offset (see `_Layout.bird_dy`).
        self.bird.reset(self.vp.cx, config.BIRD_START_Y)
        self.app.world.set_theme(self.app.world.theme_for_score(0), immediate=True)
        self.app.audio.start_music(self.app.world.theme)
        self._built_for = None
        self._build()

    def on_resize(self, vp: Viewport) -> None:
        self._built_for = None
        self._build()

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        vp = self.vp
        # The screen size is part of the key, not just the play area: two
        # displays can share a world width and a scale and still overscan by
        # different amounts (1920x1080 and 2340x1080 both resolve to 1040x1080
        # world units), and this screen anchors to what is visible.
        key = (vp.screen_w, vp.screen_h, vp.world_w, vp.scale, vp.safe)
        if self._built_for == key:
            return
        self._built_for = key
        self.bird.x = vp.cx

        m = metrics_for(vp)
        self._metrics = m
        # The gutter inside the content box is what makes a tablet read as a
        # tablet: the same content, held further off every edge.
        area = _content_box(vp).inset(m.gutter)
        # Side by side only where the board is genuinely wide. Width alone was
        # not the test it claimed to be: a 4:3 tablet in portrait clears the
        # expanded threshold on width and would get a landscape row with half a
        # screen of empty sky above it, so the shape has to be asked about too.
        wide = m.size_class == "expanded" and area.w >= area.h

        self.group.clear()
        # Settings is the least-used destination, so it sits in the top corner
        # of the status strip rather than competing with Play.
        settings = IconButton(
            Box(area.right - m.icon_button, area.y, m.icon_button, m.icon_button),
            "settings", on_click=lambda: self._go("settings"), min_tap=m.min_tap,
        )

        # -- actions -------------------------------------------------------
        # Laid out before the brand, because the mark and the bird take what is
        # left above them rather than the other way round: the one thing on this
        # screen with a fixed home is the primary action, low and centred.
        hint_size = _type(m, "small")
        hint_h = ui_text.measure(self._hint(vp), hint_size, vp, outline=_outline(hint_size))[1]
        # Nothing interactive may cross the ground seam. The grass silhouette is
        # ragged and the secondary button's fill is translucent, so a control
        # that straddles it ends up with two tones and a torn edge through its
        # own label -- the one thing on the screen that reads as unfinished.
        actions_bottom = min(area.bottom - hint_h - m.section_gap, vp.ground_y - m.gap)
        # The hint is the line the ground strip carries, so it is centred in the
        # band below the seam: a section gap under the buttons would leave it
        # astride the grass fringe, which is the busiest edge on the screen.
        # It still keeps its distance from the actions on a shape with no band
        # to speak of, and never leaves the content box.
        hint_y = min(area.bottom - hint_h * 0.5,
                     max(actions_bottom + m.section_gap + hint_h * 0.5,
                         (vp.ground_y + area.bottom) * 0.5))

        if wide:
            # Weighted rather than halved. An even split made the primary
            # narrower on a monitor than on a phone and left the pair near
            # enough identical that only colour carried the hierarchy.
            row_w = min(m.button_w + m.gap + m.row_h * _SECONDARY_OF_ROW_H, area.w)
            play_w = max(m.row_h, min(m.button_w, row_w - m.gap - m.row_h))
            birds_w = max(m.row_h, row_w - m.gap - play_w)
            play_box = Box(area.cx - row_w * 0.5, actions_bottom - m.button_h,
                           play_w, m.button_h)
            # Bottom-aligned, so the pair shares one baseline. Centring two
            # controls of different heights misses on both edges at once, which
            # reads as a bug rather than as a hierarchy.
            birds_box = Box(play_box.right + m.gap, play_box.bottom - m.row_h,
                            birds_w, m.row_h)
        else:
            col_w = min(m.button_w, area.w)
            birds_box = Box(area.cx - col_w * 0.5, actions_bottom - m.row_h, col_w, m.row_h)
            play_box = Box(area.cx - col_w * 0.5, birds_box.y - m.gap - m.button_h,
                           col_w, m.button_h)

        play = Button(play_box, "Play", icon="play", text_size=_type(m, "large"),
                      on_click=lambda: self._go("game"), min_tap=m.min_tap)
        # Quieter on every axis the eye reads: ghost fill, shorter, smaller type.
        birds = Button(birds_box, "Birds", icon="bird", variant="ghost",
                       text_size=_type(m, "body"),
                       on_click=lambda: self._go("shop"), min_tap=m.min_tap)
        self.group.add(play, birds, settings)

        # -- brand ---------------------------------------------------------
        # The bird hovers lower here than it does in play, where its height is
        # tuned around the first pillar: on the menu it belongs between the mark
        # and the actions. It lifts off that height on a shape where the action
        # block reaches higher, so a wingtip never rests on the primary button.
        bird_y = min(vp.world_h * _BIRD_Y_FRAC,
                     play_box.y - m.section_gap - config.IDLE_BOB_AMPLITUDE
                     - config.BIRD_H * 0.5)
        # One band for the mark: from under the status strip down to the bird,
        # which is scenery rather than the next block of interface and so is
        # held off by one `gap`, not a section gap.
        band_top = area.y + m.icon_button + m.section_gap
        band_bottom = (bird_y - config.BIRD_H * 0.5
                       - config.IDLE_BOB_AMPLITUDE - m.gap)
        band_h = max(1.0, band_bottom - band_top)
        # The mark is a share of the content column -- `panel_w` -- at every
        # class, not whatever happens to be left over above the bird. The band
        # is only a backstop, for a shape short enough that the two would meet.
        logo_w = min(m.panel_w, area.w, band_h / self._logo_aspect())
        logo_h = logo_w * self._logo_aspect()
        logo = Box(area.cx - logo_w * 0.5,
                   band_top + (band_h - logo_h) * _LOGO_IN_BAND, logo_w, logo_h)

        self._layout = _Layout(
            cx=area.cx,
            logo=logo,
            sway=m.gap * _SWAY_OF_GAP,
            strip_x=area.x,
            strip_y=area.y + m.icon_button * 0.5,
            strip_w=max(1.0, area.w - m.icon_button - m.section_gap),
            hint_y=hint_y,
            stat_size=_type(m, "body"),
            hint_size=hint_size,
            rise=m.section_gap * _RISE_OF_SECTION,
            bird_dy=bird_y - config.BIRD_START_Y,
        )
        self._rest = [(w, w.rect.y) for w in (play, birds, settings)]
        # Place them where the entrance currently is, so a frame drawn before
        # the first update does not show the buttons already settled while the
        # text is still on its way up.
        self._slide()

    def _logo_aspect(self) -> float:
        """Height / width of the logo art, so layout never assumes its shape."""
        src = assets.original(_LOGO)
        return src.get_height() / max(1, src.get_width())

    def _hint(self, vp: Viewport) -> str:
        # Keyed on `touch`, not the size class: a desktop window dragged narrow
        # is still driven by a keyboard, and a tablet is still tapped.
        return "Tap anywhere to play" if vp.touch else "Press Space to play"

    def _go(self, scene: str) -> None:
        self.app.audio.play("ui_tap")
        self.app.go(scene)

    # -- frame -------------------------------------------------------------
    def handle(self, frame: Frame) -> None:
        if frame.back:
            self.app.running = False
            return
        consumed = False
        for ptr in frame.down:
            consumed |= self.group.on_pointer_down(ptr.x, ptr.y)
        for ptr in frame.moved:
            self.group.on_pointer_move(ptr.x, ptr.y)
        for ptr in frame.up:
            consumed |= self.group.on_pointer_up(ptr.x, ptr.y)
        if not consumed and frame.flap:
            self._go("game")

    def step(self, dt: float) -> None:
        self.time += dt
        self.app.world.step(dt, config.SPEED_START * 0.55)
        self.bird.step(dt)
        if self.app.particles is not None and self.time % 1.0 < dt:
            self.app.particles.emit_preset("ambient", self.vp.view_right + 20,
                                           self.vp.world_h * 0.35)

    def update(self, dt: float) -> None:
        self.intro = min(_INTRO, self.intro + dt)
        self._slide()
        self.group.update(dt)

    def _slide(self) -> None:
        """Park every widget at its resting place plus the entrance offset."""
        offset = self._offset()
        for widget, rest_y in self._rest:
            widget.rect.y = rest_y + offset

    def _progress(self) -> float:
        return easing.out_cubic(min(1.0, self.intro / _INTRO)) if _INTRO > 0 else 1.0

    def _offset(self) -> float:
        """Entrance travel still to be made up, in world units."""
        lay = self._layout
        return 0.0 if lay is None else lay.rise * (1.0 - self._progress())

    # -- drawing -----------------------------------------------------------
    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        lay, m = self._layout, self._metrics
        self.app.world.draw_background(surface, vp)
        if self.app.particles is not None:
            self.app.particles.draw(surface, vp)
        # The menu hovers the bird lower than gameplay does; `Bird.step` owns
        # the bob, so the height is applied here rather than fought over there.
        self.bird.draw(surface, vp, alpha, offset=(0.0, 0.0 if lay is None else lay.bird_dy))
        self.app.world.draw_ground(surface, vp)

        if lay is None or m is None:
            return
        offset = self._offset()
        ink = int(255 * self._progress())

        self._draw_logo(surface, vp, lay, offset, ink)
        self._draw_stats(surface, vp, m, lay, offset, ink)
        self._draw_actions(surface, vp, m, ink)
        ui_text.draw_text(surface, vp, self._hint(vp), (lay.cx, lay.hint_y + offset),
                          lay.hint_size, color=config.COLOR_TEXT_DIM,
                          outline=_outline(lay.hint_size), alpha=ink)

    def _draw_logo(self, surface: pygame.Surface, vp: Viewport, lay: _Layout,
                   offset: float, ink: int) -> None:
        sway = math.sin(self.time * _SWAY_RATE) * lay.sway
        art = assets.scaled_by_width(_LOGO, vp.px(lay.logo.w))
        art.set_alpha(ink)
        surface.blit(art, art.get_rect(center=vp.to_screen(lay.logo.cx,
                                                           lay.logo.cy + sway + offset)))
        # The surface is the shared asset cache's, so hand it back opaque.
        art.set_alpha(255)

    def _draw_actions(self, surface: pygame.Surface, vp: Viewport, m: Metrics,
                      ink: int) -> None:
        """The three controls, carrying the same entrance as everything else.

        `Button` and `IconButton` draw at a fixed opacity, and they are shared
        with every other screen, so the fade is applied from this side: each
        control is composed onto a scratch of its own and blitted at `ink`.
        Without it the first quarter-second of every launch was three solid
        buttons on a nameless screen -- no logo, no stats, no hint.

        The offscreen pass lasts exactly as long as the entrance does. `ink`
        reaches 255 within `_INTRO` and the group draws straight to the screen
        from then on, which is every frame the player spends reading this.
        """
        if ink >= 255:
            self.group.draw(surface, vp)
            return
        clip = surface.get_rect()
        for widget in self.group:
            if not widget.visible:
                continue
            # The margin covers what a control paints outside its own box: a
            # label's shadow and outline, and the overshoot of the press spring.
            rect = widget.rect.inflated(m.gap).to_rect(vp).clip(clip)
            if rect.w <= 0 or rect.h <= 0:
                continue
            scratch = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            # A viewport whose origin is shifted into the scratch lets the
            # widget draw itself normally, knowing nothing about any of this.
            sub = replace(vp, screen_w=rect.w, screen_h=rect.h,
                          origin_x=vp.origin_x - rect.x, origin_y=vp.origin_y - rect.y)
            widget.draw(scratch, sub)
            scratch.set_alpha(ink)
            surface.blit(scratch, rect.topleft)

    def _draw_stats(self, surface: pygame.Surface, vp: Viewport, m: Metrics,
                    lay: _Layout, offset: float, ink: int) -> None:
        """Best and feathers as one strip, so the screen carries one stat line.

        Tinted with the live theme accent rather than a fixed colour, so the
        strip shifts with the sky instead of sitting on top of it.
        """
        # Every stat is earned, so every stat is gated. A lone feather icon and
        # a nought at the left of an empty strip is what a fresh install used to
        # open with, and it reads as debug output rather than a designed state.
        pairs: List[Tuple[str, int]] = [
            (icon, value) for icon, value in
            (("trophy", int(self.app.profile["high_score"])),
             ("feather", int(self.app.profile["feathers"])))
            if value
        ]
        if not pairs:
            return

        size = lay.stat_size
        stroke = _outline(size)
        fixed = m.section_gap * (len(pairs) - 1) + (m.hud_icon + m.gap) * len(pairs)
        digits = sum(ui_text.number_width(v, size, vp, outline=stroke) for _, v in pairs)
        if fixed + digits > lay.strip_w and digits > 0:
            # A six-figure feather count shrinks rather than running under the
            # settings button -- the strip is the one row that cannot reflow.
            size *= max(0.0, lay.strip_w - fixed) / digits
            stroke = _outline(size)

        accent = self.app.world.accent
        y = lay.strip_y + offset
        x = lay.strip_x
        for i, (icon, value) in enumerate(pairs):
            if i:
                x += m.section_gap
            draw_icon(surface, vp, icon,
                      Box(x, y - m.hud_icon * 0.5, m.hud_icon, m.hud_icon), ink, accent)
            x += m.hud_icon + m.gap
            ui_text.draw_number(surface, vp, value, (x, y), size,
                                color=config.COLOR_TEXT_DIM, align="left",
                                outline=stroke, alpha=ink)
            x += ui_text.number_width(value, size, vp, outline=stroke)
