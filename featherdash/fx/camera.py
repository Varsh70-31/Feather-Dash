"""Camera: screen shake, impact kicks, zoom punch, hit-stop and flashes.

The camera never moves the world. It publishes a world-unit `offset` that the
renderer adds while drawing, so collision, scoring and spawn logic are entirely
unaffected by the fact that the screen is being thrown around -- a shake can
never make the game unfair or desynchronise the simulation.

Two details separate shake that feels expensive from shake that reads as a bug:

* It is sampled from **smooth noise**, not `random.uniform` per frame. Pure
  per-frame randomness is white noise: at 60 Hz it is a buzz, it strobes
  against the display's refresh, and players read it as a rendering glitch
  rather than as force. Interpolated value noise gives continuous motion with
  real direction to it.
* It **settles exactly on zero**. An asymptotic decay leaves the world a
  fraction of a unit off-centre forever, and those fractions accumulate across
  a session into a visibly crooked playfield.
"""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, List, Optional, Tuple

import pygame

from .. import config
from .easing import approach, clamp01, in_quad
from .tween import Spring

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..display import Viewport

__all__ = ["Camera"]

_SHAKE_FREQ = 19.0        # noise samples per second; ~3 oscillations per 1/6 s
_SHAKE_EPS = 0.02         # world units below which a shake is over
_ROT_PER_UNIT = 0.055     # degrees of roll per world unit of shake amplitude
_MAX_ROT = 2.6            # degrees; more than this and text becomes unreadable


class _Noise1D:
    """Value noise: random knots at integer time, smoothstep between them.

    A full Perlin implementation buys nothing here -- the signal only needs to
    be continuous, zero-mean and non-repeating over the length of a shake.
    """

    __slots__ = ("_v",)

    def __init__(self, rng: random.Random, knots: int = 61) -> None:
        # A prime-ish knot count keeps the three channels from re-aligning into
        # a visible diagonal pattern.
        self._v: List[float] = [rng.uniform(-1.0, 1.0) for _ in range(knots)]

    def __call__(self, t: float) -> float:
        n = len(self._v)
        i = int(math.floor(t))
        f = t - i
        a = self._v[i % n]
        b = self._v[(i + 1) % n]
        f = f * f * (3.0 - 2.0 * f)
        return a + (b - a) * f


class Camera:
    """Screen-space feedback for impacts. One per scene is plenty."""

    def __init__(
        self,
        decay: float = config.SHAKE_DECAY,
        seed: Optional[int] = None,
        kick_stiffness: float = 150.0,
        zoom_stiffness: float = 220.0,
    ) -> None:
        self.decay = float(decay)
        # Accessibility hook: a "reduce motion" setting scales every camera
        # effect -- shake, kick, punch, *flash and hit-stop* -- to zero without
        # any caller having to branch. The flash matters most: a full-screen
        # white strobe is the effect this setting most needs to remove.
        self.intensity = 1.0

        rng = random.Random(seed)
        self._noise_x = _Noise1D(rng)
        self._noise_y = _Noise1D(rng)
        self._noise_r = _Noise1D(rng)
        self._time = 0.0

        self._amp = 0.0
        self._falloff = 0.0        # >0 when a shake has a fixed duration
        self._shake = (0.0, 0.0)
        self._roll = 0.0

        self._kick_x = Spring(0.0, stiffness=kick_stiffness)
        self._kick_y = Spring(0.0, stiffness=kick_stiffness)
        # Underdamped so the punch-in springs back with a little bounce; a
        # critically damped zoom reads as a slow lens rather than a hit.
        self._zoom = Spring(1.0, stiffness=zoom_stiffness,
                            damping=2.0 * math.sqrt(zoom_stiffness) * 0.55, target=1.0)

        self._freeze = 0.0
        self._flash_color: Tuple[int, int, int] = (255, 255, 255)
        self._flash_t = 0.0
        self._flash_dur = 0.0
        self._flash_additive = True
        # One scratch surface serves both flash paths; see `_scratch`.
        self._add: Optional[pygame.Surface] = None
        self._add_key: Tuple[int, ...] = ()

    # -- state -------------------------------------------------------------
    # Every accessor below is gated on `intensity` as well as scaled at impulse
    # time. Scaling alone would leave an effect that is already in flight
    # running at full strength when the player turns "reduce motion" on, which
    # is the one moment they most want it to stop.
    @property
    def offset(self) -> Tuple[float, float]:
        """World-unit (x, y) to add to everything drawn in world space."""
        if self.intensity <= 0.0:
            return (0.0, 0.0)
        return (self._shake[0] + self._kick_x.value,
                self._shake[1] + self._kick_y.value)

    @property
    def zoom(self) -> float:
        return self._zoom.value if self.intensity > 0.0 else 1.0

    @property
    def rotation(self) -> float:
        """Degrees of roll. Small by design; rotating the playfield far enough
        to notice costs a full-screen rotozoom and hurts readability."""
        return self._roll if self.intensity > 0.0 else 0.0

    @property
    def frozen(self) -> bool:
        """True while hit-stop is holding the simulation. The game loop skips
        its world update on these frames but still updates the camera, so the
        freeze itself expires and the flash keeps animating under it."""
        return self._freeze > 0.0 and self.intensity > 0.0

    @property
    def shaking(self) -> bool:
        return self._amp > 0.0 and self.intensity > 0.0

    @property
    def flashing(self) -> bool:
        return self._flash_dur > 0.0 and self.intensity > 0.0

    # -- impulses ----------------------------------------------------------
    def shake(self, amount: float, duration: Optional[float] = None) -> None:
        """Shake with `amount` world units of amplitude.

        Amplitude is taken as a maximum rather than a sum: a small score shake
        landing during a death shake must not cut it short, and ten events in
        one frame must not launch the camera off the screen.
        """
        amount = max(0.0, float(amount)) * self.intensity
        if amount <= self._amp:
            return
        self._amp = amount
        self._falloff = (amount / duration) if duration and duration > 0.0 else 0.0

    def kick(self, dx: float, dy: float) -> None:
        """Shove the view `dx, dy` world units and let it spring back.

        Directional, unlike shake, so the player can tell *where* the hit came
        from -- a pillar clipped from below throws the view up.
        """
        self._kick_x.nudge(float(dx) * self.intensity)
        self._kick_y.nudge(float(dy) * self.intensity)

    def punch(self, amount: float = 0.035) -> None:
        """Zoom in by `amount` (a fraction) instantly; it springs back out."""
        target = 1.0 + max(0.0, float(amount)) * self.intensity
        if target > self._zoom.value:
            self._zoom.snap(target)

    def flash(
        self,
        color: Tuple[int, int, int] = (255, 255, 255),
        duration: float = config.FLASH_ON_DEATH,
        additive: bool = True,
    ) -> None:
        """Full-screen flash, scaled by `intensity` and never armed at zero.

        A full-screen white strobe is the single most harmful effect in the
        game for a photosensitive player, so "reduce motion" has to suppress
        it outright -- it is not a lesser cousin of the shake. `intensity`
        scales the flash's *peak*, which is the quantity that matters: with
        the additive flash that is brightness, with the alpha flash it is
        opacity, and `flash_intensity()` carries the scale for both.
        """
        if self.intensity <= 0.0:
            return
        self._flash_color = (int(color[0]), int(color[1]), int(color[2]))
        self._flash_dur = max(0.0, float(duration))
        self._flash_t = 0.0
        self._flash_additive = bool(additive)

    def freeze(self, seconds: float) -> None:
        """Hit-stop. Extends an existing freeze rather than restarting it.

        Duration is the only magnitude hit-stop has, so that is what
        `intensity` scales; at zero nothing is held at all.
        """
        self._freeze = max(self._freeze, max(0.0, float(seconds)) * self.intensity)

    def reset(self) -> None:
        """Clear everything -- called on a scene change so a death shake never
        bleeds into the menu that replaces it."""
        self._amp = 0.0
        self._falloff = 0.0
        self._shake = (0.0, 0.0)
        self._roll = 0.0
        self._kick_x.snap(0.0)
        self._kick_y.snap(0.0)
        self._zoom.snap(1.0)
        self._freeze = 0.0
        self._flash_dur = 0.0
        self._flash_t = 0.0

    # -- simulation --------------------------------------------------------
    def update(self, dt: float) -> None:
        """Advance with *real* delta time, including during hit-stop."""
        if dt <= 0.0:
            return
        self._time += dt
        self._freeze = max(0.0, self._freeze - dt)

        if self._amp > 0.0:
            if self._falloff > 0.0:
                self._amp -= self._falloff * dt
            else:
                self._amp = approach(self._amp, 0.0, self.decay, dt)
            if self._amp < _SHAKE_EPS:
                self._amp = 0.0
                self._falloff = 0.0
                self._shake = (0.0, 0.0)
                self._roll = 0.0
            else:
                t = self._time * _SHAKE_FREQ
                amp = self._amp
                # The channels are offset into different parts of the noise so
                # x and y are uncorrelated; sampling the same curve twice would
                # shake along a single diagonal line.
                self._shake = (self._noise_x(t) * amp, self._noise_y(t + 31.7) * amp * 0.92)
                roll = self._noise_r(t * 0.6 + 11.3) * amp * _ROT_PER_UNIT
                self._roll = max(-_MAX_ROT, min(_MAX_ROT, roll))

        self._kick_x.update(dt)
        self._kick_y.update(dt)
        self._zoom.update(dt)

        if self._flash_dur > 0.0:
            self._flash_t += dt
            if self._flash_t >= self._flash_dur:
                self._flash_dur = 0.0
                self._flash_t = 0.0

    # -- drawing -----------------------------------------------------------
    def flash_intensity(self) -> float:
        if self._flash_dur <= 0.0 or self.intensity <= 0.0:
            return 0.0
        # Instant on, fast off: the opposite shape would look like a fade-in,
        # and an impact has no fade-in. Clamped because `intensity` is a public
        # field and a value above 1 would otherwise build an invalid colour.
        return clamp01(in_quad(1.0 - clamp01(self._flash_t / self._flash_dur)) * self.intensity)

    def draw_overlays(self, surface: pygame.Surface, viewport: "Optional[Viewport]" = None) -> None:
        """Draw the full-screen flash. Call last, over everything including UI.

        `viewport` is accepted for signature parity with the other effect draws
        (and so a future vignette can respect the safe area); the flash itself
        is a screen-space fill.

        Both paths go through a cached screen-sized surface, because
        `Surface.fill(..., special_flags=...)` does *not* reach SDL's blitter:
        it falls back to a per-pixel software loop. Measured at 1080x2400,
        `fill(BLEND_RGB_ADD)` costs 13.7 ms against 0.56 ms for the identical
        blit -- a whole frame budget, every frame, for 0.22 s after every
        death. The surface is kept rather than freed with the flash: it is
        allocated once, on the first flash, and re-allocating several MB at
        the moment of death is exactly the hitch this is avoiding.
        """
        i = self.flash_intensity()
        if i <= 0.0:
            return
        r, g, b = self._flash_color
        size = surface.get_size()

        if self._flash_additive:
            tint = (int(r * i), int(g * i), int(b * i))
            if tint == (0, 0, 0):
                return
            scratch = self._scratch(size, tint)
            # The alpha path may have left a surface alpha behind; an additive
            # blit ignores it, but clearing it keeps the two paths independent.
            scratch.set_alpha(None)
            surface.blit(scratch, (0, 0), special_flags=pygame.BLEND_RGB_ADD)
            return

        alpha = int(255 * i)
        if alpha >= 255:
            # An opaque wash is a plain fill, and must not go through
            # set_alpha(255): SDL has no fast path for "blend at full alpha"
            # and takes 19 ms at 1080x2400 where the fill takes 0.2 ms.
            surface.fill((r, g, b))
            return
        scratch = self._scratch(size, (r, g, b))
        scratch.set_alpha(alpha)
        surface.blit(scratch, (0, 0))

    def _scratch(self, size: Tuple[int, int], color: Tuple[int, int, int]) -> pygame.Surface:
        """A screen-sized opaque surface holding exactly `color`.

        Deliberately not SRCALPHA. Both flash paths want a flat colour pushed
        through SDL's blitter, and a per-pixel-alpha source is the one thing
        that guarantees the software path instead -- which is the whole defect
        this exists to avoid.
        """
        key = (size[0], size[1], color[0], color[1], color[2])
        if self._add is None or self._add.get_size() != size:
            self._add = pygame.Surface(size)
            self._add_key = ()
        if self._add_key != key:
            # The colour only moves when the decay curve crosses an integer,
            # so a long flash at a high frame rate skips most of these fills.
            self._add.fill(color)
            self._add_key = key
        return self._add
