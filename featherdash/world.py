"""Sky, parallax scenery and ground.

Four layers scroll at different rates against a gradient sky, and the whole set
cross-fades to another theme as the score climbs, so a long run visibly travels
from dawn through to night.

Two details matter more than they look:

* Layers are drawn across the *visible* bounds (`vp.view_left`/`view_right`), not
  the play area. On a screen wider than the clamped play area that surplus is
  filled with scenery instead of black bars.
* Scroll offsets are kept modulo the tile width. Letting them grow without bound
  would, after a long session, push float precision far enough that the seam
  between tiles starts to shimmer.
"""
from __future__ import annotations

import math

import pygame

from . import assets, config
from .display import Viewport


class _Layer:
    """One horizontally-tiling parallax band."""

    __slots__ = ("prefix", "height", "factor", "offset", "anchor")

    def __init__(self, prefix: str, height: float, factor: float, anchor: float) -> None:
        self.prefix = prefix
        self.height = height
        self.factor = factor
        self.offset = 0.0
        self.anchor = anchor  # world y of the layer's bottom edge, relative to ground

    def tile_width(self, theme: str) -> float:
        src = assets.original(f"{self.prefix}_{theme}.png")
        return self.height * src.get_width() / max(1, src.get_height())

    def advance(self, dt: float, speed: float, theme: str) -> None:
        width = self.tile_width(theme)
        if width <= 0:
            return
        self.offset = (self.offset + speed * self.factor * dt) % width

    def draw(self, surface: pygame.Surface, vp: Viewport, theme: str,
             alpha: int, dy: float, dx: float) -> None:
        name = f"{self.prefix}_{theme}.png"
        width = self.tile_width(theme)
        if width <= 0:
            return
        img = assets.scaled(name, vp.px(width), vp.px(self.height))
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)

        top = vp.ground_y + self.anchor - self.height + dy
        start = vp.view_left - (self.offset % width) - width
        tile_px = img.get_width()
        if tile_px <= 0:
            return
        sx, sy = vp.to_screen(start + dx, top)
        sx, sy = int(round(sx)), int(round(sy))
        span = vp.screen_w - sx
        count = int(span / tile_px) + 2
        for i in range(max(1, min(count, 32))):
            surface.blit(img, (sx + i * tile_px, sy))


class World:
    """Owns the scrolling backdrop and the current/next theme blend."""

    def __init__(self, theme: str = "day") -> None:
        self.theme = theme
        self.next_theme: str | None = None
        self.fade = 0.0
        self.time = 0.0
        self.layers = (
            _Layer("far", config.LAYER_FAR_H, config.PARALLAX_FAR,
                   config.LAYER_ANCHORS["far"]),
            _Layer("mid", config.LAYER_MID_H, config.PARALLAX_MID,
                   config.LAYER_ANCHORS["mid"]),
            _Layer("near", config.LAYER_NEAR_H, config.PARALLAX_NEAR,
                   config.LAYER_ANCHORS["near"]),
        )
        self.cloud_offset = 0.0
        self.ground_offset = 0.0

    # -- theming ----------------------------------------------------------
    def set_theme(self, theme: str, immediate: bool = False) -> None:
        if theme == self.theme or theme == self.next_theme:
            return
        if immediate:
            self.theme = theme
            self.next_theme = None
            self.fade = 0.0
            return
        # A change requested mid-fade resolves the one in flight first, so the
        # blend never stacks three themes on top of each other.
        if self.next_theme is not None:
            self.theme = self.next_theme
        self.next_theme = theme
        self.fade = 0.0

    def theme_for_score(self, score: int) -> str:
        index = (score // config.THEME_EVERY) % len(config.THEMES)
        return config.THEMES[index]

    @property
    def accent(self) -> tuple[int, int, int]:
        """Current accent colour, blended through a theme change."""
        a = config.THEME_ACCENT.get(self.theme, config.COLOR_GOLD)
        if not self.next_theme:
            return a
        b = config.THEME_ACCENT.get(self.next_theme, a)
        t = self.fade
        return (int(a[0] + (b[0] - a[0]) * t),
                int(a[1] + (b[1] - a[1]) * t),
                int(a[2] + (b[2] - a[2]) * t))

    # -- simulation -------------------------------------------------------
    def step(self, dt: float, speed: float) -> None:
        self.time += dt
        if self.next_theme is not None:
            self.fade += dt / config.THEME_FADE
            if self.fade >= 1.0:
                self.theme = self.next_theme
                self.next_theme = None
                self.fade = 0.0

        for layer in self.layers:
            layer.advance(dt, speed, self.theme)

        cloud_w = self._cloud_span()
        self.cloud_offset = (self.cloud_offset + speed * config.PARALLAX_CLOUD * dt) % cloud_w
        ground_w = self._ground_tile_width()
        if ground_w > 0:
            self.ground_offset = (
                self.ground_offset + speed * config.GROUND_SPEED_FRAC * dt
            ) % ground_w

    def _ground_tile_width(self) -> float:
        src = assets.original(f"ground_{self.theme}.png")
        return config.GROUND_ART_H * src.get_width() / max(1, src.get_height())

    def _cloud_span(self) -> float:
        return max(640.0, config.WORLD_W_MAX * 1.6)

    # -- presentation -----------------------------------------------------
    def draw_background(self, surface: pygame.Surface, vp: Viewport,
                        offset: tuple[float, float] = (0.0, 0.0)) -> None:
        dx, dy = offset
        blend = int(255 * self.fade) if self.next_theme else 0

        self._draw_sky(surface, vp, self.theme, 255)
        if blend:
            self._draw_sky(surface, vp, self.next_theme or self.theme, blend)

        # One star field serves both starry themes, so it is drawn once at an
        # interpolated alpha rather than twice: drawing it twice would brighten
        # every star during a cross-fade instead of fading one sky into another.
        self._draw_stars(surface, vp)

        # The sun cross-fades out as the next one fades in, rather than being
        # overdrawn like the sky is. Two discs at different places on the screen
        # cannot cover each other, so leaving the old one opaque would hold it
        # at full strength for the whole fade and then pop it out at the end.
        if blend:
            self._draw_sundisc(surface, vp, self.theme, 255 - blend)
            self._draw_sundisc(surface, vp, self.next_theme or self.theme, blend)
        else:
            self._draw_sundisc(surface, vp, self.theme, 255)

        self._draw_clouds(surface, vp, self.theme, 255, dx, dy)
        if blend:
            self._draw_clouds(surface, vp, self.next_theme or self.theme, blend, dx, dy)

        for layer in self.layers:
            layer.draw(surface, vp, self.theme, 255, dy, dx)
            if blend:
                layer.draw(surface, vp, self.next_theme or self.theme, blend, dy, dx)

    def _draw_sky(self, surface: pygame.Surface, vp: Viewport, theme: str, alpha: int) -> None:
        """Stretch the sky gradient over everything visible, overscan included."""
        w = max(1, vp.screen_w)
        h = max(1, vp.screen_h)
        img = assets.scaled(f"sky_{theme}.png", w, h)
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        surface.blit(img, (0, 0))

    def _star_alpha(self) -> int:
        """Star brightness for the current theme, interpolated through a fade."""
        a = config.STAR_ALPHA.get(self.theme, 0)
        if not self.next_theme:
            return a
        b = config.STAR_ALPHA.get(self.next_theme, 0)
        return int(round(a + (b - a) * self.fade))

    def _draw_stars(self, surface: pygame.Surface, vp: Viewport) -> None:
        """Tile the star field across the sky at a uniform scale.

        Scaling by the framebuffer's *height* and repeating in x is what keeps a
        star round: the sky strip under it is stretched to the screen's aspect
        ratio, so a star drawn into that strip is a dash on everything but the
        one shape the squash was tuned for.
        """
        alpha = self._star_alpha()
        if alpha <= 0:
            return
        name = "stars.png"
        src = assets.original(name)
        h = max(1, vp.screen_h)
        w = max(1, int(round(h * src.get_width() / max(1, src.get_height()))))
        img = assets.scaled(name, w, h)
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        for x in range(0, vp.screen_w, w):
            surface.blit(img, (x, 0))

    def _draw_sundisc(self, surface: pygame.Surface, vp: Viewport, theme: str,
                      alpha: int) -> None:
        """Blit the sun or moon as a square sprite over the stretched sky.

        Its position is a fraction of the framebuffer, not of the play area,
        because that is what the sky gradient behind it is stretched across --
        so disc and sky stay registered on any aspect ratio, and the disc stays
        circular because the sprite is scaled by one factor in both axes.
        """
        name = f"sundisc_{theme}.png"
        size = vp.px(config.SUN_DRAW.get(theme, 320.0))
        img = assets.scaled(name, size, size)
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)
        fx, fy = config.SUN_POS.get(theme, (0.25, 0.15))
        surface.blit(img, (int(round(fx * vp.screen_w - img.get_width() * 0.5)),
                           int(round(fy * vp.screen_h - img.get_height() * 0.5))))

    def _draw_clouds(self, surface: pygame.Surface, vp: Viewport, theme: str,
                     alpha: int, dx: float, dy: float) -> None:
        span = self._cloud_span()
        band_lo, band_hi = config.CLOUD_BAND
        for i in range(config.CLOUD_COUNT):
            # A deterministic hash spreads the clouds without storing a layout
            # and without pulling in the RNG, so the sky looks the same each run.
            fx = _hash01(i * 7 + 3)
            fy = _hash01(i * 13 + 5)
            variant = i % 4
            scale = 0.7 + _hash01(i * 17 + 11) * 0.65
            name = f"cloud_{theme}_{variant}.png"
            src = assets.original(name)
            h = config.CLOUD_H * scale
            w = h * src.get_width() / max(1, src.get_height())
            img = assets.scaled(name, vp.px(w), vp.px(h))
            if alpha < 255:
                img = img.copy()
                img.set_alpha(alpha)
            y = vp.world_h * (band_lo + fy * (band_hi - band_lo)) + dy
            base = fx * span - self.cloud_offset
            # Draw two copies a span apart so a cloud leaving on the left
            # re-enters on the right without a pop.
            for rep in (0, 1, 2):
                x = vp.view_left + ((base + rep * span) % span) - w
                if x > vp.view_right or x + w < vp.view_left:
                    continue
                surface.blit(img, vp.to_screen_i(x + dx, y))

    def draw_ground(self, surface: pygame.Surface, vp: Viewport,
                    offset: tuple[float, float] = (0.0, 0.0)) -> None:
        dx, dy = offset
        blend = int(255 * self.fade) if self.next_theme else 0
        self._draw_ground_theme(surface, vp, self.theme, 255, dx, dy)
        if blend:
            self._draw_ground_theme(surface, vp, self.next_theme or self.theme, blend, dx, dy)

    def _draw_ground_theme(self, surface: pygame.Surface, vp: Viewport, theme: str,
                           alpha: int, dx: float, dy: float) -> None:
        name = f"ground_{theme}.png"
        src = assets.original(name)
        # The strip is scaled by its *art* height, not the collision height: the
        # two differ because the art runs deeper than the 170-unit play band.
        art_h = config.GROUND_ART_H
        tile_w = art_h * src.get_width() / max(1, src.get_height())
        if tile_w <= 0:
            return
        # The ground must reach the bottom of the *visible* area, which on a tall
        # phone sits well below the play area's nominal bottom edge.
        visible_h = max(art_h, vp.view_bottom - vp.ground_y + 8.0)
        img = assets.scaled(name, vp.px(tile_w), vp.px(art_h))
        if alpha < 255:
            img = img.copy()
            img.set_alpha(alpha)

        top = vp.ground_y + dy
        start = vp.view_left - (self.ground_offset % tile_w) - tile_w
        sx, sy = vp.to_screen(start + dx, top)
        sx, sy = int(round(sx)), int(round(sy))
        tile_px = max(1, img.get_width())
        count = int((vp.screen_w - sx) / tile_px) + 2
        for i in range(max(1, min(count, 32))):
            surface.blit(img, (sx + i * tile_px, sy))

        # Below the tiled strip, extend the ground's darkest row to the bottom of
        # the screen so vertical overscan never reveals the sky underneath.
        fill_top = top + art_h
        if visible_h > art_h:
            skirt = vp.rect(vp.view_left, fill_top,
                            vp.view_right - vp.view_left, visible_h - art_h + 4.0)
            colour = _bottom_colour(name)
            if alpha < 255:
                overlay = pygame.Surface(skirt.size, pygame.SRCALPHA)
                overlay.fill((*colour, alpha))
                surface.blit(overlay, skirt.topleft)
            else:
                surface.fill(colour, skirt)


_bottom_cache: dict[str, tuple[int, int, int]] = {}


def _bottom_colour(name: str) -> tuple[int, int, int]:
    """Average colour of a ground tile's last row, used to extend it downward."""
    cached = _bottom_cache.get(name)
    if cached is not None:
        return cached
    src = assets.original(name)
    w, h = src.get_size()
    total = [0, 0, 0]
    samples = max(1, min(w, 32))
    for i in range(samples):
        r, g, b = src.get_at((int(i * w / samples), h - 1))[:3]
        total[0] += r
        total[1] += g
        total[2] += b
    colour = (total[0] // samples, total[1] // samples, total[2] // samples)
    _bottom_cache[name] = colour
    return colour


def _hash01(n: int) -> float:
    """Deterministic 0..1 value from an integer -- a stable layout without state."""
    x = math.sin(n * 127.1) * 43758.5453
    return x - math.floor(x)
