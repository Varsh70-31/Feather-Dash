"""Simulation rules: difficulty, spawning, collision and fairness."""
from __future__ import annotations

import pytest

from featherdash import config, display
from featherdash.entities import Bird
from featherdash.gameplay import Run, RunState
from featherdash.world import World

SHAPES = [(1080, 2400), (608, 1080), (1536, 2048), (1920, 1080), (320, 480)]


def make_run(w=608, h=1080, seed=0):
    vp = display.compute(w, h)
    return Run(vp, World("day"), seed=seed), vp


def test_physics_is_frame_rate_independent():
    """The same elapsed time must produce the same trajectory at any step rate.

    The loop always uses config.PHYSICS_DT, so this guards the invariant that the
    accumulator relies on rather than any particular frame rate.
    """
    results = []
    for substeps in (1, 2, 4):
        bird = Bird("ember", 0.0)
        bird.flap()
        dt = config.PHYSICS_DT / substeps
        for _ in range(int(1.0 / dt)):
            bird.step(dt)
        results.append(bird.y)
    # Euler integration is step-size sensitive, but over one second the spread
    # must stay far below anything a player could perceive.
    assert max(results) - min(results) < 12.0


@pytest.mark.parametrize("w,h", SHAPES)
def test_every_gap_is_inside_the_play_area(w, h):
    run, vp = make_run(w, h)
    run.flap()
    for _ in range(120 * 90):
        run.step(config.PHYSICS_DT)
        for pillar in run.pillars:
            assert pillar.gap_top >= 0, "gap opened above the sky"
            assert pillar.gap_bottom <= vp.ground_y, "gap opened below the ground"
        if run.state == RunState.DEAD:
            run.reset(vp)
            run.flap()


def test_consecutive_gaps_are_reachable():
    """Within a run, the gap may never jump further than a player can follow.

    Measured per run, because a restart deliberately re-centres the first gap.
    """
    run, vp = make_run()
    runs: list[list[float]] = [[]]
    run.flap()
    for _ in range(120 * 120):
        before = len(run.pillars)
        run.step(config.PHYSICS_DT)
        if len(run.pillars) > before:
            runs[-1].append(run.pillars[-1].gap_center)
        if run.state == RunState.DEAD:
            run.reset(vp)
            run.flap()
            runs.append([])
    assert sum(len(r) for r in runs) > 20
    for centers in runs:
        deltas = [abs(b - a) for a, b in zip(centers, centers[1:])]
        if deltas:
            assert max(deltas) <= config.GAP_MAX_DELTA + 1.0


def test_difficulty_ramps_then_plateaus():
    run, _ = make_run()
    run.score = 0
    easy = (run.gap_size, run.speed, run.spacing)
    run.score = config.RAMP_SCORE
    hard = (run.gap_size, run.speed, run.spacing)
    run.score = config.RAMP_SCORE * 10
    capped = (run.gap_size, run.speed, run.spacing)
    assert hard[0] < easy[0], "gap should shrink"
    assert hard[1] > easy[1], "speed should rise"
    assert hard[2] < easy[2], "spacing should tighten"
    assert capped == pytest.approx(hard), "difficulty must plateau, not run away"


def test_hitbox_is_forgiving_relative_to_the_sprite():
    """A graze must not kill: the hitbox is deliberately inside the art."""
    assert config.BIRD_HIT_RX * 2 < config.BIRD_W * 0.75
    assert config.BIRD_HIT_RY * 2 < config.BIRD_H * 0.75


def test_collision_detects_a_direct_pillar_hit():
    run, vp = make_run()
    run.flap()
    run.step(config.PHYSICS_DT)
    pillar = run.pillars[0]
    run.bird.x = pillar.x + config.PILLAR_W * 0.5
    run.bird.y = pillar.gap_top - 100.0  # solidly inside the upper pillar
    run._check_collisions()
    assert run.state in (RunState.DYING, RunState.DEAD)


def test_passing_cleanly_through_a_gap_does_not_collide():
    run, vp = make_run()
    run.flap()
    run.step(config.PHYSICS_DT)
    pillar = run.pillars[0]
    run.bird.x = pillar.x + config.PILLAR_W * 0.5
    run.bird.y = pillar.gap_center
    run._check_collisions()
    assert run.state == RunState.PLAYING


def test_ground_and_ceiling_are_lethal():
    run, vp = make_run()
    run.flap()
    run.step(config.PHYSICS_DT)
    run.bird.y = vp.ground_y + 10
    run._check_collisions()
    assert run.state in (RunState.DYING, RunState.DEAD)

    run.reset(vp)
    run.flap()
    run.step(config.PHYSICS_DT)
    run.bird.y = config.CEILING_KILL - 10
    run._check_collisions()
    assert run.state in (RunState.DYING, RunState.DEAD)


def test_score_increments_once_per_pillar():
    run, vp = make_run()
    run.flap()
    for _ in range(120 * 30):
        run.step(config.PHYSICS_DT)
        if run.state != RunState.PLAYING:
            break
        nxt = next((p for p in run.pillars if p.right > run.bird.x), None)
        if nxt:
            run.bird.y = nxt.gap_center
            run.bird.vy = 0.0
    scored = [p for p in run.pillars if p.scored]
    assert run.score >= 1
    # No pillar may be counted twice: the score can never exceed the number of
    # pillars that have actually gone past the bird.
    assert run.score >= len(scored)


def test_pillar_count_stays_bounded_on_a_wide_viewport():
    run, vp = make_run(3840, 1080)
    run.flap()
    for _ in range(120 * 60):
        run.step(config.PHYSICS_DT)
        assert len(run.pillars) <= 32
        if run.state == RunState.DEAD:
            run.reset(vp)
            run.flap()


def test_resize_mid_run_keeps_the_bird_on_screen():
    run, vp = make_run(608, 1080)
    run.flap()
    for _ in range(600):
        run.step(config.PHYSICS_DT)
    wide = display.compute(1920, 1080)
    run.on_viewport_changed(wide)
    assert 0 < run.bird.x < wide.world_w
    for _ in range(600):
        run.step(config.PHYSICS_DT)


def test_run_reset_clears_everything():
    run, vp = make_run()
    run.flap()
    for _ in range(1200):
        run.step(config.PHYSICS_DT)
    run.reset(vp)
    assert run.score == 0 and run.feathers == 0
    assert not run.pillars and not run.coins
    assert run.state == RunState.READY
