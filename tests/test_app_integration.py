"""Whole-application integration: the loop, scene switching, input and resize.

The unit tests cover each piece; this covers the wiring between them, which is
where the bugs that actually reach players tend to live -- a scene that forgets
to rebuild its layout after a resize, an input event that reaches no one, a
results screen that never appears.
"""
from __future__ import annotations

import pygame
import pytest

from featherdash import config
from featherdash.gameplay import RunState
from featherdash.scenes.game import Phase


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERDASH_WINDOW", "480x854")
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    from featherdash.app import App

    instance = App()
    yield instance
    instance.profile.flush()


def pump(app, frames: int, events: list | None = None, autopilot: bool = False) -> None:
    """Drive the real frame pipeline, optionally flying the bird competently."""
    for i in range(frames):
        if events and i == 0:
            for event in events:
                pygame.event.post(event)
        if autopilot:
            _fly(app)
        frame = app.input.poll(app.display.viewport)
        app._handle_global(frame)
        app.scene.handle(frame)
        app.scene.step(config.PHYSICS_DT)
        app.scene.update(config.PHYSICS_DT)
        if app.particles is not None:
            app.particles.update(config.PHYSICS_DT)
        if app.camera is not None:
            app.camera.update(config.PHYSICS_DT)
        app.audio.update(config.PHYSICS_DT)
        app._render(0.0)
        app._apply_pending()


def _fly(app) -> None:
    run = getattr(app.scene, "run", None)
    if run is None or run.state not in (RunState.READY, RunState.PLAYING):
        return
    nxt = next((p for p in run.pillars if p.right > run.bird.x - config.BIRD_HIT_RX), None)
    rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
    target = (nxt.gap_center + rise * 0.5) if nxt else config.WORLD_H * 0.45
    if run.bird.y > target and run.bird.vy > -120:
        run.flap()


def tap():
    return pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0)


def test_boots_into_the_menu(app):
    pump(app, 20)
    assert app.scene.name == "menu"


def test_a_tap_starts_a_game_and_a_second_launches_the_bird(app):
    pump(app, 10)
    pump(app, 5, [tap()])
    assert app.scene.name == "game"
    assert app.scene.phase is Phase.TUTORIAL
    pump(app, 5, [tap()])
    assert app.scene.phase is Phase.PLAYING


def test_a_competent_player_scores_and_banks_progress(app):
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    pump(app, 120 * 40, autopilot=True)
    run = app.scene.run
    assert run.score > 5, f"autopilot only reached {run.score}"
    assert run.feathers > 0, "no feathers collected over 40 seconds of play"


def test_dying_reaches_the_results_screen_and_saves(app):
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    pump(app, 120 * 25, autopilot=True)
    scored = app.scene.run.score
    # Stop flying; the bird falls and the run ends.
    pump(app, 120 * 6)
    assert app.scene.phase is Phase.RESULTS
    assert app.profile["high_score"] >= scored
    assert app.profile["games_played"] == 1


def test_mouse_leaving_the_window_does_not_pause(app):
    """ACTIVEEVENT fires on mouse enter/leave; honouring it would pause mid-run."""
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    assert app.scene.phase is Phase.PLAYING
    pump(app, 10, [pygame.event.Event(pygame.ACTIVEEVENT, gain=0, state=1)], autopilot=True)
    assert app.scene.phase is Phase.PLAYING, "mouse leaving the window paused the game"


def test_losing_focus_does_pause(app):
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    focus_lost = getattr(pygame, "WINDOWFOCUSLOST", None)
    if focus_lost is None:
        pytest.skip("this SDL build has no WINDOWFOCUSLOST")
    pump(app, 5, [pygame.event.Event(focus_lost)])
    assert app.scene.phase is Phase.PAUSED


@pytest.mark.parametrize("size", [(320, 480), (480, 854), (900, 500), (1280, 720), (1080, 2400)])
def test_resizing_mid_run_keeps_everything_consistent(app, size):
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    pump(app, 600, autopilot=True)

    pygame.display.set_mode(size, pygame.RESIZABLE)
    app.display.sync()
    app._on_resize()
    vp = app.display.viewport
    assert vp.view_left <= 0.01 and vp.view_right >= vp.world_w - 0.01
    assert 0 < app.scene.run.bird.x < vp.world_w
    pump(app, 300, autopilot=True)
    assert app.scene.run.state in (RunState.PLAYING, RunState.DYING, RunState.DEAD)


def test_every_scene_can_be_entered_and_drawn(app):
    for name in ("menu", "game", "shop", "settings", "menu"):
        app.go(name)
        app._apply_pending()
        pump(app, 40)
        assert app.scene.name == name


def test_back_from_a_subscreen_returns_to_the_menu(app):
    app.go("shop")
    app._apply_pending()
    pump(app, 20)
    pump(app, 10, [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0)])
    assert app.scene.name == "menu"


def test_no_asset_is_missing(app):
    """Any sprite the game asks for must exist; a placeholder means a gap."""
    from featherdash import assets

    for name in ("menu", "game", "shop", "settings"):
        app.go(name)
        app._apply_pending()
        pump(app, 60, autopilot=True)
    assert not assets.missing(), f"missing assets: {sorted(assets.missing())}"


# --------------------------------------------------------------------------
# regressions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(2560, 1080), (320, 480), (1536, 2048), (1080, 2400)])
def test_resizing_mid_run_does_not_kill_the_player(app, size):
    """A resize moves the bird; the world must move with it.

    The bird sits at a fraction of the world width, so widening the window used
    to slide it forward into whatever was in front of it -- 144 units straight
    into the next pillar on an ultrawide resize, ending a run that was in no
    danger.
    """
    pump(app, 10)
    pump(app, 5, [tap()])
    pump(app, 5, [tap()])
    pump(app, 600, autopilot=True)
    run = app.scene.run
    assert run.state is RunState.PLAYING, "precondition: the bot should still be alive"

    gaps_before = [(p.x - run.bird.x, p.gap_center) for p in run.pillars]

    pygame.display.set_mode(size, pygame.RESIZABLE)
    app.display.sync()
    app._on_resize()

    # Measured before stepping: one physics step legitimately scrolls the world
    # by speed * dt, which would mask the thing under test.
    gaps_after = [(p.x - run.bird.x, p.gap_center) for p in run.pillars]
    for (d0, c0), (d1, c1) in zip(gaps_before, gaps_after):
        assert abs(d1 - d0) < 0.01, f"pillar distance moved {d0:.1f} -> {d1:.1f}"
        assert c0 == c1

    # And the run must survive the frames that follow.
    pump(app, 120, autopilot=True)
    assert run.state is RunState.PLAYING, f"resizing to {size} killed the run"


def test_resetting_progress_does_not_leave_the_game_muted(app):
    """The profile can change without a toggle, and the mixer must follow.

    Reset rewrites every field at once; before the fix the mixer kept the old
    state, so the game stayed silent while Settings showed sound as on.
    """
    app.profile["sfx"] = False
    app.profile["music"] = False
    app.apply_audio_settings()
    assert not app.audio.sfx_enabled

    app.profile.reset()
    app.apply_audio_settings()

    assert app.profile["sfx"] is True
    assert app.audio.sfx_enabled is True, "reset restored the setting but not the mixer"
    assert app.audio.music_enabled is True


def test_a_null_in_the_profile_does_not_brick_the_launch(tmp_path, monkeypatch):
    """A truncated or hand-edited save must not stop the game starting."""
    import json

    from featherdash import save

    (tmp_path / "profile.json").write_text(
        json.dumps({"version": 1, "unlocked": None, "skin": None, "feathers": None}),
        encoding="utf-8")
    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("FEATHERDASH_WINDOW", "480x854")

    from featherdash.app import App

    instance = App()
    assert instance.profile["skin"] == "ember"
    assert instance.profile["unlocked"] == ["ember"]
    assert instance.profile["feathers"] == 0
