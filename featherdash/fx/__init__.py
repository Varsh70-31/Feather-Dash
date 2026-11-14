"""The juice layer: easing, tweens, springs, particles, camera and transitions.

Everything a scene needs to make an event *feel* like something is re-exported
here, so callers write::

    from featherdash import fx

    self.sparks = fx.ParticleSystem(capacity=400)
    self.camera = fx.Camera()
    self.pop = fx.Spring(1.0, stiffness=260.0)
    y = fx.approach(y, target_y, 9.0, dt)

Nothing in this package reads input, owns game state or knows what a bird is.
It is driven entirely by delta time and told what happened, which is what keeps
it reusable across scenes and testable without a window.
"""
from __future__ import annotations

from .camera import Camera
from .easing import (
    EASINGS,
    EaseFn,
    approach,
    clamp,
    clamp01,
    in_back,
    in_cubic,
    in_out_cubic,
    in_out_quad,
    in_out_sine,
    in_quad,
    inv_lerp,
    lerp,
    lerp_color,
    linear,
    out_back,
    out_bounce,
    out_cubic,
    out_elastic,
    out_expo,
    out_quad,
    remap,
)
from .particles import PRESETS, SPRITES, ParticleSystem
from .particles import invalidate as invalidate_particles
from .transition import IN, OUT, TRANSITIONS, Fade, Iris, SwipeWipe, Transition
from .transition import make as make_transition
from .tween import Spring, Timeline, Tween

__all__ = [
    # easing
    "EaseFn", "EASINGS",
    "linear", "in_quad", "out_quad", "in_out_quad",
    "in_cubic", "out_cubic", "in_out_cubic",
    "out_back", "in_back", "out_elastic", "out_bounce",
    "in_out_sine", "out_expo",
    "clamp", "clamp01", "lerp", "inv_lerp", "remap", "approach", "lerp_color",
    # animation
    "Tween", "Timeline", "Spring",
    # particles
    "ParticleSystem", "PRESETS", "SPRITES", "invalidate_particles",
    # camera
    "Camera",
    # transitions
    "Transition", "Fade", "Iris", "SwipeWipe", "TRANSITIONS", "make_transition",
    "IN", "OUT",
]
