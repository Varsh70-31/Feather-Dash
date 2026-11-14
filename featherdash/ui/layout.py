"""Composing screens in world units.

Screens are described as boxes, stacks and grids measured in world units and
anchored to the viewport's **safe area** -- never to its raw edges. A phone puts
a camera cutout over the top of a fullscreen app and a gesture handle over the
bottom, so a button anchored to the literal screen edge is a button some
fraction of players physically cannot press. `Viewport.safe_*()` already folds
the device insets together with `config.SAFE_PAD`; everything here resolves
against those.

Nothing in this module draws. It produces `Box`es, and widgets turn boxes into
pixels through the viewport at draw time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

import pygame

from ..display import Viewport

__all__ = [
    "Box", "Stack", "Column", "Row", "Grid", "distribute",
    "safe_box", "anchor", "anchor_point", "clamp_into_safe", "ANCHORS",
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
]


# --------------------------------------------------------------------------
# the one geometry type
# --------------------------------------------------------------------------
@dataclass
class Box:
    """An axis-aligned rectangle in world units.

    Deliberately mutable in x/y/w/h so a widget can be repositioned in place,
    while every derived operation returns a new `Box` -- mutating helpers that
    sometimes alias and sometimes copy are a reliable source of layout bugs.
    """

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    # -- edges -------------------------------------------------------------
    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w * 0.5

    @property
    def cy(self) -> float:
        return self.y + self.h * 0.5

    @property
    def center(self) -> Tuple[float, float]:
        return (self.cx, self.cy)

    @property
    def size(self) -> Tuple[float, float]:
        return (self.w, self.h)

    @property
    def topleft(self) -> Tuple[float, float]:
        return (self.x, self.y)

    # -- derivation --------------------------------------------------------
    def copy(self) -> "Box":
        return Box(self.x, self.y, self.w, self.h)

    def moved(self, dx: float, dy: float) -> "Box":
        return Box(self.x + dx, self.y + dy, self.w, self.h)

    def at(self, x: float, y: float) -> "Box":
        return Box(x, y, self.w, self.h)

    def centered_on(self, cx: float, cy: float) -> "Box":
        return Box(cx - self.w * 0.5, cy - self.h * 0.5, self.w, self.h)

    def resized(self, w: float, h: float) -> "Box":
        """Same centre, new size -- how a press animation scales a button."""
        return Box(self.cx - w * 0.5, self.cy - h * 0.5, w, h)

    def scaled(self, factor: float) -> "Box":
        return self.resized(self.w * factor, self.h * factor)

    def inflated(self, dx: float, dy: Optional[float] = None) -> "Box":
        dy = dx if dy is None else dy
        return Box(self.x - dx, self.y - dy, self.w + dx * 2, self.h + dy * 2)

    def inset(self, left: float, top: Optional[float] = None,
              right: Optional[float] = None, bottom: Optional[float] = None) -> "Box":
        """Shrink by per-edge padding; one argument pads all four edges."""
        top = left if top is None else top
        right = left if right is None else right
        bottom = top if bottom is None else bottom
        return Box(self.x + left, self.y + top,
                   max(0.0, self.w - left - right),
                   max(0.0, self.h - top - bottom))

    def expanded_to(self, min_w: float, min_h: Optional[float] = None) -> "Box":
        """Grow about the centre so neither side is below a minimum."""
        min_h = min_w if min_h is None else min_h
        return self.resized(max(self.w, min_w), max(self.h, min_h))

    def union(self, other: "Box") -> "Box":
        x = min(self.x, other.x)
        y = min(self.y, other.y)
        return Box(x, y, max(self.right, other.right) - x, max(self.bottom, other.bottom) - y)

    # -- queries -----------------------------------------------------------
    def contains(self, wx: float, wy: float) -> bool:
        return self.x <= wx <= self.right and self.y <= wy <= self.bottom

    def contains_box(self, other: "Box", tolerance: float = 0.0) -> bool:
        return (other.x >= self.x - tolerance and other.y >= self.y - tolerance
                and other.right <= self.right + tolerance
                and other.bottom <= self.bottom + tolerance)

    def intersects(self, other: "Box") -> bool:
        return not (other.x > self.right or other.right < self.x
                    or other.y > self.bottom or other.bottom < self.y)

    # -- output ------------------------------------------------------------
    def to_rect(self, vp: Viewport) -> pygame.Rect:
        return vp.rect(self.x, self.y, self.w, self.h)

    @staticmethod
    def from_center(cx: float, cy: float, w: float, h: float) -> "Box":
        return Box(cx - w * 0.5, cy - h * 0.5, w, h)


# --------------------------------------------------------------------------
# anchors (safe-area relative)
# --------------------------------------------------------------------------
ANCHORS = (
    "top_left", "top_center", "top_right",
    "center_left", "center", "center_right",
    "bottom_left", "bottom_center", "bottom_right",
)

_FRACTIONS: Dict[str, Tuple[float, float]] = {
    "top_left": (0.0, 0.0), "top_center": (0.5, 0.0), "top_right": (1.0, 0.0),
    "center_left": (0.0, 0.5), "center": (0.5, 0.5), "center_right": (1.0, 0.5),
    "bottom_left": (0.0, 1.0), "bottom_center": (0.5, 1.0), "bottom_right": (1.0, 1.0),
}


def safe_box(vp: Viewport) -> Box:
    """The rectangle UI is allowed to occupy, in world units."""
    left, top = vp.safe_left(), vp.safe_top()
    return Box(left, top, max(0.0, vp.safe_right() - left), max(0.0, vp.safe_bottom() - top))


def anchor_point(vp: Viewport, name: str = "center") -> Tuple[float, float]:
    fx, fy = _FRACTIONS.get(name, _FRACTIONS["center"])
    area = safe_box(vp)
    return (area.x + area.w * fx, area.y + area.h * fy)


def anchor(vp: Viewport, name: str = "center", w: float = 0.0, h: float = 0.0,
           dx: float = 0.0, dy: float = 0.0, *, within: Optional[Box] = None) -> Box:
    """Place a `w` x `h` box at a named anchor of the safe area.

    The box is pulled inside the area rather than centred on the anchor point,
    so `top_right` means "tucked into the top-right corner" and not "half off
    the screen". `within` overrides the safe area for anchoring inside a panel.
    """
    area = safe_box(vp) if within is None else within
    fx, fy = _FRACTIONS.get(name, _FRACTIONS["center"])
    return Box(area.x + (area.w - w) * fx + dx, area.y + (area.h - h) * fy + dy, w, h)


def _anchor_fn(name: str):
    def fn(vp: Viewport, w: float = 0.0, h: float = 0.0, dx: float = 0.0, dy: float = 0.0,
           *, within: Optional[Box] = None) -> Box:
        return anchor(vp, name, w, h, dx, dy, within=within)
    fn.__name__ = name
    fn.__doc__ = f"Box of size (w, h) anchored {name.replace('_', '-')} in the safe area."
    return fn


top_left = _anchor_fn("top_left")
top_center = _anchor_fn("top_center")
top_right = _anchor_fn("top_right")
center_left = _anchor_fn("center_left")
center = _anchor_fn("center")
center_right = _anchor_fn("center_right")
bottom_left = _anchor_fn("bottom_left")
bottom_center = _anchor_fn("bottom_center")
bottom_right = _anchor_fn("bottom_right")


def clamp_into_safe(vp: Viewport, box: Box) -> Box:
    """Nudge a box back inside the safe area, shrinking it only if it cannot fit."""
    area = safe_box(vp)
    w = min(box.w, area.w)
    h = min(box.h, area.h)
    x = min(max(box.x, area.x), area.right - w)
    y = min(max(box.y, area.y), area.bottom - h)
    return Box(x, y, w, h)


# --------------------------------------------------------------------------
# stacks
# --------------------------------------------------------------------------
# Cross-axis alignment accepts both vocabularies so a Column and a Row read
# naturally ("left" in a column, "top" in a row) without two separate APIs.
_CROSS = {
    "left": 0.0, "top": 0.0, "start": 0.0,
    "center": 0.5, "centre": 0.5, "middle": 0.5,
    "right": 1.0, "bottom": 1.0, "end": 1.0,
}


class Stack:
    """Accumulates item sizes, then places them all at once.

    Two-phase on purpose: you cannot centre a stack until you know how tall it
    is, and measuring first means a screen can be laid out from its centre
    outwards without hand-summing heights that change the moment a label does.
    """

    _AXIS = 1  # 1 = stack vertically (Column), 0 = horizontally (Row)

    def __init__(self, spacing: float = 0.0, align: str = "center") -> None:
        self.spacing = float(spacing)
        self.align = align
        self._items: List[Tuple[float, float, Optional[Hashable]]] = []
        self._placed: List[Box] = []
        self._keys: Dict[Hashable, int] = {}

    # -- building ----------------------------------------------------------
    def add(self, w: float, h: float, key: Optional[Hashable] = None) -> "Stack":
        if key is not None:
            self._keys[key] = len(self._items)
        self._items.append((float(w), float(h), key))
        return self

    def add_box(self, box: Box, key: Optional[Hashable] = None) -> "Stack":
        return self.add(box.w, box.h, key)

    def gap(self, amount: float) -> "Stack":
        """An invisible spacer, for when one gap differs from the rest.

        It is an ordinary zero-thickness item, so `spacing` still applies on
        both sides of it: the extra separation it creates is `amount + spacing`,
        not `amount`.
        """
        return self.add(amount, 0.0) if self._AXIS == 0 else self.add(0.0, amount)

    def __len__(self) -> int:
        return len(self._items)

    # -- measuring ---------------------------------------------------------
    @property
    def size(self) -> Tuple[float, float]:
        if not self._items:
            return (0.0, 0.0)
        main = sum(i[self._AXIS] for i in self._items) + self.spacing * (len(self._items) - 1)
        cross = max(i[1 - self._AXIS] for i in self._items)
        return (cross, main) if self._AXIS == 1 else (main, cross)

    # -- placing -----------------------------------------------------------
    def place(self, x: float, y: float) -> List[Box]:
        """Place with (x, y) as the top-left of the stack's bounding box."""
        frac = _CROSS.get(self.align, 0.5)
        total_w, total_h = self.size
        boxes: List[Box] = []
        cursor = y if self._AXIS == 1 else x
        for w, h, _ in self._items:
            if self._AXIS == 1:
                bx = x + (total_w - w) * frac
                boxes.append(Box(bx, cursor, w, h))
                cursor += h + self.spacing
            else:
                by = y + (total_h - h) * frac
                boxes.append(Box(cursor, by, w, h))
                cursor += w + self.spacing
        self._placed = boxes
        return boxes

    def place_in(self, vp: Viewport, at: str = "center", dx: float = 0.0, dy: float = 0.0,
                 *, within: Optional[Box] = None) -> List[Box]:
        """Place the whole stack at a named anchor of the safe area (or `within`)."""
        w, h = self.size
        outer = anchor(vp, at, w, h, dx, dy, within=within)
        return self.place(outer.x, outer.y)

    def place_centered(self, cx: float, cy: float) -> List[Box]:
        w, h = self.size
        return self.place(cx - w * 0.5, cy - h * 0.5)

    @property
    def boxes(self) -> List[Box]:
        return self._placed

    def __getitem__(self, key) -> Box:
        if isinstance(key, int):
            return self._placed[key]
        return self._placed[self._keys[key]]


class Column(Stack):
    """Vertical stack. `align` is horizontal: left / center / right."""
    _AXIS = 1


class Row(Stack):
    """Horizontal stack. `align` is vertical: top / middle / bottom."""
    _AXIS = 0


# --------------------------------------------------------------------------
# grid
# --------------------------------------------------------------------------
class Grid:
    """Fixed-column grid, used for the skin shop."""

    def __init__(self, cols: int, cell_w: float, cell_h: float,
                 gap_x: float = 0.0, gap_y: Optional[float] = None) -> None:
        self.cols = max(1, int(cols))
        self.cell_w = float(cell_w)
        self.cell_h = float(cell_h)
        self.gap_x = float(gap_x)
        self.gap_y = float(gap_x if gap_y is None else gap_y)

    @classmethod
    def fit(cls, width: float, cols: int, gap: float, aspect: float = 1.0) -> "Grid":
        """Derive the cell size from the width available, so a grid fills its panel."""
        cols = max(1, int(cols))
        cell_w = max(1.0, (width - gap * (cols - 1)) / cols)
        return cls(cols, cell_w, cell_w / max(0.01, aspect), gap, gap)

    def rows_for(self, count: int) -> int:
        return max(0, (int(count) + self.cols - 1) // self.cols)

    def size(self, count: int) -> Tuple[float, float]:
        rows = self.rows_for(count)
        if rows == 0:
            return (0.0, 0.0)
        cols = min(self.cols, max(1, int(count)))
        return (cols * self.cell_w + self.gap_x * (cols - 1),
                rows * self.cell_h + self.gap_y * (rows - 1))

    def place(self, count: int, x: float, y: float) -> List[Box]:
        boxes: List[Box] = []
        for i in range(max(0, int(count))):
            col, row = i % self.cols, i // self.cols
            boxes.append(Box(x + col * (self.cell_w + self.gap_x),
                             y + row * (self.cell_h + self.gap_y),
                             self.cell_w, self.cell_h))
        return boxes

    def place_in(self, vp: Viewport, count: int, at: str = "center",
                 dx: float = 0.0, dy: float = 0.0, *, within: Optional[Box] = None) -> List[Box]:
        w, h = self.size(count)
        outer = anchor(vp, at, w, h, dx, dy, within=within)
        return self.place(count, outer.x, outer.y)

    @staticmethod
    def index_at(boxes: Sequence[Box], wx: float, wy: float) -> Optional[int]:
        for i, box in enumerate(boxes):
            if box.contains(wx, wy):
                return i
        return None


def distribute(area: Box, count: int, gap: float, horizontal: bool = True) -> List[Box]:
    """Split a box into `count` equal cells -- a bottom bar of equal buttons."""
    count = max(1, int(count))
    if horizontal:
        w = max(0.0, (area.w - gap * (count - 1)) / count)
        return [Box(area.x + i * (w + gap), area.y, w, area.h) for i in range(count)]
    h = max(0.0, (area.h - gap * (count - 1)) / count)
    return [Box(area.x, area.y + i * (h + gap), area.w, h) for i in range(count)]
