"""Audio, haptics, game speed and progress management.

The screen is a single dark sheet over the living world, laid out from
`ui.metrics` so the same code reads as a phone settings page, a tablet sheet and
a desktop dialog without any per-device branching beyond two decisions:

* **How much room is there** (`vp.size_class`) picks the type scale, the spacing
  rhythm and -- once the sheet is wide enough to hold a toggle label beside its
  control twice over -- a two-column body, so a landscape window gets a dialog
  rather than a tall ribbon down the middle of the screen.
* **How is it touched** (`vp.touch`, via `Metrics.min_tap`) picks only the hit
  boxes. The controls look the same size on a tablet and a desktop; the finger
  simply gets more forgiving targets.

The one thing this screen cannot take from the metrics table is vertical room:
the world is always `config.WORLD_H` tall, while a *regular* or *expanded*
device asks for components 10-25% larger than a *compact* one. Four toggle rows,
a stepper, a stats strip and a destructive action at full metric spacing do not
fit in that fixed height. So the rhythm -- and only the rhythm: gaps, section
gaps and the row pitch -- is solved down by one shared factor until the sheet
fits, which keeps `section_gap : gap` constant and leaves every type size,
button and icon at the size its device asked for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Tuple

import pygame

from .. import __version__, config
from ..display import Viewport
from ..input import Frame
from ..ui import text as ui_text
from ..ui.layout import Box, distribute, safe_box
from ..ui.metrics import Metrics, metrics_for
from ..ui.widgets import (
    Button,
    IconButton,
    Panel,
    Toggle,
    Widget,
    WidgetGroup,
    draw_icon,
)
from .base import Scene

# (label, profile key, icon when on, icon when off)
_TOGGLES: Tuple[Tuple[str, str, str, str], ...] = (
    ("Sound effects", "sfx", "sound_on", "sound_off"),
    ("Music", "music", "music_on", "music_off"),
    ("Vibration", "haptics", "haptics_on", "haptics_off"),
    ("Dash", "dash_enabled", "dash", "dash"),
)

# (short label, profile key) -- short because three tiles share one row and a
# dim caption that has been shrunk to fit is worse than a dim caption that is
# simply shorter.
_STATS: Tuple[Tuple[str, str], ...] = (
    ("Games", "games_played"),
    ("Best", "high_score"),
    ("Feathers", "total_feathers_earned"),
)

_ARMED_LABEL = "Tap again to confirm"
_RESET_LABEL = "Reset progress"
_ATTRIBUTION = "Type: Bai Jamjuree by Cadson Demak (SIL OFL 1.1)"

# Ratios, not sizes: these shape elements that metrics sizes, they never invent
# a dimension of their own.
_SPEED_SUBGAP = 0.60      # the three speed rows sit tighter than two controls do
_BAR_OF_ICON = 0.74       # ramp height, as a fraction of the icon size
_PIP_LOW = 0.34           # shortest ramp segment, as a fraction of the ramp
_RESET_OF_BUTTON = 0.66   # the destructive action is deliberately not a full button
_RESET_OF_WIDTH = 0.80    # ...and deliberately narrower than the primary action
_RULE_OF_GAP = 0.085      # hairline thickness
_HEAD_OF_ICON = 0.82      # the speed group's header is a caption, not a control
_PAD_FLOOR = 0.62         # the sheet keeps most of its padding however tight it gets
_FIT_FLOOR = 0.40         # never solve the rhythm away entirely
_RULE_ALPHA = 46          # a divider the eye finds only when it looks for it
_ARMED_ALPHA = 58         # ...and a danger wash it cannot miss
_ARM_FADE = 0.6           # seconds of the arm window spent fading the wash out
# A gap at 60% of its ideal still reads as a gap; a row at 60% of its ideal has
# stopped holding its control, so the pitch gives way at half the rhythm's rate.
_PITCH_YIELD = 0.5


@dataclass
class _Sheet:
    """Every box the settings sheet draws into, in world units."""

    panel: Box = field(default_factory=Box)
    title: Box = field(default_factory=Box)
    close: Box = field(default_factory=Box)
    rows: List[Box] = field(default_factory=list)
    toggle: float = 0.0
    label_size: float = 0.0
    speed_head: Box = field(default_factory=Box)
    speed_prev: Box = field(default_factory=Box)
    speed_next: Box = field(default_factory=Box)
    speed_value: Box = field(default_factory=Box)
    speed_scale: Box = field(default_factory=Box)
    bar: Box = field(default_factory=Box)
    bar_words: bool = True
    stats: List[Box] = field(default_factory=list)
    rule: Box = field(default_factory=Box)
    reset: Box = field(default_factory=Box)
    gap: float = 0.0
    section: float = 0.0
    pad: float = 0.0


class SettingsScene(Scene):
    name = "settings"

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.group = WidgetGroup()
        self.confirm_reset = 0.0
        self.sheet = _Sheet()
        self.m: Metrics | None = None
        self.toggles: List[Toggle] = []
        self.arrows: List[IconButton] = []
        self.reset_button: Button | None = None
        self.panel: Panel | None = None
        self._line: dict[str, float] = {}
        self.footer_y: tuple[float, float] = (0.0, 0.0)
        self._built_for: tuple[float, float] | None = None

    # -- lifecycle ---------------------------------------------------------
    def enter(self, **kwargs: Any) -> None:
        self.confirm_reset = 0.0
        self._built_for = None
        self._build()

    def on_resize(self, vp: Viewport) -> None:
        self._built_for = None
        self._build()

    # -- layout ------------------------------------------------------------
    def _build(self) -> None:
        vp = self.vp
        key = (vp.world_w, vp.scale)
        if self._built_for == key:
            return
        self._built_for = key

        m = metrics_for(vp)
        self.m = m
        area = safe_box(vp)
        # "Hg" carries both an ascender and a descender, so one measurement is
        # the real line box rather than the height of whichever glyphs a given
        # label happens to use.
        self._line = {
            name: ui_text.measure("Hg", m.scaled(size), vp)[1]
            for name, size in config.FONT_SIZES.items()
        }

        two_col = self._two_column(m, vp, area)
        panel_w = area.w if two_col else min(area.w, m.panel_w)

        # The attribution and version sit outside the sheet, on the world: they
        # are credits, not settings, and keeping them out stops the sheet from
        # growing a footer it has to pad around.
        foot_gap = m.gap * 0.25
        footer_h = self._line["tiny"] * 2 + foot_gap
        avail = max(1.0, area.h - footer_h - m.gap * _SPEED_SUBGAP)

        fit = self._solve_fit(m, vp, panel_w, two_col, avail)
        panel_h = min(self._compose(m, vp, fit, 0.0, 0.0, panel_w, two_col)[1], avail)
        sheet, _ = self._compose(m, vp, fit, area.x + (area.w - panel_w) * 0.5,
                                 area.y + (avail - panel_h) * 0.5, panel_w, two_col)
        sheet.panel = Box(sheet.panel.x, sheet.panel.y, panel_w, panel_h)
        self.sheet = sheet

        self.footer_y = (area.bottom - self._line["tiny"] * 1.5 - foot_gap,
                         area.bottom - self._line["tiny"] * 0.5)
        self._wire(m, sheet)

    def _two_column(self, m: Metrics, vp: Viewport, area: Box) -> bool:
        """Split the body only when a half-width column is genuinely a column.

        A desktop window resized narrow is not an expanded device, and an
        expanded device whose halves cannot hold a label beside its toggle --
        or a stepper either side of a readable ramp -- is better off as one
        column than as two cramped ones.
        """
        if vp.size_class != "expanded":
            return False
        half = (area.w - m.panel_pad * 2 - m.section_gap) * 0.5
        body = m.scaled(config.FONT_SIZES["body"])
        label_w = max(ui_text.measure(label, body, vp)[0] for label, *_ in _TOGGLES)
        return (half >= label_w + m.gap + m.icon_button
                and half >= m.icon_button * 2 + m.gap * 2 + m.icon * 3)

    def _solve_fit(self, m: Metrics, vp: Viewport, panel_w: float,
                   two_col: bool, budget: float) -> float:
        """Largest rhythm factor whose sheet still fits the height available.

        Bisected rather than solved in closed form because a two-column body is
        the *taller* of its columns, which is not linear in the factor.
        """
        if self._compose(m, vp, 1.0, 0.0, 0.0, panel_w, two_col)[1] <= budget:
            return 1.0
        lo, hi = _FIT_FLOOR, 1.0
        for _ in range(18):
            mid = (lo + hi) * 0.5
            if self._compose(m, vp, mid, 0.0, 0.0, panel_w, two_col)[1] <= budget:
                lo = mid
            else:
                hi = mid
        return lo

    def _compose(self, m: Metrics, vp: Viewport, fit: float, px: float, py: float,
                 panel_w: float, two_col: bool) -> Tuple[_Sheet, float]:
        """Lay the sheet out at `fit`, with (px, py) as the panel's top-left.

        Returns the boxes and the panel height, so measuring and placing can
        never disagree -- the caller runs it once at the origin to size the
        panel and once again where that size puts it.
        """
        gap = m.gap * fit
        section = m.section_gap * fit
        pitch = m.row_h * (1.0 - _PITCH_YIELD + _PITCH_YIELD * fit)
        pad = m.panel_pad * max(_PAD_FLOOR, fit)
        inner_w = max(1.0, panel_w - pad * 2)
        x, y = px + pad, py + pad
        s = _Sheet(gap=gap, section=section, pad=pad, panel=Box(px, py, panel_w, 0.0))

        # -- header: the one big thing, with the way out beside it ----------
        head_h = max(self._line["title"], m.icon_button)
        s.title = Box(x, y, max(1.0, inner_w - m.icon_button - gap), head_h)
        s.close = Box(x + inner_w - m.icon_button, y + (head_h - m.icon_button) * 0.5,
                      m.icon_button, m.icon_button)
        cursor = y + head_h + section

        # -- settings group ---------------------------------------------------
        col_w = (inner_w - section) * 0.5 if two_col else inner_w
        # The control tracks its row, so tightening the rhythm never lets two
        # toggles touch; `m.icon` is the floor below which the on/off glyph
        # stops being readable at all.
        s.toggle = min(m.icon_button, max(m.icon, pitch - gap * 0.5))
        rows_h = pitch * len(_TOGGLES)

        speed_h = self._speed_height(m, gap)
        stats_h = self._line["small"] + gap * 0.25 + self._line["tiny"]
        # Beside four toggle rows the stepper alone leaves a half-empty column,
        # so the stats ride with it; stacked, they belong with the button that
        # erases them.
        aside_h = speed_h + section + stats_h if two_col else speed_h
        # Two columns only balance when neither is left hanging: the shorter one
        # is centred against the taller so the body reads as one block.
        body_h = max(rows_h, aside_h) if two_col else rows_h + section + aside_h
        rows_y = cursor + (body_h - rows_h) * 0.5 if two_col else cursor
        s.rows = [Box(x, rows_y + i * pitch, col_w, pitch) for i in range(len(_TOGGLES))]
        s.label_size = self._label_size(m, vp, max(1.0, col_w - s.toggle - gap))

        aside_x = x + col_w + section if two_col else x
        aside_y = cursor + (body_h - aside_h) * 0.5 if two_col else cursor + rows_h + section
        self._compose_speed(m, vp, s, gap, Box(aside_x, aside_y, col_w, 0.0))
        if two_col:
            s.stats = distribute(Box(aside_x, aside_y + speed_h + section, col_w, stats_h),
                                 len(_STATS), gap * 0.5)
        cursor += body_h + section

        # -- the destructive action, fenced off -------------------------------
        s.rule = Box(x, cursor, inner_w, max(m.gap * _RULE_OF_GAP, 1.0 / max(vp.scale, 1e-6)))
        cursor += s.rule.h + section * 0.75
        if not two_col:
            s.stats = distribute(Box(x, cursor, inner_w, stats_h), len(_STATS), gap * 0.5)
            cursor += stats_h + section * 0.75

        reset_w = min(m.button_w * _RESET_OF_WIDTH, inner_w)
        reset_h = m.button_h * _RESET_OF_BUTTON
        s.reset = Box(x + (inner_w - reset_w) * 0.5, cursor, reset_w, reset_h)
        return s, (cursor + reset_h + pad) - py

    def _label_size(self, m: Metrics, vp: Viewport, room: float) -> float:
        """One type size for all four toggle labels.

        Letting `max_width` shrink each label on its own gives a column of four
        different type sizes, which is the single most obvious way a settings
        list can look unconsidered.
        """
        body = m.scaled(config.FONT_SIZES["body"])
        widest = max(ui_text.measure(label, body, vp)[0] for label, *_ in _TOGGLES)
        if widest <= room or widest <= 0:
            return body
        return max(m.scaled(config.FONT_SIZES["tiny"]), body * room / widest)

    def _speed_rows(self, m: Metrics, gap: float) -> Tuple[float, float, float, float]:
        """(head, sub-gap, stepper, ramp) heights -- the one source for both
        measuring the speed block and placing it."""
        return (max(m.icon * _HEAD_OF_ICON, self._line["small"]), gap * _SPEED_SUBGAP,
                m.icon_button, max(m.icon * _BAR_OF_ICON, self._line["tiny"]))

    def _speed_height(self, m: Metrics, gap: float) -> float:
        head, sub, ctl, bar = self._speed_rows(m, gap)
        return head + sub + ctl + sub + bar

    def _compose_speed(self, m: Metrics, vp: Viewport, s: _Sheet,
                       gap: float, box: Box) -> None:
        """Three stacked rows: what it is, what it is set to, where that sits."""
        head_h, sub, ctl_h, bar_h = self._speed_rows(m, gap)

        s.speed_head = Box(box.x, box.y, box.w, head_h)
        ctl = Box(box.x, box.y + head_h + sub, box.w, ctl_h)
        s.speed_prev = Box(ctl.x, ctl.y, ctl_h, ctl_h)
        s.speed_next = Box(ctl.right - ctl_h, ctl.y, ctl_h, ctl_h)
        s.speed_value = Box(ctl.x + ctl_h + gap, ctl.y,
                            max(1.0, ctl.w - (ctl_h + gap) * 2), ctl.h)
        s.speed_scale = Box(box.x, ctl.bottom + sub, box.w, bar_h)

        # The end captions only earn their width when what is left still reads
        # as a ramp; below that the arrows and the ramp's own slope carry it.
        word = max(ui_text.measure(w, m.scaled(config.FONT_SIZES["tiny"]), vp)[0]
                   for w in ("Slower", "Faster"))
        inset = word + gap * 0.5
        s.bar_words = (box.w - inset * 2) >= m.icon * 3
        s.bar = (Box(box.x + inset, s.speed_scale.y, box.w - inset * 2, bar_h)
                 if s.bar_words else Box(box.x, s.speed_scale.y, box.w, bar_h))

    def _wire(self, m: Metrics, s: _Sheet) -> None:
        """Rebuild the widgets onto the freshly solved boxes."""
        self.group.clear()
        self.toggles = []
        presets = config.SPEED_PRESETS
        index = self._speed_index()

        children: List[Widget] = [_Chrome(self)]
        for row, (_, key, on_icon, off_icon) in zip(s.rows, _TOGGLES):
            toggle = Toggle(
                Box(row.right - s.toggle, row.cy - s.toggle * 0.5, s.toggle, s.toggle),
                self.app.profile, key, icon_on=on_icon, icon_off=off_icon,
                on_change=_bind_toggle(self._on_toggle, key), min_tap=m.min_tap,
            )
            self.toggles.append(toggle)
            children.append(toggle)

        # Disabled, not inert: an arrow that silently does nothing at the end of
        # the range reads as a broken button.
        self.arrows = [
            IconButton(s.speed_prev, "left", on_click=lambda: self._nudge_speed(-1),
                       min_tap=m.min_tap, enabled=index > 0),
            IconButton(s.speed_next, "right", on_click=lambda: self._nudge_speed(1),
                       min_tap=m.min_tap, enabled=index < len(presets) - 1),
        ]
        children.extend(self.arrows)

        self.reset_button = Button(s.reset, _RESET_LABEL, variant="ghost", text_size="body",
                                   text_color=config.COLOR_TEXT_DIM, min_tap=m.min_tap,
                                   on_click=self._reset)
        children.append(self.reset_button)
        children.append(IconButton(s.close, "close", on_click=self._back, min_tap=m.min_tap))

        self.panel = Panel(s.panel, dark=True, children=children, pad=s.pad,
                           open_now=False)
        self.panel.open()
        self.group.add(self.panel)

    # -- state -------------------------------------------------------------
    def _speed_index(self) -> int:
        scale = float(self.app.profile["speed_scale"])
        scales = [preset for preset, _ in config.SPEED_PRESETS]
        return min(range(len(scales)), key=lambda i: abs(scales[i] - scale))

    def _nudge_speed(self, delta: int) -> None:
        index = max(0, min(len(config.SPEED_PRESETS) - 1, self._speed_index() + delta))
        scale = config.SPEED_PRESETS[index][0]
        if scale != float(self.app.profile["speed_scale"]):
            self.app.profile["speed_scale"] = scale
            self.app.profile.save()
            self.app.audio.play("ui_tap")
        else:
            self.app.audio.play("ui_back", volume=0.4)
        self._sync_arrows()

    def _sync_arrows(self) -> None:
        """Grey out whichever end of the range the player is already standing on."""
        if len(self.arrows) != 2:
            return
        index = self._speed_index()
        self.arrows[0].enabled = index > 0
        self.arrows[1].enabled = index < len(config.SPEED_PRESETS) - 1

    def _on_toggle(self, key: str, value: bool) -> None:
        if key == "sfx":
            self.app.audio.set_sfx_enabled(value)
        elif key == "music":
            self.app.audio.set_music_enabled(value)
        self.app.audio.play("ui_tap")

    def _reset(self) -> None:
        if self.confirm_reset > 0:
            self.app.profile.reset()
            self.app.apply_audio_settings()
            self.app.audio.play("ui_back")
            self.confirm_reset = 0.0
            # The stats and the speed ramp read the profile at draw time, so
            # only the toggles -- which cache their value -- need telling.
            for toggle in self.toggles:
                toggle.sync()
            self._sync_arrows()
        else:
            # Two taps, because wiping a high score by mis-tap is unrecoverable.
            self.confirm_reset = 4.0
            self.app.audio.play("ui_tap")

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
        self.app.world.step(dt, config.SPEED_START * 0.3)
        if self.confirm_reset > 0:
            self.confirm_reset = max(0.0, self.confirm_reset - dt)

    def update(self, dt: float) -> None:
        if self.reset_button is not None:
            armed = self.confirm_reset > 0
            self.reset_button.label = _ARMED_LABEL if armed else _RESET_LABEL
            self.reset_button.text_color = (config.COLOR_DANGER if armed
                                            else config.COLOR_TEXT_DIM)
        self.group.update(dt)

    # -- drawing -----------------------------------------------------------
    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        self.app.world.draw_background(surface, vp)
        self.app.world.draw_ground(surface, vp)
        self.group.draw(surface, vp)

        # Credits sit on the world, so they carry an outline the sheet's own
        # text does not need.
        m = self.m
        if m is None:
            return
        outline = m.gap * _RULE_OF_GAP
        tiny = m.scaled(config.FONT_SIZES["tiny"])
        width = safe_box(vp).w
        ui_text.draw_text(surface, vp, _ATTRIBUTION, (vp.cx, self.footer_y[0]), tiny,
                          color=config.COLOR_TEXT_DIM, outline=outline, max_width=width)
        ui_text.draw_text(surface, vp, f"Feather Dash  v{__version__}",
                          (vp.cx, self.footer_y[1]), tiny,
                          color=config.COLOR_TEXT_DIM, outline=outline, max_width=width)

    def _draw_chrome(self, surface: pygame.Surface, vp: Viewport) -> None:
        """Everything inside the sheet that is drawn rather than pressed."""
        m = self.m
        if m is None:
            return
        s = self.sheet
        accent = self.app.world.accent
        small = m.scaled(config.FONT_SIZES["small"])
        tiny = m.scaled(config.FONT_SIZES["tiny"])

        ui_text.draw_text(surface, vp, "SETTINGS", (s.title.x, s.title.cy),
                          m.scaled(config.FONT_SIZES["title"]), align="left",
                          max_width=s.title.w)

        for row, (label, *_rest) in zip(s.rows, _TOGGLES):
            ui_text.draw_text(surface, vp, label, (row.x, row.cy), s.label_size,
                              align="left", max_width=max(1.0, row.w - s.toggle - s.gap))

        self._draw_speed(surface, vp, m, s, accent, small, tiny)
        self._draw_stats(surface, vp, m, s, small, tiny)

        _fill(surface, s.rule.to_rect(vp), config.COLOR_TEXT_DIM, _RULE_ALPHA)
        if self.confirm_reset > 0:
            # Quiet until armed: the danger wash appears only with the second
            # tap, and fades out with the window it opened rather than vanishing.
            t = min(1.0, self.confirm_reset / _ARM_FADE)
            box = s.reset.inflated(s.gap * 0.25)
            _fill(surface, box.to_rect(vp), config.COLOR_DANGER, int(_ARMED_ALPHA * t),
                  radius=0.42)

    def _draw_speed(self, surface: pygame.Surface, vp: Viewport, m: Metrics, s: _Sheet,
                    accent: tuple[int, int, int], small: float, tiny: float) -> None:
        index = self._speed_index()
        scale, preset = config.SPEED_PRESETS[index]

        head = s.speed_head
        size = min(m.icon, head.h)
        icon = Box(head.x, head.cy - size * 0.5, size, size)
        draw_icon(surface, vp, "speed", icon, tint=config.COLOR_TEXT_DIM)
        label_x = icon.right + s.gap * 0.5
        ui_text.draw_text(surface, vp, "GAME SPEED", (label_x, head.cy), small,
                          color=config.COLOR_TEXT_DIM, align="left",
                          max_width=max(1.0, head.w * 0.5 - size))
        # Against version 1's pace, so the multiplier means something concrete.
        ui_text.draw_text(surface, vp, f"{config.LEGACY_SPEED * scale:.0f} units/s",
                          (head.right, head.cy), tiny, color=config.COLOR_TEXT_DIM,
                          align="right", max_width=head.w * 0.4)

        # Name and multiplier as one string: it can never collide with the
        # arrows, and it shrinks as one piece instead of two.
        ui_text.draw_text(surface, vp, f"{preset}  {scale:g}×",
                          s.speed_value.center, m.scaled(config.FONT_SIZES["large"]),
                          color=accent, max_width=s.speed_value.w)

        if s.bar_words:
            ui_text.draw_text(surface, vp, "Slower", (s.speed_scale.x, s.speed_scale.cy),
                              tiny, color=config.COLOR_TEXT_DIM, align="left")
            ui_text.draw_text(surface, vp, "Faster", (s.speed_scale.right, s.speed_scale.cy),
                              tiny, color=config.COLOR_TEXT_DIM, align="right")
        self._draw_ramp(surface, vp, s, index, accent)

    def _draw_ramp(self, surface: pygame.Surface, vp: Viewport, s: _Sheet,
                   index: int, accent: tuple[int, int, int]) -> None:
        """Eight discrete pips that climb to the right -- the range, at a glance."""
        rect = s.bar.to_rect(vp)
        if rect.w <= 0 or rect.h <= 0:
            return
        count = len(config.SPEED_PRESETS)
        layer = pygame.Surface(rect.size, pygame.SRCALPHA)
        pitch = rect.w / count
        pip_gap = max(1, int(round(pitch * 0.26)))
        pip_w = max(2, int(round(pitch)) - pip_gap)
        for i in range(count):
            lit = i <= index
            h = max(2, int(round(rect.h * (_PIP_LOW + (1.0 - _PIP_LOW) * i / (count - 1)))))
            pip = pygame.Rect(int(round(i * pitch)), rect.h - h, pip_w, h)
            colour = (*accent, 255) if lit else (*config.COLOR_TEXT_DIM, _RULE_ALPHA + 12)
            pygame.draw.rect(layer, colour, pip, border_radius=max(1, pip_w // 3))
        surface.blit(layer, rect.topleft)

    def _draw_stats(self, surface: pygame.Surface, vp: Viewport, m: Metrics, s: _Sheet,
                    small: float, tiny: float) -> None:
        """Three tiles: the number, then what it counts, quieter beneath it."""
        profile = self.app.profile
        for tile, (label, key) in zip(s.stats, _STATS):
            ui_text.draw_number(surface, vp, int(profile[key]),
                                (tile.cx, tile.y + self._line["small"] * 0.5), small)
            ui_text.draw_text(surface, vp, label,
                              (tile.cx, tile.bottom - self._line["tiny"] * 0.5), tiny,
                              color=config.COLOR_TEXT_DIM, max_width=tile.w)


class _Chrome(Widget):
    """The sheet's painted layer, as a Panel child.

    Labels, the speed ramp and the stats belong to the panel's entrance as much
    as the buttons do; drawing them separately would slide a frame out from
    under text that was already sitting still.
    """

    def __init__(self, scene: SettingsScene) -> None:
        super().__init__(Box(), min_tap=0.0)
        self.scene = scene

    def draw(self, surface: pygame.Surface, vp: Viewport) -> None:
        self.scene._draw_chrome(surface, vp)


def _fill(surface: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int],
          alpha: int, radius: float = 0.0) -> None:
    """Blit a translucent rounded rect; the window surface has no alpha channel."""
    if rect.w <= 0 or rect.h <= 0 or alpha <= 0:
        return
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(layer, (*color, max(0, min(255, alpha))),
                     pygame.Rect(0, 0, rect.w, rect.h),
                     border_radius=int(min(rect.w, rect.h) * radius))
    surface.blit(layer, rect.topleft)


def _bind_toggle(handler: Callable[[str, bool], None], key: str) -> Callable[[bool], None]:
    """Adapt Toggle's `on_change(value)` to the scene's `(key, value)` handler."""
    return lambda value: handler(key, value)
