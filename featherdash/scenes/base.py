"""Scene interface.

Scenes separate `step` (fixed timestep, for anything that affects gameplay) from
`update` (variable timestep, for animation that must not change the simulation).
Keeping those apart is what lets the game run its physics identically at any
refresh rate while still animating smoothly on a 120 Hz panel.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from ..display import Viewport
from ..input import Frame

if TYPE_CHECKING:  # avoids a circular import at runtime
    from ..app import App


class Scene:
    name = "scene"

    def __init__(self, app: "App") -> None:
        self.app = app

    # -- lifecycle ---------------------------------------------------------
    def enter(self, **kwargs: Any) -> None:
        """Called each time this scene becomes current."""

    def leave(self) -> None:
        """Called when another scene takes over."""

    def on_resize(self, vp: Viewport) -> None:
        """The window changed shape; re-derive any cached layout."""

    def on_focus_lost(self) -> None:
        """The app was backgrounded or lost focus."""

    # -- frame -------------------------------------------------------------
    def handle(self, frame: Frame) -> None:
        """React to this frame's input."""

    def step(self, dt: float) -> None:
        """Fixed-timestep simulation. `dt` is always config.PHYSICS_DT."""

    def update(self, dt: float) -> None:
        """Variable-timestep animation. Never change gameplay state here."""

    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        """Render. `alpha` interpolates between the last two simulation steps."""

    # -- convenience -------------------------------------------------------
    @property
    def vp(self) -> Viewport:
        return self.app.display.viewport
