"""Runtime audio: sound effects and the looping music beds.

This module is deliberately numpy-free. `pygame.sndarray` (the obvious way to
vary a sound's pitch) needs numpy, and numpy is not in the Android APK -- it is
a build-time dependency only. So pitch variation is pre-rendered: `gen_audio.py`
writes `flap_0.wav` .. `flap_3.wav` at slightly different pitches and `play()`
picks between them. The chooser reads `assets/audio/index.json` with the stdlib
`json` module, and falls back to scanning the directory if that file is absent.

Everything here degrades to silence rather than failing. A device with no audio
output, a mixer that will not initialise, a WAV that did not make it into the
build, an `index.json` someone hand-edited into nonsense -- each of those leaves
the game completely playable, just quieter. There is no code path in this module
that can propagate an exception to the caller, so the index is validated once at
construction and every public argument is coerced rather than trusted.

Channel budget (`pygame.mixer.set_reserved` keeps the first few out of the pool
that `Sound.play()` draws from):

    0, 1    the two music beds, so one can cross-fade into the other
    2-4     critical cues (hit, fall, fanfares)
    5+      the free pool: flaps, coins, score blips, UI taps

Reserving matters because a flap fires up to ten times a second. Without it, a
long run of flaps owns every channel and the death cue -- the one sound the
player must hear -- is silently dropped.

Two channels means one rule governs all music: **never start a bed on a channel
that can still be heard.** Every path into playback goes through `_begin`, which
enforces it; a request that arrives too early is remembered and started by
`update` on the frame the spare channel actually frees.
"""
from __future__ import annotations

import json
import math
import os
import random
from typing import Any, Optional

import pygame

from . import config
from .platform import asset_root

AUDIO_SUBDIR = "audio"
INDEX_NAME = "index.json"

# --------------------------------------------------------------------------
# channel layout
# --------------------------------------------------------------------------
CH_MUSIC_A = 0
CH_MUSIC_B = 1
CH_CRITICAL_FIRST = 2
CRITICAL_CHANNELS = 3
RESERVED_CHANNELS = CH_CRITICAL_FIRST + CRITICAL_CHANNELS
MIXER_CHANNELS = 20

# Cues that are allowed to take a reserved channel. They are the ones that
# carry information the player acts on, or that only happen once per run.
CRITICAL = frozenset({"hit", "fall", "best", "medal", "unlock"})

# --------------------------------------------------------------------------
# mix
# --------------------------------------------------------------------------
# Every WAV is peak-normalised to the same ceiling by the generator, which makes
# the files consistent but says nothing about how loud each cue should *feel*.
# That judgement is here, because only the game knows a flap fires constantly
# and a fanfare fires once. Changing the balance of the game means editing this
# table, never re-rendering the audio.
MIX = {
    "flap": 0.34,
    "score": 0.78,
    "coin": 0.58,
    "hit": 1.00,
    "fall": 0.82,
    "ui_tap": 0.44,
    "ui_back": 0.44,
    "unlock": 0.88,
    "medal": 0.84,
    "best": 0.95,
    "countdown": 0.54,
    "streak": 0.68,
}
DEFAULT_MIX = 0.75

# Minimum seconds between two plays of the same cue. Two identical one-shots
# started a few milliseconds apart sum almost coherently, which is heard as one
# louder and harsher sound rather than two -- the classic "machine gun" artefact.
RETRIGGER = {"flap": 0.045, "coin": 0.035, "score": 0.050, "ui_tap": 0.050}
DEFAULT_RETRIGGER = 0.02

# --------------------------------------------------------------------------
# music
# --------------------------------------------------------------------------
MUSIC_XFADE = 1.6          # seconds, theme to theme
MUSIC_FADE_IN = 0.9        # seconds, silence to a bed
DUCK_SECONDS = 0.4
DUCK_AMOUNT = 0.5

# How many beds may stay decoded at once, and whether to decode them all up
# front. SDL_mixer converts a chunk to the mixer's own format on load, so a
# 22 kHz mono 820 kB file becomes ~3 MB of 44 kHz stereo in RAM: ~12 MB for all
# four themes, decoded in ~75 ms.
#
# That memory buys the absence of a stall. Decoding one bed costs ~19 ms, which
# is longer than a frame, and the theme changes every config.THEME_EVERY points
# -- i.e. in the middle of play, which is the one place a dropped frame is
# unacceptable in a reflex game. Loading up front moves the whole cost behind
# the loading screen. Set MUSIC_PRELOAD False to trade it back for the RAM.
MUSIC_RESIDENT = 4
MUSIC_PRELOAD = True

# Pitch range the fallback chooser assumes when index.json is missing and all we
# have are filenames; the real ratios live in the index.
FALLBACK_PITCH_LO = 0.90
FALLBACK_PITCH_HI = 1.12


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    # Spelled `not (value > lo)` rather than `value < lo` so a NaN lands on `lo`
    # instead of comparing False against both bounds and reaching SDL intact.
    if not (value > lo):
        return lo
    return hi if value > hi else value


def _clean_index(data: Any) -> "Optional[dict[str, Any]]":
    """Reduce a parsed index.json to the entries this module can actually use.

    Only the top-level "sfx" key used to be checked and everything below it was
    trusted, so a truncated or badly merged file parsed happily and then threw
    from inside `play()` -- on a device, on the first flap. Validating per entry
    rather than all-or-nothing means one mistyped variant costs that variant and
    not the other eleven cues. None means nothing usable survived, which is the
    caller's signal to scan the directory instead.
    """
    if not isinstance(data, dict):
        return None

    sfx: "dict[str, dict]" = {}
    raw_sfx = data.get("sfx")
    if isinstance(raw_sfx, dict):
        for name, entry in raw_sfx.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            variants: "list[dict]" = []
            for variant in (entry.get("variants") if isinstance(entry.get("variants"), list) else ()):
                if not isinstance(variant, dict):
                    continue
                filename = variant.get("file")
                if not isinstance(filename, str) or not filename:
                    continue
                try:
                    pitch = float(variant.get("pitch", 1.0))
                except (TypeError, ValueError):
                    pitch = 1.0
                if pitch != pitch:          # NaN would sort nowhere useful
                    pitch = 1.0
                variants.append({"file": filename, "pitch": pitch})
            if variants:
                pick = entry.get("pick")
                sfx[name] = {"pick": pick if isinstance(pick, str) else "nearest",
                             "variants": variants}
    if not sfx:
        return None

    music: "dict[str, dict]" = {}
    raw_music = data.get("music")
    if isinstance(raw_music, dict):
        for theme, entry in raw_music.items():
            if not isinstance(theme, str) or not isinstance(entry, dict):
                continue
            filename = entry.get("file")
            if isinstance(filename, str) and filename:
                music[theme] = {"file": filename}
    return {"sfx": sfx, "music": music}


class Audio:
    """The whole audio layer. One instance, created once, owned by the game."""

    def __init__(self, profile: Any) -> None:
        self.profile = profile
        self.dir = os.path.join(asset_root(), AUDIO_SUBDIR)

        self._rng = random.Random()
        self._sounds: "dict[str, Optional[pygame.mixer.Sound]]" = {}
        self._music: "dict[str, Optional[pygame.mixer.Sound]]" = {}
        self._warned: "set[str]" = set()
        self._recent: "dict[str, int]" = {}
        self._last_play: "dict[str, float]" = {}
        self._clock = 0.0
        self._crit_rr = 0
        self._pool_rr = 0

        self._sfx_on = bool(self._pref("sfx", True))
        self._music_on = bool(self._pref("music", True))

        self._theme: Optional[str] = None       # what the game last asked for
        self._playing: Optional[str] = None     # what is actually on a channel
        # Whether the game wants to hear music at all. Kept apart from
        # `_playing` because a start that had to wait for a busy channel looks
        # exactly like a deliberate stop if all you have is what is on a
        # channel -- and confusing the two means either silence that never
        # recovers or music that comes back by itself.
        self._want_music = False
        self._slot = 0                          # which music channel is the live one
        self._on_channel: "list[Optional[str]]" = [None, None]
        self._gain = [0.0, 0.0]
        self._target = [0.0, 0.0]
        # One ramp rate per slot, in gain per second. A single shared fade
        # duration let any new fade silently re-time one already in flight.
        self._rate = [0.0, 0.0]
        self._duck_level = 1.0
        self._duck_rate = 0.0

        self._index = self._read_index()
        self._sfx: "dict[str, dict]" = self._index.get("sfx", {})
        self._beds: "dict[str, dict]" = self._index.get("music", {})

        self.available = self._init_mixer()
        if self.available:
            self._preload()

    # -- introspection -----------------------------------------------------
    @property
    def sfx_enabled(self) -> bool:
        return self._sfx_on

    @property
    def music_enabled(self) -> bool:
        return self._music_on

    @property
    def theme(self) -> Optional[str]:
        return self._theme

    def names(self) -> "list[str]":
        """Every cue name `play()` will accept."""
        return sorted(self._sfx)

    def themes(self) -> "list[str]":
        return sorted(self._beds)

    # -- construction helpers ---------------------------------------------
    def _pref(self, key: str, default: bool) -> bool:
        try:
            return bool(self.profile[key])
        except Exception:
            return default

    def _warn(self, message: str) -> None:
        if message not in self._warned:
            self._warned.add(message)
            print("[audio] %s" % message)

    def _init_mixer(self) -> bool:
        """Bring the mixer up on our terms. False means "run the game silently"."""
        want = (config.AUDIO_RATE, -16, 2)
        try:
            # The mixer is re-initialised even when one is already running,
            # because `get_init()` does not report the buffer size and the buffer
            # size is the single biggest lever on tap-to-sound latency on
            # Android. A mixer someone else started with the default buffer
            # would make every flap feel late.
            if pygame.mixer.get_init() is not None:
                pygame.mixer.quit()
            pygame.mixer.pre_init(want[0], want[1], want[2], config.AUDIO_BUFFER)
            pygame.mixer.init(want[0], want[1], want[2], config.AUDIO_BUFFER)
            pygame.mixer.set_num_channels(MIXER_CHANNELS)
            pygame.mixer.set_reserved(RESERVED_CHANNELS)
        except (pygame.error, OSError, ValueError) as exc:
            self._warn("mixer unavailable (%s); running silently" % exc)
            return False
        return True

    def _read_index(self) -> "dict[str, Any]":
        """Load the chooser's table, accepting only what is usable.

        The shape is checked once, here, instead of being discovered by a
        traceback later: `_scan_dir` was only ever reached on a JSON *parse*
        error, so a file that parsed but lied about its contents sailed through.
        """
        clean: "Optional[dict[str, Any]]" = None
        reason = ""
        try:
            with open(os.path.join(self.dir, INDEX_NAME), "r", encoding="utf-8") as fh:
                clean = _clean_index(json.load(fh))
            if clean is None:
                reason = "not the shape this module expects"
        except Exception as exc:        # unreadable, not JSON, not even text
            reason = str(exc) or type(exc).__name__

        if clean is None:
            self._warn("no usable %s (%s); falling back to a directory scan"
                       % (INDEX_NAME, reason))
            return self._scan_dir()
        if not clean["music"]:
            # Only the music half was unusable. Bed filenames follow a fixed
            # pattern, so they are recoverable from the directory alone, and
            # recovering them beats shipping a silent game.
            clean["music"] = self._scan_dir().get("music", {})
        return clean

    def _scan_dir(self) -> "dict[str, Any]":
        """Rebuild the index from filenames alone.

        Only a degradation path: the real pitch ratios live in index.json, so
        here they are assumed to be evenly spaced across the usual range. A cue
        asked for by pitch lands on roughly the right variant, which is enough
        to keep the game sounding correct if the index is ever lost.
        """
        groups: "dict[str, list[int]]" = {}
        beds: "dict[str, dict]" = {}
        try:
            entries = os.listdir(self.dir)
        except OSError:
            return {"sfx": {}, "music": {}}

        for entry in sorted(entries):
            if not entry.endswith(".wav"):
                continue
            stem = entry[:-4]
            if stem.startswith("music_"):
                beds[stem[len("music_"):]] = {"file": entry}
                continue
            base, _, tail = stem.rpartition("_")
            if base and tail.isdigit():
                groups.setdefault(base, []).append(int(tail))

        sfx: "dict[str, dict]" = {}
        for base, indices in groups.items():
            indices.sort()
            count = len(indices)
            variants = []
            for slot, idx in enumerate(indices):
                if count == 1:
                    pitch = 1.0
                else:
                    span = FALLBACK_PITCH_HI - FALLBACK_PITCH_LO
                    pitch = FALLBACK_PITCH_LO + span * slot / float(count - 1)
                variants.append({"file": "%s_%d.wav" % (base, idx), "pitch": pitch})
            sfx[base] = {"pick": "random" if count > 1 else "nearest", "variants": variants}
        return {"sfx": sfx, "music": beds}

    def _preload(self) -> None:
        """Decode everything up front, ~110 ms, behind whatever the game shows first.

        Loading a Sound on first use costs a file read and a format conversion on
        the frame that wanted it, which lands as a stutter at exactly the wrong
        moment -- the first flap, the death cue, or a theme change mid-run.
        """
        for entry in self._sfx.values():
            for variant in entry.get("variants", ()):
                self._sound(variant.get("file", ""))
        if MUSIC_PRELOAD:
            # Capped at the residency limit: loading more beds than may stay
            # resident would just decode them and immediately evict them.
            for theme in list(self._beds)[:MUSIC_RESIDENT]:
                self._bed(theme)

    # -- loading -----------------------------------------------------------
    def _sound(self, filename: str) -> "Optional[pygame.mixer.Sound]":
        if not filename:
            return None
        if filename in self._sounds:
            return self._sounds[filename]
        snd: "Optional[pygame.mixer.Sound]" = None
        try:
            snd = pygame.mixer.Sound(os.path.join(self.dir, filename))
        except Exception:               # a loader may not raise past this layer
            self._warn("missing or unreadable sound: %s" % (filename,))
        self._sounds[filename] = snd
        return snd

    def _bed(self, theme: "Optional[str]") -> "Optional[pygame.mixer.Sound]":
        # A theme name that is not a usable string cannot index the cache, let
        # alone name a file; refusing it here keeps every caller simple.
        if not isinstance(theme, str) or not theme:
            return None
        if theme in self._music:
            return self._music[theme]
        snd: "Optional[pygame.mixer.Sound]" = None
        filename = ""
        try:
            filename = (self._beds.get(theme) or {}).get("file") or ("music_%s.wav" % theme)
            snd = pygame.mixer.Sound(os.path.join(self.dir, filename))
        except Exception:
            self._warn("missing or unreadable music bed: %s" % (filename or theme))
        self._music[theme] = snd
        self._evict_beds()
        return snd

    def _evict_beds(self) -> None:
        """Hold at most MUSIC_RESIDENT beds, dropping the least recently loaded.

        Never drops the bed that is playing or the one being faded in; dict
        insertion order does the rest.
        """
        if len(self._music) <= MUSIC_RESIDENT:
            return
        protected = {self._playing, self._theme,
                     self._on_channel[0], self._on_channel[1]}
        for key in list(self._music):
            if len(self._music) <= MUSIC_RESIDENT:
                break
            if key not in protected:
                del self._music[key]

    # -- sound effects -----------------------------------------------------
    def _choose(self, name: str, entry: "dict[str, Any]", pitch: float) -> str:
        variants = entry.get("variants") or ()
        if not variants:
            return ""
        if len(variants) == 1:
            return variants[0].get("file", "")

        asked = abs(pitch - 1.0) > 1e-3
        if asked or entry.get("pick") != "random":
            best = min(range(len(variants)),
                       key=lambda i: abs(float(variants[i].get("pitch", 1.0)) - pitch))
            return variants[best].get("file", "")

        # Random, but never the same variant twice running: an immediate repeat
        # is precisely the sameness the variants exist to hide, and it is the
        # most audible failure of a randomised cue.
        index = self._rng.randrange(len(variants))
        last = self._recent.get(name, -1)
        if index == last:
            index = (last + 1 + self._rng.randrange(len(variants) - 1)) % len(variants)
        self._recent[name] = index
        return variants[index].get("file", "")

    def _critical_channel(self) -> "Optional[pygame.mixer.Channel]":
        try:
            for i in range(CH_CRITICAL_FIRST, CH_CRITICAL_FIRST + CRITICAL_CHANNELS):
                channel = pygame.mixer.Channel(i)
                if not channel.get_busy():
                    return channel
            # All busy: round-robin rather than always stealing the same one, so
            # a hit followed immediately by a fall does not cut the hit short.
            self._crit_rr = (self._crit_rr + 1) % CRITICAL_CHANNELS
            return pygame.mixer.Channel(CH_CRITICAL_FIRST + self._crit_rr)
        except (pygame.error, IndexError):
            return None

    def _free_channel(self) -> "Optional[pygame.mixer.Channel]":
        """An idle channel from the unreserved pool, picked explicitly.

        `Sound.play()` also honours the reservation, but it starts the sound
        before the volume can be set on its channel -- and setting the volume on
        the *Sound* instead is global to every channel playing it, so a second
        flap would retroactively change the level of the first one still ringing.
        Choosing the channel ourselves is the only way to have the gain correct
        from the very first sample.

        Note that `pygame.mixer.find_channel()` is not usable here: measured
        against pygame 2.6.1 it hands back reserved channels too, so a flap could
        land on top of a music bed.
        """
        try:
            pool = pygame.mixer.get_num_channels() - RESERVED_CHANNELS
            if pool <= 0:
                return None
            for k in range(pool):
                index = RESERVED_CHANNELS + (self._pool_rr + k) % pool
                channel = pygame.mixer.Channel(index)
                if not channel.get_busy():
                    self._pool_rr = (index - RESERVED_CHANNELS + 1) % pool
                    return channel
        except (pygame.error, IndexError):
            return None
        # Pool exhausted. Dropping this cue is correct: everything that reaches
        # here is a flap, a coin or a UI tap, and stealing a channel would cut
        # off a sound the player is already hearing to start one more of many.
        return None

    def play(self, name: str, volume: float = 1.0, pitch: float = 1.0) -> None:
        """Fire a one-shot cue.

        `pitch` is a hint, not a resampler: it selects the pre-rendered variant
        closest to that ratio. Cues marked "random" in the index ignore a pitch
        of exactly 1.0 and shuffle instead, which is what keeps a hundred flaps
        in a row from sounding like one looped sample.

        The repeat guard is measured on the clock that `update()` advances, so a
        game that stops calling `update()` also stops hearing repeated cues.
        That dependency is deliberate: a deterministic clock keeps the guard
        testable and keeps it correct if the game is ever run at a time scale
        other than real time.
        """
        if not self.available or not self._sfx_on or not isinstance(name, str):
            return
        entry = self._sfx.get(name)
        if not entry:
            self._warn("unknown cue: %s" % (name,))
            return
        # The game is not the only caller -- a scene passes whatever it computed
        # -- and this layer promises never to raise, so arguments are coerced
        # rather than trusted.
        try:
            volume, pitch = float(volume), float(pitch)
        except (TypeError, ValueError):
            volume, pitch = 1.0, 1.0
        if volume != volume or pitch != pitch:      # NaN
            volume, pitch = 1.0, 1.0

        gap = RETRIGGER.get(name, DEFAULT_RETRIGGER)
        last = self._last_play.get(name)
        if last is not None and (self._clock - last) < gap:
            return

        # Belt and braces behind _clean_index: whatever the index turned out to
        # contain, a cue that cannot be resolved is silence, not a crash.
        try:
            snd = self._sound(self._choose(name, entry, pitch))
        except Exception:
            self._warn("unusable entry for cue: %s" % (name,))
            return
        if snd is None:
            return

        level = _clamp(volume) * MIX.get(name, DEFAULT_MIX) * float(config.SFX_VOLUME)
        channel = self._critical_channel() if name in CRITICAL else self._free_channel()
        if channel is None:
            return
        try:
            # Gain lives entirely on the channel; Sound.set_volume is never
            # touched, so two overlapping copies of one variant stay independent.
            channel.set_volume(_clamp(level))
            channel.play(snd)
        except (pygame.error, AttributeError):
            return
        self._last_play[name] = self._clock

    # -- music -------------------------------------------------------------
    def _default_theme(self) -> Optional[str]:
        if self._beds:
            return sorted(self._beds)[0]
        return None

    def _channel(self, slot: int) -> "Optional[pygame.mixer.Channel]":
        try:
            return pygame.mixer.Channel(CH_MUSIC_A + slot)
        except (pygame.error, IndexError):
            return None

    def _aim(self, slot: int, target: float, fade: float) -> None:
        """Point one slot's gain at `target`, to arrive `fade` seconds from now.

        The rate comes from the distance still to travel, not from the full
        0..1 range, so a ramp that is interrupted half way still takes the time
        it was given instead of racing to the end. That, plus one rate per slot
        rather than one shared duration, is what makes two overlapping fades
        independent: a stop landing on a bed that is already fading can no
        longer re-time the bed next to it.
        """
        self._target[slot] = target
        self._rate[slot] = abs(target - self._gain[slot]) / max(0.05, float(fade))

    def _retarget(self, theme: str, fade: float) -> bool:
        """Turn a bed that is already on a channel back up, if it is this one.

        A bed that is fading out is still *playing*. Starting it again on the
        other channel would put two copies of one loop, at different offsets,
        into the same mix, and two phase-offset copies of the same material comb
        and flange -- they do not cross-fade. The case that matters is the
        settings screen: toggling music off and straight back on has to catch
        the bed on its way down, not layer a second copy over it.
        """
        if not isinstance(theme, str) or not theme:
            return False
        for slot in (self._slot, 1 - self._slot):
            if self._on_channel[slot] != theme:
                continue
            self._aim(slot, 1.0, fade)
            self._aim(1 - slot, 0.0, fade)
            self._slot = slot
            self._playing = theme
            self._want_music = True
            self._apply_music_volume()
            return True
        return False

    def _begin(self, theme: str, fade: float) -> bool:
        """Start `theme` on the idle music channel and fade the other one out.

        False means "not now": either the bed will not load, or -- the usual
        reason -- the spare channel can still be heard.
        """
        other = 1 - self._slot
        # Only two channels exist, so reusing one that is still audible cuts
        # that bed to silence in a single sample: precisely the pop a cross-fade
        # exists to prevent. The guard lives here, not in the callers, because
        # every path into playback funnels through this method and the one path
        # that had its own copy of the check was the one that was wrong. Callers
        # record the theme they wanted before calling, so `update` can start the
        # change on the frame the channel genuinely frees.
        if self._gain[other] > 0.0:
            return False
        snd = self._bed(theme)
        if snd is None:
            return False
        channel = self._channel(other)
        if channel is None:
            return False
        try:
            channel.set_volume(0.0)
            channel.play(snd, loops=-1)
        except (pygame.error, AttributeError):
            return False

        self._gain[other] = 0.0
        self._on_channel[other] = theme
        self._aim(other, 1.0, fade)
        self._aim(self._slot, 0.0, fade)        # before _slot moves
        self._slot = other
        self._playing = theme
        self._apply_music_volume()
        return True

    def start_music(self, theme: Optional[str] = None) -> None:
        if isinstance(theme, str) and theme:
            self._theme = theme
        if self._theme is None:
            self._theme = self._default_theme()
        if self._theme is None or not self.available or not self._music_on:
            return
        # Recorded before anything can refuse. A start that has to wait for a
        # channel is still the game's standing request, and this flag is the
        # only thing that distinguishes it from a deliberate stop.
        self._want_music = True
        # Already playing this bed: returning instead of restarting is the whole
        # point of "begin *or continue*" -- a restart would jump the loop.
        if self._playing == self._theme and self._target[self._slot] > 0.0:
            return
        if self._retarget(self._theme, MUSIC_FADE_IN):
            return
        self._begin(self._theme, MUSIC_FADE_IN)

    def set_theme(self, theme: str) -> None:
        """Cross-fade to another theme's bed.

        With no bed playing this only records the theme and `start_music` is
        what begins playback. Starting from silence here would let a routine
        mid-run theme change override a deliberate `stop_music`, which is the
        kind of bug that only shows up as "the music came back by itself".
        """
        if theme == self._theme or not isinstance(theme, str) or not theme:
            return
        self._theme = theme
        if not self.available or not self._music_on:
            return
        if self._playing is None or self._target[self._slot] <= 0.0:
            return
        # Changing back to the bed that is still fading out: reverse the fade
        # rather than queue a restart, which would jump its loop.
        if self._retarget(theme, MUSIC_XFADE):
            return
        # `_begin` refuses while the spare channel is audible; the theme is
        # already recorded, so `update` starts the change once it frees.
        self._begin(theme, MUSIC_XFADE)

    def stop_music(self, fade_ms: int = 400) -> None:
        try:
            fade = max(0.0, float(fade_ms) / 1000.0)
        except (TypeError, ValueError):
            fade = 0.4
        if fade != fade:                # NaN
            fade = 0.4
        self._playing = None
        self._want_music = False
        if fade <= 0.0:
            self._gain[0] = self._gain[1] = 0.0
            self._target[0] = self._target[1] = 0.0
            self._rate[0] = self._rate[1] = 0.0
            self._silence_music()
            return
        # A stop is a deadline rather than a suggestion: everything still
        # audible, including a bed already fading out on a slower ramp of its
        # own, has to arrive at silence together, when the caller said.
        self._aim(0, 0.0, fade)
        self._aim(1, 0.0, fade)
        self._apply_music_volume()

    def _release(self, slot: int) -> None:
        """Stop a music channel if it is still holding a bed."""
        self._on_channel[slot] = None
        channel = self._channel(slot)
        if channel is None:
            return
        try:
            if channel.get_busy():
                channel.stop()
        except (pygame.error, AttributeError):
            pass

    def _silence_music(self) -> None:
        for slot in (0, 1):
            self._release(slot)

    def _apply_music_volume(self) -> None:
        if not self.available:
            return
        base = float(config.MUSIC_VOLUME) * self._duck_level
        for slot in (0, 1):
            channel = self._channel(slot)
            if channel is None:
                continue
            try:
                # Equal *power*, not equal gain. The beds are deliberately
                # different music, so they do not correlate and what is heard is
                # the sum of their powers: two linear ramps crossing at 0.5 each
                # sum to 0.707, an audible -3 dB scoop in the middle of every
                # theme change -- five times the 0.61 dB spread the generator
                # works to hold between beds. Taking the root on the way out
                # makes the powers sum to one while leaving `_gain` linear, so
                # the ramp arithmetic and the release test stay exact.
                channel.set_volume(_clamp(base * math.sqrt(_clamp(self._gain[slot]))))
            except (pygame.error, AttributeError):
                pass

    def duck(self, amount: float = DUCK_AMOUNT, seconds: float = DUCK_SECONDS) -> None:
        """Pull the bed down under a big cue, then let it climb back.

        The deepest duck in flight wins, so a medal landing during a death duck
        does not accidentally raise the music back up.
        """
        try:
            amount, seconds = float(amount), float(seconds)
        except (TypeError, ValueError):
            amount, seconds = DUCK_AMOUNT, DUCK_SECONDS
        level = _clamp(1.0 - _clamp(amount))
        self._duck_level = min(self._duck_level, level)
        self._duck_rate = (1.0 - self._duck_level) / max(1e-3, seconds)
        self._apply_music_volume()

    # -- settings ----------------------------------------------------------
    def _store(self, key: str, value: bool) -> None:
        try:
            if bool(self.profile[key]) != value:
                self.profile[key] = value
                self.profile.save()
        except Exception:
            # A read-only profile must not stop the toggle taking effect for
            # this session; it just will not survive a restart.
            pass

    def set_sfx_enabled(self, on: bool) -> None:
        on = bool(on)
        self._sfx_on = on
        self._store("sfx", on)
        if not on:
            self._stop_sfx()

    def set_music_enabled(self, on: bool) -> None:
        on = bool(on)
        self._music_on = on
        self._store("music", on)
        if on:
            self.start_music(self._theme)
        else:
            self.stop_music(fade_ms=250)

    def _stop_sfx(self) -> None:
        """Silence every effect channel, leaving the music beds untouched."""
        if not self.available:
            return
        try:
            total = pygame.mixer.get_num_channels()
        except (pygame.error, AttributeError):
            return
        for i in range(CH_CRITICAL_FIRST, total):
            try:
                pygame.mixer.Channel(i).stop()
            except (pygame.error, IndexError):
                continue

    # -- frame -------------------------------------------------------------
    def update(self, dt: float) -> None:
        """Advance fades, ducking and the repeat guard's clock.

        Must be called once per frame with elapsed seconds; `play()`'s repeat
        suppression is measured against the time accumulated here.
        """
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        # `not (dt > 0.0)` rather than `dt <= 0.0` so a NaN frame time is
        # rejected too. A NaN added to the clock stays there for the rest of
        # the session, and every comparison against it is False, which silently
        # disables the retrigger guard -- a fault that is inaudible as a crash
        # and very audible as machine-gunned flaps.
        if not (dt > 0.0):
            dt = 0.0
        self._clock += dt
        if not self.available:
            return

        changed = False
        for slot in (0, 1):
            gain, target = self._gain[slot], self._target[slot]
            if gain != target:
                step = self._rate[slot] * dt
                if gain < target:
                    self._gain[slot] = min(target, gain + step)
                else:
                    self._gain[slot] = max(target, gain - step)
                changed = True
            # Release the channel whenever a bed is both silent and meant to be
            # silent -- tested here rather than only on the frame a fade lands,
            # because a bed stopped before it ever faded up starts and ends at
            # gain 0 and would otherwise hold its channel, decoding inaudibly,
            # for the rest of the session.
            if self._gain[slot] <= 0.0 and self._target[slot] <= 0.0:
                self._release(slot)

        # Start whatever had to wait for the spare channel: a theme change
        # mid cross-fade, or a start_music that arrived during one. Both are
        # refused by `_begin` rather than allowed to cut a bed dead, so this is
        # where they actually land. `_want_music` is what keeps a deliberate
        # stop from being resurrected here.
        if (self._music_on and self._want_music and self._theme is not None
                and self._playing != self._theme
                and self._gain[1 - self._slot] <= 0.0):
            # A bed is still up, so this is a change of theme; nothing is, so
            # it is a start from silence and gets the shorter fade.
            fade = MUSIC_XFADE if self._target[self._slot] > 0.0 else MUSIC_FADE_IN
            if not self._begin(self._theme, fade):
                if self._playing is not None:
                    # The bed will not load. Stop asking for it every frame and
                    # stay on the one that is already playing.
                    self._theme = self._playing
                else:
                    # Nothing to fall back to. Keep the theme recorded, since
                    # the game still believes in it, but stop retrying it.
                    self._want_music = False
            changed = True

        if self._duck_level < 1.0:
            self._duck_level = min(1.0, self._duck_level + self._duck_rate * dt)
            changed = True

        if changed:
            self._apply_music_volume()

    def stop_all(self) -> None:
        """Hard stop: every channel, no fade. For quitting or going to background."""
        self._playing = None
        self._want_music = False
        self._on_channel = [None, None]
        self._gain = [0.0, 0.0]
        self._target = [0.0, 0.0]
        self._rate = [0.0, 0.0]
        self._duck_level = 1.0
        self._duck_rate = 0.0
        if not self.available:
            return
        try:
            pygame.mixer.stop()
        except (pygame.error, AttributeError):
            pass
