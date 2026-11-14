"""The dash move and the game-speed multiplier."""
from __future__ import annotations

import pytest

from featherdash import config, display
from featherdash.entities import Bird
from featherdash.gameplay import Run, RunState, clamp_speed_scale
from featherdash.world import World


def make_run(w=608, h=1080, seed=0, scale=None):
    vp = display.compute(w, h)
    return Run(vp, World("day"), seed=seed, speed_scale=scale), vp


def launched_bird(x: float = 200.0) -> Bird:
    bird = Bird("ember", x)
    bird.flap()
    return bird


# --------------------------------------------------------------------------
# dash
# --------------------------------------------------------------------------

def test_dash_travels_the_configured_distance_and_returns():
    bird = launched_bird()
    assert bird.dash()
    peak = 0.0
    for _ in range(300):
        bird.step(config.PHYSICS_DT)
        peak = max(peak, bird.dash_offset)
    assert peak == pytest.approx(config.DASH_DISTANCE, abs=1.0)
    # Returning to exactly the anchor matters: a residual offset would
    # permanently shift the bird's collision position.
    assert bird.dash_offset == 0.0
    assert bird.x == pytest.approx(200.0)


def test_dash_is_rate_limited_by_its_cooldown():
    bird = launched_bird()
    assert bird.dash()
    assert not bird.dash(), "a second dash must not stack"
    for _ in range(int(config.DASH_COOLDOWN * 120) + 4):
        bird.step(config.PHYSICS_DT)
    assert bird.dash_ready
    assert bird.dash()


def test_dash_charge_meter_runs_from_zero_to_one():
    bird = launched_bird()
    bird.dash()
    assert bird.dash_charge == 0.0
    seen_middle = False
    for _ in range(int(config.DASH_COOLDOWN * 120) + 4):
        bird.step(config.PHYSICS_DT)
        if 0.3 < bird.dash_charge < 0.9:
            seen_middle = True
    assert seen_middle, "the meter should pass through the middle, not snap"
    assert bird.dash_charge == 1.0


def test_dash_is_unavailable_before_launch_and_after_death():
    idle = Bird("ember", 200.0)
    assert not idle.dash_ready, "no dashing while hovering on the start screen"
    assert not idle.dash()

    dead = launched_bird()
    dead.kill()
    assert not dead.dash_ready
    assert not dead.dash()


def test_dash_holds_a_line_instead_of_arcing():
    """Gravity is nearly cancelled mid-dash, which is what makes it usable."""
    plain, dashing = launched_bird(), launched_bird()
    dashing.dash()
    steps = int(config.DASH_OUT * 120)
    for _ in range(steps):
        plain.step(config.PHYSICS_DT)
        dashing.step(config.PHYSICS_DT)
    assert abs(dashing.vy) < abs(plain.vy) * 0.6


def test_dash_does_not_grant_invulnerability():
    """Dashing into a pillar must still kill, or it is an escape button."""
    run, vp = make_run()
    run.flap()
    run.step(config.PHYSICS_DT)
    pillar = run.pillars[0]
    assert run.dash()
    run.bird.base_x = pillar.x + config.PILLAR_W * 0.5 - run.bird.dash_offset
    run.bird.y = pillar.gap_top - 90.0
    run._check_collisions()
    assert run.state in (RunState.DYING, RunState.DEAD)


def test_dash_actually_closes_distance_on_the_next_pillar():
    run, vp = make_run()
    run.flap()
    for _ in range(60):
        run.step(config.PHYSICS_DT)
    target = next(p for p in run.pillars if p.right > run.bird.x)
    before = target.x - run.bird.x
    run.dash()
    for _ in range(int((config.DASH_OUT + config.DASH_HOLD) * 120)):
        run.step(config.PHYSICS_DT)
    after = target.x - run.bird.x
    closed = before - after
    # The world also scrolls toward the bird, so the dash must account for
    # clearly more than the scroll alone would.
    scroll_only = run.speed * (config.DASH_OUT + config.DASH_HOLD)
    assert closed > scroll_only + config.DASH_DISTANCE * 0.8


def test_run_reports_blocked_dash_attempts():
    run, _ = make_run()
    run.flap()
    run.step(config.PHYSICS_DT)
    assert run.dash()
    assert not run.dash()
    events = run.drain()
    assert events.dashed and events.dash_blocked


# --------------------------------------------------------------------------
# game speed
# --------------------------------------------------------------------------

def test_legacy_speed_matches_version_one():
    """2.4 px/frame at 60 fps in a 600px window, scaled into a 1080-unit world."""
    assert pytest.approx(2.4 * 60 * (config.WORLD_H / 600.0)) == config.LEGACY_SPEED


@pytest.mark.parametrize("scale,name", config.SPEED_PRESETS)
def test_each_preset_scales_the_baseline(scale, name):
    run, _ = make_run(scale=scale)
    assert run.speed == pytest.approx(config.LEGACY_SPEED * scale)
    run.score = config.RAMP_SCORE
    assert run.speed == pytest.approx(config.LEGACY_SPEED * scale * config.SPEED_RAMP_FACTOR)


def test_default_preserves_the_hand_tuned_curve():
    run, _ = make_run(scale=config.SPEED_SCALE_DEFAULT)
    assert run.speed == pytest.approx(config.SPEED_START, rel=0.02)
    run.score = config.RAMP_SCORE
    assert run.speed == pytest.approx(config.SPEED_END, rel=0.02)


@pytest.mark.parametrize("value,expected", [
    (0.1, config.SPEED_SCALE_MIN), (99.0, config.SPEED_SCALE_MAX),
    (float("nan"), config.SPEED_SCALE_DEFAULT), ("fast", config.SPEED_SCALE_DEFAULT),
    (None, config.SPEED_SCALE_DEFAULT), (2.0, 2.0),
])
def test_speed_scale_is_clamped(value, expected):
    assert clamp_speed_scale(value) == pytest.approx(expected)


def test_presets_span_the_supported_range():
    scales = [s for s, _ in config.SPEED_PRESETS]
    assert min(scales) == config.SPEED_SCALE_MIN
    assert max(scales) == config.SPEED_SCALE_MAX
    assert scales == sorted(scales), "presets must be ordered for the stepper"
    assert config.SPEED_SCALE_DEFAULT in scales


def test_no_tunnelling_at_the_fastest_setting():
    """A pillar must never cross the bird within one physics step."""
    run, _ = make_run(scale=config.SPEED_SCALE_MAX)
    run.score = config.RAMP_SCORE
    per_step = run.speed * config.PHYSICS_DT
    # The dash closes distance too, so count both.
    dash_per_step = config.DASH_DISTANCE / max(config.DASH_OUT, 1e-6) * config.PHYSICS_DT
    assert per_step + dash_per_step < config.PILLAR_W * 0.5


@pytest.mark.parametrize("scale", [0.5, 1.0, 1.25, 2.0])
def test_the_game_is_playable_at_every_pace(scale):
    """A straightforward controller must survive; if it cannot, the pace is broken."""
    vp = display.compute(608, 1080)
    best = 0
    for seed in range(3):
        run = Run(vp, World("day"), seed=seed, speed_scale=scale)
        run.flap()
        for _ in range(120 * 90):
            if run.state in (RunState.DYING, RunState.DEAD):
                break
            nxt = next((p for p in run.pillars if p.right > run.bird.x - config.BIRD_HIT_RX), None)
            rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
            target = (nxt.gap_center + rise * 0.5) if nxt else vp.world_h * 0.45
            if run.bird.y > target and run.bird.vy > -120:
                run.flap()
            run.step(config.PHYSICS_DT)
        best = max(best, run.score)
    assert best >= 8, f"bot only reached {best} at {scale}x"


# --------------------------------------------------------------------------
# the settings actually reach the game
# --------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERDASH_WINDOW", "480x854")
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    from featherdash.app import App

    return App()


@pytest.mark.parametrize("scale", [0.5, 1.0, 2.0, 4.0])
def test_speed_setting_takes_effect_on_the_next_run(app, scale):
    app.profile["speed_scale"] = scale
    app.profile.save()
    app.go("game")
    app._apply_pending()
    assert app.scene.run.speed == pytest.approx(config.LEGACY_SPEED * scale)


def test_dash_can_be_turned_off(app):
    from featherdash.input import Frame
    from featherdash.scenes.game import Phase

    app.profile["dash_enabled"] = False
    app.go("game")
    app._apply_pending()
    scene = app.scene
    scene.phase = Phase.PLAYING
    scene.run.flap()
    for _ in range(30):
        scene.step(config.PHYSICS_DT)

    scene.handle(Frame(dash=True))
    assert not scene.run.bird.dashing, "the dash key must do nothing when disabled"

    app.profile["dash_enabled"] = True
    scene.handle(Frame(dash=True))
    assert scene.run.bird.dashing, "the dash key must work when enabled"


def test_a_full_run_at_the_fastest_setting_stays_sane(app):
    """4x is brutal by design, but it must not break the simulation."""
    from featherdash.scenes.game import Phase

    app.profile["speed_scale"] = config.SPEED_SCALE_MAX
    app.go("game")
    app._apply_pending()
    scene = app.scene
    scene.phase = Phase.PLAYING
    scene.run.flap()
    for _ in range(120 * 20):
        run = scene.run
        if run.state in (RunState.DYING, RunState.DEAD):
            run.reset(app.display.viewport)
            run.flap()
        nxt = next((p for p in run.pillars if p.right > run.bird.x - config.BIRD_HIT_RX), None)
        rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
        target = (nxt.gap_center + rise * 0.5) if nxt else config.WORLD_H * 0.45
        if run.bird.y > target and run.bird.vy > -120:
            run.flap()
        scene.step(config.PHYSICS_DT)
        assert len(run.pillars) <= 32, "pillar list grew without bound at high speed"
        for pillar in run.pillars:
            assert pillar.gap_bottom <= app.display.viewport.ground_y + 1.0
    scene.draw(app.display.surface, app.display.viewport, 1.0)


@pytest.mark.parametrize("scale,_name", config.SPEED_PRESETS)
def test_opening_runway_is_measured_in_time_not_distance(scale, _name):
    """Every pace must give the player a comparable moment before the first pillar.

    A fixed spawn distance collapses to well under a second of reaction time at
    4x, which loses the run before the player has done anything.
    """
    run, vp = make_run(1080, 2400, scale=scale)
    run.flap()
    run.step(config.PHYSICS_DT)
    first = run.pillars[0]
    seconds = (first.x - run.bird.x) / run.speed
    assert seconds >= config.INTRO_SECONDS, (
        f"only {seconds:.2f}s of runway at {scale}x")
    # And not so much that the slowest preset feels like it has stalled.
    assert seconds <= config.INTRO_SECONDS * 3.0


@pytest.mark.parametrize("scale", [s for s, _ in config.SPEED_PRESETS])
def test_a_naive_controller_scores_at_every_preset(scale):
    """Even the hardest preset must be playable, not merely survivable-in-theory."""
    vp = display.compute(1080, 2400)
    rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
    best = 0
    for seed in range(4):
        run = Run(vp, World("day"), seed=seed, speed_scale=scale)
        run.flap()
        for _ in range(120 * 45):
            if run.state in (RunState.DYING, RunState.DEAD):
                break
            nxt = next((p for p in run.pillars if p.right > run.bird.x - config.BIRD_HIT_RX), None)
            target = (nxt.gap_center + rise * 0.5) if nxt else vp.world_h * 0.45
            if run.bird.y > target and run.bird.vy > -120:
                run.flap()
            run.step(config.PHYSICS_DT)
        best = max(best, run.score)
    # Deliberately low: "Insane" is meant to be punishing, just not impossible.
    assert best >= 3, f"a naive bot could not score at all at {scale}x (best {best})"
