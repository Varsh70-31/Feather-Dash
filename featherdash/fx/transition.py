"""Scene transitions: Fade, Iris and SwipeWipe.

A transition runs in two halves. `start("in")` obscures the screen; once
`covering` is True the scene underneath can be swapped with nothing visible to
the player; `start("out")` then reveals the new one. Keeping the two halves
separate (rather than one call with a callback in the middle) means the scene
swap happens in the game loop's own control flow, where it can fail, log or be
cancelled, instead of inside a draw call.

Timing comes from `config.SCENE_FADE`. These are deliberately short: a
transition is a cut that hides a load, and every millisecond of it is a
millisecond the player is not playing.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Type

import pygame

from .. import config
from .easing import EaseFn, clamp01, in_out_quad, in_quad, out_quad

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..display import Viewport

__all__ = ["Transition", "Fade", "Iris", "SwipeWipe", "TRANSITIONS", "make"]

IN = "in"
OUT = "out"

# The polygon rasteriser excludes its right edge, so a wipe whose leading edge
# lands exactly on the last column leaves that column uncovered. One pixel of
# lead is invisible mid-wipe and removes the corner leak at the end of it.
_LEAD_BIAS = 1.0


def _cover_around(surface: pygame.Surface, box: pygame.Rect,
                  color: Tuple[int, int, int]) -> None:
    """Flat-fill everything outside `box`, in up to four rectangles."""
    w, h = surface.get_size()
    if box.top > 0:
        surface.fill(color, (0, 0, w, box.top))
    if box.bottom < h:
        surface.fill(color, (0, box.bottom, w, h - box.bottom))
    if box.left > 0:
        surface.fill(color, (0, box.top, box.left, box.height))
    if box.right < w:
        surface.fill(color, (box.right, box.top, w - box.right, box.height))


class Transition:
    """Base timing and state. Subclasses only implement `_draw`."""

    def __init__(
        self,
        duration: float = config.SCENE_FADE,
        color: Tuple[int, int, int] = (9, 11, 22),
        ease: EaseFn = in_out_quad,
    ) -> None:
        self.duration = max(0.0, float(duration))
        self.color = (int(color[0]), int(color[1]), int(color[2]))
        self.ease = ease
        self._elapsed = 0.0
        self._direction = OUT
        self._started = False
        self._origin: Optional[Tuple[float, float]] = None
        self._surface: Optional[pygame.Surface] = None
        self._surface_key: Tuple[Any, ...] = ()
        # The part of the scratch surface last frame's shape wrote into, and
        # therefore the only part the next frame has to restore.
        self._dirty: Optional[pygame.Rect] = None

    # -- control -----------------------------------------------------------
    def start(
        self,
        direction: str = IN,
        origin: Optional[Tuple[float, float]] = None,
        duration: Optional[float] = None,
    ) -> "Transition":
        """Begin obscuring (`"in"`) or revealing (`"out"`) the screen.

        `origin` is a world-unit point for the shapes that have one (Iris),
        e.g. the bird's position, so the wipe closes on what the player was
        looking at rather than on an arbitrary screen centre.
        """
        if direction not in (IN, OUT):
            raise ValueError("direction must be 'in' or 'out', got " + repr(direction))
        self._direction = direction
        self._elapsed = 0.0
        self._started = True
        if duration is not None:
            self.duration = max(0.0, float(duration))
        if origin is not None:
            self._origin = (float(origin[0]), float(origin[1]))
        return self

    def update(self, dt: float) -> bool:
        """Advance; returns True once this half of the transition is finished."""
        if not self._started:
            return True
        if self.duration <= 0.0:
            self._elapsed = 0.0
            return True
        if self._elapsed < self.duration:
            self._elapsed = min(self.duration, self._elapsed + max(0.0, dt))
        return self._elapsed >= self.duration

    def reset(self, covered: bool = False) -> None:
        """Force the transition fully open (or fully closed) with no animation."""
        self._started = covered
        self._direction = IN
        self._elapsed = self.duration

    # -- state -------------------------------------------------------------
    @property
    def direction(self) -> str:
        return self._direction

    @property
    def progress(self) -> float:
        if not self._started:
            return 1.0
        if self.duration <= 0.0:
            return 1.0
        return clamp01(self._elapsed / self.duration)

    @property
    def obscurity(self) -> float:
        """0 = the scene is fully visible, 1 = the screen is fully hidden."""
        if not self._started:
            return 0.0
        e = self.ease(self.progress)
        return e if self._direction == IN else 1.0 - e

    @property
    def done(self) -> bool:
        return (not self._started) or self.progress >= 1.0

    @property
    def active(self) -> bool:
        """True while something still has to be drawn -- note this stays True
        after an inward transition finishes, because the cover must keep being
        drawn until the outward half starts."""
        return self._started and self.obscurity > 0.0

    @property
    def covering(self) -> bool:
        """True only when `draw` paints a solid rectangle of `color` over the
        whole surface: the safe window in which to swap scenes.

        This is a statement about the *timeline*, not about the eased value,
        and `draw` enforces it by short-circuiting to a plain fill whenever it
        is True. An epsilon on `obscurity` cannot do the job: at 0.999 the
        iris still has a feathered hole in it (measured: 6036 leaked pixels at
        1080x2400, 4 of them raw scene), and no epsilon small enough to close
        that hole can be reached at all by an easing whose endpoint is
        0.9999999999999998 in floating point -- `in_back` is exactly that, and
        the ease is a constructor argument.
        """
        if not self._started:
            return False
        if self.duration <= 0.0:
            return self._direction == IN
        if self._direction == IN:
            return self._elapsed >= self.duration
        return self._elapsed <= 0.0

    # -- drawing -----------------------------------------------------------
    def draw(self, surface: pygame.Surface, viewport: "Optional[Viewport]" = None) -> None:
        k = self.obscurity
        if k <= 0.0:
            return
        if k >= 1.0 or self.covering:
            # The one place the `covering` promise is kept. It is also the
            # cheapest draw in the file (0.2 ms at 1080x2400 against 6.5 for
            # the blit it replaces), which is what the end of every wipe is.
            surface.fill(self.color)
            return
        self._draw(surface, viewport, k)

    def _draw(self, surface: pygame.Surface, viewport: "Optional[Viewport]", k: float) -> None:
        raise NotImplementedError

    def _overlay(
        self,
        surface: pygame.Surface,
        srcalpha: bool = True,
        base: Optional[Tuple[int, int, int, int]] = None,
        region: Optional[pygame.Rect] = None,
    ) -> pygame.Surface:
        """A screen-sized scratch surface, rebuilt only when the window, the
        colour or the pixel format changes -- allocating one per frame would
        make the transition the most expensive thing on screen.

        The returned surface holds `base` everywhere outside `region`; the
        caller promises to draw only inside `region`, which is restored on the
        next call. A shape that moves a little per frame therefore pays for
        its own bounding box rather than for the whole screen.

        `srcalpha` is a real choice, not a detail: a per-pixel-alpha source
        blitted with a surface alpha is pygame's slowest path (6.5 ms at
        1080x2400 for a flat wash), so a shape that does not punch holes must
        ask for an opaque surface instead.
        """
        w, h = surface.get_size()
        if base is None:
            base = (self.color[0], self.color[1], self.color[2], 255)
        key = (w, h, srcalpha, base)
        if self._surface is None or self._surface_key != key:
            self._surface = (pygame.Surface((w, h), pygame.SRCALPHA) if srcalpha
                             else pygame.Surface((w, h)))
            self._surface_key = key
            self._surface.fill(base)
        elif self._dirty is not None:
            self._surface.fill(base, self._dirty)
        self._dirty = pygame.Rect(region) if region is not None else None
        return self._surface

    def _origin_px(self, surface: pygame.Surface, viewport: "Optional[Viewport]") -> Tuple[int, int]:
        w, h = surface.get_size()
        if self._origin is None or viewport is None:
            return (w // 2, h // 2)
        x, y = viewport.to_screen(self._origin[0], self._origin[1])
        return (int(x), int(y))


class Fade(Transition):
    """Dip to a flat colour. The default, and the right answer most of the time."""

    def __init__(
        self,
        duration: float = config.SCENE_FADE,
        color: Tuple[int, int, int] = (9, 11, 22),
        ease: EaseFn = in_out_quad,
    ) -> None:
        super().__init__(duration, color, ease)

    def _draw(self, surface: pygame.Surface, viewport: "Optional[Viewport]", k: float) -> None:
        # `round`, not `int`: truncation leaves the screen a level off the
        # cover colour for the whole last 0.2% of the wipe.
        alpha = int(round(255.0 * k))
        if alpha >= 255:
            surface.fill(self.color)
            return
        # Opaque, because a flat wash has no per-pixel alpha to carry and the
        # SRCALPHA source is what made this the most expensive draw on screen.
        overlay = self._overlay(surface, srcalpha=False)
        overlay.set_alpha(alpha)
        surface.blit(overlay, (0, 0))


class Iris(Transition):
    """A circular wipe closing on (or opening from) a point.

    Used when the transition should point at something -- closing on the bird
    at the moment of death ties the cut to the event that caused it.
    """

    def __init__(
        self,
        duration: float = config.SCENE_FADE * 1.4,
        color: Tuple[int, int, int] = (9, 11, 22),
        ease: EaseFn = in_quad,
        feather: float = 0.018,
    ) -> None:
        super().__init__(duration, color, ease)
        # A hard-edged circle on a high-density display aliases badly; a few
        # feathered rings cost nothing and remove the stair-stepping.
        self.feather = float(feather)

    def _draw(self, surface: pygame.Surface, viewport: "Optional[Viewport]", k: float) -> None:
        w, h = surface.get_size()
        r, g, b = self.color
        cx, cy = self._origin_px(surface, viewport)
        # The hole must clear the furthest corner, or the last sliver of scene
        # never disappears on an off-centre origin.
        reach = max(
            math.hypot(cx, cy), math.hypot(w - cx, cy),
            math.hypot(cx, h - cy), math.hypot(w - cx, h - cy),
        )
        radius = reach * (1.0 - k)
        if radius < 1.0:
            # Sub-pixel hole. Drawing it anyway would leave a lit speck -- and,
            # since the feather rings *overwrite* alpha rather than blending
            # it, a soft window tens of pixels across around a hole one pixel
            # wide. Nothing is visible through it that is worth that.
            surface.fill(self.color)
            return

        # The feather can never be wider than the hole it is softening. Left
        # unclamped it is a fixed fraction of the screen, so the iris appears
        # to stop shrinking at ~45 px and then pop out of existence.
        fw = min(max(2.0, h * self.feather), radius)
        steps = 4
        # Everything beyond the outermost ring is flat cover, and filling it
        # straight onto the target is ~5x cheaper per pixel than pushing it
        # through the per-pixel-alpha blit. The two are identical by
        # construction: that region of the overlay holds nothing else.
        edge = int(math.ceil(radius + fw)) + 3
        box = pygame.Rect(cx - edge, cy - edge, edge * 2, edge * 2).clip(surface.get_rect())
        if box.width <= 0 or box.height <= 0:
            surface.fill(self.color)
            return
        _cover_around(surface, box, self.color)

        overlay = self._overlay(surface, region=box)
        overlay.set_clip(box)
        for i in range(steps, 0, -1):
            rr = int(radius + fw * (i / steps))
            a = int(255 * (i / (steps + 1.0)))
            pygame.draw.circle(overlay, (r, g, b, a), (cx, cy), rr, int(fw / steps) + 2)
        pygame.draw.circle(overlay, (0, 0, 0, 0), (cx, cy), int(radius))
        overlay.set_clip(None)
        # set_alpha(255) looks redundant and is not: it is measurably the
        # faster of pygame's two per-pixel-alpha blit paths (3.2 ms against
        # 5.6 ms at 1080x2400), and set_alpha(None) is not an option at all --
        # it turns the blit into a raw copy that punches the hole through to
        # black instead of showing the scene.
        overlay.set_alpha(255)

        # The middle of the hole is pure transparency, so blitting it only
        # pushes the scene through the blender to arrive back at itself. The
        # largest square that fits inside the hole covers 64% of it, and
        # skipping it takes a third of the screen out of the blit while the
        # iris is wide -- which is most of the wipe.
        inner_half = int((int(radius) - 2) / 1.4143)
        hole = pygame.Rect(cx - inner_half, cy - inner_half,
                           inner_half * 2, inner_half * 2).clip(box)
        # An origin outside the view can leave nothing of the square on
        # screen, and `clip` reports that as a rect at (0, 0) rather than
        # where it was asked about -- which the four-way split below would
        # happily believe.
        if inner_half < 16 or hole.width <= 0 or hole.height <= 0:
            surface.blit(overlay, box.topleft, box)
            return
        for part in (
            pygame.Rect(box.left, box.top, box.width, hole.top - box.top),
            pygame.Rect(box.left, hole.bottom, box.width, box.bottom - hole.bottom),
            pygame.Rect(box.left, hole.top, hole.left - box.left, hole.height),
            pygame.Rect(hole.right, hole.top, box.right - hole.right, hole.height),
        ):
            if part.width > 0 and part.height > 0:
                surface.blit(overlay, part.topleft, part)


class SwipeWipe(Transition):
    """A slanted bar sweeping across the screen.

    The slant is what sells it: a vertical edge reads as a loading bar, while
    a raked one reads as motion in the same direction the bird travels.
    """

    def __init__(
        self,
        duration: float = config.SCENE_FADE,
        color: Tuple[int, int, int] = (9, 11, 22),
        ease: EaseFn = out_quad,
        skew: float = 0.16,
        highlight: Tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        super().__init__(duration, color, ease)
        self.skew = float(skew)
        self.highlight = highlight

    def _draw(self, surface: pygame.Surface, viewport: "Optional[Viewport]", k: float) -> None:
        w, h = surface.get_size()
        skew = h * self.skew
        colour = (self.color[0], self.color[1], self.color[2], 255)
        glow_w = max(2, int(h * 0.006))

        inward = self._direction == IN
        # Covered region grows from the left going in, and is everything ahead
        # of the front coming out, which slides off the right-hand edge.
        front = -skew + (w + 2.0 * skew) * (k if inward else 1.0 - k)
        edge = ((front, -2.0), (front - skew, h + 2.0))
        lead = front + _LEAD_BIAS
        if inward:
            pts = [(-skew - 2.0, -2.0), (lead, -2.0), (lead - skew, h + 2.0), (-skew - 2.0, h + 2.0)]
        else:
            pts = [(lead, -2.0), (w + skew + 2.0, -2.0),
                   (w + skew + 2.0, h + 2.0), (lead - skew, h + 2.0)]

        # Only the slanted band is actually in transition: every column left
        # of it is one of solid cover or untouched scene, and so is every
        # column right of it. Resolving those with a flat fill (or nothing)
        # keeps the per-pixel-alpha blit down to the band itself -- at the
        # default skew that is a third of the screen rather than all of it.
        margin = glow_w + 3
        left = max(0, min(w, int(math.floor(front - skew)) - margin))
        right = max(0, min(w, int(math.ceil(front)) + margin))
        band = pygame.Rect(left, 0, max(0, right - left), h)
        if inward and band.left > 0:
            surface.fill(self.color, (0, 0, band.left, h))
        elif not inward and band.right < w:
            surface.fill(self.color, (band.right, 0, w - band.right, h))
        if band.width <= 0:
            return

        overlay = self._overlay(surface, base=(0, 0, 0, 0), region=band)
        # The clip bounds the rasteriser as well as the refill: without it the
        # polygon is screen-sized at the end of the wipe.
        overlay.set_clip(band)
        pygame.draw.polygon(overlay, colour, pts)
        # A bright leading edge, faded out as the bar settles so it does not
        # sit as a static line once the screen is covered. Drawn on the true
        # front rather than the biased one, so the lead never shows.
        glow = int(190 * (1.0 - abs(k * 2.0 - 1.0)))
        if glow > 4:
            pygame.draw.line(overlay, (self.highlight[0], self.highlight[1], self.highlight[2], glow),
                             edge[0], edge[1], glow_w)
        overlay.set_clip(None)
        overlay.set_alpha(255)
        surface.blit(overlay, band.topleft, band)


TRANSITIONS: Dict[str, Type[Transition]] = {
    "fade": Fade,
    "iris": Iris,
    "swipe": SwipeWipe,
}


def make(kind: str = "fade", **kwargs: object) -> Transition:
    """Build a transition by name, so scenes can be configured from data."""
    cls = TRANSITIONS.get(kind)
    if cls is None:
        raise KeyError("unknown transition: " + kind)
    return cls(**kwargs)  # type: ignore[arg-type]
