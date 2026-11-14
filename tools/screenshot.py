"""Capture Play Store screenshots by driving the real game.

Shots come from the actual renderer at a real store resolution rather than from
mock-ups, so what the listing shows is what the player gets. Each shot forces a
specific scene and a deterministic simulation state, which keeps the set
reproducible between runs.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT_DIR = os.path.join(ROOT, "store", "screenshots")

# Play accepts 320-3840 px per side; 1080x1920 is the safe, universally-liked
# phone shot, and 1600x2560 covers the 7-inch tablet slot.
PHONE = (1080, 1920)
TABLET = (1600, 2560)


def _boot(size: tuple[int, int], headless: bool):
    os.environ["FEATHERDASH_WINDOW"] = f"{size[0]}x{size[1]}"
    if headless:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    # Store listings are for phone players, so render the touch layout even
    # though the capture runs on a desktop: the dash button and its hint only
    # exist when the viewport reports touch.
    os.environ["FEATHERDASH_FORCE_TOUCH"] = "1"
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    # Point the save layer at a throwaway directory BEFORE the App is built.
    # Otherwise the capture reads whatever the developer last played, and the
    # shots silently reflect their equipped bird, their score and their
    # settings -- which is exactly how a wrong screenshot reaches the listing.
    from featherdash import save

    tmp = tempfile.mkdtemp(prefix="featherdash-shots-")
    save.data_dir = lambda: tmp

    from featherdash.app import App

    app = App()
    _apply_showcase_profile(app)
    return app


def _apply_showcase_profile(app) -> None:
    """A deterministic profile that makes the shots look like a played game.

    Every value is fixed here rather than inherited, so re-running the capture
    on any machine produces byte-comparable output.
    """
    app.profile["high_score"] = 128
    app.profile["feathers"] = 940
    app.profile["games_played"] = 61
    app.profile["total_score"] = 2180
    app.profile["total_feathers_earned"] = 3120
    app.profile["best_medal"] = "platinum"
    app.profile["skin"] = "ember"          # the bird the store art is built around
    app.profile["unlocked"] = ["ember", "sky", "mint", "violet"]
    app.profile["speed_scale"] = 1.25
    app.profile["dash_enabled"] = True
    app.profile["seen_tutorial"] = True


def _medal(score: int):
    from featherdash.save import medal_for

    return medal_for(score)


def _autopilot(run) -> None:
    """Keep the bird alive so a gameplay shot shows gameplay, not a death screen.

    Same controller as tests/autoplay.py: flap when the bird sinks past a point
    half a flap-arc below the gap centre, which centres its oscillation in the gap.
    """
    from featherdash import config
    from featherdash.gameplay import RunState

    if run.state not in (RunState.READY, RunState.PLAYING):
        return
    nxt = None
    for pillar in run.pillars:
        if pillar.right > run.bird.x - config.BIRD_HIT_RX:
            nxt = pillar
            break
    rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
    target = (nxt.gap_center + rise * 0.5) if nxt else config.WORLD_H * 0.45
    if run.bird.y > target and run.bird.vy > -120:
        run.flap()


def _advance(app, scene_name: str, steps: int, *, score: int = 0,
             feathers: int = 0, phase=None, autopilot: bool = False,
             dash_at: int | None = None) -> None:
    """Put the game into a specific, repeatable state."""
    from featherdash import config
    from featherdash.scenes.game import Phase

    app.go(scene_name)
    app._apply_pending()
    scene = app.scene

    run = getattr(scene, "run", None)
    if run is not None and score:
        app.world.set_theme(app.world.theme_for_score(score), immediate=True)

    if phase is not None:
        scene.phase = phase
    if phase is Phase.RESULTS and run is not None:
        # Present a finished run rather than whatever the bot happened to do.
        run.score = score
        run.feathers = feathers
        scene.result = {"score": score, "feathers": feathers,
                        "new_best": False, "medal": _medal(score), "new_medal": False}
        scene.counter.set_target(score, from_value=0)
        scene.counter.finish()
        scene.results_time = 1.0

    for i in range(steps):
        if run is not None and autopilot:
            _autopilot(run)
        # Fire the dash a beat before the capture so the trail and the shockwave
        # are both still on screen when the frame is taken.
        if run is not None and dash_at is not None and i == dash_at:
            run.dash()
        scene.step(config.PHYSICS_DT)
        scene.update(config.PHYSICS_DT)
        if app.particles is not None:
            app.particles.update(config.PHYSICS_DT)
        if app.camera is not None:
            app.camera.update(config.PHYSICS_DT)
        if run is not None and score:
            # Pin the score so the shot shows the intended number and theme even
            # though the bot has only been flying for a few seconds.
            run.score = score
            run.feathers = feathers
            for pillar in run.pillars:
                pillar.theme = app.world.next_theme or app.world.theme
            if phase is Phase.PLAYING and scene.phase is not Phase.PLAYING:
                scene.phase = Phase.PLAYING


def _capture(app, path: str) -> None:
    import pygame

    surface = app.display.surface
    vp = app.display.viewport
    app.scene.draw(surface, vp, 1.0)
    if app.camera is not None:
        try:
            app.camera.draw_overlays(surface, vp)
        except Exception:
            pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pygame.image.save(surface, path)
    print(f"  wrote {os.path.relpath(path, ROOT)}  {surface.get_size()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Play Store screenshots.")
    parser.add_argument("--tablet", action="store_true", help="also capture tablet sizes")
    parser.add_argument("--windowed", action="store_true", help="show the window while capturing")
    args = parser.parse_args()

    sizes = [("phone", PHONE)] + ([("tablet", TABLET)] if args.tablet else [])

    for label, size in sizes:
        app = _boot(size, headless=not args.windowed)
        from featherdash.scenes.game import Phase

        # Clear this label's previous output first. Renaming a shot would
        # otherwise leave the old file behind, and the orphan is indistinguishable
        # from a current one when someone is uploading eight files to Play.
        for stale in sorted(glob.glob(os.path.join(OUT_DIR, f"{label}_*.png"))):
            os.unlink(stale)
        print(f"{label} {size[0]}x{size[1]}:")
        # (name, scene, steps, kwargs) rather than closures: a lambda here would
        # capture `app`, which the enclosing loop rebinds for each output size.
        shots = [
            ("01_menu",     "menu",     180, {}),
            ("02_day",      "game",     460, dict(score=14, feathers=11,
                                                  phase=Phase.PLAYING, autopilot=True)),
            ("03_dusk",     "game",     430, dict(score=27, feathers=22,
                                                  phase=Phase.PLAYING, autopilot=True)),
            ("04_night",    "game",     400, dict(score=39, feathers=31,
                                                  phase=Phase.PLAYING, autopilot=True)),
            ("05_dash",     "game",     400, dict(score=2, feathers=2,
                                                  phase=Phase.PLAYING, autopilot=True,
                                                  dash_at=376)),
            ("06_results",  "game",     250, dict(score=57, feathers=44,
                                                  phase=Phase.RESULTS)),
            ("07_shop",     "shop",     150, {}),
            ("08_settings", "settings", 120, {}),
        ]
        for name, scene, steps, kwargs in shots:
            _advance(app, scene, steps, **kwargs)
            _capture(app, os.path.join(OUT_DIR, f"{label}_{name}.png"))

        import pygame

        pygame.quit()
        for mod in [m for m in sys.modules if m.startswith("featherdash")]:
            del sys.modules[mod]

    print(f"\nScreenshots in {os.path.relpath(OUT_DIR, ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
