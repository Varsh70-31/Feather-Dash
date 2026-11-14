"""The viewport is the project's core abstraction; these pin its contract."""
from __future__ import annotations

import pytest

from featherdash import config, display

# A deliberately awkward spread: tall phones, square-ish tablets, wide desktops
# and a window small enough to expose division-by-small-number problems.
SHAPES = [
    (1179, 2556), (1080, 2400), (1440, 3120), (720, 1600),
    (1640, 2360), (1536, 2048), (2048, 1536),
    (1920, 1080), (2560, 1080), (320, 480), (1000, 1000), (3840, 2160),
]


@pytest.mark.parametrize("w,h", SHAPES)
def test_visible_area_always_covers_the_play_area(w, h):
    """No black bars, ever: the visible region must contain the whole play area."""
    vp = display.compute(w, h)
    assert vp.view_left <= 0.01
    assert vp.view_right >= vp.world_w - 0.01
    assert vp.view_top <= 0.01
    assert vp.view_bottom >= vp.world_h - 0.01


@pytest.mark.parametrize("w,h", SHAPES)
def test_play_area_width_stays_within_bounds(w, h):
    vp = display.compute(w, h)
    assert config.WORLD_W_MIN - 0.01 <= vp.world_w <= config.WORLD_W_MAX + 0.01


@pytest.mark.parametrize("w,h", SHAPES)
def test_screen_world_round_trip(w, h):
    vp = display.compute(w, h)
    for point in ((0, 0), (100, 200), (vp.world_w, vp.world_h), (-50, 1500)):
        back = vp.to_world(*vp.to_screen(*point))
        assert back[0] == pytest.approx(point[0], abs=1e-6)
        assert back[1] == pytest.approx(point[1], abs=1e-6)


@pytest.mark.parametrize("w,h", SHAPES)
def test_world_fills_the_screen_in_at_least_one_axis(w, h):
    """The play area must touch the screen edges somewhere, or we have letterboxed."""
    vp = display.compute(w, h)
    fills_h = abs(vp.origin_x) < 1.0
    fills_v = abs(vp.origin_y) < 1.0
    assert fills_h or fills_v


def test_gap_is_the_same_fraction_of_height_everywhere():
    """Vertical difficulty must not depend on the device."""
    fractions = {config.GAP_START / display.compute(w, h).world_h for w, h in SHAPES}
    assert len(fractions) == 1


@pytest.mark.parametrize("w,h", SHAPES)
def test_rects_from_adjacent_boxes_do_not_gap(w, h):
    """Rounding must not leave a seam between tiles laid edge to edge."""
    vp = display.compute(w, h)
    a = vp.rect(10.0, 0.0, 33.3, 10.0)
    b = vp.rect(43.3, 0.0, 33.3, 10.0)
    assert a.right == b.left


def test_degenerate_sizes_do_not_divide_by_zero():
    for size in ((0, 0), (1, 1), (1, 10000), (10000, 1)):
        vp = display.compute(*size)
        assert vp.scale > 0
        assert vp.world_w > 0


def test_safe_area_insets_shrink_the_usable_box():
    vp = display.compute(1080, 2400, safe=(20.0, 60.0, 20.0, 40.0))
    assert vp.safe_left() == pytest.approx(20.0 + config.SAFE_PAD)
    assert vp.safe_top() == pytest.approx(60.0 + config.SAFE_PAD)
    assert vp.safe_right() == pytest.approx(vp.world_w - 20.0 - config.SAFE_PAD)
    assert vp.safe_bottom() == pytest.approx(vp.world_h - 40.0 - config.SAFE_PAD)
