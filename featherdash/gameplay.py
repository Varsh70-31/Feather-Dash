"""The simulation for a single run.

Deliberately free of presentation concerns beyond its own `draw`: it owns the
bird, the pillars, the coins, the score and the difficulty curve, and it reports
what happened through `Events` so the scene above can decide what to play, shake
or spawn. Effects are injected and optional, which keeps the simulation testable
headlessly and independently of the FX layer.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum

import pygame

from . import config
from .display import Viewport
from .entities import Bird, Coin, PillarPair
from .world import World


class RunState(Enum):
    READY = "ready"      # waiting for the first tap; the bird hovers
    PLAYING = "playing"
    DYING = "dying"      # hit something, still falling
    DEAD = "dead"        # landed; the results screen may take over


@dataclass
class Events:
    """What happened during the steps taken since this was last drained."""

    flapped: bool = False
    dashed: bool = False
    dash_blocked: bool = False
    scored: int = 0
    coins: int = 0
    streak_bonus: int = 0
    near_miss: bool = False
    died: bool = False
    landed: bool = False
    theme_changed: str | None = None
    score_positions: list[tuple[float, float]] = field(default_factory=list)
    coin_positions: list[tuple[float, float]] = field(default_factory=list)
    death_position: tuple[float, float] | None = None

    def clear(self) -> None:
        self.flapped = False
        self.dashed = False
        self.dash_blocked = False
        self.scored = 0
        self.coins = 0
        self.streak_bonus = 0
        self.near_miss = False
        self.died = False
        self.landed = False
        self.theme_changed = None
        self.score_positions.clear()
        self.coin_positions.clear()
        self.death_position = None


def clamp_speed_scale(value: float) -> float:
    """Keep a speed multiplier inside the supported range.

    The profile is a plain JSON file, so this is the only thing standing between
    a hand-edited 1e9 and a pillar that teleports past the bird in one step.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return config.SPEED_SCALE_DEFAULT
    if v != v:  # NaN
        return config.SPEED_SCALE_DEFAULT
    return max(config.SPEED_SCALE_MIN, min(config.SPEED_SCALE_MAX, v))


def _ease(t: float) -> float:
    """Smoothstep, so difficulty ramps in gently rather than linearly."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class Run:
    """One playthrough, from the first tap to the ground."""

    def __init__(self, vp: Viewport, world: World, skin: str = "ember",
                 seed: int | None = None, speed_scale: float | None = None) -> None:
        self.vp = vp
        self.world = world
        self.rng = random.Random(seed)
        self.speed_scale = clamp_speed_scale(
            config.SPEED_SCALE_DEFAULT if speed_scale is None else speed_scale)
        self.bird = Bird(skin, vp.bird_x)
        self.pillars: list[PillarPair] = []
        self.coins: list[Coin] = []
        self.events = Events()

        self.state = RunState.READY
        self.score = 0
        self.feathers = 0
        self.coin_streak = 0
        self.elapsed = 0.0
        self.distance = 0.0
        self.dying_time = 0.0
        self._last_gap_center = vp.world_h * 0.5
        self._spawn_x = 0.0
        self._reset_spawn()

    # -- lifecycle ---------------------------------------------------------
    def reset(self, vp: Viewport | None = None, skin: str | None = None,
              speed_scale: float | None = None) -> None:
        if vp is not None:
            self.vp = vp
        if speed_scale is not None:
            self.speed_scale = clamp_speed_scale(speed_scale)
        self.bird.reset(self.vp.bird_x)
        if skin:
            self.bird.skin = skin
        self.pillars.clear()
        self.coins.clear()
        self.events.clear()
        self.state = RunState.READY
        self.score = 0
        self.feathers = 0
        self.coin_streak = 0
        self.elapsed = 0.0
        self.distance = 0.0
        self.dying_time = 0.0
        self._last_gap_center = self.vp.world_h * 0.5
        self._reset_spawn()

    def on_viewport_changed(self, vp: Viewport) -> None:
        """Keep the run coherent when the window is resized mid-play.

        The bird sits at a fraction of the world width, so a resize moves it --
        and moving it alone would slide it through whatever is in front of it.
        Resizing from a phone shape to an ultrawide one pushed the bird 144 units
        forward, straight into the next pillar, and killed a run that was in no
        danger. So the world moves with it: every obstacle shifts by the same
        delta, which leaves the geometry the player is reacting to untouched.
        """
        self.vp = vp
        delta = vp.bird_x - self.bird.base_x
        self.bird.base_x = vp.bird_x
        if delta:
            for pillar in self.pillars:
                pillar.x += delta
                pillar.prev_x += delta
            for coin in self.coins:
                coin.x += delta
                coin.prev_x += delta
            self._spawn_x += delta

    def _reset_spawn(self) -> None:
        """Place the first pillar a fixed number of *seconds* away.

        Distance alone would make the opening unfair at high speeds: the same
        gap that gives nearly two seconds of runway at the default pace gives
        half a second at 4x, and the run is over before the player has reacted.
        """
        runway = max(config.PILLAR_W * 1.2, self.speed * config.INTRO_SECONDS)
        # Measured from the play area's edge, not the visible one: obstacles are
        # clipped to the play area, so spawning out in the overscan would just
        # make the first pillar arrive later on a wide screen.
        self._spawn_x = self.vp.world_w + runway

    # -- difficulty --------------------------------------------------------
    @property
    def difficulty(self) -> float:
        return _ease(self.score / config.RAMP_SCORE)

    @property
    def speed(self) -> float:
        """Scroll speed in world units/s.

        Anchored to version 1's pace (config.LEGACY_SPEED) so the player's
        multiplier means something concrete, with the difficulty ramp layered on
        top -- a run always accelerates, whatever pace it starts at.
        """
        ramp = 1.0 + (config.SPEED_RAMP_FACTOR - 1.0) * self.difficulty
        return config.LEGACY_SPEED * self.speed_scale * ramp

    @property
    def gap_size(self) -> float:
        d = self.difficulty
        return config.GAP_START + (config.GAP_END - config.GAP_START) * d

    @property
    def spacing(self) -> float:
        d = self.difficulty
        return config.PILLAR_SPACING_START + (
            config.PILLAR_SPACING_END - config.PILLAR_SPACING_START
        ) * d

    # -- input -------------------------------------------------------------
    def flap(self) -> bool:
        if self.state == RunState.READY:
            self.state = RunState.PLAYING
        elif self.state != RunState.PLAYING:
            return False
        if self.bird.flap():
            self.events.flapped = True
            return True
        return False

    def dash(self) -> bool:
        """Trigger the forward burst. Reports a blocked attempt so the UI can
        answer the input rather than appearing to ignore it."""
        if self.state != RunState.PLAYING or not self.bird.launched:
            return False
        if self.bird.dash():
            self.events.dashed = True
            return True
        self.events.dash_blocked = True
        return False

    # -- simulation --------------------------------------------------------
    def step(self, dt: float) -> None:
        if self.state in (RunState.READY, RunState.PLAYING):
            self.world.step(dt, self.speed if self.state == RunState.PLAYING else self.speed * 0.45)
        elif self.state == RunState.DYING:
            # The world keeps drifting a little so the death does not feel like
            # the game simply stopped.
            self.world.step(dt, self.speed * 0.2)

        self.bird.step(dt)

        if self.state == RunState.READY:
            return

        if self.state == RunState.PLAYING:
            self.elapsed += dt
            self.distance += self.speed * dt
            self._advance_obstacles(dt)
            self._spawn_if_needed()
            self._check_scoring()
            self._check_coins()
            self._check_collisions()
        elif self.state == RunState.DYING:
            self.dying_time += dt
            for pillar in self.pillars:
                pillar.step(dt, self.speed * 0.2)
            if self.bird.y >= self.vp.ground_y - config.BIRD_HIT_RY:
                self.bird.y = self.vp.ground_y - config.BIRD_HIT_RY
                self.state = RunState.DEAD
                self.events.landed = True

    def _advance_obstacles(self, dt: float) -> None:
        speed = self.speed
        for pillar in self.pillars:
            pillar.step(dt, speed)
        for coin in self.coins:
            coin.step(dt, speed)

        cutoff = self.vp.view_left - config.PILLAR_W * 2
        if self.pillars and self.pillars[0].right < cutoff:
            self.pillars.pop(0)
        self.coins = [c for c in self.coins
                      if c.x > cutoff and not (c.collected and c.pop <= 0.0)]

    def _spawn_if_needed(self) -> None:
        spacing = self.spacing
        right_edge = self.vp.world_w + config.PILLAR_W
        while not self.pillars or self.pillars[-1].x < right_edge:
            x = (self.pillars[-1].x + spacing) if self.pillars else self._spawn_x
            self._spawn_pillar(x)
            # A viewport far wider than the spacing could otherwise spawn an
            # unbounded number in one frame.
            if len(self.pillars) > 24:
                break

    def _spawn_pillar(self, x: float) -> None:
        gap = self.gap_size
        half = gap * 0.5
        lo = config.GAP_MARGIN_TOP + half
        hi = self.vp.ground_y - config.GAP_MARGIN_BOTTOM - half
        if hi < lo:
            lo = hi = (lo + hi) * 0.5
        # Clamp how far a gap may move from the last one, so the next obstacle is
        # always reachable from where the player has just been forced to fly.
        # The previous centre is first pulled into the band that is legal for the
        # *current* gap size, which shrinks as difficulty ramps: without that, a
        # centre that was legal for a wide gap could demand a jump larger than
        # GAP_MAX_DELTA to get back inside a narrow one.
        anchor = min(max(self._last_gap_center, lo), hi)
        lo = max(lo, anchor - config.GAP_MAX_DELTA)
        hi = min(hi, anchor + config.GAP_MAX_DELTA)
        center = self.rng.uniform(lo, hi) if hi > lo else lo
        self._last_gap_center = center

        theme = self.world.next_theme or self.world.theme
        self.pillars.append(PillarPair(x, center, gap, theme))

        if self.rng.random() < config.COIN_CHANCE:
            self.coins.append(Coin(x + config.PILLAR_W * 0.5, center,
                                   phase=self.rng.random() * 6.28))

    def _check_scoring(self) -> None:
        for pillar in self.pillars:
            if pillar.scored or pillar.right >= self.bird.x:
                continue
            pillar.mark_scored()
            self.score += 1
            self.events.scored += 1
            self.events.score_positions.append((pillar.right, pillar.gap_center))

            theme = self.world.theme_for_score(self.score)
            if theme != (self.world.next_theme or self.world.theme):
                self.world.set_theme(theme)
                self.events.theme_changed = theme

    def _check_coins(self) -> None:
        for coin in self.coins:
            if coin.collected:
                continue
            if self.bird.intersects_circle(coin.x, coin.y, coin.radius):
                coin.collect()
                self.feathers += config.COIN_VALUE
                self.coin_streak += 1
                self.events.coins += 1
                self.events.coin_positions.append((coin.x, coin.y))
                if self.coin_streak % 5 == 0:
                    self.feathers += config.COIN_STREAK_BONUS
                    self.events.streak_bonus += config.COIN_STREAK_BONUS

    def _check_collisions(self) -> None:
        if not self.bird.alive:
            return

        if self.bird.y + config.BIRD_HIT_RY >= self.vp.ground_y:
            self._die(ground=True)
            return
        if self.bird.y < config.CEILING_KILL:
            self._die()
            return

        for pillar in self.pillars:
            if pillar.right < self.bird.x - config.BIRD_HIT_RX * 2:
                continue
            if pillar.x > self.bird.x + config.BIRD_HIT_RX * 2:
                break
            for rect in pillar.collision_rects(self.vp):
                if self.bird.intersects_rect(*rect):
                    self._die()
                    return
            # A pass this close to the cap is worth celebrating.
            edge = min(abs(self.bird.y - pillar.gap_top), abs(self.bird.y - pillar.gap_bottom))
            if edge < config.BIRD_HIT_RY + 16 and abs(pillar.x + config.PILLAR_W * 0.5 - self.bird.x) < 40:
                self.events.near_miss = True

    def _die(self, ground: bool = False) -> None:
        self.bird.kill()
        self.coin_streak = 0
        self.state = RunState.DYING
        self.dying_time = 0.0
        self.events.died = True
        self.events.death_position = (self.bird.x, self.bird.y)
        if ground:
            # Hitting the ground skips the fall; it is already over.
            self.bird.y = self.vp.ground_y - config.BIRD_HIT_RY
            self.state = RunState.DEAD
            self.events.landed = True

    # -- presentation ------------------------------------------------------
    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float = 1.0,
             offset: tuple[float, float] = (0.0, 0.0)) -> None:
        for pillar in self.pillars:
            pillar.draw(surface, vp, alpha, offset)
        for coin in self.coins:
            coin.draw(surface, vp, alpha, offset)
        self.bird.draw(surface, vp, alpha, offset)

    def drain(self) -> Events:
        """Hand the accumulated events to the caller and start collecting again."""
        snapshot = Events(
            flapped=self.events.flapped,
            dashed=self.events.dashed,
            dash_blocked=self.events.dash_blocked,
            scored=self.events.scored,
            coins=self.events.coins,
            streak_bonus=self.events.streak_bonus,
            near_miss=self.events.near_miss,
            died=self.events.died,
            landed=self.events.landed,
            theme_changed=self.events.theme_changed,
            score_positions=list(self.events.score_positions),
            coin_positions=list(self.events.coin_positions),
            death_position=self.events.death_position,
        )
        self.events.clear()
        return snapshot
