"""A headless autoplay bot.

Doubles as the game's playability regression test: if a straightforward
controller cannot survive, the physics, the gap sizing or the difficulty ramp has
been broken, and that is a far more useful signal than any unit test on the
individual numbers. It also exercises the full simulation at several viewport
shapes, which is where resolution bugs surface.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame  # noqa: E402

from featherdash import config, display  # noqa: E402
from featherdash.gameplay import Run, RunState  # noqa: E402
from featherdash.world import World  # noqa: E402


def play(vp, seed: int = 0, max_seconds: float = 180.0, aim_bias: float = 0.0) -> Run:
    """Run one bot game to death or the time limit."""
    run = Run(vp, World("day"), seed=seed)
    run.flap()
    steps = int(max_seconds / config.PHYSICS_DT)
    for _ in range(steps):
        if run.state in (RunState.DYING, RunState.DEAD):
            break
        target = _aim(run, vp, aim_bias)
        # Flap while sinking below the target. The cooldown in the bird caps the
        # rate, so this cannot degenerate into holding the bird at the ceiling.
        if run.bird.y > target and run.bird.vy > -120:
            run.flap()
        run.step(config.PHYSICS_DT)
    return run


def _aim(run: Run, vp, bias: float) -> float:
    nxt = None
    for pillar in run.pillars:
        if pillar.right > run.bird.x - config.BIRD_HIT_RX:
            nxt = pillar
            break
    if nxt is None:
        return vp.world_h * 0.45
    # Flapping at the gap centre would put the APEX of the arc above the gap,
    # because a flap carries the bird `rise` units upward. Aiming half a rise
    # below centre is what centres the resulting oscillation inside the gap.
    rise = (config.FLAP_VELOCITY ** 2) / (2.0 * config.GRAVITY)
    return nxt.gap_center + rise * (0.5 + bias)


def main() -> int:
    pygame.init()
    pygame.display.set_mode((64, 64))

    shapes = [
        ("phone 9:19.5", 1080, 2400),
        ("phone 9:16", 608, 1080),
        ("tablet 3:4", 1536, 2048),
        ("desktop 16:9", 1920, 1080),
        ("tiny window", 320, 480),
    ]

    worst = 10**9
    print(f"{'shape':16} {'world':>12} {'score':>6} {'feathers':>9} {'time':>7}  outcome")
    for name, w, h in shapes:
        vp = display.compute(w, h)
        scores = []
        for seed in range(5):
            run = play(vp, seed=seed)
            scores.append(run.score)
        best, avg = max(scores), sum(scores) / len(scores)
        worst = min(worst, best)
        run = play(vp, seed=0)
        print(f"{name:16} {vp.world_w:6.0f}x{vp.world_h:<5.0f} {best:6d} {run.feathers:9d} "
              f"{run.elapsed:6.1f}s  avg {avg:.1f} over 5 seeds")

    # The ramp must actually bite: a bot that never dies means the game has no
    # difficulty curve, and one that dies instantly means it is unplayable.
    print()
    ok = True
    if worst < 15:
        print(f"FAIL: bot could not reach 15 points on every shape (worst best = {worst})")
        ok = False
    else:
        print(f"PASS: bot reaches at least {worst} points on every viewport shape")

    vp = display.compute(608, 1080)
    long_run = play(vp, seed=3, max_seconds=600.0)
    print(f"long run: score {long_run.score} in {long_run.elapsed:.0f}s "
          f"(final gap {long_run.gap_size:.0f}, speed {long_run.speed:.0f})")
    if long_run.state not in (RunState.DYING, RunState.DEAD) and long_run.score < 60:
        print("FAIL: long run neither died nor scored well -- the ramp may be broken")
        ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
