"""Tuning constants for Feather Dash.

Every gameplay number lives in world units per second (not pixels per frame), so
the game feels identical on a 60 Hz phone, a 120 Hz tablet and a 144 Hz monitor.
The world is WORLD_H units tall regardless of the display; see `display.py`.
"""
from __future__ import annotations

from .platform import IS_MOBILE

# --------------------------------------------------------------------------
# virtual resolution
# --------------------------------------------------------------------------
WORLD_H = 1080
WORLD_W_MIN = 560          # narrower than this and the gap becomes unreadable
WORLD_W_MAX = 1040         # wider than this and seeing ahead becomes an advantage
WORLD_W_REF = 608          # 9:16, the shape the art is composed for

# Desktop window defaults (the window is freely resizable afterwards).
WINDOW_DEFAULT = (480, 854)
WINDOW_MIN = (320, 480)

# --------------------------------------------------------------------------
# simulation
# --------------------------------------------------------------------------
PHYSICS_HZ = 120.0                 # fixed timestep; render interpolates between steps
PHYSICS_DT = 1.0 / PHYSICS_HZ
MAX_FRAME_TIME = 0.25              # clamp after a stall so the sim never spirals
TARGET_FPS = 60 if IS_MOBILE else 0  # 0 = uncapped, let vsync pace it

# --------------------------------------------------------------------------
# bird
# --------------------------------------------------------------------------
BIRD_X_FRAC = 0.30                 # horizontal position as a fraction of world width
BIRD_START_Y = WORLD_H * 0.44
BIRD_W = 96
BIRD_H = 81
# The hitbox is deliberately smaller than the sprite. Players read a near miss as
# a miss, so a forgiving capsule inside the art feels "fair" while a tight box
# feels broken -- this is the single biggest contributor to perceived fairness.
BIRD_HIT_RX = 30
BIRD_HIT_RY = 26

GRAVITY = 4300.0                   # world units / s^2
FLAP_VELOCITY = -1280.0            # instantaneous upward velocity on tap
TERMINAL_VELOCITY = 2150.0
FLAP_COOLDOWN = 0.09               # seconds; stops a mashed tap from cancelling gravity

ROT_UP = -26.0                     # degrees at full climb
ROT_DOWN = 78.0                    # degrees at terminal fall
ROT_RESPONSE = 9.0                 # how fast the nose chases the velocity-implied angle
ROT_DIVE_AFTER = 0.32              # seconds of falling before the nose starts to drop

FLAP_ANIM_FPS = 18.0
IDLE_BOB_AMPLITUDE = 14.0
IDLE_BOB_HZ = 0.75

# --------------------------------------------------------------------------
# obstacles
# --------------------------------------------------------------------------
PILLAR_W = 124
PILLAR_SPACING_START = 430         # horizontal distance between pillar centres
PILLAR_SPACING_END = 372
GAP_START = 400.0                  # vertical opening, world units
GAP_END = 302.0
SPEED_START = 322.0                # world units / s
SPEED_END = 476.0
RAMP_SCORE = 42                    # score at which the curve reaches its hardest values

# Keep the gap away from the sky's ceiling and the ground, with margin for the cap art.
GAP_MARGIN_TOP = 110.0
GAP_MARGIN_BOTTOM = 120.0
# Consecutive gaps may not jump more than this, or the game stops being readable.
GAP_MAX_DELTA = 260.0

GROUND_H = 170                     # world units from the collision surface to y = WORLD_H
# The ground *art* is drawn taller than that band, because a 20:9 phone can see
# 252 world units below the collision surface. Painting the surplus with one
# flat colour put a dead sixth of the screen under the player, so the strip
# carries real soil down to here and the flat skirt is only a last resort.
GROUND_ART_H = 262.0
CEILING_KILL = -140.0              # how far above the view the bird may climb before dying

# --------------------------------------------------------------------------
# pickups & progression
# --------------------------------------------------------------------------
COIN_SIZE = 56
COIN_CHANCE = 0.62                 # probability a given gap carries a feather token
COIN_VALUE = 1
COIN_STREAK_BONUS = 5              # extra feathers for collecting a full run of 5

THEME_EVERY = 12                   # score interval between theme changes
THEME_FADE = 2.2                   # seconds of cross-fade between themes

MEDAL_THRESHOLDS = (
    ("diamond", 200),
    ("platinum", 100),
    ("gold", 50),
    ("silver", 25),
    ("bronze", 10),
)

# --------------------------------------------------------------------------
# feel / juice
# --------------------------------------------------------------------------
SHAKE_ON_HIT = 26.0
SHAKE_ON_SCORE = 2.4
SHAKE_DECAY = 7.0
HIT_STOP_ON_DEATH = 0.14           # brief freeze on impact, then the fall
FLASH_ON_DEATH = 0.22
SCENE_FADE = 0.28

PARALLAX_FAR = 0.12                # layer scroll speed as a fraction of world speed
PARALLAX_MID = 0.26
PARALLAX_NEAR = 0.52
PARALLAX_CLOUD = 0.18
GROUND_SPEED_FRAC = 1.0

# --------------------------------------------------------------------------
# interface
# --------------------------------------------------------------------------
SAFE_PAD = 26                      # minimum inset from any screen edge, world units
TOUCH_MIN = 132                    # minimum tappable size (~48dp on a typical phone)

COLOR_TEXT = (255, 255, 255)
COLOR_TEXT_DIM = (196, 204, 222)
COLOR_GOLD = (255, 206, 92)
COLOR_DANGER = (255, 112, 104)
COLOR_GOOD = (122, 226, 168)
COLOR_SHADOW = (12, 14, 28)

FONT_FILE = "BaiJamjuree-Bold.ttf"
FONT_SIZES = {
    "mega": 132,
    "huge": 92,
    "title": 64,
    "large": 46,
    "body": 34,
    "small": 27,
    "tiny": 22,
}

# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------
AUDIO_RATE = 44100
AUDIO_BUFFER = 1024 if IS_MOBILE else 512
MUSIC_VOLUME = 0.42
SFX_VOLUME = 0.72


# --------------------------------------------------------------------------
# content constants shared with the art pipeline
# --------------------------------------------------------------------------
# `tools/manifest.py` is the authoring-side contract, but `tools/` is deliberately
# excluded from the Android bundle (it pulls in Pillow and numpy, which the game
# never needs at runtime). Anything the *running game* has to know therefore lives
# here instead, and `tests/test_contract.py` asserts the two never drift apart.

THEMES = ("dawn", "day", "dusk", "night")

BIRD_FRAMES = 4
COIN_FRAMES = 8

# (key, display name, unlock cost in feathers)
SKINS = (
    ("ember", "Ember", 0),
    ("sky", "Sky", 150),
    ("mint", "Mint", 300),
    ("violet", "Violet", 600),
    ("gold", "Gold", 1200),
    ("shadow", "Shadow", 2400),
)
SKIN_KEYS = tuple(key for key, _, _ in SKINS)

PARTICLE_SPRITES = ("soft", "spark", "star", "feather", "ring", "dust")

# Mirrors tools/manifest.PANEL_SIZE/PANEL_INSET -- tests/test_contract.py pins
# them together. See the note there: the inset is the panel's shading budget,
# not a cosmetic corner size.
PANEL_INSET = 150
BUTTON_INSET = 96

ICONS = (
    "play", "pause", "home", "restart", "settings", "shop", "trophy", "close",
    "sound_on", "sound_off", "music_on", "music_off", "haptics_on", "haptics_off",
    "left", "right", "check", "lock", "feather", "share", "star", "medal", "info",
    "dash", "speed", "bird",
)

MEDALS = ("bronze", "silver", "gold", "platinum", "diamond")

# Accent colour per theme, used to tint UI and particles so the interface shifts
# with the sky instead of sitting on top of it as a fixed overlay.
THEME_ACCENT = {
    "dawn": (255, 176, 118),
    "day": (255, 206, 92),
    "dusk": (255, 160, 108),
    "night": (126, 214, 255),
}

# World layer heights in world units. Widths follow from each source image's
# aspect ratio, so retouching the art never needs a code change.
LAYER_FAR_H = 291.8
LAYER_MID_H = 350.0
LAYER_NEAR_H = 290.0
# World y of each layer's *bottom* edge, relative to the ground surface. These
# live here rather than inside world.py so a preview rig (tools/mock_scene.py)
# composites at the shipping anchors instead of restating them and drifting.
# Far is negative: its silhouette is authored for the ~150-unit window it
# actually gets above the mid skyline, and everything below that window is
# cropped out of the texture rather than shipped and hidden.
LAYER_ANCHORS = {"far": -78.2, "mid": 26.0, "near": 8.0}
CLOUD_H = 150.0
CLOUD_COUNT = 5
CLOUD_BAND = (0.06, 0.52)   # vertical band clouds occupy, as a fraction of world height

# Sun/moon disc and star field. Both are separate sprites drawn at a uniform
# scale over the stretched sky; see the sky-furniture note in tools/manifest.py.
# Positions are fractions of the framebuffer, because that is what the sky
# strip is stretched across.
SUN_DRAW = {"dawn": 400.0, "day": 300.0, "dusk": 440.0, "night": 300.0}
SUN_POS = {
    "dawn": (0.30, 0.650),
    "day": (0.23, 0.130),
    "dusk": (0.74, 0.670),
    "night": (0.26, 0.150),
}
STAR_ALPHA = {"dusk": 104, "night": 255}


# --------------------------------------------------------------------------
# game speed
# --------------------------------------------------------------------------
# The reference pace is version 1's, so the speed setting means something
# concrete rather than being an arbitrary slider. Version 1 scrolled its
# pipes at 2.4 px per frame at 60 fps in a 600px-tall window -- 144 px/s -- which
# is 144 * (1080 / 600) = 259.2 units/s in this game's world.
LEGACY_SPEED = 259.2

# The player's multiplier is applied to that baseline. The difficulty ramp still
# runs on top of it, so a run always accelerates; this sets where it starts.
SPEED_SCALE_MIN = 0.5
SPEED_SCALE_MAX = 4.0
SPEED_SCALE_DEFAULT = 1.25      # 1.25 * 259.2 = 324 units/s, the tuned default

# How much faster the hardest point of the ramp is than its start, preserved
# from the hand-tuned SPEED_START -> SPEED_END curve.
SPEED_RAMP_FACTOR = 1.478

# Stepped rather than continuous: eight labelled presets are quicker to set on a
# phone than a slider, and each one is a pace a player can actually learn.
SPEED_PRESETS = (
    (0.5, "Relaxed"),
    (0.75, "Easy"),
    (1.0, "Classic"),
    (1.25, "Normal"),
    (1.5, "Brisk"),
    (2.0, "Fast"),
    (3.0, "Rapid"),
    (4.0, "Insane"),
)

# --------------------------------------------------------------------------
# dash
# --------------------------------------------------------------------------
# A short forward burst: the one skill move in the game. It does NOT grant
# invulnerability -- dashing into a pillar still kills -- so it stays a decision
# rather than an escape button, and the cooldown makes that decision cost
# something.
DASH_DISTANCE = 178.0           # world units gained at full extension
DASH_OUT = 0.20                 # seconds accelerating forward
DASH_HOLD = 0.07                # seconds held at full extension
DASH_RECOVER = 0.55             # seconds drifting back to the anchor
DASH_COOLDOWN = 1.45            # from activation; the meter refills over this
DASH_GRAVITY_SCALE = 0.16       # gravity is nearly cancelled mid-dash
DASH_VY_DAMP = 9.0              # and vertical velocity bleeds off, so it flies level
DASH_SHAKE = 5.0
DASH_TRAIL_HZ = 60.0            # trail particles per second while dashing

# The dash button must clear the pause button and the player's own thumb.
DASH_BUTTON_SIZE = 132.0

# Seconds of clear sky before the first pillar arrives. Expressed as TIME, not
# distance: a fixed spawn distance gives two seconds of runway at the default
# pace and barely half a second at 4x, which makes the fastest presets lose
# before the player has done anything.
INTRO_SECONDS = 1.9
