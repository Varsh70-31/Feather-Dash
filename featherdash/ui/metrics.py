"""Per-device component sizing.

One table, read by every screen, so "make the buttons bigger on a tablet" is a
number in one place rather than an edit in four scenes. Values are world units;
the viewport turns them into pixels.

The split that matters: *size class* comes from how much room there is and drives
type scale, spacing and how far a panel may spread; *touch* comes from how the
device is operated and drives only the minimum hit target. Collapsing those into
a single "is this a phone" flag is what produces tablet layouts with phone-sized
buttons and desktop windows with thumb-sized ones.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..display import Viewport


@dataclass(frozen=True)
class Metrics:
    """Every dimension a screen needs, resolved for one viewport."""

    size_class: str
    touch: bool

    # typography
    type_scale: float          # multiplier applied to config.FONT_SIZES

    # spacing rhythm -- everything is a multiple of these, so vertical
    # alignment stays consistent without each screen inventing its own numbers
    gutter: float              # inset from the safe area to content
    gap: float                 # between adjacent controls
    section_gap: float         # between groups of controls

    # components
    button_h: float
    button_w: float
    icon_button: float
    icon: float
    hud_icon: float
    panel_w: float
    panel_pad: float
    row_h: float
    min_tap: float

    # the dash control, which is the one HUD element sized by reach rather than
    # by available room
    dash_button: float

    def scaled(self, size_key_px: float) -> float:
        """Apply the type scale to a font size from config.FONT_SIZES."""
        return size_key_px * self.type_scale


# size_class -> the values that depend only on available room
_TABLE = {
    "compact": dict(
        type_scale=1.00, gutter=20.0, gap=18.0, section_gap=34.0,
        button_h=118.0, button_w_frac=0.74, button_w_cap=360.0,
        icon_button=78.0, icon=44.0, hud_icon=40.0,
        panel_w_frac=0.93, panel_w_cap=560.0, panel_pad=32.0, row_h=104.0,
        dash_button=132.0,
    ),
    "regular": dict(
        type_scale=1.07, gutter=34.0, gap=22.0, section_gap=44.0,
        button_h=128.0, button_w_frac=0.62, button_w_cap=440.0,
        icon_button=88.0, icon=50.0, hud_icon=46.0,
        panel_w_frac=0.84, panel_w_cap=640.0, panel_pad=42.0, row_h=116.0,
        dash_button=146.0,
    ),
    "expanded": dict(
        type_scale=1.12, gutter=44.0, gap=24.0, section_gap=52.0,
        button_h=122.0, button_w_frac=0.46, button_w_cap=420.0,
        icon_button=82.0, icon=48.0, hud_icon=44.0,
        panel_w_frac=0.68, panel_w_cap=680.0, panel_pad=48.0, row_h=112.0,
        dash_button=138.0,
    ),
}

# A finger needs roughly 48dp; a mouse does not, and forcing thumb-sized targets
# onto a desktop makes a settings screen look like a toddler's toy.
_MIN_TAP_TOUCH = 132.0
_MIN_TAP_POINTER = 88.0

_cache: dict[tuple[str, bool, float], Metrics] = {}


def metrics_for(vp: Viewport) -> Metrics:
    """Resolve the component table for a viewport. Cached per distinct shape."""
    key = (vp.size_class, vp.touch, round(vp.world_w, 1))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    t = _TABLE.get(vp.size_class, _TABLE["compact"])
    usable = max(1.0, vp.safe_right() - vp.safe_left())

    m = Metrics(
        size_class=vp.size_class,
        touch=vp.touch,
        type_scale=t["type_scale"],
        gutter=t["gutter"],
        gap=t["gap"],
        section_gap=t["section_gap"],
        button_h=t["button_h"],
        button_w=min(usable * t["button_w_frac"], t["button_w_cap"], usable),
        icon_button=t["icon_button"],
        icon=t["icon"],
        hud_icon=t["hud_icon"],
        panel_w=min(usable * t["panel_w_frac"], t["panel_w_cap"], usable),
        panel_pad=t["panel_pad"],
        row_h=t["row_h"],
        min_tap=_MIN_TAP_TOUCH if vp.touch else _MIN_TAP_POINTER,
        dash_button=t["dash_button"],
    )
    if len(_cache) > 32:
        _cache.clear()
    _cache[key] = m
    return m


def invalidate() -> None:
    _cache.clear()
