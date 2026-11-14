"""Single source of truth for Feather Dash's art spec.

Both the generators in `tools/` and the game's asset loader import this module,
so a filename or size can only ever be defined in one place. Sizes are the
*source* pixel dimensions; the game rescales them for the device it is on.

The margin to author against is measured in *device* pixels, not world units.
`display.compute` sets scale = screen_h / WORLD_H, which is 2.17 px per world
unit on a 1080x2340 phone, 2.22 on 1080x2400 and 2.89 on 1440x3120. A sprite
drawn D world units wide therefore needs about 2.9 * D source pixels before the
densest supported screen stops enlarging it -- and `assets.scaled` enlarges
through a bilinear filter, which softens exactly the crisp facet steps and
hairline grooves the art depends on. Every size below is chosen so the worst
case still downsamples.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "assets", "images")
AUDIO = os.path.join(ROOT, "assets", "audio")
FONTS = os.path.join(ROOT, "assets", "fonts")

# --------------------------------------------------------------------------
# virtual resolution
# --------------------------------------------------------------------------
# The world is a fixed number of units tall and a variable number wide, so the
# game fills any aspect ratio without letterboxing and without changing how
# tall a jump feels. Width is clamped so an ultra-wide window cannot see so far
# ahead that it becomes an unfair advantage.
WORLD_H = 1080
WORLD_W_MIN = 560
WORLD_W_MAX = 1040
WORLD_W_REF = 608  # 9:16, the reference phone shape the art is composed for

# --------------------------------------------------------------------------
# themes
# --------------------------------------------------------------------------
# Themes rotate as the player's score climbs, so a long run visibly travels
# through a day. Each theme drives the sky, the parallax layers, the ground and
# the obstacle tint.
THEMES = ("dawn", "day", "dusk", "night")

# Dawn and dusk are deliberately built as opposites rather than as one palette
# at two exposures: dawn runs *cool to warm* (indigo overhead, pale lemon at the
# horizon) with the sun low on the left and warm earth; dusk runs *hot to cool*
# (crimson at the horizon, near-black indigo overhead) with the sun low on the
# right and cold earth. Within a theme the far/mid/near triple is held about
# 20 L* apart and strictly ordered light-to-dark, so aerial perspective still
# reads once the sky stops helping -- at night it is the only depth cue left.
THEME_PALETTE = {
    "dawn": {
        "sky": [(0.00, (62, 82, 142)), (0.32, (128, 124, 186)), (0.60, (224, 158, 166)),
                (0.82, (252, 206, 150)), (1.00, (253, 243, 188))],
        "sun": (255, 240, 198),
        "far": (104, 98, 154),
        "mid": (120, 96, 146),
        "near": (108, 66, 92),
        "cloud": (255, 212, 198),
        "ground_top": (186, 140, 104),
        "ground_body": (82, 56, 52),
        # Obstacle colours are picked to sit *off* the theme's own sky ramp, not
        # inside it: the shaft's lit main face must stay at least dE76 20 from
        # the sky at every visible screen height or the pillar is held together
        # by nothing but its 1.5%-wide contact edge. `gen_props.verify` asserts
        # it. The old mauve sat almost exactly on the 0.32 sky stop.
        "pillar": (116, 64, 146),
        "pillar_light": (181, 123, 195),
        "accent": (255, 176, 118),
        "fog": (246, 194, 168),
    },
    "day": {
        "sky": [(0.00, (58, 142, 222)), (0.45, (116, 190, 238)), (0.78, (178, 224, 246)),
                (1.00, (226, 245, 250))],
        "sun": (255, 250, 222),
        "far": (142, 186, 212),
        "mid": (108, 170, 160),
        "near": (56, 118, 104),
        "cloud": (255, 255, 255),
        "ground_top": (122, 190, 110),
        "ground_body": (92, 128, 80),
        "pillar": (86, 176, 118),
        "pillar_light": (142, 214, 158),
        "accent": (255, 206, 92),
        "fog": (198, 230, 244),
    },
    "dusk": {
        "sky": [(0.00, (14, 16, 48)), (0.32, (62, 40, 100)), (0.58, (196, 62, 104)),
                (0.80, (244, 124, 88)), (1.00, (252, 188, 116))],
        "sun": (255, 214, 150),
        "far": (86, 72, 128),
        "mid": (92, 60, 108),
        "near": (34, 30, 72),
        "cloud": (246, 170, 158),
        "ground_top": (96, 56, 104),
        "ground_body": (40, 26, 58),
        "pillar": (132, 52, 148),       # off the 0.58 crimson stop, see dawn
        "pillar_light": (193, 103, 175),
        "accent": (255, 160, 108),
        "fog": (226, 142, 134),
    },
    "night": {
        "sky": [(0.00, (8, 12, 38)), (0.42, (20, 28, 68)), (0.74, (36, 52, 102)),
                (1.00, (62, 84, 134))],
        "sun": (226, 234, 255),  # the moon
        "far": (56, 72, 124),
        "mid": (36, 50, 96),
        "near": (14, 20, 44),
        "cloud": (118, 134, 186),
        "ground_top": (44, 62, 102),
        "ground_body": (20, 28, 54),
        "pillar": (56, 66, 152),        # deeper and bluer than the sky, see dawn
        "pillar_light": (100, 126, 201),
        "accent": (126, 214, 255),
        "fog": (70, 92, 148),
    },
}

# --------------------------------------------------------------------------
# bird
# --------------------------------------------------------------------------
BIRD_FRAMES = 4  # wings: level, down, level, up
BIRD_SIZE = (200, 168)  # source px per frame
BIRD_DRAW_W = 96  # world units wide when drawn

# (key, display name, body, belly, wing, beak, unlock cost in feathers)
BIRD_SKINS = [
    # Every body colour here is held away from all four sky gradients by
    # `gen_bird.py --check` (CIELAB dE76 >= 20 over the flight band), because a
    # bird the colour of the sky behind it survives only on its outline, which
    # is the first thing a moving parallax eats. Ember is hotter than a plain
    # orange so it clears the dusk sunset band; Sky and Violet are deeper than
    # the instinct for "sky blue" wants, so they clear the day and dawn ramps.
    ("ember",  "Ember",   (248, 138, 42),  (255, 206, 142), (226, 106, 48),  (255, 196, 72),  0),
    ("sky",    "Sky",     (20, 102, 200),  (190, 228, 252), (10, 68, 152),   (255, 186, 92),  150),
    ("mint",   "Mint",    (96, 212, 170),  (198, 244, 226), (62, 176, 140),  (255, 200, 96),  300),
    ("violet", "Violet",  (150, 112, 226), (218, 198, 252), (112, 76, 194),  (255, 186, 118), 600),
    ("gold",   "Gold",    (248, 198, 72),  (255, 238, 176), (214, 158, 42),  (255, 148, 72),  1200),
    ("shadow", "Shadow",  (72, 78, 104),   (128, 136, 168), (48, 52, 74),    (186, 206, 255), 2400),
]

# --------------------------------------------------------------------------
# obstacles & pickups
# --------------------------------------------------------------------------
# 124 world units of shaft is 358 device px on a 1440x3120 screen, and the cap
# is 151 units -- hence 384 and 468 here rather than the 220/268 these were
# first drawn at, which every current phone was enlarging.
PILLAR_BODY = (384, 224)   # tileable shaft segment
PILLAR_CAP = (468, 230)    # flared rim at the mouth of the gap
PILLAR_DRAW_W = 124        # world units wide

COIN_FRAMES = 8
COIN_SIZE = (192, 192)     # 56 world units -> 162 device px at 1440x3120

# --------------------------------------------------------------------------
# sky furniture
# --------------------------------------------------------------------------
# The sky strip is stretched to the whole framebuffer at run time, so anything
# drawn into it is scaled by a different factor in x than in y -- by 1.90x on a
# 20:9 phone and 6.75x on a landscape tablet. Only content that is *uniform in
# x* can live there. The sun/moon and the stars are therefore separate sprites,
# drawn at a uniform scale, and are round on every display instead of only at
# the one aspect ratio the pre-squash was tuned for.
SKY_SIZE = (256, 1080)

SUNDISC_SIZE = (320, 320)
# World units the square sun sprite is drawn at, and its centre as a fraction
# of the framebuffer (matching the stretched sky behind it). Dawn sits low left,
# dusk low right: that mirror is most of what keeps the two themes apart.
SUN_DRAW = {"dawn": 400.0, "day": 300.0, "dusk": 440.0, "night": 300.0}
SUN_POS = {
    "dawn": (0.30, 0.650),
    "day": (0.23, 0.130),
    "dusk": (0.74, 0.670),
    "night": (0.26, 0.150),
}

# One star field, shared by the two themes that show stars and drawn at a
# per-theme alpha: dusk and night are consecutive, so the same sky quietly
# deepening into the same sky is the correct reading, not a coincidence.
STARS_SIZE = (768, 960)      # tileable in x; drawn at the framebuffer's height
STAR_ALPHA = {"dusk": 104, "night": 255}

# --------------------------------------------------------------------------
# world layers (all horizontally tileable)
# --------------------------------------------------------------------------
LAYER_FAR = (1280, 380)
LAYER_MID = (1280, 460)
LAYER_NEAR = (1280, 360)
# The ground art is taller than the 170-unit collision band: a 20:9 phone shows
# 252 world units of ground, and the surplus used to be one flat colour.
GROUND_SIZE = (640, 400)
GROUND_DRAW_H = 170        # world units to the collision surface
GROUND_ART_H = 262.0       # world units the strip is actually drawn at
CLOUD_VARIANTS = 4
CLOUD_SIZE = (520, 260)

# --------------------------------------------------------------------------
# effects
# --------------------------------------------------------------------------
PARTICLES = {
    "soft": (96, 96),
    "spark": (72, 72),
    "star": (96, 96),
    "feather": (96, 96),
    "ring": (256, 256),
    "dust": (80, 80),
}

# --------------------------------------------------------------------------
# interface
# --------------------------------------------------------------------------
# 9-slice geometry.  The sizes here are not "how big the art looks" -- they are
# how much of each frame survives being stretched.  Everything outside the inset
# is reproduced at the authored scale; everything inside it is flattened to a
# single colour by the stretch, so the inset *is* the shading budget.
#
# The game draws panels 430-605 world units tall and buttons 96-126 tall, and
# the runtime maps a source inset to `inset / UI_ART_SOURCE_SCALE` world units
# (widgets.py).  A 72 px inset therefore bought only 36 world units of shading
# on a 430-unit panel -- 8% of its height -- and the other 84% rendered as one
# flat colour.  These numbers are chosen so the shaded bands keep a useful
# share of the panel at the sizes the game actually ships:
#   panels : 150 px inset -> 75 world units -> 35% of a 430-unit panel shaded
#   buttons:  96 px inset -> 48 world units -> 76% of a 126-unit button shaded,
#             and a 96-unit button is shaded edge to edge with no flat band.
PANEL_SIZE = (400, 400)      # 9-slice; corners are PANEL_INSET px
PANEL_INSET = 150
BUTTON_SIZE = (560, 216)
BUTTON_INSET = 96
ICON_SIZE = (144, 144)
ICONS = (
    "play", "pause", "home", "restart", "settings", "shop", "trophy", "close",
    "sound_on", "sound_off", "music_on", "music_off", "haptics_on", "haptics_off",
    "left", "right", "check", "lock", "feather", "share", "star", "medal", "info",
    "dash", "speed", "bird",
)

MEDALS = ("bronze", "silver", "gold", "platinum", "diamond")
MEDAL_SIZE = (288, 288)
MEDAL_THRESHOLDS = {"bronze": 10, "silver": 25, "gold": 50, "platinum": 100, "diamond": 200}

LOGO_SIZE = (1200, 420)

# --------------------------------------------------------------------------
# store & launcher art
# --------------------------------------------------------------------------
APP_ICON = 1024               # Play Store listing icon
ADAPTIVE_LAYER = 432          # Android adaptive icon layers (108dp @ 4x)
ADAPTIVE_SAFE = 264           # guaranteed-visible circle inside the 432 layer
FEATURE_GRAPHIC = (1024, 500)
# The launch screen is authored portrait, not square. The app is locked to
# portrait and p4a scales the presplash FIT_CENTER, so a square asset on a
# 20:9 phone fills only 45% of the screen and paints the other 55% in
# android.presplash_color -- two hard seams on every cold start. At 1:2 the
# residual band on a 20:9 phone is 10%, and gen_brand locks the asset's border
# to exactly that colour so the join is invisible on any aspect.
PRESPLASH = (1080, 2160)


def image_path(name: str) -> str:
    return os.path.join(IMAGES, name)


def ensure_dirs() -> None:
    for d in (IMAGES, AUDIO, FONTS, os.path.join(ROOT, "store")):
        os.makedirs(d, exist_ok=True)


# --------------------------------------------------------------------------
# expected output inventory -- `tools/verify_assets.py` checks against this
# --------------------------------------------------------------------------

def expected_images() -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for skin, *_ in BIRD_SKINS:
        for i in range(BIRD_FRAMES):
            out[f"bird_{skin}_{i}.png"] = BIRD_SIZE
    out["stars.png"] = STARS_SIZE
    for theme in THEMES:
        out[f"sky_{theme}.png"] = SKY_SIZE
        out[f"sundisc_{theme}.png"] = SUNDISC_SIZE
        out[f"far_{theme}.png"] = LAYER_FAR
        out[f"mid_{theme}.png"] = LAYER_MID
        out[f"near_{theme}.png"] = LAYER_NEAR
        out[f"ground_{theme}.png"] = GROUND_SIZE
        out[f"pillar_body_{theme}.png"] = PILLAR_BODY
        # Two caps, not one flipped twice: mirroring a sprite mirrors its
        # lighting, and a crown lit from below reads as a tray.
        out[f"pillar_cap_{theme}.png"] = PILLAR_CAP
        out[f"pillar_cap_up_{theme}.png"] = PILLAR_CAP
        for i in range(CLOUD_VARIANTS):
            out[f"cloud_{theme}_{i}.png"] = CLOUD_SIZE
    for i in range(COIN_FRAMES):
        out[f"coin_{i}.png"] = COIN_SIZE
    for name, size in PARTICLES.items():
        out[f"fx_{name}.png"] = size
    out["panel.png"] = PANEL_SIZE
    out["panel_dark.png"] = PANEL_SIZE
    out["button.png"] = BUTTON_SIZE
    out["button_down.png"] = BUTTON_SIZE
    out["button_ghost.png"] = BUTTON_SIZE
    out["button_ghost_down.png"] = BUTTON_SIZE
    for icon in ICONS:
        out[f"icon_{icon}.png"] = ICON_SIZE
    for medal in MEDALS:
        out[f"medal_{medal}.png"] = MEDAL_SIZE
    out["logo.png"] = LOGO_SIZE
    return out
