"""Time-based animation primitives: Tween, Timeline and Spring.

Three jobs, deliberately kept separate:

* `Tween`   -- a known value over a known duration with a known curve. Use it
               when the destination and the timing are both decided up front
               (a panel sliding in, a fade, a counter rolling up).
* `Timeline`-- sequencing and parallelism over tweens and callbacks, so a
               multi-beat moment (medal stamp, then score count, then buttons)
               is described in one place instead of smeared across a pile of
               timer floats in a scene.
* `Spring`  -- a target that can change at any moment, reached with momentum.
               Use it for anything reacting to input: a pressed button, a score
               number punching on a point. A tween restarted every time its
               target moves snaps and stutters; a spring absorbs the change.

Everything here is driven by delta time in seconds and is stable at any frame
rate; the spring in particular is integrated analytically rather than by Euler
steps, so a 30 Hz phone and a 144 Hz monitor land on the same curve instead of
the low frame rate exploding or crawling.
"""
from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Union

from .easing import EaseFn, clamp01, lerp, out_quad

__all__ = ["Tween", "Timeline", "Spring"]


class Tween:
    """Interpolate `start` -> `end` over `duration` seconds through `ease`."""

    __slots__ = (
        "start", "end", "duration", "ease", "delay", "on_complete",
        "loop", "yoyo", "elapsed", "_value", "_done", "_fired",
    )

    def __init__(
        self,
        start: float,
        end: float,
        duration: float,
        ease: EaseFn = out_quad,
        delay: float = 0.0,
        on_complete: Optional[Callable[[], None]] = None,
        loop: bool = False,
        yoyo: bool = False,
    ) -> None:
        self.start = float(start)
        self.end = float(end)
        self.duration = max(0.0, float(duration))
        self.ease = ease
        self.delay = max(0.0, float(delay))
        self.on_complete = on_complete
        self.loop = bool(loop)
        # yoyo plays the curve back down again, so an idle pulse needs one
        # tween instead of a two-step timeline that has to be rebuilt forever.
        self.yoyo = bool(yoyo)
        self.elapsed = 0.0
        self._value = self.start
        self._done = False
        self._fired = False

    # -- state -------------------------------------------------------------
    @property
    def value(self) -> float:
        return self._value

    @property
    def done(self) -> bool:
        return self._done

    @property
    def progress(self) -> float:
        """Raw (un-eased) 0..1 position through the active part of the tween."""
        if self.duration <= 0.0:
            return 1.0 if self.elapsed >= self.delay else 0.0
        return clamp01((self.elapsed - self.delay) / self.duration)

    @property
    def total_duration(self) -> float:
        """Wall-clock length of one pass, including the delay and the way back
        down again when yoyo is set."""
        span = self.delay + self.duration
        return span * 2.0 if self.yoyo else span

    @property
    def overflow(self) -> float:
        """Seconds of the last step that landed past the end. Timeline uses
        this to hand leftover time to the next step instead of discarding it,
        which is what stops a long frame from stalling a chain of animations."""
        if not self._done:
            return 0.0
        return max(0.0, self.elapsed - self.total_duration)

    # -- control -----------------------------------------------------------
    def update(self, dt: float) -> float:
        if self._done:
            return self._value
        self.elapsed += max(0.0, dt)

        span = self.delay + self.duration
        total = self.total_duration
        if self.loop and total > 0.0:
            # Wrap rather than reset, so looping stays phase-accurate across
            # thousands of cycles instead of drifting a frame at a time.
            self.elapsed = math.fmod(self.elapsed, total)

        t = self.progress
        if self.yoyo and self.duration > 0.0 and self.elapsed > span:
            t = clamp01((span * 2.0 - self.elapsed) / self.duration)
        self._value = lerp(self.start, self.end, self.ease(t))

        if not self.loop and self.elapsed >= total:
            self._value = lerp(self.start, self.end, self.ease(0.0 if self.yoyo else 1.0))
            self._done = True
            self._fire()
        return self._value

    def restart(self, delay: Optional[float] = None) -> "Tween":
        if delay is not None:
            self.delay = max(0.0, float(delay))
        self.elapsed = 0.0
        self._value = self.start
        self._done = False
        self._fired = False
        return self

    def finish(self) -> float:
        """Jump to the end immediately, firing `on_complete` exactly once."""
        self.elapsed = self.total_duration
        self._value = lerp(self.start, self.end, self.ease(0.0 if self.yoyo else 1.0))
        self._done = True
        self._fire()
        return self._value

    def reverse(self) -> "Tween":
        """Swap the endpoints and restart -- a panel that slides out the way
        it came in, without a second tween definition."""
        self.start, self.end = self.end, self.start
        return self.restart()

    def _fire(self) -> None:
        if self._fired:
            return
        self._fired = True
        if self.on_complete is not None:
            self.on_complete()


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------
class _Wait:
    """A pure delay between steps."""

    __slots__ = ("seconds", "elapsed")

    def __init__(self, seconds: float) -> None:
        self.seconds = max(0.0, float(seconds))
        self.elapsed = 0.0

    @property
    def done(self) -> bool:
        return self.elapsed >= self.seconds

    @property
    def overflow(self) -> float:
        return max(0.0, self.elapsed - self.seconds)

    def update(self, dt: float) -> None:
        self.elapsed += max(0.0, dt)

    def restart(self) -> None:
        self.elapsed = 0.0


class _Call:
    """A callback fired once when the timeline reaches it."""

    __slots__ = ("fn", "_done", "_overflow")

    def __init__(self, fn: Callable[[], None]) -> None:
        self.fn = fn
        self._done = False
        self._overflow = 0.0

    @property
    def done(self) -> bool:
        return self._done

    @property
    def overflow(self) -> float:
        return self._overflow

    def update(self, dt: float) -> None:
        if self._done:
            return
        self._done = True
        self._overflow = max(0.0, dt)
        self.fn()

    def restart(self) -> None:
        self._done = False
        self._overflow = 0.0


class _Step:
    """One beat of a timeline. Entries inside a step run in parallel."""

    __slots__ = ("entries", "_overflow")

    def __init__(self, entries: Sequence[object]) -> None:
        self.entries = list(entries)
        self._overflow = 0.0

    @property
    def done(self) -> bool:
        return all(e.done for e in self.entries)  # type: ignore[attr-defined]

    @property
    def overflow(self) -> float:
        return self._overflow

    def update(self, dt: float) -> None:
        if not self.entries:
            self._overflow = max(0.0, dt)
            return
        for e in self.entries:
            if not e.done:  # type: ignore[attr-defined]
                e.update(dt)  # type: ignore[attr-defined]
        if self.done:
            # The step ends when its *last* entry ends, so the leftover time is
            # the smallest of the entries' leftovers.
            self._overflow = min(e.overflow for e in self.entries)  # type: ignore[attr-defined]

    def restart(self) -> None:
        self._overflow = 0.0
        for e in self.entries:
            e.restart()  # type: ignore[attr-defined]


Entry = Union[Tween, "Timeline", Callable[[], None], float, int]


class Timeline:
    """A sequence of steps; each step may hold several entries running together.

    Built by chaining, so a cutscene reads top to bottom in the order it plays::

        (Timeline()
            .then(Tween(0, 1, 0.3, ease=out_back))
            .wait(0.1)
            .then(tween_a, tween_b)          # these two run in parallel
            .call(spawn_confetti))
    """

    def __init__(self, loop: bool = False) -> None:
        self._steps: List[_Step] = []
        self._index = 0
        self._done = False
        self._overflow = 0.0
        self.loop = bool(loop)

    # -- construction ------------------------------------------------------
    def then(self, *items: Entry) -> "Timeline":
        """Append a step. Multiple items in one call run in parallel."""
        self._steps.append(_Step([self._wrap(i) for i in items]))
        self._done = False
        return self

    # `parallel` is `then` under a name that documents the intent at the call
    # site; a step with several entries is already parallel.
    parallel = then

    def wait(self, seconds: float) -> "Timeline":
        return self.then(float(seconds))

    def call(self, fn: Callable[[], None]) -> "Timeline":
        return self.then(fn)

    @staticmethod
    def _wrap(item: Entry) -> object:
        if isinstance(item, (int, float)):
            return _Wait(float(item))
        if callable(item) and not hasattr(item, "update"):
            return _Call(item)
        return item

    # -- state -------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self._done

    @property
    def overflow(self) -> float:
        return self._overflow if self._done else 0.0

    @property
    def step_index(self) -> int:
        return self._index

    def __len__(self) -> int:
        return len(self._steps)

    # -- control -----------------------------------------------------------
    def update(self, dt: float) -> None:
        if self._done or not self._steps:
            if not self._steps:
                self._done = True
                self._overflow = max(0.0, dt)
            return

        remaining = max(0.0, dt)
        # Bounded: each pass either advances a step or breaks. The bound also
        # stops a looping timeline made only of instant callbacks from spinning
        # forever inside one frame.
        for _ in range(len(self._steps) + 2):
            if self._index >= len(self._steps):
                if self.loop:
                    self.restart()
                    continue
                self._done = True
                self._overflow = remaining
                return
            step = self._steps[self._index]
            step.update(remaining)
            if not step.done:
                return
            remaining = step.overflow
            self._index += 1

    def restart(self) -> "Timeline":
        self._index = 0
        self._done = False
        self._overflow = 0.0
        for step in self._steps:
            step.restart()
        return self

    def skip(self) -> None:
        """Run everything to completion now -- the 'player tapped through the
        animation' path, which must still fire every callback so state that
        the timeline was responsible for setting is not left half-applied."""
        guard = 0
        while not self._done and guard < len(self._steps) * 4 + 8:
            self.update(3600.0)
            guard += 1
        self._done = True

    def clear(self) -> "Timeline":
        self._steps.clear()
        return self.restart()


# --------------------------------------------------------------------------
# spring
# --------------------------------------------------------------------------
class Spring:
    """A damped spring toward `target`, critically damped unless told otherwise.

    Solved analytically per step instead of integrated. Euler integration of a
    stiff spring blows up when a frame is long (an Android app resuming, a GC
    pause) -- precisely the moment a UI must not detonate -- and its effective
    stiffness changes with frame rate. The closed form is unconditionally
    stable and gives the identical curve at any step size.
    """

    __slots__ = (
        "stiffness", "damping", "target", "_value", "_velocity",
        "rest_epsilon", "_omega", "_zeta",
    )

    def __init__(
        self,
        value: float = 0.0,
        stiffness: float = 180.0,
        damping: Optional[float] = None,
        target: Optional[float] = None,
        rest_epsilon: float = 1e-4,
    ) -> None:
        self.stiffness = max(1e-6, float(stiffness))
        # Critical damping is the default because UI that overshoots on every
        # interaction is exhausting; callers opt into bounce explicitly.
        self.damping = float(damping) if damping is not None else 2.0 * math.sqrt(self.stiffness)
        self._value = float(value)
        self.target = float(target) if target is not None else float(value)
        self._velocity = 0.0
        self.rest_epsilon = float(rest_epsilon)
        self._recompute()

    def _recompute(self) -> None:
        self._omega = math.sqrt(self.stiffness)
        self._zeta = self.damping / (2.0 * self._omega)

    # -- state -------------------------------------------------------------
    @property
    def value(self) -> float:
        return self._value

    @property
    def velocity(self) -> float:
        return self._velocity

    @property
    def at_rest(self) -> bool:
        return self._velocity == 0.0 and self._value == self.target

    # A settled spring is "done" in the same sense a finished tween is, so a
    # caller can poll a heterogeneous bag of animations the same way.
    done = at_rest

    # -- control -----------------------------------------------------------
    def kick(self, impulse: float) -> "Spring":
        """Add velocity without moving the value -- the press/punch gesture."""
        self._velocity += float(impulse)
        return self

    def nudge(self, delta: float) -> "Spring":
        """Displace the value instantly, keeping momentum -- the shove gesture.

        Displacement rather than velocity because callers think in "move it 20
        units and let it come back", and the peak of a velocity impulse depends
        on stiffness in a way that makes every call site a tuning exercise.
        """
        self._value += float(delta)
        return self

    def snap(self, value: Optional[float] = None) -> "Spring":
        """Teleport to a value (default: the target) and kill all motion."""
        if value is not None:
            self._value = float(value)
        else:
            self._value = self.target
        self._velocity = 0.0
        return self

    def configure(self, stiffness: Optional[float] = None, damping: Optional[float] = None) -> "Spring":
        if stiffness is not None:
            self.stiffness = max(1e-6, float(stiffness))
            if damping is None:
                self.damping = 2.0 * math.sqrt(self.stiffness)
        if damping is not None:
            self.damping = float(damping)
        self._recompute()
        return self

    def update(self, dt: float) -> float:
        if dt <= 0.0:
            return self._value
        if self.at_rest:
            return self._value

        x = self._value - self.target          # displacement from equilibrium
        v = self._velocity
        w = self._omega
        z = self._zeta

        if abs(z - 1.0) < 1e-3:                # critically damped
            e = math.exp(-w * dt)
            c = v + w * x
            nx = (x + c * dt) * e
            nv = (v - c * w * dt) * e
        elif z < 1.0:                          # underdamped: it rings
            wd = w * math.sqrt(1.0 - z * z)
            e = math.exp(-z * w * dt)
            cs = math.cos(wd * dt)
            sn = math.sin(wd * dt)
            a = (v + z * w * x) / wd
            nx = e * (x * cs + a * sn)
            nv = -z * w * nx + e * wd * (a * cs - x * sn)
        else:                                  # overdamped: it crawls home
            rt = w * math.sqrt(z * z - 1.0)
            r1 = -z * w + rt
            r2 = -z * w - rt
            c2 = (r1 * x - v) / (r1 - r2)
            c1 = x - c2
            e1 = math.exp(r1 * dt)
            e2 = math.exp(r2 * dt)
            nx = c1 * e1 + c2 * e2
            nv = c1 * r1 * e1 + c2 * r2 * e2

        # Settle exactly. Leaving a spring to decay asymptotically means a
        # permanent sub-pixel offset and a value that is never equal to its
        # target, which quietly breaks any `if spring.done` the caller writes.
        if abs(nx) < self.rest_epsilon and abs(nv) < self.rest_epsilon * 10.0:
            self._value = self.target
            self._velocity = 0.0
        else:
            self._value = self.target + nx
            self._velocity = nv
        return self._value
