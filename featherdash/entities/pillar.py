"""The obstacle: a pair of carved monoliths with a gap between them.

The shaft is drawn by repeating one seamless tile rather than stretching a single
sprite, so a tall pillar keeps the same stone texture density as a short one
instead of smearing. The flared cap at the mouth of the gap is the element the
player's eye actually tracks, so it is drawn last and never clipped.

The two caps are two sprites. Flipping one vertically would be cheaper, and it
is what this used to do, but a flip mirrors the lighting along with the shape:
the crown's lit top lip ends up underneath and its cast shadow ends up above,
pointing back at the light. Every obstacle pair then shows one crown lit from
above and one lit from below in the same frame. The art ships a cap authored
for each orientation instead, and they share a silhouette so the pair still
reads as one carved object.
"""
from __future__ import annotations

import pygame

from .. import assets, config
from ..display import Viewport


class PillarPair:
    __slots__ = ("x", "prev_x", "gap_center", "gap_size", "scored", "passed", "theme", "_flash")

    def __init__(self, x: float, gap_center: float, gap_size: float, theme: str = "day") -> None:
        self.x = x
        self.prev_x = x
        self.gap_center = gap_center
        self.gap_size = gap_size
        self.scored = False
        self.passed = False
        self.theme = theme
        self._flash = 0.0

    # -- geometry ---------------------------------------------------------
    @property
    def gap_top(self) -> float:
        return self.gap_center - self.gap_size * 0.5

    @property
    def gap_bottom(self) -> float:
        return self.gap_center + self.gap_size * 0.5

    @property
    def right(self) -> float:
        return self.x + config.PILLAR_W

    def step(self, dt: float, speed: float) -> None:
        self.prev_x = self.x
        self.x -= speed * dt
        if self._flash > 0.0:
            self._flash = max(0.0, self._flash - dt * 3.2)

    def mark_scored(self) -> None:
        self.scored = True
        self._flash = 1.0

    def render_x(self, alpha: float) -> float:
        return self.prev_x + (self.x - self.prev_x) * alpha

    def collision_rects(self, vp: Viewport) -> tuple[tuple[float, float, float, float], ...]:
        """Top and bottom solid boxes, extended past the view so nothing sneaks round."""
        top_extent = vp.view_top - 400.0
        bottom_extent = vp.view_bottom + 400.0
        return (
            (self.x, top_extent, config.PILLAR_W, self.gap_top - top_extent),
            (self.x, self.gap_bottom, config.PILLAR_W, bottom_extent - self.gap_bottom),
        )

    # -- presentation -----------------------------------------------------
    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float = 1.0,
             offset: tuple[float, float] = (0.0, 0.0)) -> None:
        x = self.render_x(alpha) + offset[0]
        dy = offset[1]
        if x > vp.view_right + config.PILLAR_W or x + config.PILLAR_W < vp.view_left - 4:
            return

        body = f"pillar_body_{self.theme}.png"
        cap = f"pillar_cap_{self.theme}.png"
        cap_up = f"pillar_cap_up_{self.theme}.png"

        # Derive the cap's world size from the art itself, so retouching the
        # sprites never requires touching the game.
        body_src = assets.original(body)
        cap_src = assets.original(cap)
        tile_h = config.PILLAR_W * body_src.get_height() / max(1, body_src.get_width())
        cap_w = config.PILLAR_W * cap_src.get_width() / max(1, body_src.get_width())
        cap_h = cap_w * cap_src.get_height() / max(1, cap_src.get_width())
        cap_x = x - (cap_w - config.PILLAR_W) * 0.5

        px_w = vp.px(config.PILLAR_W)
        tile = assets.scaled(body, px_w, vp.px(tile_h))
        cap_img = assets.scaled(cap, vp.px(cap_w), vp.px(cap_h))
        cap_up_img = assets.scaled(cap_up, vp.px(cap_w), vp.px(cap_h))

        self._draw_shaft(surface, vp, tile, x, vp.view_top + dy - 40.0,
                         self.gap_top + dy - cap_h * 0.35, tile_h)
        self._draw_shaft(surface, vp, tile, x, self.gap_bottom + dy + cap_h * 0.35,
                         vp.view_bottom + dy + 40.0, tile_h)

        surface.blit(cap_up_img, vp.to_screen_i(cap_x, self.gap_top + dy - cap_h))
        surface.blit(cap_img, vp.to_screen_i(cap_x, self.gap_bottom + dy))

        if self._flash > 0.0:
            self._draw_flash(surface, vp, cap_img, cap_up_img, cap_x,
                             self.gap_top + dy - cap_h, self.gap_bottom + dy)

    def _draw_shaft(self, surface: pygame.Surface, vp: Viewport, tile: pygame.Surface,
                    x: float, top: float, bottom: float, tile_h: float) -> None:
        if bottom <= top or tile_h <= 0:
            return
        sx = int(round(vp.to_screen(x, 0)[0]))
        y = top
        tile_px_h = tile.get_height()
        # Align the tile grid to the gap edge so the seam never lands mid-cap.
        count = int((bottom - top) / tile_h) + 1
        for _ in range(min(count, 64)):  # bounded: a pathological viewport cannot spin here
            sy = int(round(vp.to_screen(0, y)[1]))
            surface.blit(tile, (sx, sy))
            y += tile_h
            if y >= bottom:
                break
        # Cover any sub-tile remainder so no sliver of sky shows through.
        remainder = bottom - y
        if remainder > 0 and tile_px_h > 0:
            sy = int(round(vp.to_screen(0, y)[1]))
            clip = max(1, min(tile_px_h, int(round(vp.px(remainder)))))
            surface.blit(tile, (sx, sy), pygame.Rect(0, 0, tile.get_width(), clip))

    def _draw_flash(self, surface: pygame.Surface, vp: Viewport,
                    cap_img: pygame.Surface, cap_up_img: pygame.Surface,
                    cap_x: float, top_y: float, bottom_y: float) -> None:
        """A brief glint across the caps when the player scores through the gap.

        The glow is shaped by the cap sprite rather than being a rectangle over
        the opening: an additive blit ignores the source alpha channel, so any
        filled rect shows up as a hard block of colour instead of a highlight.
        Pre-multiplying by alpha is what makes the soft edges add softly.
        """
        accent = config.THEME_ACCENT.get(self.theme, config.COLOR_GOLD)
        # Quantised so the per-level surfaces can be cached instead of rebuilt
        # every frame of the fade.
        level = max(1, min(6, int(self._flash * 6.0 + 0.5)))
        glow_up = _glow_for(cap_img, self.theme, accent, level, upper=False)
        glow_down = _glow_for(cap_up_img, self.theme, accent, level, upper=True)
        surface.blit(glow_down, vp.to_screen_i(cap_x, top_y),
                     special_flags=pygame.BLEND_RGB_ADD)
        surface.blit(glow_up, vp.to_screen_i(cap_x, bottom_y),
                     special_flags=pygame.BLEND_RGB_ADD)


_glow_cache: dict[tuple, pygame.Surface] = {}


def _glow_for(cap: pygame.Surface, theme: str, accent: tuple[int, int, int],
              level: int, upper: bool) -> pygame.Surface:
    """An additive-ready tinted copy of a cap sprite, cached per size and level."""
    key = (theme, cap.get_width(), cap.get_height(), level, upper)
    cached = _glow_cache.get(key)
    if cached is not None:
        return cached
    glow = cap.copy()
    glow.fill((*accent, 255), special_flags=pygame.BLEND_RGBA_MULT)
    # Pre-multiplying stops soft antialiased edges adding at full strength and
    # gaining a hard outline. Guarded because the pygame that python-for-android
    # ships on Android is older than the one used for development, and the
    # highlight is cosmetic -- it must never be the reason the game will not run.
    if hasattr(glow, "premul_alpha"):
        glow = glow.premul_alpha()
    v = int(255 * (level / 6.0) * 0.72)
    glow.fill((v, v, v, 255), special_flags=pygame.BLEND_RGB_MULT)
    if len(_glow_cache) > 96:
        _glow_cache.clear()
    _glow_cache[key] = glow
    return glow
