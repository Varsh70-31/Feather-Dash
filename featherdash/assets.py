"""Asset loading, scaling and caching.

Source art is authored large (see `tools/manifest.py`) and scaled down to
whatever the current device needs. Scaling is done once per size and cached, so
a resize costs one rescale rather than one per frame, and the game always blits
pixel-exact surfaces instead of asking pygame to scale during the draw.

Every loader degrades instead of raising: a missing file produces a visibly
magenta placeholder rather than a crash, which keeps the game running while art
is still being produced and turns a packaging mistake into something obvious on
screen instead of a stack trace in a user's hands.
"""
from __future__ import annotations

import os
from typing import Iterable

import pygame

from . import config
from .platform import asset_root

_IMAGE_DIR = os.path.join(asset_root(), "images")
_FONT_DIR = os.path.join(asset_root(), "fonts")

_originals: dict[str, pygame.Surface] = {}
_scaled: dict[tuple[str, int, int], pygame.Surface] = {}
_tinted: dict[tuple[int, int, int, int, int], pygame.Surface] = {}
_fonts: dict[tuple[str, int], pygame.font.Font] = {}
_missing: set[str] = set()


def _placeholder(size: tuple[int, int] = (64, 64)) -> pygame.Surface:
    surf = pygame.Surface(size, pygame.SRCALPHA)
    surf.fill((255, 0, 170, 180))
    pygame.draw.rect(surf, (20, 20, 20, 220), surf.get_rect(), 2)
    return surf


def original(name: str) -> pygame.Surface:
    """Load (and memoise) a source PNG at its authored resolution."""
    surf = _originals.get(name)
    if surf is not None:
        return surf
    path = os.path.join(_IMAGE_DIR, name)
    try:
        surf = pygame.image.load(path).convert_alpha()
    except (pygame.error, FileNotFoundError):
        if name not in _missing:
            _missing.add(name)
            print(f"[assets] missing image: {name}")
        surf = _placeholder()
    _originals[name] = surf
    return surf


def exists(name: str) -> bool:
    return os.path.isfile(os.path.join(_IMAGE_DIR, name))


def scaled(name: str, w: float, h: float) -> pygame.Surface:
    """A copy of `name` at exactly (w, h) pixels, cached by that size."""
    iw, ih = max(1, int(round(w))), max(1, int(round(h)))
    key = (name, iw, ih)
    surf = _scaled.get(key)
    if surf is not None:
        return surf
    src = original(name)
    if src.get_size() == (iw, ih):
        surf = src
    else:
        # smoothscale gives clean downsampling; the cache means we pay once.
        surf = pygame.transform.smoothscale(src, (iw, ih))
    _scaled[key] = surf
    return surf


def scaled_by_width(name: str, w: float) -> pygame.Surface:
    """Scale to a target width, preserving the source aspect ratio."""
    src = original(name)
    ratio = src.get_height() / max(1, src.get_width())
    return scaled(name, w, w * ratio)


def scaled_by_height(name: str, h: float) -> pygame.Surface:
    src = original(name)
    ratio = src.get_width() / max(1, src.get_height())
    return scaled(name, h * ratio, h)


def tinted(surf: pygame.Surface, color: tuple[int, int, int], alpha: int = 255) -> pygame.Surface:
    """Recolour a luminance sprite (the particle textures) while keeping its alpha."""
    key = (id(surf), color[0], color[1], color[2], alpha)
    out = _tinted.get(key)
    if out is not None:
        return out
    out = surf.copy()
    out.fill((*color, alpha), special_flags=pygame.BLEND_RGBA_MULT)
    if len(_tinted) > 512:
        _tinted.clear()
    _tinted[key] = out
    return out


def font(size: int, name: str = config.FONT_FILE) -> pygame.font.Font:
    """A font at an already-scaled pixel size."""
    size = max(8, int(round(size)))
    key = (name, size)
    f = _fonts.get(key)
    if f is not None:
        return f
    path = os.path.join(_FONT_DIR, name)
    try:
        f = pygame.font.Font(path, size)
    except (pygame.error, FileNotFoundError, OSError):
        if name not in _missing:
            _missing.add(name)
            print(f"[assets] missing font: {name}, falling back to the bundled default")
        f = pygame.font.Font(None, int(size * 1.15))
    _fonts[key] = f
    return f


def invalidate_scaled() -> None:
    """Drop size-dependent caches. Called when the viewport changes."""
    _scaled.clear()
    _tinted.clear()
    _fonts.clear()
    NineSlice.invalidate_all()


def preload(names: Iterable[str]) -> None:
    for name in names:
        original(name)


def missing() -> set[str]:
    return set(_missing)


class NineSlice:
    """Stretch a rounded panel or button to any size without distorting corners.

    The four corners are blitted unscaled, the four edges are stretched along one
    axis only, and the centre is stretched both ways -- so a 420x148 source
    button can become a 900x120 bar with its radius and bevel intact.
    """

    _instances: list["NineSlice"] = []

    def __init__(self, name: str, inset: int) -> None:
        self.name = name
        self.inset = inset
        self._cache: dict[tuple[int, int, int], pygame.Surface] = {}
        NineSlice._instances.append(self)

    @classmethod
    def invalidate_all(cls) -> None:
        for inst in cls._instances:
            inst._cache.clear()

    def render(self, w: float, h: float, alpha: int = 255) -> pygame.Surface:
        iw, ih = max(1, int(round(w))), max(1, int(round(h)))
        key = (iw, ih, alpha)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        src = original(self.name)
        sw, sh = src.get_size()
        # Never let the corners overlap on a target smaller than the source
        # corners; shrink the inset to fit instead of producing garbage.
        inset = min(self.inset, sw // 2, sh // 2, iw // 2, ih // 2)
        out = pygame.Surface((iw, ih), pygame.SRCALPHA)

        mid_sw, mid_sh = sw - inset * 2, sh - inset * 2
        mid_dw, mid_dh = iw - inset * 2, ih - inset * 2

        def piece(sx: int, sy: int, pw: int, ph: int) -> pygame.Surface:
            return src.subsurface(pygame.Rect(sx, sy, pw, ph))

        if inset > 0:
            out.blit(piece(0, 0, inset, inset), (0, 0))
            out.blit(piece(sw - inset, 0, inset, inset), (iw - inset, 0))
            out.blit(piece(0, sh - inset, inset, inset), (0, ih - inset))
            out.blit(piece(sw - inset, sh - inset, inset, inset), (iw - inset, ih - inset))

        if mid_dw > 0 and mid_sw > 0 and inset > 0:
            top = pygame.transform.smoothscale(piece(inset, 0, mid_sw, inset), (mid_dw, inset))
            bottom = pygame.transform.smoothscale(piece(inset, sh - inset, mid_sw, inset), (mid_dw, inset))
            out.blit(top, (inset, 0))
            out.blit(bottom, (inset, ih - inset))

        if mid_dh > 0 and mid_sh > 0 and inset > 0:
            left = pygame.transform.smoothscale(piece(0, inset, inset, mid_sh), (inset, mid_dh))
            right = pygame.transform.smoothscale(piece(sw - inset, inset, inset, mid_sh), (inset, mid_dh))
            out.blit(left, (0, inset))
            out.blit(right, (iw - inset, inset))

        if mid_dw > 0 and mid_dh > 0 and mid_sw > 0 and mid_sh > 0:
            centre = pygame.transform.smoothscale(piece(inset, inset, mid_sw, mid_sh), (mid_dw, mid_dh))
            out.blit(centre, (inset, inset))
        elif inset == 0:
            out = pygame.transform.smoothscale(src, (iw, ih))

        if alpha < 255:
            out.set_alpha(alpha)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = out
        return out
