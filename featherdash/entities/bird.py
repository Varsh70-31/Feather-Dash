"""The player character.

Simulated on the fixed timestep and drawn with interpolation, so the motion is
smooth on a 120 Hz panel while the physics stays identical to a 60 Hz one. The
hitbox is an ellipse noticeably smaller than the sprite: players perceive a graze
as a miss, so a generous hitbox reads as "fair" and a tight one reads as "broken".
"""
from __future__ import annotations

import math

import pygame

from .. import assets, config
from ..display import Viewport


class Bird:
    def __init__(self, skin: str = "ember", x: float = 0.0, y: float = config.BIRD_START_Y) -> None:
        self.skin = skin
        # `base_x` is the anchor the bird returns to; `x` is where it actually is
        # this frame, which the dash pushes forward. Keeping them separate means
        # a resize can move the anchor without cancelling a dash in flight.
        self.base_x = x
        self.dash_offset = 0.0
        self.dash_time = -1.0       # <0 when not dashing
        self.dash_cooldown = 0.0
        self.y = y
        self.prev_y = y
        self.vy = 0.0
        self.angle = 0.0
        self.prev_angle = 0.0
        self.alive = True
        self.launched = False          # False until the first tap, while the bird hovers
        self.anim_time = 0.0
        self.time_falling = 0.0
        self.flap_cooldown = 0.0
        self.idle_time = 0.0
        self.dead_spin = 0.0

    # -- position -----------------------------------------------------------
    @property
    def x(self) -> float:
        """Where the bird is right now, anchor plus whatever the dash has added."""
        return self.base_x + self.dash_offset

    @x.setter
    def x(self, value: float) -> None:
        # Assigning an absolute position moves the anchor; callers that do this
        # (a resize, a menu re-layout) mean "put the bird here", not "dash".
        self.base_x = value

    # -- state ------------------------------------------------------------
    def reset(self, x: float, y: float = config.BIRD_START_Y) -> None:
        self.base_x = x
        self.dash_offset = 0.0
        self.dash_time = -1.0
        self.dash_cooldown = 0.0
        self.y = self.prev_y = y
        self.vy = 0.0
        self.angle = self.prev_angle = 0.0
        self.alive = True
        self.launched = False
        self.anim_time = 0.0
        self.time_falling = 0.0
        self.flap_cooldown = 0.0
        self.idle_time = 0.0
        self.dead_spin = 0.0

    def flap(self) -> bool:
        """Apply the upward impulse. Returns False if the tap was swallowed."""
        if not self.alive or self.flap_cooldown > 0.0:
            return False
        self.launched = True
        self.vy = config.FLAP_VELOCITY
        self.flap_cooldown = config.FLAP_COOLDOWN
        self.time_falling = 0.0
        self.anim_time = 0.0
        return True

    # -- dash ---------------------------------------------------------------
    @property
    def dashing(self) -> bool:
        return self.dash_time >= 0.0

    @property
    def dash_ready(self) -> bool:
        return self.alive and self.launched and self.dash_cooldown <= 0.0 and not self.dashing

    @property
    def dash_charge(self) -> float:
        """0..1 meter for the HUD: 1.0 means the dash is available."""
        if self.dashing:
            return 0.0
        if config.DASH_COOLDOWN <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - self.dash_cooldown / config.DASH_COOLDOWN))

    def dash(self) -> bool:
        """Begin a dash. Returns False when it is on cooldown or unavailable."""
        if not self.dash_ready:
            return False
        self.dash_time = 0.0
        self.dash_cooldown = config.DASH_COOLDOWN
        return True

    def _update_dash(self, dt: float) -> None:
        self.dash_cooldown = max(0.0, self.dash_cooldown - dt)
        if not self.dashing:
            # Ease any residual offset away, in case the dash was cut short by a
            # death; otherwise the bird would stay displaced for the fall.
            if self.dash_offset:
                self.dash_offset = max(0.0, self.dash_offset - config.DASH_DISTANCE * dt * 2.0)
            return

        self.dash_time += dt
        out, hold = config.DASH_OUT, config.DASH_HOLD
        recover = config.DASH_RECOVER
        t = self.dash_time

        if t < out:
            # Accelerate out hard, so the burst reads as a burst.
            k = t / out
            self.dash_offset = config.DASH_DISTANCE * (1.0 - (1.0 - k) ** 3)
        elif t < out + hold:
            self.dash_offset = config.DASH_DISTANCE
        elif t < out + hold + recover:
            k = (t - out - hold) / recover
            # Ease back slowly: a snap-back would look like a rubber band.
            self.dash_offset = config.DASH_DISTANCE * (1.0 - k * k * (3.0 - 2.0 * k))
        else:
            self.dash_offset = 0.0
            self.dash_time = -1.0

    def kill(self) -> None:
        self.alive = False
        # A small upward kick on death makes the fall read as an impact rather
        # than the physics simply being switched off.
        self.vy = min(self.vy, -260.0)

    # -- simulation -------------------------------------------------------
    def step(self, dt: float) -> None:
        self.prev_y = self.y
        self.prev_angle = self.angle
        self.flap_cooldown = max(0.0, self.flap_cooldown - dt)
        self._update_dash(dt)

        if not self.launched:
            # Hover on the start screen. The bob is a pure function of elapsed
            # time so it cannot drift with frame timing.
            self.idle_time += dt
            self.y = config.BIRD_START_Y + math.sin(
                self.idle_time * config.IDLE_BOB_HZ * math.tau
            ) * config.IDLE_BOB_AMPLITUDE
            self.anim_time += dt
            self.angle = math.sin(self.idle_time * config.IDLE_BOB_HZ * math.tau + 1.2) * 6.0
            return

        if self.dashing:
            # Nearly weightless mid-dash, and the vertical velocity bleeds off,
            # so the bird holds a line instead of arcing out of the gap.
            self.vy += config.GRAVITY * config.DASH_GRAVITY_SCALE * dt
            self.vy *= math.exp(-config.DASH_VY_DAMP * dt)
        else:
            self.vy = min(self.vy + config.GRAVITY * dt, config.TERMINAL_VELOCITY)
        self.y += self.vy * dt

        if self.vy > 0:
            self.time_falling += dt
        else:
            self.time_falling = 0.0

        if self.alive:
            self._update_angle(dt)
            self.anim_time += dt
        else:
            # Tumble after death.
            self.dead_spin += dt
            self.angle = min(config.ROT_DOWN + 26.0, self.angle + 300.0 * dt)

    def _update_angle(self, dt: float) -> None:
        """Chase a velocity-implied angle with frame-rate-independent smoothing.

        The nose only starts to drop after ROT_DIVE_AFTER seconds of falling, so
        a quick tap-tap keeps the bird level instead of sawing up and down.
        """
        if self.dashing:
            # Level and slightly nose-forward: the silhouette sells the speed.
            target = -8.0
        elif self.vy < 0:
            target = config.ROT_UP
        elif self.time_falling < config.ROT_DIVE_AFTER:
            target = config.ROT_UP * (1.0 - self.time_falling / config.ROT_DIVE_AFTER)
        else:
            fall = min(1.0, self.vy / config.TERMINAL_VELOCITY)
            target = config.ROT_DOWN * fall * fall
        self.angle += (target - self.angle) * (1.0 - math.exp(-config.ROT_RESPONSE * dt))

    # -- collision --------------------------------------------------------
    def hit_ellipse(self) -> tuple[float, float, float, float]:
        """(cx, cy, rx, ry) of the collision ellipse, in world units."""
        return (self.x, self.y, config.BIRD_HIT_RX, config.BIRD_HIT_RY)

    def intersects_rect(self, rx: float, ry: float, rw: float, rh: float) -> bool:
        """Ellipse/axis-aligned-rect overlap.

        Works by squashing the space so the ellipse becomes a unit circle, then
        measuring to the closest point on the transformed rectangle -- exact, and
        cheaper than a polygon test.
        """
        cx, cy, erx, ery = self.hit_ellipse()
        nearest_x = min(max(cx, rx), rx + rw)
        nearest_y = min(max(cy, ry), ry + rh)
        dx = (cx - nearest_x) / erx
        dy = (cy - nearest_y) / ery
        return dx * dx + dy * dy <= 1.0

    def intersects_circle(self, cx: float, cy: float, radius: float) -> bool:
        bx, by, erx, ery = self.hit_ellipse()
        # Approximate the bird as a circle of its mean radius for pickups, where
        # being slightly generous is a feature.
        br = (erx + ery) * 0.5
        dx, dy = bx - cx, by - cy
        reach = br + radius
        return dx * dx + dy * dy <= reach * reach

    # -- presentation -----------------------------------------------------
    def render_y(self, alpha: float) -> float:
        return self.prev_y + (self.y - self.prev_y) * alpha

    def render_angle(self, alpha: float) -> float:
        return self.prev_angle + (self.angle - self.prev_angle) * alpha

    def frame_index(self) -> int:
        """Which wing frame to show.

        While diving the wings are held back rather than cycling, which reads as
        a committed dive; the rest of the time they beat at a constant rate.
        """
        if self.launched and self.alive and self.time_falling > config.ROT_DIVE_AFTER * 1.6:
            return 3
        return int(self.anim_time * config.FLAP_ANIM_FPS) % config.BIRD_FRAMES

    def sprite_name(self) -> str:
        return f"bird_{self.skin}_{self.frame_index()}.png"

    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float = 1.0,
             offset: tuple[float, float] = (0.0, 0.0)) -> None:
        y = self.render_y(alpha)
        angle = self.render_angle(alpha)
        w = vp.px(config.BIRD_W)
        h = vp.px(config.BIRD_H)
        sprite = assets.scaled(self.sprite_name(), w, h)
        # pygame rotates counter-clockwise, the game's angles are clockwise-positive.
        rotated = pygame.transform.rotate(sprite, -angle) if abs(angle) > 0.5 else sprite
        sx, sy = vp.to_screen(self.x + offset[0], y + offset[1])
        rect = rotated.get_rect(center=(sx, sy))
        surface.blit(rotated, rect)
