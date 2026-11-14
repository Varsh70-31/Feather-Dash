"""Easing curves and interpolation helpers.

Every curve is a pure function of normalised time `t` in 0..1 returning a
normalised value. Input is clamped, output is not: `out_back` and `out_elastic`
deliberately leave the 0..1 range in the middle, which is the whole point of
them -- a value that overshoots and settles reads as weight, and weight is what
makes a UI feel physical rather than animated.

`approach()` is the most important function in this file. Anything that chases
a target over time must use it, because the obvious alternative
(`current += (target - current) * 0.1`) silently makes the game feel different
at 30, 60 and 120 fps: it applies the same fraction per *frame* instead of per
*second*. On a 120 Hz tablet the camera would snap twice as fast as on a 60 Hz
phone. The exponential form below is exact under any step size and any number
of steps, so one tuning value holds on every device.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Sequence, Tuple

EaseFn = Callable[[float], float]

__all__ = [
    "EaseFn", "EASINGS",
    "linear", "in_quad", "out_quad", "in_out_quad",
    "in_cubic", "out_cubic", "in_out_cubic",
    "out_back", "in_back", "out_elastic", "out_bounce",
    "in_out_sine", "out_expo",
    "clamp", "clamp01", "lerp", "inv_lerp", "remap", "approach", "lerp_color",
]


# --------------------------------------------------------------------------
# scalar helpers
# --------------------------------------------------------------------------
def clamp(v: float, lo: float, hi: float) -> float:
    """Constrain `v` to [lo, hi]."""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def lerp(a: float, b: float, t: float) -> float:
    """Interpolate a->b. `t` is intentionally unclamped so eased values that
    overshoot (out_back) still overshoot the endpoints they drive."""
    return a + (b - a) * t


def inv_lerp(a: float, b: float, v: float) -> float:
    """Where `v` sits between a and b, as 0..1. A zero-width range yields 0."""
    span = b - a
    if span == 0.0:
        return 0.0
    return (v - a) / span


def remap(
    v: float,
    in_min: float,
    in_max: float,
    out_min: float,
    out_max: float,
    clamped: bool = True,
    ease: "EaseFn | None" = None,
) -> float:
    """Map `v` from one range to another, optionally through an easing curve.

    Clamped by default: a remap is nearly always driving something physical
    (an alpha, a scale, a y offset) where running off the end of the range is a
    bug rather than an effect.
    """
    t = inv_lerp(in_min, in_max, v)
    if clamped:
        t = clamp01(t)
    if ease is not None:
        t = ease(t)
    return out_min + (out_max - out_min) * t


def approach(current: float, target: float, rate: float, dt: float) -> float:
    """Frame-rate-independent exponential smoothing toward `target`.

    `rate` is the reciprocal of the time constant: the gap shrinks by 63% in
    `1/rate` seconds regardless of how that second is cut into frames. Stepping
    this once with dt=1 or sixty times with dt=1/60 lands on the same value.
    """
    if dt <= 0.0 or rate <= 0.0:
        return current
    return current + (target - current) * (1.0 - math.exp(-rate * dt))


def lerp_color(
    a: Sequence[int],
    b: Sequence[int],
    t: float,
) -> Tuple[int, int, int]:
    """Blend two RGB triples. Rounds rather than truncates so a 0->255 ramp
    actually reaches 255 instead of stopping one short."""
    t = clamp01(t)
    return (
        int(a[0] + (b[0] - a[0]) * t + 0.5),
        int(a[1] + (b[1] - a[1]) * t + 0.5),
        int(a[2] + (b[2] - a[2]) * t + 0.5),
    )


# --------------------------------------------------------------------------
# curves
# --------------------------------------------------------------------------
def linear(t: float) -> float:
    return clamp01(t)


def in_quad(t: float) -> float:
    t = clamp01(t)
    return t * t


def out_quad(t: float) -> float:
    t = clamp01(t)
    u = 1.0 - t
    return 1.0 - u * u


def in_out_quad(t: float) -> float:
    t = clamp01(t)
    if t < 0.5:
        return 2.0 * t * t
    u = -2.0 * t + 2.0
    return 1.0 - (u * u) * 0.5


def in_cubic(t: float) -> float:
    t = clamp01(t)
    return t * t * t


def out_cubic(t: float) -> float:
    t = clamp01(t)
    u = 1.0 - t
    return 1.0 - u * u * u


def in_out_cubic(t: float) -> float:
    t = clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    u = -2.0 * t + 2.0
    return 1.0 - (u * u * u) * 0.5


# Penner's canonical back/elastic constants. 1.70158 is the overshoot that
# gives roughly 10% past the target, which is the amount that reads as "snap"
# without reading as "broken".
_BACK_C1 = 1.70158
_BACK_C3 = _BACK_C1 + 1.0
_ELASTIC_C4 = (2.0 * math.pi) / 3.0


def out_back(t: float) -> float:
    t = clamp01(t)
    u = t - 1.0
    return 1.0 + _BACK_C3 * u * u * u + _BACK_C1 * u * u


def in_back(t: float) -> float:
    t = clamp01(t)
    return _BACK_C3 * t * t * t - _BACK_C1 * t * t


def out_elastic(t: float) -> float:
    t = clamp01(t)
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return math.pow(2.0, -10.0 * t) * math.sin((t * 10.0 - 0.75) * _ELASTIC_C4) + 1.0


def out_bounce(t: float) -> float:
    t = clamp01(t)
    n1 = 7.5625
    d1 = 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


def in_out_sine(t: float) -> float:
    t = clamp01(t)
    return -(math.cos(math.pi * t) - 1.0) * 0.5


def out_expo(t: float) -> float:
    t = clamp01(t)
    if t >= 1.0:
        return 1.0
    return 1.0 - math.pow(2.0, -10.0 * t)


# Lookup by name, so a tween can be configured from data (a preset table, a
# tuning file) without the caller importing every curve.
EASINGS: Dict[str, EaseFn] = {
    "linear": linear,
    "in_quad": in_quad,
    "out_quad": out_quad,
    "in_out_quad": in_out_quad,
    "in_cubic": in_cubic,
    "out_cubic": out_cubic,
    "in_out_cubic": in_out_cubic,
    "out_back": out_back,
    "in_back": in_back,
    "out_elastic": out_elastic,
    "out_bounce": out_bounce,
    "in_out_sine": in_out_sine,
    "out_expo": out_expo,
}
