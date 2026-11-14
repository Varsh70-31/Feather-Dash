"""The collectible feather token.

Pickup radius is generous on purpose: a coin the player clearly flew through but
did not collect feels like a bug, and the coin sits inside a gap the player is
already threading, so there is no difficulty cost to being forgiving here.
"""
from __future__ import annotations

import math

import pygame

from .. import assets, config
from ..display import Viewport


class Coin:
    __slots__ = ("x", "prev_x", "y", "base_y", "collected", "anim", "pop", "_bob_phase")

    def __init__(self, x: float, y: float, phase: float = 0.0) -> None:
        self.x = x
        self.prev_x = x
        self.base_y = y
        self.y = y
        self.collected = False
        self.anim = phase
        self.pop = 0.0
        self._bob_phase = phase

    def step(self, dt: float, speed: float) -> None:
        self.prev_x = self.x
        self.x -= speed * dt
        self.anim += dt
        self._bob_phase += dt
        self.y = self.base_y + math.sin(self._bob_phase * 2.1) * 11.0
        if self.collected and self.pop > 0.0:
            self.pop = max(0.0, self.pop - dt * 3.0)

    def collect(self) -> None:
        self.collected = True
        self.pop = 1.0

    @property
    def radius(self) -> float:
        return config.COIN_SIZE * 0.62

    def render_x(self, alpha: float) -> float:
        return self.prev_x + (self.x - self.prev_x) * alpha

    def frame_index(self) -> int:
        return int(self.anim * 13.0) % config.COIN_FRAMES

    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float = 1.0,
             offset: tuple[float, float] = (0.0, 0.0)) -> None:
        if self.collected and self.pop <= 0.0:
            return
        x = self.render_x(alpha) + offset[0]
        if x > vp.view_right + 80 or x < vp.view_left - 80:
            return

        size = config.COIN_SIZE
        img = assets.scaled(f"coin_{self.frame_index()}.png", vp.px(size), vp.px(size))
        if self.collected:
            # Flare out and up as it is absorbed.
            grow = 1.0 + (1.0 - self.pop) * 0.9
            img = pygame.transform.smoothscale(
                img, (max(1, int(img.get_width() * grow)), max(1, int(img.get_height() * grow)))
            )
            img = img.copy()
            img.set_alpha(int(255 * self.pop))
            lift = (1.0 - self.pop) * 56.0
        else:
            lift = 0.0

        sx, sy = vp.to_screen(x, self.y + offset[1] - lift)
        surface.blit(img, img.get_rect(center=(sx, sy)))
