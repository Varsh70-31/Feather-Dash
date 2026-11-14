"""The interface kit every Feather Dash screen is built from.

Three layers, all measured in world units and converted to pixels only at draw
time through the `Viewport`:

* `text`   -- cached, shadowed, outlined text and fixed-advance numbers.
* `layout` -- boxes, stacks, grids and safe-area anchors.
* `widgets`-- buttons, toggles, panels, a carousel and the pointer plumbing.

A scene normally only needs `from featherdash import ui`, and must call
`ui.invalidate()` whenever the viewport changes so the size-dependent caches
are rebuilt at the new scale.
"""
from __future__ import annotations

from .layout import (
    ANCHORS,
    Box,
    Column,
    Grid,
    Row,
    Stack,
    anchor,
    anchor_point,
    bottom_center,
    bottom_left,
    bottom_right,
    center,
    center_left,
    center_right,
    clamp_into_safe,
    distribute,
    safe_box,
    top_center,
    top_left,
    top_right,
)
from .text import (
    cache_size,
    cache_stats,
    draw_number,
    draw_text,
    font,
    measure,
    measure_px,
    number_width,
    px_for,
)
from .widgets import (
    UI_ART_SOURCE_SCALE,
    Button,
    Carousel,
    IconButton,
    Panel,
    ScoreCounter,
    Toggle,
    Widget,
    WidgetGroup,
    draw_frame,
    draw_icon,
    missing_art,
    set_haptics_hook,
    spring_backend,
)

# Re-exported one at a time because both shadow a name: `cache_bytes` is the
# kit-wide total (frames, icons, dots and text), not `text.cache_bytes`, which
# counts only its own module.
from .widgets import cache_bytes as cache_bytes
from .widgets import invalidate as invalidate

__all__ = [
    # layout
    "Box", "Stack", "Column", "Row", "Grid", "distribute",
    "safe_box", "anchor", "anchor_point", "clamp_into_safe", "ANCHORS",
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
    # text
    "draw_text", "draw_number", "measure", "measure_px", "px_for",
    "number_width", "font", "cache_size", "cache_bytes", "cache_stats",
    # widgets
    "Widget", "Button", "IconButton", "Toggle", "Panel", "Carousel",
    "ScoreCounter", "WidgetGroup", "draw_icon", "draw_frame",
    "set_haptics_hook", "spring_backend", "UI_ART_SOURCE_SCALE", "missing_art",
    # lifecycle
    "invalidate",
]
