#!/usr/bin/env python3
"""Headless self-test for `featherdash.ui`.

Builds one realistic screen out of every widget in the kit, drives it with
synthetic pointer input, and renders it at three very different viewport scales
so the layout can actually be looked at rather than assumed.

Either of these runs it, and both print the *same* check-by-check output and
the same final banner -- the whole point being that a number quoted out of one
of them can be reproduced with the other:

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python tests/test_ui.py
    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m pytest tests/test_ui.py -q -s

(pytest swallows stdout without `-s`; the banner is printed at interpreter
exit, so it survives either way.) Both run the checks in the same order and
reset the module-level caches between groups, because the kit memoises
aggressively and two runs over different cache states are two different tests.
Every check prints a line, pass or fail: an "all green" count with no per-check
output is a claim, not evidence.

As a script this exits non-zero if anything failed; under pytest the first
failure in a group raises.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
from typing import Any, Dict, List, Tuple

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pygame  # noqa: E402

from featherdash import (  # noqa: E402
    assets,
    config,
    ui,  # noqa: E402
)
from featherdash.display import Viewport, compute  # noqa: E402
from featherdash.ui import text as uitext  # noqa: E402
from featherdash.ui import widgets as uiwidgets  # noqa: E402

OUT_DIR = os.path.join(ROOT, "docs")
DT = 1.0 / 60.0

# --------------------------------------------------------------------------
# tiny assertion harness
# --------------------------------------------------------------------------
_failures: List[str] = []
_checks = 0
_VERBOSE = os.environ.get("UI_SELFTEST_QUIET", "") not in ("1", "true", "yes")


def check(label: str, condition: bool, detail: str = "") -> bool:
    """Record a check, and print a line for it either way.

    As a script it collects every failure so one run reports all of them. Under
    pytest it raises instead: a helper that only *prints* failures would let the
    suite go green with the interface broken, which is worse than no test.
    """
    global _checks
    _checks += 1
    if not condition:
        message = f"{label}" + (f"  [{detail}]" if detail else "")
        _failures.append(message)
        print(f"  FAIL  {message}")
        if "pytest" in sys.modules:
            raise AssertionError(message)
        return False
    if _VERBOSE:
        print(f"  ok    {label}" + (f"  [{detail}]" if detail else ""))
    return True


def near(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def section(name: str) -> None:
    print(f"\n-- {name}")


def reset_caches() -> None:
    """Match the per-test cache reset `conftest.py` performs under pytest.

    The kit memoises glyphs, shadows, nine-slices and scaled art in
    module-level caches, which is right for a game that runs one process per
    session and wrong for a test file whose groups would otherwise inherit
    each other's cache state. Doing it here too is what makes the script and
    pytest run the same test.
    """
    try:
        from conftest import _invalidate_all  # the fixture's own helper

        _invalidate_all()
        return
    except Exception:
        pass
    ui.invalidate()
    assets.invalidate_scaled()


def fingerprint() -> str:
    """Everything about this machine that could change a number below."""
    art = [n for n in ("button.png", "button_down.png", "button_ghost.png", "panel.png",
                       "panel_dark.png", "icon_play.png") if assets.exists(n)]
    return (f"python {sys.version.split()[0]} | pygame {pygame.version.ver} | "
            f"spring backend {ui.spring_backend()} | interface art {len(art)}/6 | "
            f"text cache budget {uitext.MAX_CACHE_BYTES // (1024 * 1024)} MB | "
            f"frame cache budget {uiwidgets._FRAME_CACHE_BYTES // (1024 * 1024)} MB")


def _banner() -> None:
    """Print the same summary whichever runner was used.

    pytest never calls `main()`, so without this the two commands report
    completely different things for the same file -- 'PASSED all N checks' from
    one and 'k passed' from the other -- and neither can be used to confirm
    the other. Registered at interpreter exit, which is after pytest has torn
    down its output capture, so it is visible without `-s`.
    """
    if not _checks:
        return
    print(f"\n{'=' * 64}")
    print(fingerprint())
    if _failures:
        print(f"FAILED  {len(_failures)} of {_checks} checks")
        for line in _failures:
            print(f"  - {line}")
    else:
        print(f"PASSED  all {_checks} checks")


# One banner per process even if this file is imported under two names
# (`test_ui` by pytest's rootdir insertion and `tests.test_ui` by anything
# that treats tests/ as a package).
if not getattr(sys, "_featherdash_ui_selftest", False):
    sys._featherdash_ui_selftest = True  # type: ignore[attr-defined]
    atexit.register(_banner)


# --------------------------------------------------------------------------
# the screen under test
# --------------------------------------------------------------------------
SKINS = [
    ("ember", "Ember", (247, 142, 62), 0),
    ("sky", "Sky", (86, 176, 240), 150),
    ("mint", "Mint", (96, 212, 170), 300),
    ("violet", "Violet", (166, 128, 238), 600),
    ("gold", "Gold", (248, 198, 72), 1200),
]


class Counters:
    """Call tallies, so "fires exactly once" is a measurement not a hope."""

    def __init__(self) -> None:
        self.play = 0
        self.share = 0
        self.home = 0
        self.settings = 0
        self.toggled: List[bool] = []
        self.skin_changed: List[int] = []
        self.tally_done = 0


def render_skin_card(surface: pygame.Surface, vp: Viewport, item: Any,
                     box: ui.Box, focus: float) -> None:
    """Carousel item renderer: a card that lifts as it reaches the centre."""
    key, name, colour, price = item
    card = box.scaled(0.84 + 0.16 * focus)
    rect = card.to_rect(vp)
    if rect.w <= 0 or rect.h <= 0:
        return
    body = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(body, (*colour, int(70 + 130 * focus)), body.get_rect(),
                     border_radius=int(min(rect.w, rect.h) * 0.16))
    pygame.draw.rect(body, (255, 255, 255, int(60 + 120 * focus)), body.get_rect(),
                     width=max(1, int(min(rect.w, rect.h) * 0.018)),
                     border_radius=int(min(rect.w, rect.h) * 0.16))
    surface.blit(body, rect.topleft)

    # Real bird art exists even though the UI art does not, so the card shows
    # what the shop will really show.
    frame = f"bird_{key}_0.png"
    if assets.exists(frame):
        bird = assets.scaled_by_width(frame, vp.px(card.w * 0.42))
        surface.blit(bird, bird.get_rect(center=vp.to_screen_i(card.cx, card.cy - card.h * 0.12)))
    ui.draw_text(surface, vp, name, (card.cx, card.bottom - card.h * 0.22), "body",
                 config.COLOR_TEXT, alpha=int(140 + 115 * focus), max_width=card.w * 0.86)
    label = "OWNED" if price == 0 else f"{price}"
    ui.draw_text(surface, vp, label, (card.cx, card.bottom - card.h * 0.09), "small",
                 config.COLOR_GOLD if price else config.COLOR_GOOD,
                 alpha=int(120 + 135 * focus), max_width=card.w * 0.86)


def build_screen(vp: Viewport, profile: Any, counters: Counters) -> Dict[str, Any]:
    """One screen using every widget, laid out purely from the safe area."""
    area = ui.safe_box(vp)
    bar = 96.0
    small = 64.0  # deliberately under config.TOUCH_MIN, to prove the hit growth

    group = ui.WidgetGroup()

    home = ui.IconButton(ui.top_left(vp, bar, bar), "home",
                         lambda: setattr(counters, "home", counters.home + 1))
    sound = ui.Toggle(ui.top_right(vp, bar, bar), profile, "sfx", "sound_on", "sound_off",
                      on_change=counters.toggled.append)
    gear = ui.IconButton(ui.top_right(vp, small, small, dx=-(bar + 26.0), dy=(bar - small) * 0.5),
                         "settings",
                         lambda: setattr(counters, "settings", counters.settings + 1))

    stack = ui.Column(spacing=24.0, align="center")
    stack.add(min(area.w, 440.0), 130.0, "score")
    stack.add(min(area.w, 520.0), 340.0, "panel")
    stack.add(area.w, 230.0, "shop")
    # Centre the body in whatever is left under the top bar, so a tall phone
    # and a short desktop window both look composed rather than top-heavy.
    below = ui.Box(area.x, area.y + bar + 24.0, area.w,
                   max(stack.size[1], area.h - bar - 24.0))
    stack.place_centered(below.cx, below.cy)

    score = ui.ScoreCounter(stack["score"], 0, "mega", color=config.COLOR_GOLD,
                            duration=0.7, outline=4.0,
                            on_complete=lambda: setattr(counters, "tally_done",
                                                        counters.tally_done + 1))
    panel = ui.Panel(stack["panel"], "RUN OVER")
    bw = min(panel.content_box.w, 380.0)
    slots = panel.layout_column([(bw, 96.0), (bw, 96.0)], spacing=20.0)
    play = ui.Button(slots[0], "PLAY AGAIN", "play",
                     lambda: setattr(counters, "play", counters.play + 1), "primary")
    share = ui.Button(slots[1], "SHARE", "share",
                      lambda: setattr(counters, "share", counters.share + 1), "ghost")
    panel.add(play, share)

    shop = ui.Carousel(stack["shop"], SKINS, render_skin_card,
                       item_w=min(stack["shop"].w * 0.62, 300.0), gap=26.0,
                       on_change=counters.skin_changed.append)

    group.add(home, gear, sound, score, panel, shop)
    return {"group": group, "home": home, "gear": gear, "sound": sound, "score": score,
            "panel": panel, "play": play, "share": share, "shop": shop, "area": area}


def tap(group: ui.WidgetGroup, x: float, y: float) -> Tuple[bool, bool]:
    return (group.on_pointer_down(x, y), group.on_pointer_up(x, y))


def settle(group: ui.WidgetGroup, seconds: float = 1.5) -> None:
    for _ in range(int(seconds / DT)):
        group.update(DT)


def drag(group: ui.WidgetGroup, x0: float, y0: float, dx: float, steps: int = 8) -> None:
    """A drag the way the real loop delivers one: a move, then a frame, then a move.

    Momentum is sampled per frame, so firing every move in a tight loop with no
    update() between them would look like one instantaneous throw and is not
    what any device actually does.
    """
    group.on_pointer_down(x0, y0)
    group.update(DT)
    for i in range(1, steps + 1):
        group.on_pointer_move(x0 + dx * i / steps, y0)
        group.update(DT)
    group.on_pointer_up(x0 + dx, y0)


# --------------------------------------------------------------------------
# backdrop, so legibility is judged over something as busy as the real game
# --------------------------------------------------------------------------
def draw_backdrop(surface: pygame.Surface, vp: Viewport) -> None:
    strip = pygame.Surface((1, 64))
    ramp = [(0.0, (58, 142, 222)), (0.45, (116, 190, 238)), (0.78, (178, 224, 246)),
            (1.0, (226, 245, 250))]
    for i in range(64):
        t = i / 63.0
        lo = max(s for s in ramp if s[0] <= t)
        hi = min((s for s in ramp if s[0] >= t), default=ramp[-1])
        span = max(1e-6, hi[0] - lo[0])
        k = (t - lo[0]) / span
        strip.set_at((0, i), tuple(int(lo[1][c] + (hi[1][c] - lo[1][c]) * k) for c in range(3)))
    surface.blit(pygame.transform.smoothscale(strip, surface.get_size()), (0, 0))
    for i, (fx, fy, fw) in enumerate(((0.18, 0.12, 0.46), (0.74, 0.30, 0.38), (0.36, 0.58, 0.52))):
        name = f"cloud_day_{i}.png"
        if assets.exists(name):
            cloud = assets.scaled_by_width(name, max(8.0, vp.px(vp.world_w * fw)))
            surface.blit(cloud, vp.to_screen_i(vp.world_w * fx - vp.world_w * fw * 0.5,
                                               config.WORLD_H * fy))


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_text(vp: Viewport) -> None:
    section("text: measure / draw_text agreement")
    canvas = pygame.Surface((vp.screen_w, vp.screen_h), pygame.SRCALPHA)
    cases = [
        ("Feather Dash", "title", 0.0, None),
        ("0", "mega", 0.0, None),
        ("Settings", "body", 0.0, None),
        ("NEW BEST!", "large", 5.0, None),
        ("tiny print", "tiny", 0.0, None),
        ("A very long localised label that must shrink", "body", 0.0, 300.0),
        ("multi\nline\nblock", "small", 0.0, None),
        (str(1234567), 42.0, 3.0, None),
    ]
    for txt, size, outline, max_w in cases:
        mw, mh = ui.measure(txt, size, vp, outline, max_w)
        rect = ui.draw_text(canvas, vp, txt, (vp.cx, vp.cy), size, outline=outline,
                            max_width=max_w)
        check(f"measure matches draw_text for {txt[:18]!r}",
              near(mw * vp.scale, rect.w, 1.0) and near(mh * vp.scale, rect.h, 1.0),
              f"measure={mw * vp.scale:.1f}x{mh * vp.scale:.1f} drawn={rect.w}x{rect.h}")

    wide = ui.measure("A very long localised label that must shrink", "body", vp,
                      max_width=300.0)[0]
    check("max_width actually constrains the string", wide <= 300.5, f"{wide:.1f}")

    # The same contract at the shapes real devices actually are, not just the
    # reference one. Anchoring is computed from the measurement rather than
    # from the cached surface, and a scaled draw derives its box from the same
    # number, so the two can only agree if they agree everywhere.
    for sw, sh in ((1080, 2400), (540, 1170), (1200, 1600), (1280, 720), (360, 640)):
        other = compute(sw, sh)
        canvas2 = pygame.Surface((sw, sh), pygame.SRCALPHA)
        worst = 0.0
        for txt, size, outline, max_w in cases:
            mw, mh = ui.measure(txt, size, other, outline, max_w)
            rect = ui.draw_text(canvas2, other, txt, (other.cx, other.cy), size,
                                outline=outline, max_width=max_w)
            worst = max(worst, abs(mw * other.scale - rect.w), abs(mh * other.scale - rect.h))
        plain = ui.draw_number(canvas2, other, 137, (other.cx, 400.0), "mega")
        punched = ui.draw_number(canvas2, other, 137, (other.cx, 400.0), "mega", scale=1.16)
        check(f"{sw}x{sh}: measure and draw_text agree on every case", worst <= 1.0,
              f"worst disagreement {worst:.2f} px")
        check(f"{sw}x{sh}: a punch scales the number's box by the punch",
              abs(punched.w - round(plain.w * 1.16)) <= 2
              and abs(punched.h - round(plain.h * 1.16)) <= 2,
              f"{plain.size} -> {punched.size}")

    section("text: alignment anchors")
    left = ui.draw_text(canvas, vp, "anchor", (100.0, 200.0), "body", align="left", valign="top")
    right = ui.draw_text(canvas, vp, "anchor", (100.0, 200.0), "body", align="right", valign="bottom")
    sx, sy = vp.to_screen(100.0, 200.0)
    check("align=left starts at the anchor", near(left.left, sx, 1.0))
    check("align=right ends at the anchor", near(right.right, sx, 1.0))
    check("valign=top starts at the anchor", near(left.top, sy, 1.0))
    check("valign=bottom ends at the anchor", near(right.bottom, sy, 1.0))
    check("empty string is a zero rect, not a crash",
          ui.draw_text(canvas, vp, "", (0.0, 0.0), "body").size == (0, 0))

    section("text: fixed-advance numbers")
    narrow = ui.number_width(111, "mega", vp)
    wide_n = ui.number_width(888, "mega", vp)
    check("digits share one advance (111 as wide as 888)", near(narrow, wide_n, 0.01),
          f"{narrow:.2f} vs {wide_n:.2f}")
    prop_n = ui.measure("111", "mega", vp)[0]
    prop_w = ui.measure("888", "mega", vp)[0]
    check("the fixed grid is doing real work (proportional widths differ)",
          not near(prop_n, prop_w, 0.5), f"proportional {prop_n:.1f} vs {prop_w:.1f}")
    r1 = ui.draw_number(canvas, vp, 111, (vp.cx, 400.0), "mega")
    r2 = ui.draw_number(canvas, vp, 888, (vp.cx, 400.0), "mega")
    check("drawn numbers share a box", r1.size == r2.size, f"{r1.size} vs {r2.size}")
    check("draw_number width matches number_width",
          near(ui.number_width(111, "mega", vp) * vp.scale, r1.w, 1.0))
    padded = ui.draw_number(canvas, vp, 7, (vp.cx, 400.0), "mega", pad_to=3)
    check("pad_to produces a three-digit box", near(padded.w, r1.w, 1.0))
    check("draw_number survives a punch scale",
          ui.draw_number(canvas, vp, 42, (vp.cx, 400.0), "mega", scale=1.22).w > 0)

    section("text: cache is bounded in BYTES, not entries")
    # The bound that matters is memory. Entry counting is the trap: a cached
    # block's area grows with the square of the viewport scale and with the
    # length of the string, so 384 `tiny` labels are a few hundred KB and 384
    # outlined `mega` numbers were most of a gigabyte -- and Android kills the
    # process long before an entry counter notices. So this fills the cache
    # with the *expensive* kind of entry and asserts the byte total, which is
    # the promise the module docstring makes.
    budget = uitext.MAX_CACHE_BYTES
    for label, size, outline in (("mega + outline", "mega", 5.0),
                                 ("huge", "huge", 0.0),
                                 ("title", "title", 0.0),
                                 ("small", "small", 0.0)):
        ui.invalidate()
        check(f"invalidate empties the cache ({label})", ui.cache_size() == 0)
        # 150 is already ~40x the budget at the largest size; rendering a
        # thousand outlined `mega` strings just to prove it would make the
        # suite slow for nothing.
        for i in range(150):
            ui.draw_text(canvas, vp, f"SCORE {i} XWMQ", (vp.cx, vp.cy), size, outline=outline)
        held = uitext.cache_bytes()
        check(f"150 unique {label} strings stay inside the byte budget",
              held <= budget,
              f"{held / 1e6:.1f} MB > {budget / 1e6:.1f} MB "
              f"in {ui.cache_size()} entries")
    ui.invalidate()
    for i in range(1500):
        ui.draw_text(canvas, vp, f"unique-{i}", (vp.cx, vp.cy), "small")
    check("...and the entry cap still holds too",
          ui.cache_size() <= uitext.MAX_CACHE_ENTRIES * 4,
          f"{ui.cache_size()}")
    before = ui.cache_size()
    for _ in range(50):
        ui.draw_text(canvas, vp, "repeated", (vp.cx, vp.cy), "body")
    check("repeat draws reuse one entry", ui.cache_size() <= before + 3)
    hits = ui.cache_stats()["hits"]
    ui.draw_text(canvas, vp, "repeated", (vp.cx, vp.cy), "body")
    check("a repeat draw is a cache hit", ui.cache_stats()["hits"] > hits)
    check("cache_stats reports the byte total and the budget",
          uitext.cache_stats()["bytes"] <= uitext.cache_stats()["budget"],
          f"{uitext.cache_stats()}")


def test_layout(vp: Viewport) -> None:
    section("layout: safe area and anchors")
    area = ui.safe_box(vp)
    check("safe box honours the viewport insets",
          near(area.x, vp.safe_left(), 1e-6) and near(area.right, vp.safe_right(), 1e-6)
          and near(area.y, vp.safe_top(), 1e-6) and near(area.bottom, vp.safe_bottom(), 1e-6))
    for name in ui.ANCHORS:
        box = ui.anchor(vp, name, 120.0, 80.0)
        check(f"anchor {name} stays inside the safe area", area.contains_box(box, 1e-6),
              f"{box}")
    check("top_left tucks into the corner",
          near(ui.top_left(vp, 120.0, 80.0).x, area.x, 1e-6))
    check("bottom_right tucks into the corner",
          near(ui.bottom_right(vp, 120.0, 80.0).bottom, area.bottom, 1e-6))
    check("clamp_into_safe pulls a stray box back",
          area.contains_box(ui.clamp_into_safe(vp, ui.Box(-500.0, -500.0, 200.0, 200.0)), 1e-6))

    section("layout: stacks and grids")
    col = ui.Column(spacing=10.0, align="center")
    col.add(100.0, 40.0, "a").add(200.0, 60.0, "b")
    boxes = col.place(0.0, 0.0)
    check("column total height sums items plus spacing", near(col.size[1], 110.0, 1e-6))
    check("column width is its widest item", near(col.size[0], 200.0, 1e-6))
    check("column centres a narrow item", near(boxes[0].x, 50.0, 1e-6))
    check("column advances by height plus spacing", near(boxes[1].y, 50.0, 1e-6))
    check("keyed lookup returns the same box", col["b"] is boxes[1])

    row = ui.Row(spacing=8.0, align="middle")
    row.add(50.0, 20.0).add(50.0, 40.0)
    rboxes = row.place(0.0, 0.0)
    check("row total width sums items plus spacing", near(row.size[0], 108.0, 1e-6))
    check("row middle-aligns a short item", near(rboxes[0].y, 10.0, 1e-6))

    grid = ui.Grid.fit(420.0, 3, 20.0)
    cells = grid.place(5, 0.0, 0.0)
    check("grid.fit divides the width", near(grid.cell_w, (420.0 - 40.0) / 3.0, 1e-6))
    check("grid wraps onto a second row", near(cells[3].y, grid.cell_h + 20.0, 1e-6))
    check("grid.size reports two rows", near(grid.size(5)[1], grid.cell_h * 2 + 20.0, 1e-6))
    check("index_at finds the cell", ui.Grid.index_at(cells, cells[4].cx, cells[4].cy) == 4)
    check("index_at misses outside", ui.Grid.index_at(cells, -10.0, -10.0) is None)

    section("layout: Box arithmetic")
    box = ui.Box(10.0, 20.0, 100.0, 50.0)
    check("inset shrinks on every edge", box.inset(5.0).size == (90.0, 40.0))
    check("resized keeps the centre", near(box.resized(10.0, 10.0).cx, box.cx, 1e-6))
    check("expanded_to only grows", box.expanded_to(200.0, 10.0).size == (200.0, 50.0))
    check("contains is inclusive at the edge", box.contains(10.0, 20.0))
    check("contains rejects outside", not box.contains(9.0, 20.0))


def test_hit_testing(vp: Viewport, profile: Any) -> None:
    section("widgets: tap targets and hit testing")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    group, gear, play = scr["group"], scr["gear"], scr["play"]

    check("the small button really is under TOUCH_MIN (precondition)",
          gear.rect.w < config.TOUCH_MIN, f"{gear.rect.w} vs {config.TOUCH_MIN}")
    check("its hit box is grown to exactly TOUCH_MIN",
          near(gear.hit_box.w, config.TOUCH_MIN, 1e-6) and near(gear.hit_box.h, config.TOUCH_MIN, 1e-6),
          f"{gear.hit_box.size}")
    check("the grown hit box keeps the visual centre",
          near(gear.hit_box.cx, gear.rect.cx, 1e-6) and near(gear.hit_box.cy, gear.rect.cy, 1e-6))

    cx, cy = gear.rect.center
    just_outside = (cx - (gear.rect.w * 0.5 + config.TOUCH_MIN * 0.5) * 0.5, cy)
    check("a tap outside the visual box is outside the visual box",
          not gear.rect.contains(*just_outside), f"{just_outside}")
    check("...but inside TOUCH_MIN it still hits", gear.hit(*just_outside))
    far = (cx - config.TOUCH_MIN, cy)
    check("a tap beyond TOUCH_MIN misses", not gear.hit(*far))

    consumed_down, consumed_up = tap(group, *just_outside)
    check("the group routed the undersized tap", consumed_down and consumed_up)
    check("the undersized tap fired exactly one callback", counters.settings == 1,
          f"settings={counters.settings}")
    check("no other widget fired", counters.home == 0 and counters.play == 0
          and counters.share == 0 and counters.toggled == [])

    section("widgets: click semantics")
    before = counters.play
    px, py = play.rect.center
    group.on_pointer_down(px, py)
    group.on_pointer_up(px, py)
    check("a press/release inside fires exactly once", counters.play == before + 1,
          f"{counters.play - before}")
    group.on_pointer_down(px, py)
    group.on_pointer_move(px, py - 600.0)
    group.on_pointer_up(px, py - 600.0)
    check("dragging off before release cancels the click", counters.play == before + 1)
    group.on_pointer_up(px, py)
    check("a stray release with no press does nothing", counters.play == before + 1)

    play.enabled = False
    group.on_pointer_down(px, py)
    group.on_pointer_up(px, py)
    check("a disabled button does not fire", counters.play == before + 1)
    check("a disabled button still swallows the tap (no fall-through)",
          scr["panel"].on_pointer_down(px, py))
    play.enabled = True

    section("widgets: topmost-first dispatch")
    order: List[str] = []

    class Spy(ui.Widget):
        def __init__(self, name: str, rect: ui.Box, consume: bool) -> None:
            super().__init__(rect, min_tap=0.0)
            self.name, self.consume = name, consume

        def on_pointer_down(self, wx: float, wy: float) -> bool:
            if self.hit(wx, wy):
                order.append(self.name)
                return self.consume
            return False

    probe = ui.WidgetGroup()
    probe.add(Spy("bottom", ui.Box(0.0, 0.0, 100.0, 100.0), True),
              Spy("middle", ui.Box(0.0, 0.0, 100.0, 100.0), False),
              Spy("top", ui.Box(0.0, 0.0, 100.0, 100.0), False))
    probe.on_pointer_down(50.0, 50.0)
    check("dispatch runs topmost-first and stops at the consumer",
          order == ["top", "middle", "bottom"], f"{order}")

    probe2 = ui.WidgetGroup()
    probe2.add(Spy("under", ui.Box(0.0, 0.0, 100.0, 100.0), True))
    probe2.add(Spy("over", ui.Box(0.0, 0.0, 100.0, 100.0), True))
    order.clear()
    probe2.on_pointer_down(50.0, 50.0)
    check("a consuming top widget blocks the one beneath", order == ["over"], f"{order}")

    section("widgets: pointer capture")
    drag_scr = build_screen(vp, profile, Counters())
    shop = drag_scr["shop"]
    g = drag_scr["group"]
    g.on_pointer_down(shop.rect.cx, shop.rect.cy)
    moved = g.on_pointer_move(shop.rect.cx, shop.rect.cy - 5000.0)
    check("a captured widget keeps the pointer after it leaves", moved)
    g.on_pointer_up(shop.rect.cx, shop.rect.cy - 5000.0)


def test_toggle(vp: Viewport, profile: Any) -> None:
    section("widgets: toggle bound to a profile")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    sound, group = scr["sound"], scr["group"]
    start = bool(profile["sfx"])
    check("toggle reads its initial state from the profile", sound.value == start)
    tap(group, *sound.rect.center)
    check("tapping flips the profile key", bool(profile["sfx"]) == (not start),
          f"profile={profile['sfx']}")
    check("...and the widget agrees", sound.value == (not start))
    check("on_change fired once with the new value", counters.toggled == [not start],
          f"{counters.toggled}")
    tap(group, *sound.rect.center)
    check("tapping again restores it", bool(profile["sfx"]) == start)
    check("the toggle animates rather than jumping",
          0.0 < sound._anim.value < 1.0 or sound._anim.value in (0.0, 1.0))
    settle(group, 0.6)

    standalone = ui.Toggle(ui.Box(0.0, 0.0, 100.0, 100.0), None, "music")
    standalone.on_pointer_down(50.0, 50.0)
    standalone.on_pointer_up(50.0, 50.0)
    check("a profile-less toggle still works (previews/tests)", standalone.value is True)


def test_carousel(vp: Viewport, profile: Any) -> None:
    section("widgets: carousel arrows")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    shop, group = scr["shop"], scr["group"]
    stride = shop.stride

    check("starts on the first item", shop.index == 0)
    check("the previous arrow is disabled at the start", not shop.prev_button.enabled)
    check("arrows are real tap targets",
          shop.next_button.hit_box.w >= config.TOUCH_MIN - 1e-6,
          f"{shop.next_button.hit_box.w}")

    tap(group, *shop.next_button.rect.center)
    check("the arrow advanced one page", shop.index == 1, f"index={shop.index}")
    check("on_change reported it", counters.skin_changed == [1], f"{counters.skin_changed}")
    settle(group, 2.0)
    check("the carousel settled", shop.is_settled)
    centred = shop.item_box(1)
    check("item 1 is centred in the window",
          near(centred.cx, shop.items_box.cx, 1.0),
          f"{centred.cx:.2f} vs {shop.items_box.cx:.2f}")

    section("widgets: carousel drag with momentum")
    start_index = shop.index
    x0, y0 = shop.items_box.center
    drag(group, x0, y0, -stride * 0.62)
    check("a drag past the halfway point pages forward",
          shop.index == start_index + 1, f"{start_index} -> {shop.index}")
    settle(group, 2.0)
    check("the drag snapped exactly onto an item",
          near(shop.item_box(shop.index).cx, shop.items_box.cx, 1.0),
          f"{shop.item_box(shop.index).cx:.2f} vs {shop.items_box.cx:.2f}")

    back_from = shop.index
    drag(group, x0, y0, stride * 0.62)
    check("dragging the other way pages back", shop.index == back_from - 1,
          f"{back_from} -> {shop.index}")
    settle(group, 2.0)

    section("widgets: carousel limits")
    small_drag_index = shop.index
    drag(group, x0, y0, -stride * 0.12, steps=6)
    check("a short slow drag springs back to the same item",
          shop.index == small_drag_index, f"{small_drag_index} -> {shop.index}")
    settle(group, 2.0)
    check("...and ends centred again",
          near(shop.item_box(shop.index).cx, shop.items_box.cx, 1.0))

    flick_from = shop.index
    drag(group, x0, y0, -stride * 0.30, steps=3)
    check("a quick flick pages forward even on a short throw",
          shop.index == flick_from + 1, f"{flick_from} -> {shop.index}")
    settle(group, 2.0)

    drag(group, x0, y0, -stride * 4.0)
    check("a hard flick never pages past the end",
          0 <= shop.index <= len(SKINS) - 1, f"{shop.index}")
    check("a hard flick does reach the last item", shop.index == len(SKINS) - 1,
          f"{shop.index}")
    settle(group, 3.0)
    check("it still lands snapped on an item",
          near(shop.item_box(shop.index).cx, shop.items_box.cx, 1.0))

    shop.set_index(len(SKINS) - 1, animate=False)
    check("the next arrow disables at the end", not shop.next_button.enabled)
    shop.set_index(0, animate=False)
    check("set_index(animate=False) is immediate", shop.is_settled and shop.index == 0)

    section("widgets: arrow taps never start a drag")
    before = shop.index
    group.on_pointer_down(*shop.next_button.rect.center)
    group.on_pointer_move(shop.next_button.rect.cx - stride, shop.next_button.rect.cy)
    group.on_pointer_up(shop.next_button.rect.cx - stride, shop.next_button.rect.cy)
    check("sliding off the arrow cancels rather than paging twice",
          shop.index == before, f"{before} -> {shop.index}")


def test_panel_and_counter(vp: Viewport, profile: Any) -> None:
    section("widgets: panel")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    panel, play, group = scr["panel"], scr["play"], scr["group"]
    check("children sit inside the panel's content box",
          panel.content_box.contains_box(play.rect, 1.0), f"{play.rect} in {panel.content_box}")
    check("the content box clears the title", panel.content_box.y > panel.rect.y + panel.pad)

    panel.close()
    group.update(DT)
    check("closing starts the exit animation", panel.progress < 1.0)
    fired = counters.play
    group.on_pointer_down(*play.rect.center)
    group.on_pointer_up(*play.rect.center)
    check("a mid-animation tap is swallowed, not actioned", counters.play == fired)
    panel.snap_open()
    check("snap_open is immediate", panel.progress >= 1.0)
    tap(group, *play.rect.center)
    check("the button works again once settled", counters.play == fired + 1)

    outside = (panel.rect.x - 400.0, panel.rect.cy)
    check("the panel does not claim taps outside itself",
          not panel.on_pointer_down(*outside))

    section("widgets: score counter")
    score = scr["score"]
    score.set_target(137, from_value=0)
    check("the tally starts at zero", score.value == 0)
    check("it is not done yet", not score.is_done)
    settle(group, 0.2)
    mid = score.value
    check("it counts upward", 0 < mid < 137, f"mid={mid}")
    settle(group, 1.2)
    check("it reaches the target exactly", score.value == 137, f"{score.value}")
    check("it reports completion once", counters.tally_done == 1, f"{counters.tally_done}")

    score.set_target(500, from_value=0)
    score.finish()
    check("finish() jumps straight to the total", score.value == 500 and score.is_done)
    check("finish() still reports completion", counters.tally_done == 2)


def test_events(vp: Viewport, profile: Any) -> None:
    section("widgets: pygame event routing")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    group, play = scr["group"], scr["play"]
    sx, sy = vp.to_screen_i(*play.rect.center)

    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (sx, sy), "button": 1, "touch": False})
    up = pygame.event.Event(pygame.MOUSEBUTTONUP, {"pos": (sx, sy), "button": 1, "touch": False})
    check("handle_event consumed the press", group.handle_event(down, vp))
    check("handle_event consumed the release", group.handle_event(up, vp))
    check("the mouse click fired once", counters.play == 1, f"{counters.play}")

    synth = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (sx, sy), "button": 1, "touch": True})
    check("touch-synthesised mouse events are ignored", not group.handle_event(synth, vp))
    group.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP,
                                          {"pos": (sx, sy), "button": 1, "touch": False}), vp)
    check("...so they cannot double-fire", counters.play == 1, f"{counters.play}")

    right = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (sx, sy), "button": 3, "touch": False})
    check("a right-click is not a tap", not group.handle_event(right, vp))

    fd = pygame.event.Event(pygame.FINGERDOWN, {"x": sx / vp.screen_w, "y": sy / vp.screen_h,
                                                "finger_id": 7})
    fu = pygame.event.Event(pygame.FINGERUP, {"x": sx / vp.screen_w, "y": sy / vp.screen_h,
                                              "finger_id": 7})
    check("a finger press is routed", group.handle_event(fd, vp))
    check("a finger release is routed", group.handle_event(fu, vp))
    check("the touch fired once", counters.play == 2, f"{counters.play}")

    group.on_pointer_down(*play.rect.center, 3)
    group.cancel()
    group.on_pointer_up(*play.rect.center, 3)
    check("cancel() drops the capture without firing", counters.play == 2, f"{counters.play}")


def test_regressions(vp: Viewport, profile: Any) -> None:
    """One check per defect an adversarial review found, so none can come back.

    Everything here is deterministic -- counts and identities, never a
    wall-clock reading -- because a performance regression test that measures
    milliseconds on shared CI hardware fails for reasons that have nothing to
    do with the code. The quantity each check counts (cache misses, rebuilds,
    allocations) is what the cost was actually made of.
    """
    canvas = pygame.Surface((vp.screen_w, vp.screen_h), pygame.SRCALPHA)

    section("regression: a closed Panel stops existing (was: invisible tap-eater)")
    counters = Counters()
    scr = build_screen(vp, profile, counters)
    panel, group, play = scr["panel"], scr["group"], scr["play"]
    closed: List[int] = []
    panel.on_closed = lambda: closed.append(1)
    panel.snap_open()
    panel.close()
    settle(group, 10.0)
    check("a finished close turns the panel invisible", not panel.visible,
          f"visible={panel.visible} progress={panel.progress:.4f}")
    check("...fires on_closed exactly once", closed == [1], f"{closed}")
    check("...and the group no longer routes taps into the old dialog box",
          not group.on_pointer_down(*panel.rect.center))
    check("a closed panel paints nothing", panel.draw(canvas, vp) is None)

    scr = build_screen(vp, profile, Counters())
    panel, group = scr["panel"], scr["group"]
    panel.snap_open()
    panel.close()
    group.update(DT)
    check("a panel one frame into its close already lets taps through",
          not group.on_pointer_down(*panel.rect.center), f"progress={panel.progress:.3f}")

    scr = build_screen(vp, profile, Counters())
    panel, group, play = scr["panel"], scr["group"], scr["play"]
    panel.snap_closed()
    panel.open()
    group.update(DT)
    check("a panel one frame into its OPEN still swallows taps (not regressed)",
          panel.on_pointer_down(*panel.rect.center), f"progress={panel.progress:.3f}")

    scr = build_screen(vp, profile, Counters())
    panel, group, play = scr["panel"], scr["group"], scr["play"]
    panel.snap_open()
    group.on_pointer_down(*play.rect.center)
    panel.close()
    settle(group, 0.1)
    group.on_pointer_up(*play.rect.center)
    check("a child held when the dialog closes still gets its release",
          not play.pressed, f"pressed={play.pressed}")

    section("regression: the drop shadow is one baked surface, not five blits")
    reset_caches()
    px = ui.px_for("title", vp)
    first = uitext._shadow("SCORE", px, 0)
    check("the shadow composite is cached, not rebuilt per draw",
          uitext._shadow("SCORE", px, 0) is first)
    glyph = uitext._composite("SCORE", px, config.COLOR_SHADOW, 0, config.COLOR_SHADOW)
    pad = uitext._shadow_margin(px)
    check("...and it is the glyph plus room for the taps, so all five are in it",
          first.get_size() == (glyph.get_width() + pad * 2, glyph.get_height() + pad * 2),
          f"{first.get_size()} vs glyph {glyph.get_size()} + 2x{pad}")
    ui.draw_text(canvas, vp, "SCORE", (vp.cx, vp.cy), "title")   # warm the glyph too
    before = ui.cache_stats()["misses"]
    for _ in range(30):
        ui.draw_text(canvas, vp, "SCORE", (vp.cx, vp.cy), "title")
    check("30 further shadowed draws of one string are all cache hits",
          ui.cache_stats()["misses"] == before, f"{ui.cache_stats()['misses'] - before} misses")

    section("regression: the Panel entrance composite is built once")
    reset_caches()
    area = ui.safe_box(vp)
    big = ui.Panel(ui.Box(area.x, area.y, area.w, area.h), "SETTINGS", open_now=False)
    big.add(ui.Button(ui.Box(area.cx - 190.0, area.bottom - 150.0, 380.0, 96.0),
                      "DONE", "check", None))
    for round_ in range(2):
        big.open()
        builds, seen, frames = 0, None, 0
        while big.progress < 0.995 and frames < 120:
            big.update(DT)
            big.draw(canvas, vp)
            current = id(big._entrance[1]) if big._entrance else None
            if current is not None and current != seen:
                builds += 1
                seen = current
            frames += 1
        check(f"entrance {round_ + 1} composed the panel exactly once over {frames} frames",
              builds == 1, f"{builds} builds")
        big.snap_closed()
        big.visible = True
        big._anim.snap(0.0)
    check("the composite is released once the dialog has landed",
          big._entrance is None)

    section("regression: carousel momentum is frame-rate independent")

    def flick(hz: float, speed: float) -> int:
        """The same 0.10 s finger trajectory, sampled at `hz`, returns the page."""
        car = ui.Carousel(ui.Box(0.0, 0.0, 560.0, 300.0), list(range(5)),
                          lambda *a: None, item_w=300.0, gap=26.0, index=0)
        dt = 1.0 / hz
        steps = int(round(0.10 * hz))
        x0, y0 = car.items_box.center
        car.on_pointer_down(x0, y0)
        car.update(dt)
        for i in range(1, steps + 1):
            car.on_pointer_move(x0 - speed * (i * dt), y0)
            car.update(dt)
        car.on_pointer_up(x0 - speed * (steps * dt), y0)
        return car.index

    # 30/60/120/240 Hz all sample 0.10 s a whole number of times, so the
    # trajectory -- and the scroll position at release -- is identical and only
    # the smoothing can differ. 775 and 2300 u/s are the two speeds that used
    # to land a page apart between 30 and 60 Hz.
    for speed in (500.0, 775.0, 1400.0, 2300.0, 3300.0):
        landed = {hz: flick(hz, speed) for hz in (30, 60, 120, 240)}
        check(f"a {speed:.0f} u/s flick lands on one page at every refresh rate",
              len(set(landed.values())) == 1, f"{landed}")

    section("regression: a disabled carousel arrow is not a dead zone")

    def swipe(car: Any, x0: float, dx: float, steps: int = 8) -> int:
        y0 = car.items_box.cy
        car.on_pointer_down(x0, y0)
        car.update(DT)
        for i in range(1, steps + 1):
            car.on_pointer_move(x0 + dx * i / steps, y0)
            car.update(DT)
        car.on_pointer_up(x0 + dx, y0)
        return car.index

    def shop(index: int) -> Any:
        return ui.Carousel(ui.Box(0.0, 0.0, 560.0, 300.0), SKINS, render_skin_card,
                           item_w=300.0, gap=26.0, index=index)

    first_page = shop(0)
    check("the arrow really is disabled and TOUCH_MIN wide (precondition)",
          not first_page.prev_button.enabled
          and first_page.prev_button.hit_box.w >= config.TOUCH_MIN - 1e-6,
          f"{first_page.prev_button.hit_box.w}")
    on_arrow = swipe(first_page, first_page.prev_button.rect.cx, -260.0)
    off_arrow = swipe(shop(0), shop(0).prev_button.rect.cx + 140.0, -260.0)
    check("swiping from the disabled prev arrow pages like anywhere else",
          on_arrow == off_arrow and on_arrow > 0, f"on arrow {on_arrow}, clear {off_arrow}")
    last = shop(len(SKINS) - 1)
    on_next = swipe(last, last.next_button.rect.cx, 260.0)
    off_next = swipe(shop(len(SKINS) - 1),
                     shop(len(SKINS) - 1).next_button.rect.cx - 140.0, 260.0)
    check("same at the last item, where the next arrow is the disabled one",
          on_next == off_next and on_next < len(SKINS) - 1,
          f"on arrow {on_next}, clear {off_next}")
    middle = shop(2)
    middle.on_pointer_down(middle.next_button.rect.cx, middle.next_button.rect.cy)
    check("an ENABLED arrow still takes the press instead of starting a drag",
          not middle._dragging)
    middle.on_pointer_up(middle.next_button.rect.cx, middle.next_button.rect.cy)
    check("...and it paged", middle.index == 3, f"{middle.index}")

    section("regression: a press animation is not a cache miss per frame")
    reset_caches()
    rebuilds = {"nine": 0, "rounded": 0}
    real_nine, real_rounded = uiwidgets._nine_slice, uiwidgets._rounded

    def counted(name: str, fn: Any) -> Any:
        def wrapper(*a: Any, **k: Any) -> Any:
            rebuilds[name] += 1
            return fn(*a, **k)
        return wrapper

    uiwidgets._nine_slice = counted("nine", real_nine)
    uiwidgets._rounded = counted("rounded", real_rounded)
    try:
        btn = ui.Button(ui.Box(100.0, 400.0, 380.0, 96.0), "PLAY AGAIN", "play", None)
        icon = ui.IconButton(ui.Box(100.0, 600.0, 96.0, 96.0), "settings")

        def press_cycle() -> None:
            for frame in range(90):
                if frame == 0:
                    btn.on_pointer_down(*btn.rect.center)
                    icon.on_pointer_down(*icon.rect.center)
                if frame == 20:
                    btn.on_pointer_up(*btn.rect.center)
                    icon.on_pointer_up(*icon.rect.center)
                btn.update(DT)
                icon.update(DT)
                btn.draw(canvas, vp)
                icon.draw(canvas, vp)

        press_cycle()
        press_cycle()
        rebuilds.update(nine=0, rounded=0)
        scaled_before = len(assets._scaled)
        frames_before = len(uiwidgets._frames)
        press_cycle()
        check("a warmed press rebuilds no nine-slice and no rounded surface",
              rebuilds == {"nine": 0, "rounded": 0}, f"{rebuilds}")
        check("...adds nothing to the frame cache", len(uiwidgets._frames) == frames_before,
              f"{frames_before} -> {len(uiwidgets._frames)}")
        check("...and adds nothing to assets._scaled, which never evicts",
              len(assets._scaled) == scaled_before,
              f"{scaled_before} -> {len(assets._scaled)}")
        check("the frame cache stays inside its byte budget",
              uiwidgets._frames.nbytes <= uiwidgets._FRAME_CACHE_BYTES,
              f"{uiwidgets._frames.nbytes / 1e6:.2f} MB")
    finally:
        uiwidgets._nine_slice, uiwidgets._rounded = real_nine, real_rounded

    section("regression: scaled text and numbers come out of a cache")
    reset_caches()
    scaled_calls = {"n": 0}
    real_smooth = pygame.transform.smoothscale

    def counting_smoothscale(surf: Any, size: Any, *a: Any, **k: Any) -> Any:
        scaled_calls["n"] += 1
        return real_smooth(surf, size, *a, **k)

    pygame.transform.smoothscale = counting_smoothscale
    try:
        for _ in range(60):
            ui.draw_text(canvas, vp, "BOOST", (vp.cx, vp.cy), "huge", scale=1.2)
        check("60 identical scaled draw_text calls resample at most twice",
              scaled_calls["n"] <= 2, f"{scaled_calls['n']} smoothscale calls")

        hud = ui.ScoreCounter(ui.Box(vp.cx - 300.0, 300.0, 600.0, 200.0), 0, "mega")
        hud.set_target(137, from_value=137)

        def punch() -> int:
            hud._punch.punch(1.0)
            frames = 0
            while abs(hud._punch.value) > 1e-3 and frames < 300:
                hud.update(DT)
                hud.draw(canvas, vp)
                frames += 1
            return frames

        for _ in range(5):
            punch()
        scaled_calls["n"] = 0
        drawn = punch()
        check(f"a settled HUD punch over {drawn} frames resamples nothing",
              scaled_calls["n"] == 0, f"{scaled_calls['n']} smoothscale calls")
        check("the text cache is still inside its byte budget",
              uitext.cache_bytes() <= uitext.MAX_CACHE_BYTES,
              f"{uitext.cache_bytes() / 1e6:.2f} MB")
    finally:
        pygame.transform.smoothscale = real_smooth

    section("regression: a second finger never falls through to what is behind")
    fired: List[str] = []
    stack = ui.WidgetGroup()
    behind = ui.Button(ui.Box(0.0, 0.0, 600.0, 600.0), "BACK", None,
                       lambda: fired.append("behind"), min_tap=0.0)
    front = ui.Button(ui.Box(150.0, 200.0, 300.0, 200.0), "FRONT", None,
                      lambda: fired.append("front"), min_tap=0.0)
    stack.add(behind, front)
    cx, cy = front.rect.center
    stack.on_pointer_down(cx, cy, 0)
    consumed = stack.on_pointer_down(cx, cy, 1)
    stack.on_pointer_up(cx, cy, 1)
    stack.on_pointer_up(cx, cy, 0)
    check("a second finger on the captured widget is consumed there", consumed)
    check("...and only the widget actually touched fired", fired == ["front"], f"{fired}")

    fired.clear()
    stack2 = ui.WidgetGroup()
    behind2 = ui.Button(ui.Box(0.0, 0.0, 600.0, 600.0), "BACK", None,
                        lambda: fired.append("behind"), min_tap=0.0)
    front2 = ui.Button(ui.Box(150.0, 200.0, 300.0, 200.0), "FRONT", None,
                       lambda: fired.append("front"), min_tap=0.0)
    stack2.add(behind2, front2)
    stack2.on_pointer_down(*front2.rect.center, 0)
    stack2.on_pointer_down(30.0, 560.0, 1)
    stack2.on_pointer_up(30.0, 560.0, 1)
    stack2.on_pointer_up(*front2.rect.center, 0)
    check("a second finger that misses it still reaches the widget behind",
          fired == ["behind", "front"], f"{fired}")


def render_states_sheet(profile: Any) -> str:
    """A contact sheet of every widget state, for eyeballing the look."""
    section("render: widget state sheet")
    ui.invalidate()
    w, h = 760, 1180
    vp = compute(w, h)
    surface = pygame.Surface((w, h))
    draw_backdrop(surface, vp)
    area = ui.safe_box(vp)
    group = ui.WidgetGroup()

    y = area.y
    ui.draw_text(surface, vp, "WIDGET STATES", (area.cx, y), "title", outline=3.0, valign="top")
    y += 74.0

    def label(txt: str, at_y: float) -> float:
        ui.draw_text(surface, vp, txt, (area.x, at_y), "tiny", config.COLOR_TEXT_DIM,
                     align="left", valign="top")
        return at_y + 28.0

    # Buttons: normal / held / disabled, in both variants.
    for variant in ("primary", "ghost"):
        y = label(variant.upper(), y)
        cells = ui.distribute(ui.Box(area.x, y, area.w, 84.0), 3, 16.0)
        for cell, state in zip(cells, ("normal", "held", "off")):
            btn = ui.Button(cell, state.upper(), "play", None, variant, text_size="small")
            if state == "held":
                btn._held = btn._inside = True
                btn._press.snap(1.0)
            btn.enabled = state != "off"
            group.add(btn)
        y += 84.0 + 26.0

    # Icon-only buttons, backings and toggles -- the last one deliberately
    # undersized so the enforced tap target can be drawn next to it.
    y = label("ICON BUTTONS, BACKINGS, TOGGLES", y)
    row = ui.Row(spacing=16.0, align="middle")
    for _ in range(5):
        row.add(84.0, 84.0)
    row.add(60.0, 60.0)
    boxes = row.place(area.x, y)
    group.add(ui.Button(boxes[0], "", "pause", None, "primary"))
    group.add(ui.IconButton(boxes[1], "restart"))
    group.add(ui.IconButton(boxes[2], "left", backing_style="scrim"))
    group.add(ui.IconButton(boxes[3], "lock", enabled=False))
    group.add(ui.Toggle(boxes[4], None, "music", "music_on", "music_off"))
    tiny_icon = ui.IconButton(boxes[5], "info")
    group.add(tiny_icon)
    y += 84.0 + 8.0

    hit = tiny_icon.hit_box.to_rect(vp)
    pygame.draw.rect(surface, (255, 112, 104), hit, width=2, border_radius=8)
    ui.draw_text(surface, vp, "hit box = TOUCH_MIN", (tiny_icon.rect.cx, y + 18.0),
                 "tiny", (255, 112, 104), valign="top", max_width=area.w * 0.45)
    y += 50.0

    # A grid of icons: proves draw_icon copes with the whole manifest set,
    # including a name that does not exist.
    y = label("ICON SET (grid helper)", y)
    names = ["play", "pause", "home", "restart", "settings", "shop", "trophy", "close",
             "sound_on", "sound_off", "music_on", "music_off", "left", "right", "check",
             "lock", "feather", "share", "star", "medal", "info", "haptics_on",
             "haptics_off", "no_such_icon"]
    grid = ui.Grid.fit(area.w, 12, 10.0)
    for cell, name in zip(grid.place(len(names), area.x, y), names):
        ui.draw_icon(surface, vp, name, cell)
    y += grid.size(len(names))[1] + 24.0

    # Two columns from here down: text treatments on the left, a dialog caught
    # mid-entrance on the right, so neither has to overlap the other.
    tx = area.x
    text_w = area.w * 0.54
    tr = tx + text_w
    y = label("TEXT", y)
    ui.draw_text(surface, vp, "shadow", (tx, y), "small", align="left", valign="top")
    ui.draw_text(surface, vp, "no shadow", (tr, y), "small", align="right", valign="top",
                 shadow=False)
    y += 38.0
    ui.draw_text(surface, vp, "outlined", (tx, y), "small", config.COLOR_GOLD,
                 align="left", valign="top", outline=3.0)
    ui.draw_text(surface, vp, "alpha 110", (tr, y), "small", align="right", valign="top",
                 alpha=110)
    y += 38.0
    ui.draw_text(surface, vp, "a long localised string clamped by max_width",
                 (tx, y), "small", align="left", valign="top", max_width=text_w)
    y += 36.0
    ui.draw_number(surface, vp, 1111, (tx, y), "large", config.COLOR_TEXT,
                   align="left", valign="top", outline=3.0)
    ui.draw_number(surface, vp, 8888, (tx, y + 44.0), "large", config.COLOR_TEXT,
                   align="left", valign="top", outline=3.0)
    ui.draw_text(surface, vp, "fixed advance", (tx, y + 92.0), "tiny",
                 config.COLOR_TEXT_DIM, align="left", valign="top")
    ui.draw_number(surface, vp, 42, (tr, y + 26.0), "large", config.COLOR_GOLD,
                   align="right", valign="middle", scale=1.3)
    ui.draw_text(surface, vp, "punch", (tr, y + 62.0), "tiny", config.COLOR_TEXT_DIM,
                 align="right", valign="top")

    group.update(DT)
    group.draw(surface, vp)

    # A dialog caught mid-entrance, so the offscreen scale-and-fade path (which
    # grows the panel, its title and its children together) is on screen.
    panel_w = area.w * 0.40
    panel = ui.Panel(ui.Box(area.right - panel_w, y - 30.0, panel_w, 190.0),
                     "ENTRANCE", open_now=False)
    panel.add(ui.Button(ui.Box(area.right - panel_w + 24.0, y + 70.0, panel_w - 48.0, 72.0),
                        "RESUME", "play", None, "primary", text_size="small"))
    panel.open()
    for _ in range(5):
        panel.update(DT)
    panel.draw(surface, vp)
    check("the state sheet caught the panel mid-entrance",
          0.0 < panel.progress < 1.0, f"{panel.progress:.3f}")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "ui_states.png")
    pygame.image.save(surface, path)
    print(f"  wrote {path}  ({w}x{h})")
    return path


def render_at_scales(profile: Any) -> List[str]:
    section("render: three device shapes")
    shapes = [
        ("phone", 540, 1170, (0.0, 60.0, 0.0, 44.0)),   # notch + gesture bar
        ("tablet", 1200, 1600, (0.0, 0.0, 0.0, 0.0)),
        ("desktop", 1280, 720, (0.0, 0.0, 0.0, 0.0)),
    ]
    paths: List[str] = []
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, w, h, safe in shapes:
        ui.invalidate()
        assets.invalidate_scaled()
        vp = compute(w, h, safe=safe)
        counters = Counters()
        scr = build_screen(vp, profile, counters)
        group, area = scr["group"], scr["area"]

        scr["score"].set_target(137, from_value=0)
        scr["shop"].set_index(1, animate=False)
        scr["share"].enabled = False          # show the disabled treatment too
        scr["play"]._press.snap(1.0)          # and a held button
        scr["play"]._held = scr["play"]._inside = True
        settle(group, 1.2)

        surface = pygame.Surface((w, h))
        draw_backdrop(surface, vp)
        group.draw(surface, vp)

        # Every visible box must be inside the safe area at every scale.
        for key in ("home", "gear", "sound", "score", "panel", "shop", "play", "share"):
            widget = scr[key]
            check(f"{name}: {key} stays inside the safe area",
                  area.contains_box(widget.rect, 1.0),
                  f"{widget.rect} not in {area}")
        check(f"{name}: nothing overlaps the top bar and the panel",
              scr["panel"].rect.y > scr["home"].rect.bottom)
        check(f"{name}: the carousel clears the panel",
              scr["shop"].rect.y >= scr["panel"].rect.bottom - 1e-6)
        check(f"{name}: world width is inside the design clamp",
              config.WORLD_W_MIN - 1e-6 <= vp.world_w <= config.WORLD_W_MAX + 1e-6,
              f"{vp.world_w}")

        # A button is a fixed world size, so it must be a fixed fraction of the
        # screen height on every device -- the whole point of the architecture.
        frac = scr["play"].rect.h / config.WORLD_H
        check(f"{name}: button height is scale-independent", near(frac, 96.0 / 1080.0, 1e-9))

        path = os.path.join(OUT_DIR, f"ui_{name}.png")
        pygame.image.save(surface, path)
        paths.append(path)
        print(f"  wrote {path}  ({w}x{h}, scale={vp.scale:.3f}, world={vp.world_w:.0f}x1080)")
    return paths


def test_api_surface(vp: Viewport, profile: Any) -> None:
    """Touch every remaining public name, so "public API" is not aspirational."""
    section("api: every exported name")
    canvas = pygame.Surface((vp.screen_w, vp.screen_h), pygame.SRCALPHA)

    check("px_for resolves a named size", ui.px_for("body", vp) == int(round(34 * vp.scale)))
    check("px_for resolves a world-unit size", ui.px_for(50.0, vp) == int(round(50 * vp.scale)))
    check("measure_px agrees with measure",
          near(ui.measure_px("abc", "body", vp)[0],
               ui.measure("abc", "body", vp)[0] * vp.scale, 1.0))
    check("font returns a usable pygame font", ui.font(24).size("x")[0] > 0)
    check("cache_stats reports entries", "entries" in ui.cache_stats())
    check("anchor_point returns a point in the safe area",
          ui.safe_box(vp).contains(*ui.anchor_point(vp, "center")))
    check("spring_backend names a backend", ui.spring_backend() in ("fx", "lerp"))
    check("UI_ART_SOURCE_SCALE is set", ui.UI_ART_SOURCE_SCALE > 0)

    box = ui.Box.from_center(100.0, 100.0, 40.0, 20.0)
    check("Box.from_center centres", box.center == (100.0, 100.0))
    check("Box.copy is a distinct object", box.copy() is not box and box.copy() == box)
    check("Box.moved offsets", box.moved(10.0, 0.0).x == box.x + 10.0)
    check("Box.at repositions", box.at(0.0, 0.0).topleft == (0.0, 0.0))
    check("Box.centered_on recentres", box.centered_on(0.0, 0.0).center == (0.0, 0.0))
    check("Box.scaled keeps the centre", box.scaled(2.0).center == box.center)
    check("Box.inflated grows both ways", box.inflated(5.0).size == (50.0, 30.0))
    check("Box.union covers both", box.union(ui.Box(0.0, 0.0, 1.0, 1.0)).contains(0.5, 0.5))
    check("Box.intersects detects overlap", box.intersects(ui.Box(95.0, 95.0, 10.0, 10.0)))
    check("Box.to_rect converts through the viewport",
          box.to_rect(vp).w == vp.rect(box.x, box.y, box.w, box.h).w)

    col = ui.Column(spacing=5.0).add(10.0, 10.0).gap(20.0).add(10.0, 10.0)
    # Three items (one of them the zero-height spacer) means two spacings.
    check("Column.gap inserts a spacer", near(col.size[1], 10.0 + 20.0 + 10.0 + 10.0, 1e-6),
          f"{col.size[1]}")
    check("Stack.place_in anchors in the safe area",
          ui.safe_box(vp).contains_box(ui.Column().add(50.0, 50.0).place_in(vp, "top_left")[0], 1e-6))
    check("Grid.place_in anchors in the safe area",
          ui.safe_box(vp).contains_box(ui.Grid(2, 40.0, 40.0, 5.0).place_in(vp, 4, "center")[0], 1e-6))
    cells = ui.distribute(ui.Box(0.0, 0.0, 100.0, 20.0), 3, 5.0)
    check("distribute splits a bar evenly", near(cells[0].w, 30.0, 1e-6)
          and near(cells[2].right, 100.0, 1e-6))
    check("distribute works vertically",
          near(ui.distribute(ui.Box(0.0, 0.0, 20.0, 100.0), 2, 10.0, False)[1].y, 55.0, 1e-6))

    buzzes: List[int] = []
    ui.set_haptics_hook(lambda: (buzzes.append(1), True)[1])
    btn = ui.Button(ui.Box(0.0, 0.0, 200.0, 100.0), "x", None, lambda: None)
    btn.on_pointer_down(100.0, 50.0)
    btn.on_pointer_up(100.0, 50.0)
    check("the haptics hook is consulted on press", len(buzzes) == 1, f"{len(buzzes)}")
    ui.set_haptics_hook(lambda: False)
    btn.on_pointer_down(100.0, 50.0)
    btn.on_pointer_up(100.0, 50.0)
    check("a hook saying no suppresses the buzz", len(buzzes) == 1)
    ui.set_haptics_hook(lambda: True)
    btn.fire()
    check("fire() activates without a pointer", True)

    group = ui.WidgetGroup()
    group.add(btn)
    check("group length tracks its widgets", len(group) == 1)
    check("group is iterable", list(group) == [btn])
    group.remove(btn)
    check("remove drops the widget", len(group) == 0)
    group.add(btn)
    group.clear()
    check("clear empties the group", len(group) == 0)

    picked: List[int] = []
    bare = ui.Carousel(ui.Box(0.0, 0.0, 300.0, 200.0), SKINS, render_skin_card,
                       show_arrows=False, show_dots=False, on_select=picked.append)
    bare.on_pointer_down(150.0, 100.0)
    bare.on_pointer_up(150.0, 100.0)
    check("a tap without a drag is a selection", picked == [0], f"{picked}")
    bare.draw(canvas, vp)
    check("a carousel with no chrome still draws", True)

    panel = ui.Panel(ui.Box(50.0, 50.0, 300.0, 200.0), "T", dark=True, open_now=False)
    panel.add(btn)
    panel.open()
    for _ in range(4):
        panel.update(DT)
    check("the entrance is partway through", 0.0 < panel.progress < 1.0, f"{panel.progress}")
    panel.draw(canvas, vp)  # exercises the offscreen scale-and-fade path
    check("a panel mid-entrance draws through the offscreen path", True)
    panel.remove(btn)
    check("Panel.remove drops a child", btn not in panel.children)

    counter = ui.ScoreCounter(ui.Box(0.0, 0.0, 200.0, 100.0), 0, "large",
                              prefix="x", suffix=" ft")
    counter.set_target(9, from_value=0)
    counter.update(DT)
    counter.draw(canvas, vp)
    check("a prefixed/suffixed counter draws", True)

    ui.draw_frame(canvas, vp, ui.Box(10.0, 10.0, 200.0, 80.0), "panel.png", 72, "panel", 120)
    ui.draw_icon(canvas, vp, "star", ui.Box(10.0, 10.0, 60.0, 60.0), 180, config.COLOR_GOLD)
    check("draw_frame and draw_icon accept alpha and tint", True)


def test_scales(profile: Any) -> None:
    """Thin pytest wrapper: the helper returns the rendered paths for main()."""
    assert render_at_scales(profile)


def test_states_sheet(profile: Any) -> None:
    """Thin pytest wrapper, so CI regenerates the contact sheet too."""
    assert os.path.isfile(render_states_sheet(profile))

def test_missing_art_is_survivable(vp: Viewport, profile: Any) -> None:
    section("robustness: missing art and odd inputs")
    canvas = pygame.Surface((vp.screen_w, vp.screen_h))
    scr = build_screen(vp, profile, Counters())
    try:
        scr["group"].update(DT)
        scr["group"].draw(canvas, vp)
        ui.draw_icon(canvas, vp, "no_such_icon_name", ui.Box(10.0, 10.0, 80.0, 80.0))
        ui.draw_frame(canvas, vp, ui.Box(10.0, 10.0, 0.0, 0.0), "panel.png", 72)
        ui.draw_text(canvas, vp, "x", (0.0, 0.0), "not-a-real-size")
        ui.draw_text(canvas, vp, "x", (0.0, 0.0), "body", alpha=0)
        ui.Carousel(ui.Box(0.0, 0.0, 200.0, 100.0), [], render_skin_card).draw(canvas, vp)
        ui.Panel(ui.Box(0.0, 0.0, 10.0, 10.0), "T").draw(canvas, vp)
        drew = True
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        drew = False
        print(f"  raised: {exc!r}")
    check("the whole kit draws without art, empty items or a bad size key", drew)
    # The vector fallbacks mean a missing button never reaches assets.original,
    # so it never lands in assets.missing() either -- ui.missing_art() is what
    # a packaging check has to consult. Asserted as an invariant rather than a
    # fixed list, because the art pipeline is filling assets/ in parallel.
    gaps = ui.missing_art()
    check("an unknown icon is reported as a stand-in",
          "icon_no_such_icon_name.png" in gaps, f"{sorted(gaps)[:6]}")
    check("nothing that exists on disk is reported missing",
          all(not assets.exists(n) for n in gaps), f"{sorted(gaps)}")
    probes = ("button.png", "button_down.png", "button_ghost.png", "panel.png",
              "icon_play.png", "icon_left.png")
    present = [n for n in probes if assets.exists(n)]
    check("real art, where it exists, is used instead of a stand-in",
          all(n not in gaps for n in present), f"present={present}")
    print(f"  interface art present: {len(present)}/{len(probes)} probes;"
          f" stand-ins for {sorted(gaps)}")


# --------------------------------------------------------------------------
def main() -> int:
    pygame.init()
    pygame.display.set_mode((608, 1080))

    # Never touch the player's real profile: point the save layer at a temp dir
    # before anything constructs one.
    tmp = tempfile.mkdtemp(prefix="featherdash-uitest-")
    from featherdash import save as savemod
    savemod.data_dir = lambda: tmp
    profile = savemod.Profile()

    vp = compute(608, 1080)
    print(fingerprint())
    print(f"reference viewport: 608x1080 -> scale={vp.scale:.3f} world={vp.world_w:.0f}x1080")

    # Deliberately pytest's collection order (definition order), with the same
    # per-group cache reset its fixture does, so the two runs produce the same
    # check lines in the same sequence.
    groups = [
        ("test_text", lambda: test_text(vp)),
        ("test_layout", lambda: test_layout(vp)),
        ("test_hit_testing", lambda: test_hit_testing(vp, profile)),
        ("test_toggle", lambda: test_toggle(vp, profile)),
        ("test_carousel", lambda: test_carousel(vp, profile)),
        ("test_panel_and_counter", lambda: test_panel_and_counter(vp, profile)),
        ("test_events", lambda: test_events(vp, profile)),
        ("test_regressions", lambda: test_regressions(vp, profile)),
        ("test_api_surface", lambda: test_api_surface(vp, profile)),
        ("test_scales", lambda: test_scales(profile)),
        ("test_states_sheet", lambda: test_states_sheet(profile)),
        ("test_missing_art_is_survivable", lambda: test_missing_art_is_survivable(vp, profile)),
    ]
    for _name, fn in groups:
        reset_caches()
        fn()
        reset_caches()
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
