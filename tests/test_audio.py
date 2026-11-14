#!/usr/bin/env python3
"""Self-test for the audio layer: the generated WAVs and `featherdash.audio`.

Runs headless and exits non-zero on the first failure it finds, so it can be a
build gate. Deliberately uses only the standard library to inspect the audio
files -- the same constraint the runtime is under -- so it cannot pass by
accident on a machine that happens to have numpy.

Run::

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python tests/test_audio.py
"""
from __future__ import annotations

import array
import json
import math
import operator
import os
import shutil
import subprocess
import sys
import tempfile
import wave

# SDL reads these when a subsystem initialises, so they must be in place before
# anything touches pygame -- including the import below.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame
import pytest  # noqa: E402

from featherdash import audio as audio_mod  # noqa: E402
from featherdash import config  # noqa: E402
from featherdash import save as save_mod  # noqa: E402

AUDIO_DIR = os.path.join(ROOT, "assets", "audio")
FULL_SCALE = 32700          # anything at or past this counts as clipped
MIN_PEAK = 0.20             # below this the file is effectively silent
MAX_PEAK = 0.95             # above this there is no headroom left
LOOP_MIN_S = 16.0
LOOP_MAX_S = 24.0

FAILURES: "list[str]" = []
CHECKS = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global CHECKS
    CHECKS += 1
    if not ok:
        FAILURES.append(label + ((" -- " + detail) if detail else ""))
        print("  FAIL  %s%s" % (label, (" -- " + detail) if detail else ""))
    return bool(ok)


def section(title: str) -> None:
    print("\n== %s ==" % title)


# --------------------------------------------------------------------------
# stdlib WAV inspection
# --------------------------------------------------------------------------
def read_wav(path: str) -> "tuple[wave._wave_params, array.array]":
    with wave.open(path, "rb") as fh:
        params = fh.getparams()
        raw = fh.readframes(fh.getnframes())
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()      # WAV is little-endian regardless of the host
    return params, samples


def longest_clip_run(samples: "array.array") -> int:
    """Longest run of consecutive samples pinned at full scale.

    A single full-scale sample is a legitimate peak; a *run* of them is a
    flat-topped waveform, which is audible distortion. Scanning only happens
    when the peak is high enough to matter, because the scan is the slow part.
    """
    if not samples:
        return 0
    if max(max(samples), -min(samples)) < FULL_SCALE:
        return 0
    best = run = 0
    for value in samples:
        run = run + 1 if (value >= FULL_SCALE or value <= -FULL_SCALE) else 0
        if run > best:
            best = run
    return best


def peak_fraction(samples: "array.array") -> float:
    if not samples:
        return 0.0
    return max(max(samples), -min(samples)) / 32768.0


def seam_stats(samples: "array.array") -> "tuple[int, int]":
    """(step across the loop join, largest step inside the loop).

    A seamless loop is not one whose join step is zero -- only silence manages
    that. It is one whose join step is an ordinary step of the waveform.
    """
    if len(samples) < 3:
        return 0, 0
    join = abs(samples[0] - samples[-1])
    worst = max(map(abs, map(operator.sub, samples[1:], samples)))
    return join, worst


# --------------------------------------------------------------------------
# part 1: the generated files
# --------------------------------------------------------------------------
def build_asset_index() -> "dict":
    section("generated audio files")

    index_path = os.path.join(AUDIO_DIR, "index.json")
    if not check(os.path.isfile(index_path), "index.json exists", index_path):
        return {}
    with open(index_path, "r", encoding="utf-8") as fh:
        index = json.load(fh)

    check(index.get("sfx_rate") == config.AUDIO_RATE,
          "index sfx_rate matches config.AUDIO_RATE",
          "%s vs %s" % (index.get("sfx_rate"), config.AUDIO_RATE))
    music_rate = int(index.get("music_rate", 0))
    check(music_rate > 0, "index declares a music rate")

    sfx = index.get("sfx", {})
    check(len(sfx) >= 12, "at least the 12 specified cues exist", "got %d" % len(sfx))
    for name in ("flap", "score", "coin", "hit", "fall", "ui_tap", "ui_back",
                 "unlock", "medal", "best", "countdown", "streak"):
        check(name in sfx, "cue present: %s" % name)

    for name, entry in sorted(sfx.items()):
        variants = entry.get("variants", [])
        check(len(variants) >= 1, "%s has at least one variant" % name)
        pitches = [float(v["pitch"]) for v in variants]
        check(pitches == sorted(pitches), "%s variants ascend in pitch" % name, str(pitches))
        for variant in variants:
            path = os.path.join(AUDIO_DIR, variant["file"])
            if not check(os.path.isfile(path), "exists: %s" % variant["file"]):
                continue
            check(os.path.getsize(path) > 0, "non-empty: %s" % variant["file"])
            params, samples = read_wav(path)
            check(params.framerate == config.AUDIO_RATE,
                  "sample rate: %s" % variant["file"], str(params.framerate))
            check(params.sampwidth == 2, "16-bit: %s" % variant["file"])
            check(params.nchannels == 2, "stereo: %s" % variant["file"])
            check(params.nframes > 0, "has frames: %s" % variant["file"])
            run = longest_clip_run(samples)
            check(run <= 2, "no sustained clipping: %s" % variant["file"],
                  "%d consecutive full-scale samples" % run)
            frac = peak_fraction(samples)
            check(MIN_PEAK <= frac <= MAX_PEAK, "sane peak: %s" % variant["file"],
                  "%.3f of full scale" % frac)

    beds = index.get("music", {})
    check(len(beds) == 4, "four music beds", "got %d" % len(beds))
    for theme, entry in sorted(beds.items()):
        path = os.path.join(AUDIO_DIR, entry["file"])
        if not check(os.path.isfile(path), "exists: %s" % entry["file"]):
            continue
        check(os.path.getsize(path) > 0, "non-empty: %s" % entry["file"])
        params, samples = read_wav(path)
        check(params.framerate == music_rate, "sample rate: %s" % entry["file"],
              str(params.framerate))
        check(params.sampwidth == 2, "16-bit: %s" % entry["file"])
        seconds = params.nframes / float(params.framerate)
        check(LOOP_MIN_S <= seconds <= LOOP_MAX_S,
              "loop length in spec: %s" % entry["file"], "%.2f s" % seconds)
        run = longest_clip_run(samples)
        check(run <= 2, "no sustained clipping: %s" % entry["file"],
              "%d consecutive full-scale samples" % run)
        frac = peak_fraction(samples)
        check(MIN_PEAK <= frac <= MAX_PEAK, "sane peak: %s" % entry["file"],
              "%.3f of full scale" % frac)
        join, worst = seam_stats(samples)
        check(join <= worst, "seamless loop join: %s" % entry["file"],
              "join %d LSB vs largest internal step %d" % (join, worst))
        print("    %-7s %6.2fs  join %4d LSB / max step %4d  (%.1f%% of an ordinary step)"
              % (theme, seconds, join, worst, 100.0 * join / max(1, worst)))

    total = sum(os.path.getsize(os.path.join(AUDIO_DIR, f))
                for f in os.listdir(AUDIO_DIR)
                if os.path.isfile(os.path.join(AUDIO_DIR, f)))
    print("    assets/audio total: %d bytes (%.2f MB)" % (total, total / 1048576.0))
    return index


# --------------------------------------------------------------------------
# part 2: the runtime never imports numpy
# --------------------------------------------------------------------------
NUMPY_FREE_PROBE = '''
import os, sys
class NoNumpy:
    """Make numpy unimportable, the way the Android build makes it unimportable."""
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "numpy" or fullname.startswith("numpy."):
            raise ImportError("numpy is not present in the APK")
        return None
sys.meta_path.insert(0, NoNumpy())
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, %(root)r)
from featherdash import audio as A, save as S
S.data_dir = lambda: %(tmp)r
a = A.Audio(S.Profile())
for name in a.names():
    a.play(name)
    a.update(0.05)
a.start_music()
for theme in a.themes():
    a.set_theme(theme)
    a.update(0.1)
a.duck()
for _ in range(30):
    a.update(1.0 / 60.0)
a.stop_all()
assert "numpy" not in sys.modules, "numpy was imported at runtime"
print("ok available=%%s cues=%%d themes=%%d" %% (a.available, len(a.names()), len(a.themes())))
'''


def test_runtime_is_numpy_free() -> None:
    section("runtime is numpy-free")
    tmp = tempfile.mkdtemp(prefix="fd-audio-np-")
    script = os.path.join(tmp, "probe.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(NUMPY_FREE_PROBE % {"root": ROOT, "tmp": tmp})
    proc = subprocess.run([sys.executable, script], capture_output=True, text=True, timeout=120)
    ok = check(proc.returncode == 0,
               "featherdash.audio runs with numpy blocked",
               (proc.stderr or "").strip().splitlines()[-1] if proc.stderr.strip() else "")
    if ok:
        print("    subprocess: %s" % proc.stdout.strip().splitlines()[-1])
    # Belt and braces: parse the module and look at its actual import
    # statements, so a numpy import hidden behind a branch the probe happens not
    # to reach still fails the build. Prose mentioning numpy is not an import.
    import ast
    source_path = os.path.join(ROOT, "featherdash", "audio.py")
    with open(source_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), source_path)
    imported: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = imported & {"numpy", "PIL", "scipy"}
    check(not banned, "featherdash/audio.py imports no build-time-only package",
          str(sorted(banned)))
    print("    imports: %s" % ", ".join(sorted(imported)))


# --------------------------------------------------------------------------
# part 3: the Audio object
# --------------------------------------------------------------------------
def temp_profile() -> "tuple[object, str]":
    """A Profile rooted in a throwaway directory, so the test cannot touch the
    developer's real save file."""
    tmp = tempfile.mkdtemp(prefix="fd-audio-")
    save_mod.data_dir = lambda: tmp
    return save_mod.Profile(), tmp


def sfx_busy() -> int:
    busy = 0
    for i in range(audio_mod.CH_CRITICAL_FIRST, pygame.mixer.get_num_channels()):
        if pygame.mixer.Channel(i).get_busy():
            busy += 1
    return busy


def music_busy() -> int:
    return sum(1 for i in (audio_mod.CH_MUSIC_A, audio_mod.CH_MUSIC_B)
               if pygame.mixer.Channel(i).get_busy())


def run_updates(aud: "audio_mod.Audio", seconds: float, step: float = 1.0 / 60.0) -> None:
    elapsed = 0.0
    while elapsed < seconds:
        aud.update(step)
        elapsed += step


@pytest.fixture(autouse=True)
def _fail_on_checks():
    """Make a recorded `check()` failure fail the pytest run too.

    `check` collects instead of raising, so running this file as a script can
    report every problem in one pass. Under pytest that would pass silently, so
    each test is bracketed here and fails with whatever it recorded.
    """
    before = len(FAILURES)
    yield
    recorded = FAILURES[before:]
    assert not recorded, "\n".join(recorded)


@pytest.fixture(scope="module")
def index() -> "dict":
    """The generated-audio index, shared by the tests that inspect it."""
    return build_asset_index()


def test_generated_assets() -> None:
    build_asset_index()


def test_audio_runtime(index: "dict") -> None:
    section("Audio runtime")
    profile, tmp = temp_profile()
    aud = audio_mod.Audio(profile)
    check(aud.available, "mixer came up under the dummy driver")
    check(pygame.mixer.get_init()[0] == config.AUDIO_RATE,
          "mixer runs at config.AUDIO_RATE", str(pygame.mixer.get_init()))
    check(pygame.mixer.get_num_channels() == audio_mod.MIXER_CHANNELS,
          "channel count set", str(pygame.mixer.get_num_channels()))

    names = aud.names()
    check(len(names) >= 12, "every cue is known to the runtime", str(names))

    # Every cue, at the default pitch and at each pre-rendered variant pitch,
    # plus deliberately silly arguments.
    for name in names:
        aud.play(name)
        aud.update(0.1)
        for pitch in (0.90, 1.00, 1.12, 1.26, 2.0, 0.1):
            aud.play(name, volume=0.6, pitch=pitch)
            aud.update(0.1)
        aud.play(name, volume=-5.0)
        aud.update(0.1)
        aud.play(name, volume=99.0)
        aud.update(0.1)
    check(True, "played every cue across every pitch and volume without raising")
    aud.play("no_such_cue_at_all")
    check(True, "an unknown cue name is ignored, not fatal")

    # Variant shuffling: a "random" cue must not return the same file twice in
    # a row, which is the whole reason the variants exist.
    entry = aud._sfx.get("flap", {})
    if len(entry.get("variants", [])) > 1:
        picks = [aud._choose("flap", entry, 1.0) for _ in range(40)]
        repeats = sum(1 for a, b in zip(picks, picks[1:]) if a == b)
        check(repeats == 0, "flap never repeats a variant back to back",
              "%d immediate repeats in 40 picks" % repeats)
        check(len(set(picks)) == len(entry["variants"]),
              "all flap variants get used", str(sorted(set(picks))))
    # An explicit pitch overrides the shuffle and lands on the nearest variant.
    cd = aud._sfx.get("countdown", {})
    if len(cd.get("variants", [])) > 1:
        chosen = [aud._choose("countdown", cd, p) for p in (1.00, 1.12, 1.26)]
        check(len(set(chosen)) == 3, "an explicit pitch selects a specific variant",
              str(chosen))

    # -- playback actually happens, and disabling SFX actually stops it -----
    aud.stop_all()
    aud.update(0.05)
    aud.play("best")            # 2.35 s, long enough to still be running
    check(sfx_busy() >= 1, "playing a cue occupies a channel")
    aud.set_sfx_enabled(False)
    check(sfx_busy() == 0, "disabling SFX stops sound already playing")
    check(aud.sfx_enabled is False, "sfx_enabled reflects the toggle")
    aud.play("best")
    aud.update(0.1)
    aud.play("hit")
    check(sfx_busy() == 0, "disabling SFX suppresses new plays")
    check(bool(profile["sfx"]) is False, "the sfx setting was written to the profile")

    aud.set_sfx_enabled(True)
    aud.update(0.1)
    aud.play("best")
    check(sfx_busy() >= 1, "re-enabling SFX restores playback")
    check(bool(profile["sfx"]) is True, "the sfx setting was restored in the profile")
    aud.stop_all()

    # The repeat guard is measured on the clock update() advances, so it can be
    # driven exactly rather than with sleeps.
    aud.stop_all()
    aud.update(1.0)
    aud.play("flap")
    first = sfx_busy()
    aud.update(0.001)
    aud.play("flap")
    check(sfx_busy() == first, "a repeated cue inside the guard window is suppressed",
          "%d -> %d channels" % (first, sfx_busy()))
    aud.update(audio_mod.RETRIGGER["flap"] + 0.01)
    aud.play("flap")
    check(sfx_busy() > first, "the same cue plays again once the window has passed",
          "%d -> %d channels" % (first, sfx_busy()))
    aud.stop_all()
    aud.update(1.0)
    aud.play("coin")
    aud.play("score")
    check(sfx_busy() == 2, "the guard is per cue, not global")
    aud.stop_all()

    # -- channel discipline -----------------------------------------------
    # This is the reason the channels are reserved at all, so it is proven
    # rather than asserted: saturate the free pool and check that the cue the
    # player must hear still gets through.
    aud.stop_all()
    aud.update(0.05)
    aud.start_music(aud.themes()[0])
    run_updates(aud, 1.2)
    music_slot = audio_mod.CH_MUSIC_A + aud._slot

    long_sound = aud._sound(aud._sfx["best"]["variants"][0]["file"])
    for i in range(audio_mod.RESERVED_CHANNELS, pygame.mixer.get_num_channels()):
        pygame.mixer.Channel(i).play(long_sound, loops=-1)   # never finishes
    check(aud._free_channel() is None, "the free pool reports itself exhausted")

    aud.play("flap")
    check(pygame.mixer.Channel(music_slot).get_busy(),
          "a flap with the pool full never lands on the music channel")
    aud.play("hit")
    crit_busy = any(pygame.mixer.Channel(i).get_busy()
                    for i in range(audio_mod.CH_CRITICAL_FIRST,
                                   audio_mod.CH_CRITICAL_FIRST + audio_mod.CRITICAL_CHANNELS))
    check(crit_busy, "the death cue still plays when every free channel is taken")
    check(pygame.mixer.Channel(music_slot).get_busy(),
          "the music bed survives a saturated effects pool")

    pygame.mixer.stop()
    aud.stop_all()
    aud.update(0.05)

    # The pool chooser must never hand back a reserved index. A Channel object
    # carries no index and does not compare equal to another handle on the same
    # channel, so the index is recovered by seeing which one turns busy.
    seen: "set[int]" = set()
    busy: "set[int]" = set()
    for _ in range(80):
        channel = aud._free_channel()
        if channel is None:
            break
        channel.play(long_sound, loops=-1)
        now = {i for i in range(pygame.mixer.get_num_channels())
               if pygame.mixer.Channel(i).get_busy()}
        seen |= now - busy
        busy = now
    check(seen and min(seen) >= audio_mod.RESERVED_CHANNELS,
          "the free pool never includes a reserved channel", str(sorted(seen)))
    check(len(seen) == pygame.mixer.get_num_channels() - audio_mod.RESERVED_CHANNELS,
          "the free pool spans every unreserved channel", "%d channels" % len(seen))
    pygame.mixer.stop()
    aud.stop_all()

    # Gain must live on the channel, not on the shared Sound object: two
    # overlapping copies of one cue have to keep their own levels.
    aud.update(0.05)
    aud.play("best", volume=1.0)
    aud.update(0.05)
    aud.play("best", volume=0.2)
    levels = [pygame.mixer.Channel(i).get_volume()
              for i in range(audio_mod.CH_CRITICAL_FIRST,
                             audio_mod.CH_CRITICAL_FIRST + audio_mod.CRITICAL_CHANNELS)
              if pygame.mixer.Channel(i).get_busy()]
    check(len(levels) == 2 and abs(levels[0] - levels[1]) > 0.2,
          "two overlapping copies of one cue keep independent volumes", str(levels))
    check(abs(long_sound.get_volume() - 1.0) < 1e-6,
          "Sound volume is left at unity", "%.4f" % long_sound.get_volume())
    aud.stop_all()

    # -- music ------------------------------------------------------------
    themes = aud.themes()
    check(len(themes) == 4, "four themes available to the runtime", str(themes))
    aud.start_music(themes[0])
    check(music_busy() == 1, "start_music puts one bed on a channel")
    run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.2)
    live = pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot)
    check(abs(live.get_volume() - config.MUSIC_VOLUME) < 0.02,
          "the bed fades up to config.MUSIC_VOLUME", "%.4f" % live.get_volume())

    slot_before = aud._slot
    aud.start_music(themes[0])
    check(aud._slot == slot_before, "start_music on the current theme does not restart it")

    # Cross-fade: both beds audible at once, then only the new one.
    aud.set_theme(themes[1])
    check(music_busy() == 2, "set_theme cross-fades: both beds play during the fade")
    run_updates(aud, 0.3)
    outgoing = pygame.mixer.Channel(audio_mod.CH_MUSIC_A + (1 - aud._slot))
    incoming = pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot)
    check(0.0 < incoming.get_volume() < config.MUSIC_VOLUME,
          "the incoming bed is mid-fade", "%.4f" % incoming.get_volume())
    check(outgoing.get_volume() < config.MUSIC_VOLUME,
          "the outgoing bed is already quieter", "%.4f" % outgoing.get_volume())
    run_updates(aud, audio_mod.MUSIC_XFADE + 0.3)
    check(music_busy() == 1, "the old bed is released once the cross-fade ends")

    # Two theme changes inside one cross-fade window: the second must wait for
    # the spare channel instead of cutting a still-audible bed dead.
    aud.set_theme(themes[2])
    run_updates(aud, 0.2)
    busy_mid = music_busy()
    aud.set_theme(themes[3])
    check(music_busy() == busy_mid,
          "a theme change during a cross-fade does not seize the busy channel")
    check(aud.theme == themes[3], "the queued theme is still recorded")
    run_updates(aud, audio_mod.MUSIC_XFADE * 2 + 0.5)
    check(aud._playing == themes[3], "the queued theme change is applied once free",
          str(aud._playing))
    check(music_busy() == 1, "and settles back to a single bed")

    for theme in themes:            # walk every theme, as a long run does
        aud.set_theme(theme)
        run_updates(aud, audio_mod.MUSIC_XFADE + 0.2)
    check(music_busy() == 1, "still exactly one bed after cycling every theme")
    check(aud.theme == themes[-1], "theme property tracks the last request")

    # -- ducking ----------------------------------------------------------
    run_updates(aud, 1.0)
    full = pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot).get_volume()
    aud.duck(amount=0.6, seconds=0.5)
    ducked = pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot).get_volume()
    check(ducked < full * 0.5, "duck pulls the bed down immediately",
          "%.4f -> %.4f" % (full, ducked))
    aud.duck(amount=0.2, seconds=0.5)       # shallower duck must not undo the deep one
    check(pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot).get_volume() <= ducked + 0.01,
          "a shallower duck does not raise the music back up")
    run_updates(aud, 0.9)
    check(abs(pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot).get_volume() - full) < 0.02,
          "the bed recovers to full after the duck window",
          "%.4f vs %.4f" % (pygame.mixer.Channel(audio_mod.CH_MUSIC_A + aud._slot).get_volume(), full))

    # -- music toggle and stop --------------------------------------------
    aud.set_music_enabled(False)
    run_updates(aud, 0.6)
    check(music_busy() == 0, "disabling music silences the bed")
    check(bool(profile["music"]) is False, "the music setting was written to the profile")
    aud.set_theme(themes[0])
    aud.start_music()
    check(music_busy() == 0, "music stays off while disabled")
    aud.set_music_enabled(True)
    check(music_busy() >= 1, "re-enabling music brings the bed back")
    check(bool(profile["music"]) is True, "the music setting was restored in the profile")

    aud.stop_music(fade_ms=200)
    run_updates(aud, 0.4)
    check(music_busy() == 0, "stop_music releases the channel after its fade")
    aud.start_music()
    aud.stop_music(fade_ms=0)
    check(music_busy() == 0, "stop_music with no fade stops immediately")

    # A bed started and stopped before it ever faded up begins and ends at gain
    # zero, so it is never caught by a "fade finished" test and would hold its
    # channel, decoding inaudibly, for the rest of the session.
    aud.start_music(themes[0])
    aud.stop_music(fade_ms=400)
    run_updates(aud, 0.6)
    check(music_busy() == 0, "a bed stopped before it faded up still releases its channel")

    # set_theme records the theme but must not resurrect music from silence.
    aud.set_theme(themes[2])
    check(music_busy() == 0, "set_theme does not restart music that was stopped")
    check(aud.theme == themes[2], "set_theme still records the theme while silent")
    aud.start_music()
    check(music_busy() == 1, "start_music then uses the theme set while silent")
    run_updates(aud, 0.3)
    check(aud._playing == themes[2], "and it is the right bed", str(aud._playing))

    # -- update() robustness ----------------------------------------------
    aud.start_music(themes[0])
    for dt in (0.0, -1.0, 1e-6, 0.016, 0.5, 60.0):
        aud.update(dt)
    check(True, "update survives zero, negative and absurd frame times")

    aud.stop_all()
    check(sfx_busy() == 0 and music_busy() == 0, "stop_all silences everything")

    # The profile must survive a reload, not just live in memory.
    aud.set_sfx_enabled(False)
    aud.set_music_enabled(False)
    reloaded = save_mod.Profile()
    check(bool(reloaded["sfx"]) is False and bool(reloaded["music"]) is False,
          "settings persist across a profile reload")
    aud.set_sfx_enabled(True)
    aud.set_music_enabled(True)
    aud.stop_all()
    return


# --------------------------------------------------------------------------
# part 4: degradation
# --------------------------------------------------------------------------
def test_degrades_without_a_mixer() -> None:
    section("degradation with no audio device")
    profile, _ = temp_profile()

    real_init = pygame.mixer.init

    def refuse(*a, **kw):
        raise pygame.error("no audio device")

    pygame.mixer.quit()
    pygame.mixer.init = refuse          # the exact failure a device with no output gives
    try:
        aud = audio_mod.Audio(profile)
        check(aud.available is False, "Audio reports itself unavailable")
        for name in ("flap", "hit", "best", "nope"):
            aud.play(name, volume=0.8, pitch=1.1)
        aud.start_music("night")
        aud.set_theme("day")
        aud.duck(0.5, 0.3)
        aud.set_sfx_enabled(False)
        aud.set_sfx_enabled(True)
        aud.set_music_enabled(False)
        aud.set_music_enabled(True)
        for _ in range(10):
            aud.update(1.0 / 60.0)
        aud.stop_music()
        aud.stop_all()
        check(True, "every entry point is a silent no-op without a mixer")
        check(bool(profile["sfx"]) is True,
              "settings still persist with no audio device")
    finally:
        pygame.mixer.init = real_init

    section("degradation with missing files")
    empty = tempfile.mkdtemp(prefix="fd-audio-empty-")
    profile2, _ = temp_profile()
    aud2 = audio_mod.Audio(profile2)
    aud2.dir = empty                    # point at a directory with no WAVs at all
    aud2._sounds.clear()
    aud2._music.clear()
    for name in aud2.names():
        aud2.play(name)
    aud2.start_music("dawn")
    aud2.set_theme("dusk")
    for _ in range(10):
        aud2.update(1.0 / 60.0)
    aud2.stop_all()
    check(True, "a missing WAV is survivable, not fatal")

    # An index.json that is absent entirely must still yield a working chooser.
    rebuilt = audio_mod.Audio.__new__(audio_mod.Audio)
    rebuilt.dir = AUDIO_DIR
    rebuilt._warned = set()
    scanned = rebuilt._scan_dir()
    check(len(scanned["sfx"]) >= 12, "directory scan rebuilds the cue table",
          "%d cues" % len(scanned["sfx"]))
    check(len(scanned["music"]) == 4, "directory scan finds the beds",
          str(sorted(scanned["music"])))
    check(len(scanned["sfx"]["flap"]["variants"]) == 4,
          "directory scan recovers every flap variant")



# --------------------------------------------------------------------------
# part 5: the music-channel invariants
#
# Two channels and a 1.6 s cross-fade mean the interesting bugs all live in the
# same window: a player who pauses, dies, retries or opens the settings screen
# *during* a fade. Each check below is a defect that shipped once -- a click, a
# flange, or a hole in the music -- rather than a property invented from the
# code, so they are worth the machinery it takes to see them.
# --------------------------------------------------------------------------
REAL_CHANNEL = pygame.mixer.Channel

# SDL keeps a channel volume as a 7-bit integer, so each channel rounds to
# 1/128 and the summed power of two of them jitters by up to two of those
# steps. That floor belongs to the mixer, not to the fade.
QUANT_SLACK = 2.0 / 128.0 / config.MUSIC_VOLUME


class ChannelSpy:
    """A `pygame.mixer.Channel` stand-in that notices a bed being cut off.

    The cut is invisible from outside the module: starting a bed sets its
    channel\'s volume to zero immediately before `play()`, so by the time the
    new sound begins, the level the old one was cut off at has already been
    overwritten. Keeping the previous value is the only way to see it.
    """

    cuts: "list[str]" = []
    level: "dict[int, float]" = {}
    before: "dict[int, float]" = {}

    def __init__(self, index: int) -> None:
        self.index = index
        self.channel = REAL_CHANNEL(index)

    def set_volume(self, *args):
        if self.index in ChannelSpy.level:
            ChannelSpy.before[self.index] = ChannelSpy.level[self.index]
            ChannelSpy.level[self.index] = args[0]
        return self.channel.set_volume(*args)

    def play(self, sound, **kwargs):
        if self.index in ChannelSpy.level and self.channel.get_busy():
            was = max(ChannelSpy.level[self.index], ChannelSpy.before[self.index])
            if was > 1e-4:
                ChannelSpy.cuts.append("ch%d cut at volume %.4f" % (self.index, was))
        return self.channel.play(sound, **kwargs)

    def stop(self):
        if self.index in ChannelSpy.level:
            ChannelSpy.level[self.index] = ChannelSpy.before[self.index] = 0.0
        return self.channel.stop()

    def __getattr__(self, name):
        return getattr(self.channel, name)

    @classmethod
    def install(cls) -> None:
        cls.cuts = []
        cls.level = {audio_mod.CH_MUSIC_A: 0.0, audio_mod.CH_MUSIC_B: 0.0}
        cls.before = dict(cls.level)
        pygame.mixer.Channel = cls

    @classmethod
    def remove(cls) -> None:
        pygame.mixer.Channel = REAL_CHANNEL


def music_power() -> float:
    """Summed power of the two beds, as a fraction of one bed at full level.

    Power rather than amplitude, because the beds are deliberately different
    music: they do not correlate, so two of them at half amplitude are not one
    at full amplitude, they are 3 dB quieter. Read off the channels rather than
    the module\'s own gains so the measurement cannot agree with the code by
    construction.
    """
    total = 0.0
    for index in (audio_mod.CH_MUSIC_A, audio_mod.CH_MUSIC_B):
        level = REAL_CHANNEL(index).get_volume() / config.MUSIC_VOLUME
        total += level * level
    return math.sqrt(total)


def test_music_never_cuts_an_audible_bed() -> None:
    section("music: nothing is ever cut off mid-sample")
    profile, _ = temp_profile()
    aud = audio_mod.Audio(profile)
    themes = aud.themes()
    if not check(len(themes) >= 3, "enough beds to cross-fade between", str(themes)):
        return

    ChannelSpy.install()
    try:
        # Die or pause inside a theme cross-fade, then tap Menu or Play Again:
        # the scene resets the world to the score-0 theme and asks for it while
        # two beds are still audible.
        aud.start_music(themes[0])
        run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.2)
        aud.set_theme(themes[1])
        run_updates(aud, 0.5)
        audible = ChannelSpy.level[audio_mod.CH_MUSIC_A + (1 - aud._slot)]
        aud.start_music(themes[2])
        check(audible > 0.01, "the spare channel really is audible mid cross-fade",
              "%.4f" % audible)
        check(not ChannelSpy.cuts, "start_music during a cross-fade cuts nothing",
              "; ".join(ChannelSpy.cuts))
        run_updates(aud, audio_mod.MUSIC_XFADE * 2 + 0.5)
        check(aud._playing == themes[2], "the refused start lands once the spare frees",
              str(aud._playing))
        check(music_busy() == 1, "and settles to one bed")

        # Settings screen, double-tapping the music toggle. 50 ms into a 250 ms
        # stop fade the bed is still playing; starting it again on the other
        # channel would put two copies of one loop, at different offsets, into
        # the mix -- which combs and flanges rather than cross-fading.
        aud.stop_all()
        ChannelSpy.cuts = []
        aud.start_music(themes[0])
        run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.3)
        aud.set_music_enabled(False)
        run_updates(aud, 0.05)
        aud.set_music_enabled(True)
        run_updates(aud, 0.05)
        beds = [REAL_CHANNEL(i) for i in (audio_mod.CH_MUSIC_A, audio_mod.CH_MUSIC_B)]
        doubled = (beds[0].get_busy() and beds[1].get_busy()
                   and beds[0].get_sound() is beds[1].get_sound())
        check(not doubled, "an off/on toggle does not layer a second copy of the bed")
        check(not ChannelSpy.cuts, "an off/on toggle cuts nothing",
              "; ".join(ChannelSpy.cuts))
        run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.3)
        check(music_busy() == 1, "one bed is left playing", "%d" % music_busy())
        live = REAL_CHANNEL(audio_mod.CH_MUSIC_A + aud._slot).get_volume()
        check(abs(live - config.MUSIC_VOLUME) < 0.02, "and it is back at full level",
              "%.4f" % live)

        for i in range(10):             # the toggle as an impatient player uses it
            aud.set_music_enabled(i % 2 == 1)
            run_updates(aud, 0.05)
        run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.3)
        beds = [REAL_CHANNEL(i) for i in (audio_mod.CH_MUSIC_A, audio_mod.CH_MUSIC_B)]
        doubled = (beds[0].get_busy() and beds[1].get_busy()
                   and beds[0].get_sound() is beds[1].get_sound())
        check(music_busy() == 1 and not doubled and not ChannelSpy.cuts,
              "ten rapid toggles leave one clean bed",
              "%d bed(s), doubled=%s; %s"
              % (music_busy(), doubled, "; ".join(ChannelSpy.cuts)))
        aud.stop_all()
    finally:
        ChannelSpy.remove()


def test_music_fades_are_independent() -> None:
    section("music: overlapping fades keep their own timing and level")
    profile, _ = temp_profile()
    aud = audio_mod.Audio(profile)
    themes = aud.themes()
    if not check(len(themes) >= 2, "enough beds to cross-fade between", str(themes)):
        return

    # A stop is a deadline, not a suggestion: every bed still audible has to
    # arrive at silence when the caller said, together. One shared fade timer
    # used to give each bed whatever rate the last call happened to set.
    aud.start_music(themes[0])
    run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.2)
    aud.set_theme(themes[1])
    run_updates(aud, 0.4)                       # two beds, at roughly 0.75 and 0.25
    started = (aud._gain[0], aud._gain[1])
    aud.stop_music(fade_ms=1000)
    silent: "list" = [None, None]
    for frame in range(1, 121):
        aud.update(1.0 / 60.0)
        for slot in (0, 1):
            if silent[slot] is None and aud._gain[slot] <= 0.0:
                silent[slot] = frame / 60.0
    check(all(s is not None and abs(s - 1.0) < 0.03 for s in silent),
          "a stop silences every bed on the deadline it was given",
          "from %.3f/%.3f, silent at %s/%s s (asked for 1.000)"
          % (started[0], started[1], silent[0], silent[1]))
    aud.stop_all()

    # A theme change arriving while the first bed is still fading in is still a
    # cross-fade: the level has to keep climbing, not stall while the outgoing
    # bed races to zero at a rate meant for a fade from full.
    aud.start_music(themes[0])
    run_updates(aud, 0.3)                       # a third of the way up
    opening = music_power()
    aud.set_theme(themes[1])
    lowest = (9.9, 0.0)
    for frame in range(1, int(audio_mod.MUSIC_XFADE * 60) + 20):
        aud.update(1.0 / 60.0)
        now = music_power()
        if now < lowest[0]:
            lowest = (now, frame / 60.0)
    check(lowest[0] >= opening - QUANT_SLACK,
          "a theme change mid fade-in never dips below where it started",
          "from %.3f, lowest %.3f at %.2f s (mixer quantisation floor %.3f)"
          % (opening, lowest[0], lowest[1], QUANT_SLACK))
    aud.stop_all()

    # Equal power, not equal gain. Two linear ramps crossing at 0.5 each sum to
    # 0.707 of the power: a -3 dB scoop in the middle of every theme change,
    # five times the 0.61 dB spread the generator works to hold between beds.
    aud.start_music(themes[0])
    run_updates(aud, audio_mod.MUSIC_FADE_IN + 0.4)
    aud.set_theme(themes[1])
    worst = (0.0, 0.0)
    for frame in range(1, int(audio_mod.MUSIC_XFADE * 60) + 1):
        aud.update(1.0 / 60.0)
        decibels = 20.0 * math.log10(max(1e-9, music_power()))
        if decibels < worst[0]:
            worst = (decibels, frame / 60.0)
    check(worst[0] > -1.0, "a cross-fade holds its level within 1 dB",
          "worst %+.2f dB at %.2f s" % worst)
    print("    cross-fade level: worst %+.2f dB at %.2f s into %.1f s"
          % (worst[0], worst[1], audio_mod.MUSIC_XFADE))
    aud.stop_all()


# A hand-edited, truncated or badly merged index.json parses as JSON and then
# fails far away, inside play(), on a device. Each of these is a shape the file
# can take while still being valid JSON.
BROKEN_INDEXES = [
    ("variants is a string", lambda d: d["sfx"]["flap"].__setitem__("variants", "flap_0.wav")),
    ("variants holds strings", lambda d: d["sfx"]["flap"].__setitem__("variants", ["flap_0.wav"])),
    ("a cue is a string", lambda d: d["sfx"].__setitem__("flap", "flap_0.wav")),
    ("a variant file is a number", lambda d: d["sfx"]["flap"]["variants"][0].__setitem__("file", 42)),
    ("a variant pitch is a word", lambda d: d["sfx"]["flap"]["variants"][0].__setitem__("pitch", "high")),
    ("music is a list", lambda d: d.__setitem__("music", list(d.get("music", {}).values()))),
    ("a bed is a string", lambda d: d["music"].__setitem__("day", "music_day.wav")),
    ("a bed file is a number", lambda d: d["music"]["day"].__setitem__("file", 42)),
    ("sfx is missing", lambda d: d.pop("sfx", None)),
    ("the whole index is a list", lambda d: None),
]

FALLBACK_INDEX = {
    "sfx": {"flap": {"pick": "random", "variants": [{"file": "flap_0.wav", "pitch": 1.0}]}},
    "music": {"day": {"file": "music_day.wav"}},
}


def broken_index_root(mutate, as_list: bool) -> str:
    """An asset root whose audio dir has real WAVs and a broken index."""
    root = tempfile.mkdtemp(prefix="fd-bad-index-")
    audio_dir = os.path.join(root, audio_mod.AUDIO_SUBDIR)
    os.mkdir(audio_dir)
    if os.path.isdir(AUDIO_DIR):
        # Symlinked: the point is the index, and copying 4.5 MB ten times to
        # prove it would make the suite slower for no extra coverage.
        for name in os.listdir(AUDIO_DIR):
            if name.endswith(".wav"):
                os.symlink(os.path.join(AUDIO_DIR, name), os.path.join(audio_dir, name))
    try:
        with open(os.path.join(AUDIO_DIR, "index.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = json.loads(json.dumps(FALLBACK_INDEX))   # generator has not run yet
    mutate(data)
    with open(os.path.join(audio_dir, "index.json"), "w", encoding="utf-8") as fh:
        json.dump([data] if as_list else data, fh)
    return root


def test_a_corrupt_index_degrades_to_silence() -> None:
    section("degradation with a corrupt index.json")
    real_root = audio_mod.asset_root
    for label, mutate in BROKEN_INDEXES:
        root = broken_index_root(mutate, as_list=label.startswith("the whole"))
        try:
            audio_mod.asset_root = lambda root=root: root
            profile, _ = temp_profile()
            aud = audio_mod.Audio(profile)
            for name in ("flap", "hit", "score"):
                aud.play(name, volume=0.8, pitch=1.12)
                aud.update(0.1)
            aud.start_music("day")
            aud.set_theme("night")
            run_updates(aud, 0.5)
            aud.stop_all()
            check(True, "survived: %s" % label,
                  "%d cues / %d beds" % (len(aud.names()), len(aud.themes())))
        except Exception as exc:        # noqa: BLE001 - never raising is the contract
            check(False, "survived: %s" % label, "%s: %s" % (type(exc).__name__, exc))
        finally:
            audio_mod.asset_root = real_root
            shutil.rmtree(root, ignore_errors=True)

    # The same contract for arguments. Scenes pass whatever they computed, and
    # a NaN frame time is the nastiest of them: it poisons the clock for the
    # rest of the session and silently disables the repeat guard.
    section("degradation with nonsense arguments")
    profile, _ = temp_profile()
    aud = audio_mod.Audio(profile)
    abuse = [
        ("play(volume=None)", lambda: aud.play("flap", volume=None)),
        ("play(pitch=None)", lambda: aud.play("flap", pitch=None)),
        ("play(volume=nan)", lambda: aud.play("flap", volume=float("nan"))),
        ("play(name=None)", lambda: aud.play(None)),
        ("stop_music(None)", lambda: aud.stop_music(None)),
        ("duck(None)", lambda: aud.duck(None)),
        ("duck(0.5, 0)", lambda: aud.duck(0.5, 0)),
        ("update(None)", lambda: aud.update(None)),
        ("update(nan)", lambda: aud.update(float("nan"))),
        ("set_theme(None)", lambda: aud.set_theme(None)),
        ("start_music(42)", lambda: aud.start_music(42)),
    ]
    for label, call in abuse:
        try:
            call()
            check(True, "survived: %s" % label)
        except Exception as exc:        # noqa: BLE001
            check(False, "survived: %s" % label, "%s: %s" % (type(exc).__name__, exc))

    check(aud._clock == aud._clock, "a NaN frame time does not poison the clock",
          str(aud._clock))
    aud.stop_all()
    aud.update(1.0)
    aud.play("flap")
    first = sfx_busy()
    aud.update(0.001)
    aud.play("flap")
    check(sfx_busy() == first, "the repeat guard still works after a NaN frame",
          "%d -> %d channels" % (first, sfx_busy()))
    aud.stop_all()


# --------------------------------------------------------------------------
def main() -> int:
    pygame.init()
    print("pygame %s / SDL %s | audio driver: %s"
          % (pygame.version.ver, ".".join(str(v) for v in pygame.get_sdl_version()),
             os.environ.get("SDL_AUDIODRIVER")))

    index = build_asset_index()
    test_runtime_is_numpy_free()
    test_audio_runtime(index)
    test_degrades_without_a_mixer()
    test_music_never_cuts_an_audible_bed()
    test_music_fades_are_independent()
    test_a_corrupt_index_degrades_to_silence()

    print("\n%d checks, %d failed" % (CHECKS, len(FAILURES)))
    if FAILURES:
        print("\nFAILURES:")
        for item in FAILURES:
            print("  " + item)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
