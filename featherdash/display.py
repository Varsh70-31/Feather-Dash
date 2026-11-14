"""Resolution-independent presentation layer.

The game is authored in *world units*, never pixels. The world is always
`config.WORLD_H` units tall, and the viewport works out how many units wide the
current screen is worth. That single rule is what lets one build look right on a
20:9 phone, a 4:3 tablet and a resized desktop window:

* Vertical scale is constant, so a gap is always the same fraction of the screen
  height and the game is exactly as hard on every device.
* Horizontal extent flexes with the aspect ratio, so a wider screen simply shows
  more scenery -- clamped, because seeing too far ahead is a real advantage.
* When the screen is outside the clamp, the surplus becomes *overscan*: the
  backdrop is drawn across the whole display while gameplay stays inside the
  guaranteed box. There are never black bars.

Everything renders straight to the window at its native resolution -- there is no
intermediate low-res canvas -- so output is crisp at any size, including HiDPI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import pygame

from . import config
from .platform import IS_ANDROID, IS_MOBILE


@dataclass(frozen=True)
class Viewport:
    """An immutable description of how world units map onto the current screen."""

    screen_w: int
    screen_h: int
    scale: float          # pixels per world unit
    world_w: float        # guaranteed play-area width, in world units
    origin_x: float       # pixel x of world x=0
    origin_y: float       # pixel y of world y=0
    view_left: float      # world-space bounds of everything actually visible,
    view_right: float     # which may exceed the play area when overscanning
    view_top: float
    view_bottom: float
    safe: tuple[float, float, float, float] = (0, 0, 0, 0)  # l, t, r, b insets in world units

    # -- mapping -----------------------------------------------------------
    @property
    def world_h(self) -> float:
        return float(config.WORLD_H)

    def px(self, units: float) -> float:
        """World-unit length to pixels."""
        return units * self.scale

    def ipx(self, units: float) -> int:
        return int(round(units * self.scale))

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin_x + x * self.scale, self.origin_y + y * self.scale)

    def to_screen_i(self, x: float, y: float) -> tuple[int, int]:
        return (int(round(self.origin_x + x * self.scale)),
                int(round(self.origin_y + y * self.scale)))

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        """Screen pixels back to world units -- used for touch and mouse input."""
        return ((sx - self.origin_x) / self.scale, (sy - self.origin_y) / self.scale)

    def rect(self, x: float, y: float, w: float, h: float) -> pygame.Rect:
        """A pixel Rect from a world-space box, sized so adjacent rects never gap."""
        left, top = self.to_screen(x, y)
        right, bottom = self.to_screen(x + w, y + h)
        ileft, itop = int(round(left)), int(round(top))
        return pygame.Rect(ileft, itop, int(round(right)) - ileft, int(round(bottom)) - itop)

    # -- device adaptation -------------------------------------------------
    # Two independent questions, deliberately kept apart:
    #   * how much room is there  -> size_class, which drives type scale,
    #     spacing and how much a panel is allowed to spread out;
    #   * how is it being touched -> `touch`, which drives minimum hit targets.
    # Conflating them into one "is this a phone" flag is what produces tablet
    # layouts with phone-sized buttons, and desktop windows with thumb-sized ones.

    @property
    def size_class(self) -> str:
        """'compact', 'regular' or 'expanded', from the width actually available."""
        if self.world_w < 660.0:
            return "compact"
        if self.world_w < 880.0:
            return "regular"
        return "expanded"

    @property
    def touch(self) -> bool:
        """True when the game is being operated by finger rather than pointer.

        FEATHERDASH_FORCE_TOUCH overrides it, which is how a touch layout gets
        checked on a desktop and how the store screenshots are captured showing
        the controls a phone player will actually see.
        """
        override = os.environ.get("FEATHERDASH_FORCE_TOUCH")
        if override is not None:
            return override.strip().lower() not in ("", "0", "false", "no")
        return IS_MOBILE

    @property
    def landscape(self) -> bool:
        """True when the play area is wider than it is tall-ish.

        Not simply `screen_w > screen_h`: the play area is clamped, so a very
        wide monitor still presents a portrait-shaped board with scenery beside
        it, and the UI should stay laid out for that board.
        """
        return self.world_w / self.world_h > 0.78

    # -- layout anchors ----------------------------------------------------
    @property
    def cx(self) -> float:
        """Horizontal centre of the play area, in world units."""
        return self.world_w * 0.5

    @property
    def cy(self) -> float:
        return self.world_h * 0.5

    @property
    def ground_y(self) -> float:
        """World y where the ground's collision surface sits."""
        return self.world_h - config.GROUND_H

    @property
    def bird_x(self) -> float:
        return self.world_w * config.BIRD_X_FRAC

    def safe_left(self) -> float:
        return self.safe[0] + config.SAFE_PAD

    def safe_top(self) -> float:
        return self.safe[1] + config.SAFE_PAD

    def safe_right(self) -> float:
        return self.world_w - self.safe[2] - config.SAFE_PAD

    def safe_bottom(self) -> float:
        return self.world_h - self.safe[3] - config.SAFE_PAD

    @property
    def full_rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, self.screen_w, self.screen_h)

    @property
    def play_rect(self) -> pygame.Rect:
        """Pixels covering the guaranteed play area, excluding overscan.

        Obstacles are clipped to this. Without it, a window wider than the clamp
        shows pillars out in the pillarboxed margin, and the player sees much
        further ahead than the clamp is meant to allow -- measured at 2.7 pillar
        spacings on an ordinary 16:9 desktop against 0.9 on a phone.
        """
        return self.rect(0.0, self.view_top - 1.0,
                         self.world_w, (self.view_bottom - self.view_top) + 2.0)


def compute(screen_w: int, screen_h: int, safe: tuple[float, float, float, float] = (0, 0, 0, 0)) -> Viewport:
    """Derive a `Viewport` for a given framebuffer size.

    Height-anchored by default; falls back to width-anchoring only when the
    display is so tall and narrow that height-anchoring would leave the play
    area below the legible minimum.
    """
    screen_w = max(1, int(screen_w))
    screen_h = max(1, int(screen_h))

    scale = screen_h / config.WORLD_H
    units_wide = screen_w / scale

    if units_wide < config.WORLD_W_MIN:
        # Extremely tall/narrow (a 21:9 phone held upright, or a slim window):
        # anchor to width instead and let the surplus height become overscan.
        scale = screen_w / config.WORLD_W_MIN
        world_w = float(config.WORLD_W_MIN)
    else:
        world_w = min(units_wide, float(config.WORLD_W_MAX))

    # Centre the play area; any surplus screen is overscan on both sides.
    origin_x = (screen_w - world_w * scale) * 0.5
    origin_y = (screen_h - config.WORLD_H * scale) * 0.5

    return Viewport(
        screen_w=screen_w,
        screen_h=screen_h,
        scale=scale,
        world_w=world_w,
        origin_x=origin_x,
        origin_y=origin_y,
        view_left=-origin_x / scale,
        view_right=(screen_w - origin_x) / scale,
        view_top=-origin_y / scale,
        view_bottom=(screen_h - origin_y) / scale,
        safe=safe,
    )


class Display:
    """Owns the window and keeps a current `Viewport` in sync with its size."""

    def __init__(self) -> None:
        self.fullscreen = IS_MOBILE
        self._windowed_size = _window_override() or config.WINDOW_DEFAULT
        self.surface: pygame.Surface = self._create()
        self.viewport: Viewport = compute(*self.surface.get_size(), safe=self._query_safe_insets())

    # -- window management -------------------------------------------------
    def _flags(self) -> int:
        if IS_MOBILE and not _window_override():
            return pygame.FULLSCREEN | pygame.SCALED
        return pygame.RESIZABLE | (pygame.FULLSCREEN if self.fullscreen else 0)

    def _create(self) -> pygame.Surface:
        if IS_MOBILE and not _window_override():
            # (0, 0) asks SDL for the device's own resolution.
            surface = pygame.display.set_mode((0, 0), self._flags())
        else:
            surface = pygame.display.set_mode(self._windowed_size, self._flags(), vsync=1)
            pygame.display.set_caption("Feather Dash")
        return surface

    def resize(self, w: int, h: int) -> Viewport:
        w = max(config.WINDOW_MIN[0], int(w))
        h = max(config.WINDOW_MIN[1], int(h))
        if not self.fullscreen and not IS_MOBILE:
            self._windowed_size = (w, h)
        # On a RESIZABLE window SDL has already resized the backing surface, so
        # re-reading it is both cheaper and more accurate than setting a mode.
        surface = pygame.display.get_surface()
        self.surface = surface if surface is not None else self._create()
        self.viewport = compute(*self.surface.get_size(), safe=self._query_safe_insets())
        return self.viewport

    def toggle_fullscreen(self) -> Viewport:
        if IS_MOBILE:
            return self.viewport
        self.fullscreen = not self.fullscreen
        if self.fullscreen:
            self.surface = pygame.display.set_mode((0, 0), self._flags(), vsync=1)
        else:
            self.surface = pygame.display.set_mode(self._windowed_size, self._flags(), vsync=1)
        self.viewport = compute(*self.surface.get_size(), safe=self._query_safe_insets())
        return self.viewport

    def sync(self) -> bool:
        """Re-derive the viewport if the framebuffer changed underneath us.

        Android resizes the surface on rotation and when the system bars appear,
        without always sending a VIDEORESIZE event, so the loop calls this each
        frame. Returns True when the viewport actually changed.
        """
        surface = pygame.display.get_surface()
        if surface is None:
            return False
        self.surface = surface
        size = surface.get_size()
        if (size[0], size[1]) != (self.viewport.screen_w, self.viewport.screen_h):
            self.viewport = compute(*size, safe=self._query_safe_insets())
            return True
        return False

    # -- display cutouts ---------------------------------------------------
    def _query_safe_insets(self) -> tuple[float, float, float, float]:
        """Insets (l, t, r, b) in world units that UI must stay clear of.

        Phones put camera cutouts, status bars and gesture handles over the
        edges of a fullscreen app. Reading the real insets keeps the score and
        the buttons out from under them.
        """
        override = os.environ.get("FEATHERDASH_SAFE_INSETS")
        if override:
            try:
                parts = [float(p) for p in override.split(",")]
                if len(parts) == 4:
                    return (parts[0], parts[1], parts[2], parts[3])
            except ValueError:
                pass
        if not IS_ANDROID:
            return (0.0, 0.0, 0.0, 0.0)
        try:
            from jnius import autoclass  # type: ignore

            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            insets = activity.getWindow().getDecorView().getRootWindowInsets()
            if insets is None:
                return (0.0, 0.0, 0.0, 0.0)
            cutout = insets.getDisplayCutout()
            if cutout is None:
                return (0.0, 0.0, 0.0, 0.0)
            # Android reports device pixels; the viewport works in world units.
            px_to_world = 1.0 / max(self.viewport.scale, 1e-6) if hasattr(self, "viewport") else 1.0
            return (
                cutout.getSafeInsetLeft() * px_to_world,
                cutout.getSafeInsetTop() * px_to_world,
                cutout.getSafeInsetRight() * px_to_world,
                cutout.getSafeInsetBottom() * px_to_world,
            )
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)


def _window_override() -> tuple[int, int] | None:
    """Force an exact window size via FEATHERDASH_WINDOW=WxH.

    Used by the screenshot tool and by tests that need a specific aspect ratio
    without depending on whatever window manager is present.
    """
    raw = os.environ.get("FEATHERDASH_WINDOW")
    if not raw:
        return None
    try:
        w, h = raw.lower().split("x")
        return (max(1, int(w)), max(1, int(h)))
    except (ValueError, AttributeError):
        return None
