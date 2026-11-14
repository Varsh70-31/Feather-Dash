#!/usr/bin/env python3
"""Headless self-test for `featherdash.fx`.

Runs standalone (`python tests/test_fx.py`) and under pytest. Everything here
is deterministic: the particle system and the camera both take a seed, so a
failure is reproducible rather than "it flickered once on CI".

Besides the assertions it renders two preview sheets (every particle preset,
and the three transitions mid-wipe) to PNG, because the only way to know a
visual effect looks right is to look at it.
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
import traceback
from typing import Callable, List, Tuple

# Must be set before pygame initialises its backends or this blocks on a
# display that is not there.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pygame  # noqa: E402

from featherdash import assets, config, display, fx  # noqa: E402
from featherdash.fx import particles as fx_particles  # noqa: E402
from featherdash.fx import transition as fx_transition  # noqa: E402

TOL = 1e-9
OUT_DIR = os.environ.get("FEATHERDASH_FX_OUT", tempfile.gettempdir())
_RENDERED: List[str] = []


def _ensure_display() -> None:
    """A video mode is needed before `convert_alpha()` or any blit.

    Done at import rather than in a main(), because CI runs this file through
    pytest, which never calls main().
    """
    if not pygame.get_init():
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((64, 64))


_ensure_display()


# ==========================================================================
# easing
# ==========================================================================
def test_easing_endpoints() -> None:
    """Every curve starts at 0 and ends at 1; overshoot in between is fine."""
    assert len(fx.EASINGS) == 13, "expected the full standard set"
    for name, fn in fx.EASINGS.items():
        assert abs(fn(0.0)) < 1e-6, "%s(0) = %r" % (name, fn(0.0))
        assert abs(fn(1.0) - 1.0) < 1e-6, "%s(1) = %r" % (name, fn(1.0))
        # Out-of-range input is clamped, not extrapolated: a tween that
        # overruns its duration must not fly off to infinity.
        assert fn(-3.0) == fn(0.0), name
        assert fn(7.0) == fn(1.0), name
        for i in range(101):
            v = fn(i / 100.0)
            assert math.isfinite(v), "%s produced %r" % (name, v)


def test_easing_overshoot_is_allowed() -> None:
    """The springy curves must actually overshoot, or they are pointless."""
    assert max(fx.out_back(t / 100.0) for t in range(101)) > 1.02
    assert min(fx.in_back(t / 100.0) for t in range(101)) < -0.02
    assert max(fx.out_elastic(t / 100.0) for t in range(101)) > 1.02
    # ...and the plain ones must not.
    for name in ("linear", "in_quad", "out_quad", "in_out_quad", "out_expo",
                 "in_cubic", "out_cubic", "in_out_cubic", "in_out_sine", "out_bounce"):
        fn = fx.EASINGS[name]
        vals = [fn(t / 100.0) for t in range(101)]
        assert min(vals) >= -1e-9 and max(vals) <= 1.0 + 1e-9, name


def test_scalar_helpers() -> None:
    assert fx.clamp(5.0, 0.0, 1.0) == 1.0
    assert fx.clamp(-5.0, 0.0, 1.0) == 0.0
    assert fx.clamp01(0.4) == 0.4
    assert fx.lerp(10.0, 20.0, 0.25) == 12.5
    # lerp stays unclamped so eased overshoot survives it.
    assert fx.lerp(0.0, 10.0, 1.2) == 12.0
    assert fx.inv_lerp(10.0, 20.0, 12.5) == 0.25
    assert fx.inv_lerp(5.0, 5.0, 9.0) == 0.0, "zero-width range must not divide by zero"
    assert fx.remap(5.0, 0.0, 10.0, 100.0, 200.0) == 150.0
    assert fx.remap(50.0, 0.0, 10.0, 100.0, 200.0) == 200.0, "clamped by default"
    assert fx.remap(50.0, 0.0, 10.0, 100.0, 200.0, clamped=False) == 600.0
    assert fx.remap(0.5, 0.0, 1.0, 0.0, 1.0, ease=fx.in_quad) == 0.25
    assert fx.lerp_color((0, 0, 0), (255, 255, 255), 1.0) == (255, 255, 255)
    assert fx.lerp_color((0, 0, 0), (255, 255, 255), 0.5) == (128, 128, 128)


def test_approach_is_frame_rate_independent() -> None:
    """THE key test: one simulated second, three frame rates, one answer."""
    def run(hz: int) -> float:
        v = 0.0
        dt = 1.0 / hz
        for _ in range(hz):
            v = fx.approach(v, 100.0, 4.0, dt)
        return v

    a, b, c = run(30), run(60), run(240)
    spread = max(a, b, c) - min(a, b, c)
    assert spread < 1e-9, "approach() drifted with frame rate: %r" % ((a, b, c),)

    expected = 100.0 * (1.0 - math.exp(-4.0))
    assert abs(a - expected) < 1e-9, "%r != %r" % (a, expected)

    # The teeth of the test: the naive per-frame form everyone writes first is
    # wildly frame-rate dependent, which is why it is banned from this project.
    def naive(hz: int) -> float:
        v = 0.0
        for _ in range(hz):
            v += (100.0 - v) * 0.1
        return v

    n30, n240 = naive(30), naive(240)
    assert n240 - n30 > 4.0, "control case failed; the test proves nothing"

    assert fx.approach(5.0, 10.0, 0.0, 0.016) == 5.0, "rate 0 must not move"
    assert fx.approach(5.0, 10.0, 8.0, 0.0) == 5.0, "dt 0 must not move"
    assert fx.approach(5.0, 10.0, 8.0, 1e9) == 10.0, "huge dt must not blow up"


# ==========================================================================
# tween / timeline / spring
# ==========================================================================
def _step(obj, seconds: float, hz: float = 60.0) -> int:
    dt = 1.0 / hz
    frames = int(round(seconds * hz))
    for _ in range(frames):
        obj.update(dt)
    return frames


def test_tween_completes() -> None:
    fired = []
    t = fx.Tween(0.0, 100.0, 0.5, ease=fx.out_cubic, on_complete=lambda: fired.append(1))
    assert not t.done and t.value == 0.0

    _step(t, 0.25)
    assert 0.0 < t.value < 100.0, t.value
    assert not t.done

    _step(t, 0.40)
    assert t.done and t.value == 100.0
    assert fired == [1], "on_complete must fire exactly once"
    assert t.overflow >= 0.0

    t.restart()
    assert not t.done and t.value == 0.0
    t.finish()
    assert t.done and t.value == 100.0 and fired == [1, 1]

    t.reverse()
    assert t.start == 100.0 and t.end == 0.0 and not t.done


def test_tween_delay_and_loop() -> None:
    t = fx.Tween(0.0, 1.0, 0.2, delay=0.3)
    _step(t, 0.25)
    assert t.value == 0.0, "a delayed tween must not move yet"
    _step(t, 0.30)
    assert t.done and t.value == 1.0

    loop = fx.Tween(0.0, 1.0, 0.25, ease=fx.in_out_sine, loop=True, yoyo=True)
    seen_low = seen_high = False
    for _ in range(600):
        loop.update(1.0 / 60.0)
        seen_low |= loop.value < 0.1
        seen_high |= loop.value > 0.9
    assert not loop.done, "a looping tween never finishes"
    assert seen_low and seen_high, "yoyo must travel in both directions"


def test_timeline_sequences_and_carries_leftover_time() -> None:
    log: List[str] = []
    a = fx.Tween(0.0, 1.0, 0.20, on_complete=lambda: log.append("a"))
    b = fx.Tween(0.0, 1.0, 0.30, on_complete=lambda: log.append("b"))
    c = fx.Tween(0.0, 1.0, 0.10, on_complete=lambda: log.append("c"))

    tl = (fx.Timeline()
          .then(a)
          .wait(0.10)
          .call(lambda: log.append("mark"))
          .parallel(b, c))
    assert len(tl) == 4

    # Stepped at a deliberately coarse 30 Hz: step boundaries land mid-frame,
    # so this only completes on time if leftover dt is carried forward.
    elapsed = 0.0
    while not tl.done and elapsed < 5.0:
        tl.update(1.0 / 30.0)
        elapsed += 1.0 / 30.0
    assert tl.done, "timeline never finished"
    assert log == ["a", "mark", "c", "b"], log
    assert abs(elapsed - 0.60) < 0.05, "expected ~0.6s of timeline, got %.3f" % elapsed

    tl.restart()
    assert not tl.done and tl.step_index == 0
    log.clear()
    tl.skip()
    assert tl.done and set(log) == {"a", "mark", "b", "c"}, log

    empty = fx.Timeline()
    empty.update(0.016)
    assert empty.done, "an empty timeline is trivially done"

    # A looping timeline of instant callbacks must not spin forever in one
    # frame; the iteration bound is what stops a hang.
    spin = fx.Timeline(loop=True).call(lambda: log.append("x"))
    spin.update(10.0)
    assert len(log) < 50, "looping timeline ran away: %d" % len(log)

    tl.clear()
    assert len(tl) == 0


def test_spring_settles_exactly_and_is_stable() -> None:
    s = fx.Spring(0.0, stiffness=200.0, target=1.0)
    _step(s, 3.0)
    assert s.done and s.at_rest
    assert s.value == 1.0, "a spring must land exactly on its target, got %r" % s.value
    assert s.velocity == 0.0

    # Critically damped: no overshoot at all.
    s2 = fx.Spring(0.0, stiffness=200.0, target=1.0)
    peak = 0.0
    for _ in range(240):
        peak = max(peak, s2.update(1.0 / 60.0))
    assert peak <= 1.0 + 1e-9, "critical damping must not overshoot (%r)" % peak

    # Underdamped: it must ring, and still settle in the end.
    s3 = fx.Spring(0.0, stiffness=200.0, damping=4.0, target=1.0)
    peak = 0.0
    for _ in range(240):
        peak = max(peak, s3.update(1.0 / 60.0))
    assert peak > 1.05, "underdamped spring should overshoot (%r)" % peak
    _step(s3, 8.0)
    assert s3.done and s3.value == 1.0, "a ringing spring must still settle exactly"

    # Overdamped: slower, never past the target.
    s4 = fx.Spring(0.0, stiffness=200.0, damping=120.0, target=1.0)
    peak = max(s4.update(1.0 / 60.0) for _ in range(600))
    assert peak <= 1.0 + 1e-9

    # Stable at a terrible frame rate, and identical to a good one over the
    # same half-second of simulated time.
    def run(hz: int) -> float:
        sp = fx.Spring(0.0, stiffness=180.0, target=10.0)
        for _ in range(hz // 2):
            sp.update(1.0 / hz)
        return sp.value
    lo, hi = run(30), run(240)
    assert abs(lo - hi) < 1e-9, "spring drifted with frame rate: %r vs %r" % (lo, hi)

    k = fx.Spring(0.0, stiffness=150.0)
    k.kick(40.0)
    assert k.velocity == 40.0 and not k.at_rest
    _step(k, 4.0)
    assert k.value == 0.0 and k.at_rest

    n = fx.Spring(0.0, stiffness=150.0)
    n.nudge(25.0)
    assert n.value == 25.0, "nudge displaces immediately"
    _step(n, 4.0)
    assert n.value == 0.0

    n.snap(3.0)
    assert n.value == 3.0 and n.velocity == 0.0
    n.configure(stiffness=400.0)
    assert abs(n.damping - 2.0 * math.sqrt(400.0)) < TOL


# ==========================================================================
# particles
# ==========================================================================
def _viewport(w: int = 720, h: int = 1280) -> "display.Viewport":
    return display.compute(w, h)


def test_particles_respect_capacity() -> None:
    ps = fx.ParticleSystem(capacity=64, seed=7)
    assert ps.capacity == 64 and ps.active == 0

    ps.emit(100.0, 100.0, 500, life=0.5, size=20.0)
    assert ps.active == 64, "cap breached: %d" % ps.active
    # The pool itself must not have grown -- that is the whole point of it.
    assert len(ps._pool) == 64, "pool grew to %d" % len(ps._pool)

    ps.update(2.0)
    assert ps.active == 0, "particles outlived their life"


def test_particles_recycle_oldest() -> None:
    ps = fx.ParticleSystem(capacity=32, seed=11)
    ps.emit(0.0, 0.0, 32, life=1.0)
    ps.update(0.5)
    ages_before = sorted(round(p.age, 3) for p in ps._pool[:ps.active])
    assert ages_before[0] > 0.0

    ps.emit(0.0, 0.0, 32, life=1.0)
    assert ps.active == 32, "capacity must hold under a second full burst"
    fresh = sum(1 for p in ps._pool[:ps.active] if p.age == 0.0)
    assert fresh == 32, "oldest particles should have been recycled, %d fresh" % fresh


def test_particle_presets_emit_and_expire() -> None:
    ps = fx.ParticleSystem(capacity=600, seed=3)
    for name in fx.PRESETS:
        ps.clear()
        n = ps.emit_preset(name, 300.0, 500.0)
        assert n > 0 and ps.active == n, "%s emitted %d" % (name, n)
        for _ in range(900):            # 15s at 60Hz: longer than any preset
            ps.update(1.0 / 60.0)
            if ps.active == 0:
                break
        assert ps.active == 0, "%s leaked %d particles" % (name, ps.active)

    assert ps.emit_preset("coin", 0.0, 0.0, count_scale=0.0) == 0
    before = ps.active
    ps.emit_preset("coin", 0.0, 0.0, count=2)
    assert ps.active - before == 4, "count override applies per layer"

    try:
        ps.emit_preset("does_not_exist", 0.0, 0.0)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown preset must raise")

    try:
        ps.emit(0.0, 0.0, 1, colour=(1, 2, 3))
    except ValueError:
        pass
    else:
        raise AssertionError("a misspelled spec key must raise, not be ignored")


def test_particles_deterministic_and_cullable() -> None:
    a = fx.ParticleSystem(capacity=32, seed=99)
    b = fx.ParticleSystem(capacity=32, seed=99)
    a.emit_preset("hit", 10.0, 20.0)
    b.emit_preset("hit", 10.0, 20.0)
    a.update(0.2)
    b.update(0.2)
    assert [(p.x, p.y) for p in a._pool[:a.active]] == [(p.x, p.y) for p in b._pool[:b.active]]

    vp = _viewport()
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    on = fx.ParticleSystem(capacity=64, seed=1)
    on.emit(vp.cx, vp.cy, 20, size=40.0, life=2.0)
    assert on.draw(surf, vp) == 20

    off = fx.ParticleSystem(capacity=64, seed=1)
    off.emit(-5000.0, -5000.0, 20, size=40.0, life=2.0)
    assert off.draw(surf, vp) == 0, "off-screen particles must be culled"
    assert off.active == 20, "culling is a draw concern, not a simulation one"

    off.clear()
    assert off.active == 0

    # dt <= 0 must be a no-op rather than an exception or a NaN.
    on.update(0.0)
    on.update(-1.0)
    assert on.active == 20


def test_particles_allocate_nothing_in_the_hot_path() -> None:
    """The pool only pays for itself if steady-state emission allocates nothing.

    A non-pooled system would create ~9000 objects over this run; a GC sweep of
    that is a visible hitch on a phone. Counting tracked objects measures the
    claim directly instead of trusting a stopwatch on an idle machine.
    """
    import gc
    import time

    vp = _viewport()
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    ps = fx.ParticleSystem(capacity=400, seed=21)

    for _ in range(90):                      # warm the surface caches first
        ps.emit_preset("hit", vp.cx, vp.cy)
        ps.update(1.0 / 60.0)
        ps.draw(surf, vp)

    gc.collect()
    before = len(gc.get_objects())
    start = time.perf_counter()
    for _ in range(300):
        ps.emit_preset("hit", vp.cx, vp.cy)
        ps.update(1.0 / 60.0)
        ps.draw(surf, vp)
    elapsed = time.perf_counter() - start
    gc.collect()
    grown = len(gc.get_objects()) - before

    assert ps.active <= 400 and len(ps._pool) == 400
    assert grown < 150, "steady-state emission allocated %d tracked objects" % grown
    print("      (%d particles, %.2f ms/frame, %+d objects)"
          % (ps.active, elapsed / 300.0 * 1000.0, grown))


def test_invalidate_reaches_live_systems() -> None:
    vp = _viewport()
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    ps = fx.ParticleSystem(capacity=32, seed=31)
    ps.emit(vp.cx, vp.cy, 8, sprite="fx_star", size=50.0, life=4.0)
    ps.draw(surf, vp)
    assert ps._variants, "drawing should have cached variants"

    fx.invalidate_particles()
    ps.draw(surf, vp)
    assert ps._cache_generation == fx_particles._generation, \
        "a live system kept surfaces from before the invalidate"


def test_invalidate_reaches_systems_that_are_not_drawing() -> None:
    """A resize has to reclaim the surfaces of every system, not just the
    busy one.

    The generation check in `draw()` only retires surfaces on a system that
    is drawing. A scene the app has left -- the menu's ambient layer while the
    game is up -- would otherwise sit on megabytes of surfaces at a scale that
    no longer exists until the player happened to navigate back to it.
    """
    import gc
    import weakref

    vp = _viewport(1080, 2400)
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    onscreen = fx.ParticleSystem(capacity=64, seed=51)
    offscreen = fx.ParticleSystem(capacity=64, seed=52)
    for ps in (onscreen, offscreen):
        for i in range(8):
            ps.emit(vp.cx, vp.cy, 4, sprite=fx.SPRITES[i % len(fx.SPRITES)],
                    size=40.0 + i * 9.0, life=60.0, rot=i * 17.0,
                    color=(250 - i * 8, 90 + i * 7, 30 + i * 11))
            ps.update(1.0 / 60.0)
            ps.draw(surf, vp)
    held = len(onscreen._variants)
    assert held and offscreen._variants, "drawing should have cached variants"

    fx.invalidate_particles()
    # Note: nothing is drawn in between. That is the whole point.
    assert not onscreen._variants, "invalidate left %d variants on a live system" \
        % len(onscreen._variants)
    assert not offscreen._variants, \
        "invalidate left %d variants on a system that is not drawing" % len(offscreen._variants)
    assert not fx.ParticleSystem._shared_scaled

    # The registry has to be weak, or the effects library becomes the reason
    # a torn-down scene is never collected.
    doomed = fx.ParticleSystem(capacity=8, seed=53)
    doomed.emit(vp.cx, vp.cy, 4, sprite="fx_soft", size=50.0, life=5.0)
    doomed.draw(surf, vp)
    ref = weakref.ref(doomed)
    del doomed
    gc.collect()
    assert ref() is None, "a dead ParticleSystem is being kept alive by the registry"
    fx.invalidate_particles()        # must not trip over the collected system
    print("      (%d variants dropped on two systems, one of them idle)" % held)


def test_procedural_fallback_when_art_is_missing() -> None:
    """The game must look right before the art build has run.

    Once the real PNGs exist they shadow this path entirely, so it is tested
    directly -- an untested fallback is a fallback that has already rotted.
    """
    for sprite in fx.SPRITES:
        tex = fx_particles._procedural(sprite)
        assert tex.get_width() >= 64 and tex.get_flags() & pygame.SRCALPHA
        peak = 0
        step = max(1, tex.get_width() // 48)
        for y in range(0, tex.get_height(), step):
            for x in range(0, tex.get_width(), step):
                peak = max(peak, tex.get_at((x, y)).a)
        assert peak > 200, "%s fallback is blank or too faint (peak alpha %d)" % (sprite, peak)

    # An unknown sprite name must still return something drawable.
    assert fx_particles._texture("fx_not_a_real_sprite").get_width() > 0

    vp = _viewport()
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    ps = fx.ParticleSystem(capacity=16, seed=2)
    ps.emit(vp.cx, vp.cy, 4, sprite="fx_not_a_real_sprite", size=60.0, life=1.0)
    ps.update(0.1)
    assert ps.draw(surf, vp) == 4, "a missing sprite must not stop the effect drawing"
    fx.invalidate_particles()


def test_particles_draw_every_sprite_and_blend() -> None:
    vp = _viewport()
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    for sprite in fx.SPRITES:
        for additive in (False, True):
            surf.fill((0, 0, 0))
            ps = fx.ParticleSystem(capacity=8, seed=5)
            ps.emit(vp.cx, vp.cy, 4, sprite=sprite, additive=additive,
                    size=90.0, life=2.0, rot=37.0, spin=90.0,
                    color=(255, 210, 120), color_end=(80, 120, 255))
            ps.update(0.3)
            assert ps.draw(surf, vp) == 4
            # Average the whole footprint rather than probing points: fx_ring
            # is an annulus and a point probe can legitimately miss it.
            box = vp.rect(vp.cx - 70.0, vp.cy - 70.0, 140.0, 140.0)
            avg = pygame.transform.average_color(surf, box)
            assert (avg[0] + avg[1] + avg[2]) > 0, \
                "%s (additive=%s) drew nothing" % (sprite, additive)
    fx.invalidate_particles()


# ==========================================================================
# camera
# ==========================================================================
def test_camera_shake_decays_to_exactly_zero() -> None:
    cam = fx.Camera(seed=4)
    assert cam.offset == (0.0, 0.0)

    cam.shake(config.SHAKE_ON_HIT)
    cam.update(1.0 / 60.0)
    assert cam.shaking and cam.offset != (0.0, 0.0)
    for _ in range(600):
        cam.update(1.0 / 60.0)
    assert cam.offset == (0.0, 0.0), "shake left a permanent offset: %r" % (cam.offset,)
    assert cam.rotation == 0.0 and not cam.shaking

    # A fixed-duration shake must still be running before its duration and be
    # completely over, at zero, once it has elapsed.
    cam.shake(20.0, duration=0.25)
    for _ in range(12):                      # 0.20s
        cam.update(1.0 / 60.0)
    assert cam.shaking, "a 0.25s shake ended early"
    for _ in range(6):                       # 0.30s total
        cam.update(1.0 / 60.0)
    assert not cam.shaking and cam.offset == (0.0, 0.0)

    # Amplitude is a maximum, not a sum: a small shake cannot cut a big one
    # short, and a hundred of them cannot launch the camera into orbit.
    cam.shake(30.0)
    cam.shake(2.0)
    cam.update(1.0 / 60.0)
    assert abs(cam.offset[0]) <= 30.0 and abs(cam.offset[1]) <= 30.0


def test_camera_shake_is_smooth_not_white_noise() -> None:
    """Pure per-frame randomness reads as a glitch; this proves we are not it."""
    cam = fx.Camera(seed=2, decay=0.0001)
    cam.shake(100.0)
    prev = None
    smooth_max = 0.0
    for _ in range(300):
        cam.update(1.0 / 60.0)
        x, y = cam.offset
        if prev is not None:
            smooth_max = max(smooth_max, math.hypot(x - prev[0], y - prev[1]))
        prev = (x, y)

    rng = random.Random(2)
    white_max = 0.0
    prev = None
    for _ in range(300):
        p = (rng.uniform(-100.0, 100.0), rng.uniform(-100.0, 100.0))
        if prev is not None:
            white_max = max(white_max, math.hypot(p[0] - prev[0], p[1] - prev[1]))
        prev = p
    assert smooth_max < white_max * 0.8, \
        "shake is too jittery to be smooth noise (%.1f vs white %.1f)" % (smooth_max, white_max)


def test_camera_kick_punch_freeze_and_flash() -> None:
    cam = fx.Camera(seed=6)
    cam.kick(0.0, 30.0)
    assert abs(cam.offset[1] - 30.0) < TOL
    for _ in range(300):
        cam.update(1.0 / 60.0)
    assert cam.offset == (0.0, 0.0)

    cam.punch(0.05)
    assert abs(cam.zoom - 1.05) < TOL
    lowest = 1.0
    for _ in range(300):
        cam.update(1.0 / 60.0)
        lowest = min(lowest, cam.zoom)
    assert lowest < 1.0, "the zoom punch should spring back past its rest point"
    assert cam.zoom == 1.0, "zoom must settle exactly (%r)" % cam.zoom

    cam.freeze(config.HIT_STOP_ON_DEATH)
    assert cam.frozen
    for _ in range(6):
        cam.update(1.0 / 60.0)
    assert cam.frozen, "hit-stop ended early"
    for _ in range(6):
        cam.update(1.0 / 60.0)
    assert not cam.frozen, "hit-stop never ended"

    surf = pygame.Surface((64, 64))
    surf.fill((10, 10, 10))
    cam.flash((255, 255, 255), config.FLASH_ON_DEATH)
    assert abs(cam.flash_intensity() - 1.0) < 1e-6 and cam.flashing
    cam.draw_overlays(surf, None)
    assert surf.get_at((32, 32)).r > 200, "additive flash did not brighten the screen"

    surf.fill((10, 10, 10))
    cam.flash((0, 0, 0), 0.2, additive=False)
    cam.draw_overlays(surf)
    assert surf.get_at((32, 32)).r < 10, "alpha flash did not darken the screen"

    for _ in range(30):
        cam.update(1.0 / 60.0)
    assert not cam.flashing and cam.flash_intensity() == 0.0
    surf.fill((10, 10, 10))
    cam.draw_overlays(surf)
    assert surf.get_at((32, 32)).r == 10, "a finished flash must draw nothing"


def test_camera_reset_and_accessibility_scale() -> None:
    cam = fx.Camera(seed=8)
    cam.shake(40.0)
    cam.kick(12.0, -9.0)
    cam.punch(0.08)
    cam.freeze(1.0)
    cam.flash()
    cam.update(1.0 / 60.0)
    cam.reset()
    assert cam.offset == (0.0, 0.0) and cam.zoom == 1.0 and cam.rotation == 0.0
    assert not cam.frozen and not cam.flashing and not cam.shaking

    cam.intensity = 0.0          # the "reduce motion" setting
    cam.shake(50.0)
    cam.kick(50.0, 50.0)
    cam.punch(0.2)
    cam.update(1.0 / 60.0)
    assert cam.offset == (0.0, 0.0) and cam.zoom == 1.0

    # The flash is the effect this setting exists for -- a full-screen white
    # strobe is the one thing in the game that can actually hurt someone --
    # so it gets asserted on the pixels, not just on a flag.
    surf = pygame.Surface((64, 64))
    for args in (((255, 255, 255), config.FLASH_ON_DEATH, True),
                 ((255, 255, 255), config.FLASH_ON_DEATH, False)):
        cam.flash(*args)
        cam.freeze(config.HIT_STOP_ON_DEATH)
        assert not cam.flashing, "reduce motion left the flash armed"
        assert cam.flash_intensity() == 0.0
        assert not cam.frozen, "reduce motion left hit-stop holding the game"
        surf.fill((10, 10, 10))
        cam.draw_overlays(surf, None)
        assert surf.get_at((32, 32))[:3] == (10, 10, 10), \
            "reduce motion still drew a flash (additive=%s)" % args[2]

    # Flipping the setting on *during* an effect must stop it there and then,
    # which is the moment a player reaches for it.
    live = fx.Camera(seed=18)
    live.flash((255, 255, 255), 1.0)
    live.freeze(1.0)
    live.shake(40.0)
    live.update(1.0 / 60.0)
    assert live.flashing and live.frozen and live.shaking
    live.intensity = 0.0
    assert not live.flashing and not live.frozen and not live.shaking
    assert live.offset == (0.0, 0.0) and live.rotation == 0.0
    surf.fill((10, 10, 10))
    live.draw_overlays(surf, None)
    assert surf.get_at((32, 32))[:3] == (10, 10, 10)

    # Between the two it scales rather than switches: half intensity is half
    # the peak brightness, not half the duration.
    half = fx.Camera(seed=28)
    half.intensity = 0.5
    half.flash((255, 255, 255), 0.2)
    surf.fill((10, 10, 10))
    half.draw_overlays(surf, None)
    lit = surf.get_at((32, 32)).r
    assert 120 < lit < 150, "intensity 0.5 should halve the flash, got %d" % lit


def test_camera_flash_is_a_blit_not_a_software_fill() -> None:
    """`Surface.fill(colour, special_flags=...)` is not SDL's blitter.

    It is a per-pixel software loop: 12 ms a frame at 1080x2400 and 22 ms at
    1440x3200, against a 16.6 ms budget, for the ~13 frames after every death.
    The identical result as a blit of a pre-filled surface costs ~0.5 ms.

    The cost assertion is a ratio against the slow path measured in the same
    process, so it means the same thing on a slow CI box as on a laptop.
    """
    import time

    w, h = 1080, 2400
    cam = fx.Camera(seed=41)
    cam.flash((255, 220, 90), config.FLASH_ON_DEATH)
    cam.update(0.03)
    i = cam.flash_intensity()
    assert i > 0.0

    # Identical output first: a faster flash that looks different is not a fix.
    tint = (int(255 * i), int(220 * i), int(90 * i))
    probe = pygame.Surface((256, 256))
    for y in range(0, 256, 4):
        probe.fill((y, 255 - y, (y * 3) % 256), (0, y, 256, 4))
    want = probe.copy()
    want.fill(tint, special_flags=pygame.BLEND_RGB_ADD)
    got = probe.copy()
    cam.draw_overlays(got, None)
    same = pygame.transform.threshold(None, got, None, (0, 0, 0),
                                      set_behavior=0, search_surf=want)
    assert same == 256 * 256, "the fast flash is not pixel-identical (%d of %d match)" \
        % (same, 256 * 256)

    big = pygame.Surface((w, h))
    big.fill((20, 30, 40))

    def _best(fn, reps: int = 5) -> float:
        fn()
        best = float("inf")
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            best = min(best, (time.perf_counter() - t0) * 1000.0)
        return best

    slow = _best(lambda: big.fill(tint, special_flags=pygame.BLEND_RGB_ADD))
    fast = _best(lambda: cam.draw_overlays(big, None))
    print("      (flash at %dx%d: %.2f ms as a blit vs %.2f ms as a blended fill)"
          % (w, h, fast, slow))
    assert fast * 3.0 < slow, \
        "the flash is back on the software fill path (%.2f ms vs %.2f ms)" % (fast, slow)


# ==========================================================================
# transitions
# ==========================================================================
def _run_transition(t: "fx.Transition", direction: str, surf: pygame.Surface,
                    vp: "display.Viewport") -> int:
    t.start(direction, origin=(vp.cx, vp.cy * 0.6))
    frames = 0
    while frames < 1200:
        done = t.update(1.0 / 60.0)
        t.draw(surf, vp)
        frames += 1
        if done:
            break
    assert t.done, "%s did not finish going %s" % (type(t).__name__, direction)
    return frames


def test_every_transition_completes_and_covers() -> None:
    vp = _viewport(540, 960)
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    probes = [(x, y)
              for x in (1, vp.screen_w // 3, vp.screen_w // 2, vp.screen_w - 2)
              for y in (1, vp.screen_h // 4, vp.screen_h // 2, vp.screen_h - 2)]

    for name, cls in fx.TRANSITIONS.items():
        t = cls()
        assert not t.covering and t.done, "%s should start idle" % name

        frames = _run_transition(t, fx.IN, surf, vp)
        assert frames > 2, "%s finished suspiciously fast" % name
        assert t.covering, "%s never fully covered the screen" % name

        # Prove it geometrically: with the transition closed, every probe must
        # be the cover colour. This is what makes it safe to swap scenes.
        surf.fill((255, 0, 255))
        t.draw(surf, vp)
        for p in probes:
            px = surf.get_at(p)
            assert (px.r, px.g, px.b) == t.color, \
                "%s leaked the scene at %r: %r" % (name, p, (px.r, px.g, px.b))

        _run_transition(t, fx.OUT, surf, vp)
        assert not t.covering and t.obscurity == 0.0, "%s never revealed again" % name
        surf.fill((255, 0, 255))
        t.draw(surf, vp)
        assert surf.get_at((vp.screen_w // 2, vp.screen_h // 2)).g == 0, \
            "%s is still drawing after it finished" % name


def _leaked(surf: pygame.Surface, color: Tuple[int, int, int]) -> int:
    """How many pixels of `surf` are not exactly `color`.

    `transform.threshold` scans the whole surface in C, which is what makes it
    affordable to check every pixel at phone resolutions rather than sampling
    a grid -- a grid is exactly how the original hole went unnoticed.
    """
    w, h = surf.get_size()
    return w * h - pygame.transform.threshold(None, surf, color, (0, 0, 0), set_behavior=0)


def _first_covering(t: "fx.Transition") -> float:
    """Bisect to the earliest moment `covering` reports True."""
    t._started = True
    t._direction = fx.IN
    lo, hi = 0.0, t.duration
    for _ in range(80):
        mid = (lo + hi) * 0.5
        t._elapsed = mid
        if t.covering:
            hi = mid
        else:
            lo = mid
    t._elapsed = hi
    assert t.covering
    return hi


def test_covering_means_every_single_pixel_is_covered() -> None:
    """`covering` is a promise that it is safe to tear the old scene down.

    So it is checked the only way that means anything: bisect to the exact
    instant it first returns True, draw over a scene that is pure magenta, and
    count every pixel that is not the cover colour. A 4x4 grid of probes
    passed happily while the iris had a feathered 45 px window open onto the
    scene at that moment, and the fade was a level off across the board.
    """
    for w, h in ((1080, 2400), (540, 960), (1536, 2048)):
        vp = display.compute(w, h)
        surf = pygame.Surface((w, h))
        for name, cls in fx.TRANSITIONS.items():
            # Including an origin off the top of the screen, where the iris
            # reaches furthest and its geometry is least symmetric.
            for origin in ((vp.cx, vp.cy * 0.6), (-900.0, -1400.0)):
                t = cls()
                t.start(fx.IN, origin=origin)
                _first_covering(t)
                surf.fill((255, 0, 255))
                t.draw(surf, vp)
                leaked = _leaked(surf, t.color)
                assert leaked == 0, "%s at %dx%d leaked %d pixels the moment it " \
                    "claimed to be covering" % (name, w, h, leaked)

    # The predicate must not depend on an easing landing exactly on 1.0:
    # in_back(1.0) is 0.9999999999999998, and `ease` is a constructor
    # argument, so any epsilon on `obscurity` is a trap waiting for whoever
    # picks a different curve.
    assert fx.in_back(1.0) != 1.0, "in_back no longer exercises the float edge"
    vp = display.compute(540, 960)
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    for cls in fx.TRANSITIONS.values():
        t = cls(ease=fx.in_back)
        t.start(fx.IN, origin=(vp.cx, vp.cy))
        done = False
        for _ in range(1200):
            done = t.update(1.0 / 60.0)
            if done:
                break
        assert done and t.covering, \
            "%s never reported covering with a non-exact easing" % cls.__name__
        surf.fill((255, 0, 255))
        t.draw(surf, vp)
        assert _leaked(surf, t.color) == 0


def test_transition_covering_is_never_claimed_early() -> None:
    """The canonical loop is `if trans.update(dt) and trans.covering`, but a
    caller is entitled to poll `covering` on its own, and that was the case
    that broke. Step frame by frame and check the promise on every frame."""
    vp = display.compute(540, 960)
    surf = pygame.Surface((vp.screen_w, vp.screen_h))
    for name, cls in fx.TRANSITIONS.items():
        for direction in (fx.IN, fx.OUT):
            t = cls()
            t.start(direction, origin=(vp.cx, vp.cy * 0.7))
            claims = 0
            for _ in range(1200):
                done = t.update(1.0 / 240.0)   # 4x oversampled, to land near the edge
                if t.covering:
                    claims += 1
                    surf.fill((255, 0, 255))
                    t.draw(surf, vp)
                    assert _leaked(surf, t.color) == 0, \
                        "%s going %s claimed cover at obscurity %.9f with a leak" \
                        % (name, direction, t.obscurity)
                if done:
                    break
            # Going in, the last frame must claim it; coming out, the screen
            # is already uncovering by the first update, so none should.
            if direction == fx.IN:
                assert claims > 0, "%s never reported covering going in" % name


# The three classes below redraw the same shapes the expensive way: one
# full-screen surface, filled and blitted whole, every frame. They exist so
# the partial-refill/partial-blit optimisation can be held to being *exactly*
# equal to the obvious implementation. They are deliberate copies: a change to
# the real geometry has to be mirrored here, and the test failing loudly is
# the point.
class _NaiveFade(fx.Fade):
    def _draw(self, surface, viewport, k):
        w, h = surface.get_size()
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((self.color[0], self.color[1], self.color[2], 255))
        overlay.set_alpha(int(round(255.0 * k)))
        surface.blit(overlay, (0, 0))


class _NaiveIris(fx.Iris):
    def _draw(self, surface, viewport, k):
        w, h = surface.get_size()
        r, g, b = self.color
        cx, cy = self._origin_px(surface, viewport)
        reach = max(math.hypot(cx, cy), math.hypot(w - cx, cy),
                    math.hypot(cx, h - cy), math.hypot(w - cx, h - cy))
        radius = reach * (1.0 - k)
        if radius < 1.0:
            surface.fill(self.color)
            return
        fw = min(max(2.0, h * self.feather), radius)
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((r, g, b, 255))
        steps = 4
        for i in range(steps, 0, -1):
            rr = int(radius + fw * (i / steps))
            a = int(255 * (i / (steps + 1.0)))
            pygame.draw.circle(overlay, (r, g, b, a), (cx, cy), rr, int(fw / steps) + 2)
        pygame.draw.circle(overlay, (0, 0, 0, 0), (cx, cy), int(radius))
        overlay.set_alpha(255)
        surface.blit(overlay, (0, 0))


class _NaiveSwipe(fx.SwipeWipe):
    def _draw(self, surface, viewport, k):
        w, h = surface.get_size()
        skew = h * self.skew
        colour = (self.color[0], self.color[1], self.color[2], 255)
        glow_w = max(2, int(h * 0.006))
        inward = self._direction == fx.IN
        front = -skew + (w + 2.0 * skew) * (k if inward else 1.0 - k)
        edge = ((front, -2.0), (front - skew, h + 2.0))
        lead = front + fx_transition._LEAD_BIAS
        if inward:
            pts = [(-skew - 2.0, -2.0), (lead, -2.0),
                   (lead - skew, h + 2.0), (-skew - 2.0, h + 2.0)]
        else:
            pts = [(lead, -2.0), (w + skew + 2.0, -2.0),
                   (w + skew + 2.0, h + 2.0), (lead - skew, h + 2.0)]
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.polygon(overlay, colour, pts)
        glow = int(190 * (1.0 - abs(k * 2.0 - 1.0)))
        if glow > 4:
            pygame.draw.line(overlay, (self.highlight[0], self.highlight[1],
                                       self.highlight[2], glow), edge[0], edge[1], glow_w)
        overlay.set_alpha(255)
        surface.blit(overlay, (0, 0))


def test_optimised_transition_draw_equals_the_naive_one() -> None:
    """Every frame of in-and-out, at four aspect ratios, pixel for pixel.

    The shipped draws reuse one scratch surface, restore only the region the
    last frame dirtied, clip the rasteriser to the band or box in play and
    blit only that -- which is where the 2-10x came from, and also where a
    stale-pixel bug would live. Reusing each instance across resolutions is
    part of the test because that is what a device rotation does.
    """
    # Fade is the one with a tolerance: it swaps a per-pixel-alpha source for
    # an opaque one, and SDL rounds that blend one level differently. Nothing
    # else is allowed to differ at all.
    pairs = (("fade", fx.Fade(), _NaiveFade(), 1),
             ("iris", fx.Iris(), _NaiveIris(), 0),
             ("swipe", fx.SwipeWipe(), _NaiveSwipe(), 0))
    frames = 0
    rounded = 0
    fade_px = 0
    # The last two origins are outside the view: the bird can die off the top
    # of the screen, and a rect clipped to nothing reports itself at (0, 0)
    # rather than where it was asked about.
    # One phone-sized case to catch anything resolution-dependent; the rest
    # are small, because what is being tested is geometry and bookkeeping and
    # a full-surface comparison at 1440p costs 20 ms a frame.
    for w, h, origin in ((1080, 2400, None), (540, 960, None), (361, 1007, None),
                         (800, 600, None), (540, 960, (-900.0, -1400.0)),
                         (800, 600, (3000.0, 2600.0))):
        vp = display.compute(w, h)
        fast = pygame.Surface((w, h))
        slow = pygame.Surface((w, h))
        here = origin if origin is not None else (vp.cx, vp.cy * 0.62)
        for name, a, b, tol in pairs:
            for direction in (fx.IN, fx.OUT):
                a.start(direction, origin=here)
                b.start(direction, origin=here)
                while True:
                    done = a.update(1.0 / 60.0)
                    b.update(1.0 / 60.0)
                    # A busy scene underneath, so a stale or missing pixel
                    # cannot hide against a flat colour.
                    for s in (fast, slow):
                        for y in range(0, h, 9):
                            s.fill((y % 256, (y * 5) % 256, (y * 11) % 256), (0, y, w, 9))
                    a.draw(fast, vp)
                    b.draw(slow, vp)
                    frames += 1
                    off = w * h - pygame.transform.threshold(
                        None, fast, None, (tol, tol, tol), set_behavior=0, search_surf=slow)
                    assert off == 0, \
                        "%s at %dx%d differs from the naive draw in %d pixels " \
                        "at obscurity %.4f going %s" % (name, w, h, off, a.obscurity, direction)
                    if tol:
                        fade_px += w * h
                        rounded += w * h - pygame.transform.threshold(
                            None, fast, None, (0, 0, 0), set_behavior=0, search_surf=slow)
                    if done:
                        break
    print("      (%d frames; iris and swipe bit-exact, %.0f%% of fade pixels "
          "one level off, none more)" % (frames, 100.0 * rounded / max(1, fade_px)))


def test_transition_edges() -> None:
    vp = _viewport(540, 960)
    surf = pygame.Surface((vp.screen_w, vp.screen_h))

    t = fx.make_transition("fade", duration=0.0)
    assert t.update(0.016) is True
    t.start(fx.IN)
    assert t.update(0.0) is True and t.covering, "a zero-length transition is instant"

    half = fx.Fade(duration=0.2)
    half.start(fx.IN)
    half.update(0.1)
    assert not half.covering and 0.0 < half.obscurity < 1.0
    half.draw(surf, vp)

    half.reset(covered=True)
    assert half.covering
    half.reset()
    assert not half.active and half.obscurity == 0.0

    try:
        half.start("sideways")
    except ValueError:
        pass
    else:
        raise AssertionError("an invalid direction must raise")

    try:
        fx.make_transition("spiral")
    except KeyError:
        pass
    else:
        raise AssertionError("an unknown transition name must raise")

    assert isinstance(fx.make_transition("iris"), fx.Iris)
    assert isinstance(fx.make_transition("swipe"), fx.SwipeWipe)


# ==========================================================================
# visual proof
# ==========================================================================
def _backdrop(surf: pygame.Surface, top=(18, 22, 44), bottom=(46, 32, 58)) -> None:
    h = surf.get_height()
    for y in range(h):
        f = y / max(1, h - 1)
        surf.fill(fx.lerp_color(top, bottom, f), (0, y, surf.get_width(), 1))


def _label(surf: pygame.Surface, text: str, x: int, y: int, size: int = 20,
           color=(236, 240, 255)) -> None:
    font = assets.font(size)
    shadow = font.render(text, True, (0, 0, 0))
    surf.blit(shadow, (x + 1, y + 1))
    surf.blit(font.render(text, True, color), (x, y))


# How long to run each preset before the snapshot, and how it is emitted.
# Tuned so each one is caught at its most legible moment.
_SHOWCASE = {
    "flap":    (0.22, "burst"),
    "score":   (0.20, "burst"),
    "coin":    (0.26, "burst"),
    "hit":     (0.34, "burst"),
    "trail":   (0.50, "stream"),
    "ambient": (2.60, "scatter"),
    "unlock":  (0.46, "burst"),
}


def render_preset_sheet(path: str) -> str:
    """Emit every preset into its own cell and photograph the result."""
    vp = _viewport(760, 1200)
    sheet = pygame.Surface((vp.screen_w, vp.screen_h))
    _backdrop(sheet)

    names = list(fx.PRESETS)
    cols, rows = 2, 4
    cell_w = vp.world_w / cols
    cell_h = vp.world_h / rows
    dt = 1.0 / 120.0

    for idx, name in enumerate(names):
        col, row = idx % cols, idx // cols
        cx = (col + 0.5) * cell_w
        cy = (row + 0.5) * cell_h
        seconds, mode = _SHOWCASE[name]
        ps = fx.ParticleSystem(capacity=900, seed=1000 + idx)
        rng = random.Random(idx)

        steps = int(seconds / dt)
        for i in range(steps):
            t = i * dt
            if mode == "burst":
                if i == 0:
                    ps.emit_preset(name, cx, cy + cell_h * 0.12)
            elif mode == "stream":
                # Drag the emitter along an arc so the ribbon has a shape.
                ps.emit_preset(name,
                               cx - cell_w * 0.30 + cell_w * 0.62 * (t / seconds),
                               cy + math.sin(t * 7.0) * cell_h * 0.16)
            else:  # scatter
                if i % 10 == 0:
                    ps.emit_preset(name,
                                   cx + rng.uniform(-0.42, 0.42) * cell_w,
                                   cy + rng.uniform(-0.40, 0.40) * cell_h)
            ps.update(dt)

        cell_px = vp.rect(col * cell_w, row * cell_h, cell_w, cell_h)
        sheet.set_clip(cell_px)
        ps.draw(sheet, vp)
        sheet.set_clip(None)
        pygame.draw.rect(sheet, (58, 64, 92), cell_px, 1)
        _label(sheet, '%s  (%d live)' % (name, ps.active), cell_px.x + 10, cell_px.y + 8)

    # A strip of the raw sprites, so a missing or broken texture is obvious.
    strip_h = 54
    pygame.draw.rect(sheet, (8, 9, 18), (0, vp.screen_h - strip_h, vp.screen_w, strip_h))
    x = 12
    for sprite in fx.SPRITES:
        tex = pygame.transform.smoothscale(fx_particles._texture(sprite), (38, 38))
        sheet.blit(tex, (x, vp.screen_h - strip_h + 8))
        _label(sheet, sprite[3:], x - 2, vp.screen_h - 16, 14, (150, 160, 190))
        x += 62
    _label(sheet, "procedural" if not assets.exists("fx_soft.png") else "shipped art",
           vp.screen_w - 110, vp.screen_h - 22, 14, (150, 160, 190))

    pygame.image.save(sheet, path)
    return path


def render_transition_sheet(path: str) -> str:
    """Three transitions caught mid-wipe, plus a camera flash and shake."""
    panel_w, panel_h = 240, 400
    gap = 10
    cols = 4
    sheet = pygame.Surface((cols * panel_w + (cols + 1) * gap, panel_h + 2 * gap + 26))
    sheet.fill((14, 16, 30))
    vp = display.compute(panel_w, panel_h)

    def mock_scene(surf: pygame.Surface, cam_offset=(0.0, 0.0)) -> None:
        _backdrop(surf, (36, 60, 108), (120, 86, 140))
        ox, oy = cam_offset
        pygame.draw.rect(surf, (72, 150, 96),
                         vp.rect(0 + ox, vp.ground_y + oy, vp.world_w, config.GROUND_H))
        for px in (0.18, 0.62):
            pygame.draw.rect(surf, (54, 120, 76),
                             vp.rect(vp.world_w * px + ox, 0 + oy, config.PILLAR_W, 420))
        pygame.draw.circle(surf, (255, 206, 92),
                           vp.to_screen_i(vp.bird_x + ox, vp.cy + oy), vp.ipx(40))

    panels = []

    for name, k in (("fade", 0.55), ("iris", 0.45), ("swipe", 0.5)):
        t = fx.make_transition(name)
        t.start(fx.IN, origin=(vp.bird_x, vp.cy))
        # Run to the requested point of the wipe so the shape is visible.
        total = t.duration
        step = total / 60.0
        for _ in range(int(60 * k)):
            t.update(step)
        surf = pygame.Surface((panel_w, panel_h))
        mock_scene(surf)
        t.draw(surf, vp)
        panels.append(("%s %d%%" % (name, int(t.obscurity * 100)), surf))

    cam = fx.Camera(seed=12)
    cam.shake(config.SHAKE_ON_HIT)
    cam.kick(0.0, 26.0)
    cam.flash((255, 255, 255), config.FLASH_ON_DEATH)
    cam.update(0.09)
    surf = pygame.Surface((panel_w, panel_h))
    mock_scene(surf, cam.offset)
    hit = fx.ParticleSystem(capacity=200, seed=13)
    hit.emit_preset("hit", vp.bird_x, vp.cy)
    for _ in range(14):
        hit.update(1.0 / 120.0)
    hit.draw(surf, vp, cam.offset)
    cam.draw_overlays(surf, vp)
    panels.append(("camera: shake+kick+flash", surf))

    for i, (title, surf) in enumerate(panels):
        x = gap + i * (panel_w + gap)
        sheet.blit(surf, (x, gap))
        pygame.draw.rect(sheet, (60, 66, 92), (x, gap, panel_w, panel_h), 1)
        _label(sheet, title, x + 2, gap + panel_h + 5, 16)

    pygame.image.save(sheet, path)
    return path


def test_visual_proof_renders() -> None:
    """Render the sheets as part of the suite so they can never silently rot.

    The assertions here only prove the renderer ran; whether the effects look
    good is a judgement that has to be made by opening the PNG.
    """
    _RENDERED.clear()
    for path in (render_preset_sheet(os.path.join(OUT_DIR, "fx_presets.png")),
                 render_transition_sheet(os.path.join(OUT_DIR, "fx_transitions.png"))):
        assert os.path.isfile(path), path
        # A blank sheet compresses to almost nothing, so size alone catches a
        # renderer that produced an empty image.
        assert os.path.getsize(path) > 10_000, "%s is suspiciously small" % path
        _RENDERED.append(path)


# ==========================================================================
# runner
# ==========================================================================
def _collect() -> List[Tuple[str, Callable[[], None]]]:
    out = []
    for name, obj in sorted(globals().items()):
        if name.startswith("test_") and callable(obj):
            out.append((name, obj))
    return out


def main() -> int:
    _ensure_display()
    failures = 0
    tests = _collect()
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failures += 1
            print("FAIL  %s" % name)
            traceback.print_exc()
        else:
            print("ok    %s" % name)

    for path in _RENDERED:
        print("\nwrote %s" % path)
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    pygame.quit()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
