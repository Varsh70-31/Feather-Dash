#!/usr/bin/env python3
"""Feather Dash -- procedural synthesiser for every sound the game makes.

Produces, into ``assets/audio/``::

    <name>_<i>.wav       sound effects, 16-bit stereo at config.AUDIO_RATE
    music_<theme>.wav    one seamless looping bed per manifest.THEMES, 16-bit mono
    index.json           what the runtime needs to pick a variant, stdlib-readable

Every sample is computed here from numpy primitives. Nothing is sampled, traced
or downloaded, so the whole soundtrack is an original work with no licence
attached to it -- which is the point, because this game is sold.

Two rules shape most of the code below:

* **The amplitude envelope is applied last.** Filtering, mixing and soft-clipping
  all happen on a raw, un-enveloped signal; the envelope goes on at the end and
  is forced to exactly zero at both ends. A raw oscillator that starts or stops
  mid-cycle CLICKS, and that click is the single most common tell of amateur
  synthesis.
* **Filtering is done in the frequency domain.** A time-domain IIR needs a
  per-sample Python loop (far too slow here) and leaves a start-up transient that
  would break a loop's periodicity. An FFT filter is zero-phase, fast, and -- when
  run without zero-padding -- *circular*, i.e. it preserves periodicity exactly.
  That is what makes the music beds join to themselves with no click.

The music loops are periodic by construction: every note is rendered into a
fixed-length buffer with wrap-around indexing (``add_wrapped``), so the release
tail of the last chord is literally the same continuous waveform that occupies
the first samples of the file. The join is therefore not "crossfaded smooth", it
is one ordinary sample step of a continuous signal, and ``main()`` measures it.

Run::

    .venv/bin/python tools/gen_audio.py
    .venv/bin/python tools/gen_audio.py --plot /tmp/waves.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave
import zlib
from typing import Callable, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import manifest  # noqa: E402

sys.path.insert(0, manifest.ROOT)
from featherdash import config  # noqa: E402

# --------------------------------------------------------------------------
# output format
# --------------------------------------------------------------------------
RATE = config.AUDIO_RATE
# The beds are bass, pad and a soft motif -- nothing in them lives above ~6 kHz,
# so half the sample rate is transparent and halves the bytes in the APK. They
# are mono for the same reason: a phone has one speaker.
MUSIC_RATE = 22050

# Every file is peak-normalised to the same ceiling so no cue can arrive
# dramatically hotter than another and so nothing can ever clip. Perceived
# loudness balance is deliberately NOT done here -- it belongs at runtime, in
# the mix table in featherdash/audio.py, because only the game knows that a flap
# fires ten times a second and a medal fanfare fires once.
SFX_PEAK_DB = -3.0
MUSIC_PEAK_DB = -6.0

INDEX_NAME = "index.json"
INDEX_VERSION = 1


# --------------------------------------------------------------------------
# small DSP toolkit
# --------------------------------------------------------------------------
def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def n_samples(seconds: float, rate: int) -> int:
    return max(1, int(round(seconds * rate)))


def phase_of(freq, n: int, rate: int, start: float = 0.0) -> np.ndarray:
    """Instantaneous phase for a constant or per-sample frequency.

    The frequency is integrated rather than multiplied by t, so a glide stays
    phase-continuous; multiplying a swept frequency by t produces a waveform
    that jumps, which is heard as a buzz riding the sweep.
    """
    f = np.full(n, float(freq)) if np.isscalar(freq) else np.asarray(freq, dtype=np.float64)
    step = 2.0 * np.pi * f / rate
    ph = np.empty(n, dtype=np.float64)
    ph[0] = start
    if n > 1:
        np.cumsum(step[:-1], out=ph[1:])
        ph[1:] += start
    return ph


def spread_phase(k: int) -> float:
    """Deterministic, maximally-spaced start phase for partial `k`.

    Starting every partial at phase 0 makes them add coherently on the first
    cycle, so the file's peak is a spike that no ear registers as loudness --
    and peak normalisation then scales the whole sound *down* around it. Real
    struck or plucked tones have incoherent partials; the golden angle gives us
    the same decorrelation reproducibly.
    """
    return 2.0 * math.pi * ((k * 0.6180339887) % 1.0)


def partials(ph: np.ndarray, weights: Sequence[float], ratios: Optional[Sequence[float]] = None,
             spread: bool = False) -> np.ndarray:
    """Additive synthesis: sum of harmonics (or inharmonic ratios) of one phase.

    Additive rather than a wavetable because it is alias-free by construction --
    we simply never ask for a partial we cannot represent.
    """
    out = np.zeros_like(ph)
    rs = list(ratios) if ratios is not None else list(range(1, len(weights) + 1))
    for k, (r, a) in enumerate(zip(rs, weights)):
        if a:
            out += a * np.sin(r * ph + (spread_phase(k + 1) if spread else 0.0))
    return out


def saw(ph: np.ndarray, harmonics: int) -> np.ndarray:
    """Band-limited sawtooth, normalised to roughly unit peak."""
    out = np.zeros_like(ph)
    for k in range(1, harmonics + 1):
        out += np.sin(k * ph) / k
    return out * (2.0 / np.pi)


def triangle(ph: np.ndarray, harmonics: int = 7) -> np.ndarray:
    out = np.zeros_like(ph)
    sign = 1.0
    for k in range(1, harmonics * 2, 2):
        out += sign * np.sin(k * ph) / (k * k)
        sign = -sign
    return out * (8.0 / (np.pi * np.pi))


def _spectral(x: np.ndarray, rate: int, mag: Callable[[np.ndarray], np.ndarray],
              circular: bool) -> np.ndarray:
    """Apply a zero-phase magnitude response.

    `circular=True` means no zero-padding, so the filter wraps the signal's tail
    into its head -- exactly what a loop needs. `circular=False` pads instead, so
    a one-shot's decay tail cannot reappear at sample 0 as a pre-echo.
    """
    n = x.size
    work = x if circular else np.concatenate([x, np.zeros(n, dtype=np.float64)])
    spec = np.fft.rfft(work)
    spec *= mag(np.fft.rfftfreq(work.size, 1.0 / rate))
    return np.fft.irfft(spec, n=work.size)[:n]


def lowpass(x: np.ndarray, rate: int, cutoff: float, order: float = 2.0,
            circular: bool = False) -> np.ndarray:
    def mag(f: np.ndarray) -> np.ndarray:
        return 1.0 / np.sqrt(1.0 + (f / max(1e-6, cutoff)) ** (2.0 * order))
    return _spectral(x, rate, mag, circular)


def highpass(x: np.ndarray, rate: int, cutoff: float, order: float = 2.0,
             circular: bool = False) -> np.ndarray:
    def mag(f: np.ndarray) -> np.ndarray:
        r = (f / max(1e-6, cutoff)) ** order
        return r / np.sqrt(1.0 + r * r)
    return _spectral(x, rate, mag, circular)


def bandpass(x: np.ndarray, rate: int, lo: float, hi: float, order: float = 2.0,
             circular: bool = False) -> np.ndarray:
    def mag(f: np.ndarray) -> np.ndarray:
        r = (f / max(1e-6, lo)) ** order
        return (r / np.sqrt(1.0 + r * r)) / np.sqrt(1.0 + (f / max(1e-6, hi)) ** (2.0 * order))
    return _spectral(x, rate, mag, circular)


def env_ad(n: int, rate: int, attack: float, shape: float = 4.0) -> np.ndarray:
    """Percussive attack/decay envelope that starts and ends at exactly zero.

    The decay is exponential but has its own residual subtracted away, so it
    arrives at silence instead of being truncated at exp(-shape). Chopping a
    decay off at 2% of its peak is a step edge, and a step edge is a click.
    """
    env = np.empty(n, dtype=np.float64)
    na = min(n, max(1, int(round(attack * rate))))
    # Raised cosine: zero slope at both ends, so there is no corner to hear.
    env[:na] = 0.5 - 0.5 * np.cos(np.pi * np.arange(na) / na)
    if n > na:
        d = np.arange(n - na, dtype=np.float64) / max(1, n - na - 1)
        tail = np.exp(-shape * d)
        floor = math.exp(-shape)
        env[na:] = (tail - floor) / max(1e-12, 1.0 - floor)
    env[0] = 0.0
    env[-1] = 0.0
    return env


def env_adsr(n: int, rate: int, attack: float, decay: float, sustain: float,
             release: float) -> np.ndarray:
    """Classic ADSR, clamped to fit `n` samples and pinned to zero at both ends."""
    env = np.zeros(n, dtype=np.float64)
    na = max(1, int(round(attack * rate)))
    nd = max(1, int(round(decay * rate)))
    nr = max(1, int(round(release * rate)))
    if na + nd + nr > n:  # a short note still gets a complete shape, just compressed
        k = n / float(na + nd + nr)
        na, nd, nr = max(1, int(na * k)), max(1, int(nd * k)), max(1, int(nr * k))
    na = min(na, n)
    nd = min(nd, n - na)
    nr = min(nr, n - na - nd)
    ns = n - na - nd - nr

    env[:na] = 0.5 - 0.5 * np.cos(np.pi * np.arange(na) / na)
    if nd:
        env[na:na + nd] = 1.0 + (sustain - 1.0) * (np.arange(nd) / nd)
    if ns:
        env[na + nd:na + nd + ns] = sustain
    if nr:
        d = np.arange(nr, dtype=np.float64) / max(1, nr - 1)
        tail = np.exp(-4.5 * d)
        floor = math.exp(-4.5)
        env[n - nr:] = sustain * (tail - floor) / (1.0 - floor)
    env[0] = 0.0
    env[-1] = 0.0
    return env


def soft_clip(x: np.ndarray, knee: float = 0.72) -> np.ndarray:
    """Round off peaks above `knee` without touching anything quieter.

    A global tanh would add harmonics to the clean low-level material too, which
    on a pad reads as grit; this only engages where the signal would otherwise
    overshoot.
    """
    out = x.copy()
    a = np.abs(x)
    hot = a > knee
    if np.any(hot):
        over = (a[hot] - knee) / (1.0 - knee)
        out[hot] = np.sign(x[hot]) * (knee + (1.0 - knee) * np.tanh(over))
    return out


def normalize(x: np.ndarray, peak_db: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 1e-9:
        return x
    return x * (db_to_lin(peak_db) / peak)


def place(buf: np.ndarray, start: int, sig: np.ndarray) -> None:
    """Mix `sig` into `buf` at `start`, truncating anything past the end."""
    if start >= buf.size or sig.size == 0:
        return
    s = max(0, start)
    seg = sig[max(0, -start):]
    k = min(seg.size, buf.size - s)
    if k > 0:
        buf[s:s + k] += seg[:k]


def add_wrapped(buf: np.ndarray, start: int, sig: np.ndarray) -> None:
    """Mix `sig` into `buf` at `start`, wrapping past the end back to sample 0.

    This is the whole seamless-loop mechanism: a note that runs off the end of
    the bar grid continues at the start of the buffer, so the file *is* one
    period of a periodic signal rather than a clip that happens to end quietly.
    """
    n = buf.size
    if n == 0 or sig.size == 0:
        return
    pos = start % n
    rest = sig
    while rest.size:
        k = min(rest.size, n - pos)
        buf[pos:pos + k] += rest[:k]
        rest = rest[k:]
        pos = 0


def stereo(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    return np.stack([left, right], axis=1)


def dual(mono: np.ndarray) -> np.ndarray:
    """Same signal in both channels -- the only stereo that survives a phone speaker.

    Anything built from a delayed copy (a Haas widener, a short reverb bounce)
    comb-filters when the two channels are summed to one, and every Android
    handset sums them. Where a sound wants width it gets it from *different
    partials* panned apart, never from a delay.
    """
    return np.stack([mono, mono], axis=1)


def write_wav(path: str, data: np.ndarray, rate: int) -> int:
    """Write float [-1, 1] audio as a 16-bit PCM WAV. Returns the byte size."""
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    channels = arr.shape[1]
    ints = np.clip(np.rint(arr * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as fh:
        fh.setnchannels(channels)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(np.ascontiguousarray(ints).tobytes())
    return os.path.getsize(path)


# --------------------------------------------------------------------------
# sound effects
# --------------------------------------------------------------------------
def sfx_flap(pitch: float, seed: int) -> np.ndarray:
    dur = 0.14
    n = n_samples(dur, RATE)
    rng = np.random.default_rng(1000 + seed)
    src = rng.standard_normal(n)
    t = np.arange(n) / n

    # A wing-beat is broadband air noise whose energy climbs as the wing
    # accelerates. Sweeping a filter would need a per-sample loop, so instead the
    # same noise is split into three fixed bands and the energy is walked from
    # the low band to the high one -- perceptually the same upward whoosh.
    out = np.zeros(n)
    for (lo, hi), peak, width in (((380, 1100), 0.16, 0.26),
                                  ((1100, 2600), 0.44, 0.28),
                                  ((2600, 5800), 0.72, 0.24)):
        band = bandpass(src, RATE, lo * pitch, hi * pitch, order=2.0)
        out += band * np.exp(-0.5 * ((t - peak) / width) ** 2)

    # A little body under the hiss, or it reads as tape noise on a tiny speaker.
    out += 0.55 * lowpass(src, RATE, 280 * pitch, order=2.0) * np.exp(-0.5 * ((t - 0.10) / 0.16) ** 2)
    return dual(out * env_ad(n, RATE, 0.005, shape=2.8))


def sfx_dash(pitch: float, seed: int) -> np.ndarray:
    """The forward burst: a flap's bigger, more committed sibling.

    Longer and lower than the flap, with a descending tail so the two never
    sound like the same event -- the player has to be able to hear which move
    came out without looking.
    """
    dur = 0.30
    n = n_samples(dur, RATE)
    rng = np.random.default_rng(1700 + seed)
    src = rng.standard_normal(n)
    t = np.arange(n) / n

    out = np.zeros(n)
    # Energy sweeps DOWNWARD here, the opposite of the flap, which is most of
    # what makes it read as thrust rather than another wing-beat.
    for (lo, hi), peak, width in (((2800, 6400), 0.10, 0.16),
                                  ((1200, 2800), 0.26, 0.22),
                                  ((420, 1200), 0.46, 0.30)):
        band = bandpass(src, RATE, lo * pitch, hi * pitch, order=2.0)
        out += band * np.exp(-0.5 * ((t - peak) / width) ** 2)

    # A short pitched core gives it a note to hang on, so it carries over music.
    ph = phase_of(560.0 * pitch, n, RATE)
    core = partials(ph, (1.0, 0.30, 0.12))
    out += 0.38 * core * np.exp(-4.2 * t)

    # Low thump for weight on a phone speaker, which has no bass to speak of.
    out += 0.60 * lowpass(src, RATE, 200 * pitch, order=2.0) * np.exp(-0.5 * ((t - 0.12) / 0.14) ** 2)
    return dual(out * env_ad(n, RATE, 0.004, shape=2.2))


def sfx_score(pitch: float, seed: int) -> np.ndarray:
    # A perfect fifth up: consonant, instantly legible, and it does not collide
    # with the coin ting an octave and a half above it.
    f1, f2 = 880.0 * pitch, 1320.0 * pitch
    n1, n2 = n_samples(0.085, RATE), n_samples(0.165, RATE)
    overlap = n_samples(0.020, RATE)
    total = n1 + n2 - overlap
    buf = np.zeros(total)

    def note(freq: float, n: int, gain: float) -> np.ndarray:
        ph = phase_of(freq, n, RATE)
        body = partials(ph, (1.0, 0.24, 0.10, 0.04))
        return gain * body * env_ad(n, RATE, 0.004, shape=4.4)

    place(buf, 0, note(f1, n1, 0.80))
    place(buf, n1 - overlap, note(f2, n2, 1.0))
    return dual(buf)


def sfx_coin(pitch: float, seed: int) -> np.ndarray:
    dur = 0.28
    n = n_samples(dur, RATE)
    t = np.arange(n) / RATE
    rng = np.random.default_rng(2000 + seed)

    f1 = 2093.0 * pitch                 # C7
    f2 = 3136.0 * pitch * 1.004         # G7, detuned ~7 cents so the two beat slowly
    p1 = np.sin(phase_of(f1, n, RATE)) * np.exp(-9.0 * t)
    p2 = np.sin(phase_of(f2, n, RATE)) * np.exp(-13.5 * t)
    p3 = 0.22 * np.sin(phase_of(f1 * 2.01, n, RATE)) * np.exp(-24.0 * t)
    tick = highpass(rng.standard_normal(n), RATE, 4200.0, order=2.0) * np.exp(-320.0 * t)

    env = env_ad(n, RATE, 0.0015, shape=7.0)
    # Width from unequal partial gains, never from a delay: summed to mono this
    # is just a slightly different spectrum, with no comb filtering.
    left = (1.00 * p1 + 0.72 * p2 + p3 + 0.45 * tick) * env
    right = (0.72 * p1 + 1.00 * p2 + p3 + 0.45 * tick) * env
    return stereo(left, right)


def sfx_hit(pitch: float, seed: int) -> np.ndarray:
    dur = 0.32
    n = n_samples(dur, RATE)
    t = np.arange(n) / RATE
    rng = np.random.default_rng(3000)
    noise = rng.standard_normal(n)

    thump = lowpass(noise, RATE, 240.0, order=3.0) * env_ad(n, RATE, 0.001, shape=11.0)

    # Descending body, 210 Hz -> 120 Hz: the pitch drop is what makes an impact
    # read as "heavy" rather than merely loud. It deliberately stops well above
    # the sub range -- a 60 Hz tail only eats headroom on a phone (see below).
    k = np.minimum(1.0, t / 0.17)
    freq = 210.0 * np.exp(math.log(120.0 / 210.0) * k)
    ph = phase_of(freq, n, RATE)
    body = partials(ph, (1.0, 0.42, 0.18, 0.07)) * env_ad(n, RATE, 0.002, shape=6.0)

    # A phone speaker reproduces almost nothing below ~300 Hz, so most of the
    # weight has to be carried by a midrange slap. These three weights were set
    # by the >300 Hz figure the generator prints: sub-first they measure fine on
    # headphones and the cue is nearly inaudible on the device we ship to.
    slap = bandpass(noise, RATE, 480.0, 3400.0, order=2.0) * env_ad(n, RATE, 0.001, shape=13.0)

    return dual(soft_clip(0.60 * thump + 0.62 * body + 1.70 * slap))


def sfx_fall(pitch: float, seed: int) -> np.ndarray:
    dur = 0.62
    n = n_samples(dur, RATE)
    u = np.arange(n) / n
    rng = np.random.default_rng(3100)

    # A vibrato that widens as the pitch falls turns a plain glissando into a
    # tumble -- the bird is spinning, not sliding down a slide whistle.
    freq = 760.0 * np.exp(math.log(95.0 / 760.0) * u ** 0.85)
    vib = 1.0 + (0.012 + 0.050 * u) * np.sin(phase_of(5.0 + 11.0 * u, n, RATE))
    ph = phase_of(freq * vib, n, RATE)
    tone = np.sin(ph) + 0.30 * (1.0 - u) * np.sin(2 * ph) + 0.12 * (1.0 - u) * np.sin(3 * ph)

    air = bandpass(rng.standard_normal(n), RATE, 320.0, 2000.0, order=2.0) * 0.20 * (1.0 - u)
    env = env_adsr(n, RATE, 0.012, 0.10, 0.78, 0.26)
    return dual((tone + air) * env)


def _click(freq_hi: float, freq_lo: float, dur: float, bright: float, seed: int) -> np.ndarray:
    n = n_samples(dur, RATE)
    u = np.arange(n) / n
    rng = np.random.default_rng(seed)
    ph = phase_of(freq_hi * (freq_lo / freq_hi) ** u, n, RATE)
    tone = partials(ph, (1.0, 0.18 * bright, 0.06 * bright))
    tick = bandpass(rng.standard_normal(n), RATE, 1800.0, 6000.0, order=2.0) * 0.30 * np.exp(-70.0 * u)
    return dual((tone + tick) * env_ad(n, RATE, 0.0018, shape=5.5))


def sfx_ui_tap(pitch: float, seed: int) -> np.ndarray:
    return _click(1500.0 * pitch, 1150.0 * pitch, 0.050, 1.0, 4000 + seed)


def sfx_ui_back(pitch: float, seed: int) -> np.ndarray:
    return _click(820.0 * pitch, 610.0 * pitch, 0.080, 0.7, 4100 + seed)


def sfx_countdown(pitch: float, seed: int) -> np.ndarray:
    # Deliberately plain: the 3-2-1 tick carries information, not emotion, and a
    # musical one would fight whichever bed happens to be playing.
    return _click(700.0 * pitch, 700.0 * pitch, 0.070, 0.45, 4200 + seed)


def _bell(freq: float, n: int, decay: float, bright: float = 1.0) -> np.ndarray:
    """Inharmonic struck-bar tone. The non-integer ratios are what make it a
    chime rather than an organ; the upper partials decay faster, as they do in a
    real bar, which is most of the realism."""
    ph = phase_of(freq, n, RATE)
    t = np.arange(n) / RATE
    ratios = (1.0, 2.00, 2.76, 4.10, 5.43, 8.93)
    gains = (1.0, 0.44, 0.26 * bright, 0.15 * bright, 0.09 * bright, 0.045 * bright)
    out = np.zeros(n)
    for i, (r, g) in enumerate(zip(ratios, gains)):
        if r * freq > RATE * 0.45:
            break
        out += g * np.sin(r * ph + spread_phase(i + 1)) * np.exp(-decay * (1.0 + 0.55 * i) * t)
    return out


def sfx_unlock(pitch: float, seed: int) -> np.ndarray:
    notes = (523.25, 659.25, 783.99, 1046.50)   # C5 E5 G5 C6
    step = n_samples(0.105, RATE)
    tail = n_samples(0.30, RATE)
    total = step * (len(notes) - 1) + tail + n_samples(0.09, RATE)
    buf = np.zeros(total)
    for i, f in enumerate(notes):
        n = tail if i == len(notes) - 1 else n_samples(0.22, RATE)
        n = min(n, total - i * step)
        ph = phase_of(f, n, RATE)
        voice = partials(ph, (1.0, 0.30, 0.14, 0.06, 0.03))
        place(buf, i * step, voice * env_ad(n, RATE, 0.006, shape=4.0) * (0.72 + 0.09 * i))
    return dual(soft_clip(buf))


def sfx_medal(pitch: float, seed: int) -> np.ndarray:
    dur = 1.90
    n = n_samples(dur, RATE)
    base = 523.25   # C5
    # Two near-identical strikes a fraction of a hertz apart: the slow beat
    # between them is the "shimmer", and it costs nothing but a detune.
    core = _bell(base, n, decay=2.0) + 0.85 * _bell(base * 1.0012, n, decay=2.0)
    fifth = 0.45 * _bell(base * 1.5, n, decay=2.6, bright=0.8)
    air = 0.18 * _bell(base * 3.0, n, decay=3.4, bright=0.6)
    env = env_adsr(n, RATE, 0.008, 0.05, 0.92, 0.55)
    left = (core + fifth * 1.1 + air * 0.8) * env
    right = (core + fifth * 0.8 + air * 1.1) * env
    return stereo(soft_clip(left * 0.5), soft_clip(right * 0.5))


def sfx_best(pitch: float, seed: int) -> np.ndarray:
    dur = 2.35
    n = n_samples(dur, RATE)
    buf_l = np.zeros(n)
    buf_r = np.zeros(n)

    # Rising arpeggio into a held triad: the arpeggio says "something happened",
    # the triad says "and it was good". Brighter and longer than `medal` because
    # a personal best outranks any medal.
    arp = ((523.25, 0.00), (783.99, 0.085), (1046.50, 0.170), (1318.51, 0.255))
    for f, at in arp:
        ln = n_samples(0.55, RATE)
        tone = _bell(f, ln, decay=5.0, bright=1.1) * env_ad(ln, RATE, 0.004, shape=4.5)
        place(buf_l, n_samples(at, RATE), tone * 0.52)
        place(buf_r, n_samples(at, RATE), tone * 0.52)

    chord_at = n_samples(0.34, RATE)
    cn = n - chord_at
    for i, f in enumerate((1046.50, 1318.51, 1567.98)):   # C6 E6 G6
        tone = _bell(f, cn, decay=1.5 + 0.35 * i, bright=0.9)
        env = env_adsr(cn, RATE, 0.020, 0.08, 0.88, 0.70)
        pan = (i - 1) * 0.22
        place(buf_l, chord_at, tone * env * (0.42 * (1.0 - pan)))
        place(buf_r, chord_at, tone * env * (0.42 * (1.0 + pan)))

    return stereo(soft_clip(buf_l), soft_clip(buf_r))


def sfx_streak(pitch: float, seed: int) -> np.ndarray:
    dur = 0.30
    n = n_samples(dur, RATE)
    u = np.arange(n) / n
    f0 = 440.0 * pitch
    freq = f0 * 2.0 ** (2.0 * u)        # two octaves up over the whole gliss
    ph = phase_of(freq, n, RATE)
    tone = partials(ph, (1.0, 0.26, 0.11, 0.05))
    sparkle = 0.30 * np.sin(phase_of(freq * 4.0, n, RATE)) * u ** 2
    return dual((tone + sparkle) * env_adsr(n, RATE, 0.006, 0.05, 0.85, 0.16))


# (builder, pitch ratios, selection policy)
#   "random"  -- consecutive plays should not sound identical (flap, coin, taps)
#   "nearest" -- the caller means a specific pitch (countdown steps, streak tiers)
SFX_SPEC: "dict[str, dict]" = {
    "flap":      {"build": sfx_flap,      "pitches": (0.90, 0.97, 1.04, 1.12), "pick": "random"},
    "dash":      {"build": sfx_dash,      "pitches": (0.94, 1.00, 1.07),       "pick": "random"},
    "score":     {"build": sfx_score,     "pitches": (0.94, 1.00, 1.07),       "pick": "random"},
    "coin":      {"build": sfx_coin,      "pitches": (0.95, 1.00, 1.06),       "pick": "random"},
    "hit":       {"build": sfx_hit,       "pitches": (1.00,),                  "pick": "nearest"},
    "fall":      {"build": sfx_fall,      "pitches": (1.00,),                  "pick": "nearest"},
    "ui_tap":    {"build": sfx_ui_tap,    "pitches": (0.96, 1.04),             "pick": "random"},
    "ui_back":   {"build": sfx_ui_back,   "pitches": (1.00,),                  "pick": "nearest"},
    "unlock":    {"build": sfx_unlock,    "pitches": (1.00,),                  "pick": "nearest"},
    "medal":     {"build": sfx_medal,     "pitches": (1.00,),                  "pick": "nearest"},
    "best":      {"build": sfx_best,      "pitches": (1.00,),                  "pick": "nearest"},
    "countdown": {"build": sfx_countdown, "pitches": (1.00, 1.12, 1.26),       "pick": "nearest"},
    "streak":    {"build": sfx_streak,    "pitches": (1.00, 1.12, 1.26),       "pick": "nearest"},
}


def sfx_filename(name: str, index: int) -> str:
    return "%s_%d.wav" % (name, index)


# --------------------------------------------------------------------------
# music
# --------------------------------------------------------------------------
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_TRIAD = {"maj": (0, 4, 7), "min": (0, 3, 7)}

# Every theme is drawn from one seven-note collection (C major / A minor), so a
# cross-fade between any two beds is consonant no matter where in the bar the
# theme change lands. The progressions differ, the notes never clash.
CHORDS = {
    "C": ("C", "maj"), "F": ("F", "maj"), "G": ("G", "maj"),
    "Am": ("A", "min"), "Em": ("E", "min"), "Dm": ("D", "min"),
}

BARS = 8
BEATS_PER_BAR = 4

# Tempos sit inside a 20% band of each other for the same reason the keys match:
# a theme change mid-run should feel like the light changing, not the track.
MUSIC_SPEC: "dict[str, dict]" = {
    "dawn": {
        "bpm": 112,
        "chords": ("F", "C", "G", "Am"),          # IV - I - V - vi
        "pad_cut": 2600.0, "bus_cut": 5400.0, "bass_oct": 2,
        "pad_gain": 0.42, "mel_gain": 0.30, "bass_gain": 0.52, "air": 0.055,
        "melody": ((1.0, 69, 1.5), (2.5, 72, 1.0), (4.5, 76, 1.5), (9.0, 74, 1.0),
                   (10.0, 71, 2.0), (13.5, 72, 2.0), (16.5, 69, 1.0), (18.0, 77, 2.0),
                   (21.0, 76, 1.0), (22.0, 79, 1.5), (25.0, 74, 2.0), (29.0, 72, 1.5),
                   (30.5, 69, 2.0)),
    },
    "day": {
        "bpm": 118,
        "chords": ("C", "G", "Am", "F"),          # I - V - vi - IV
        "pad_cut": 3000.0, "bus_cut": 6200.0, "bass_oct": 2,
        "pad_gain": 0.40, "mel_gain": 0.32, "bass_gain": 0.50, "air": 0.045,
        "melody": ((0.0, 67, 1.0), (1.5, 72, 1.5), (4.0, 71, 1.0), (6.0, 74, 1.5),
                   (8.5, 72, 1.0), (10.0, 76, 2.0), (13.0, 77, 1.0), (14.0, 69, 2.0),
                   (16.5, 76, 1.0), (18.0, 79, 2.0), (21.0, 74, 1.0), (22.5, 71, 1.5),
                   (25.0, 72, 1.0), (26.5, 76, 1.5), (29.0, 69, 1.0), (30.0, 72, 2.5)),
    },
    "dusk": {
        "bpm": 106,
        "chords": ("Am", "G", "F", "C"),          # vi - V - IV - I, a falling line
        "pad_cut": 1900.0, "bus_cut": 4200.0, "bass_oct": 2,
        "pad_gain": 0.44, "mel_gain": 0.27, "bass_gain": 0.54, "air": 0.06,
        "melody": ((1.0, 76, 2.0), (5.0, 74, 1.5), (8.5, 72, 2.0), (12.5, 71, 1.0),
                   (13.5, 69, 2.0), (17.0, 72, 2.0), (21.0, 71, 1.0), (22.0, 74, 2.0),
                   (25.5, 72, 2.0), (29.0, 69, 1.5), (30.5, 64, 2.5)),
    },
    "night": {
        "bpm": 100,
        "chords": ("Am", "F", "C", "G"),          # i - VI - III - VII
        "pad_cut": 1350.0, "bus_cut": 3200.0, "bass_oct": 1,
        "pad_gain": 0.46, "mel_gain": 0.24, "bass_gain": 0.56, "air": 0.07,
        "melody": ((2.0, 69, 2.5), (6.5, 72, 2.0), (11.0, 76, 2.0), (16.5, 69, 2.5),
                   (22.0, 65, 2.0), (26.5, 67, 2.0), (30.0, 62, 3.0)),
    },
}


def midi_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def chord_pcs(name: str) -> "tuple[int, ...]":
    root, quality = CHORDS[name]
    return tuple((_PC[root] + iv) % 12 for iv in _TRIAD[quality])


def voice_chord(pcs: Sequence[int], prev: Optional[Sequence[int]],
                lo: int = 52, hi: int = 74) -> "list[int]":
    """Realise pitch classes inside one register, each voice near where it was.

    Without this the pad leaps an octave whenever the root moves by a fourth,
    and a bed that leaps is a bed you notice -- the opposite of the job.
    """
    out: "list[int]" = []
    for i, pc in enumerate(pcs):
        cands = [m for m in range(lo, hi + 1) if m % 12 == pc and m not in out]
        if not cands:
            cands = [m for m in range(lo, hi + 1) if m % 12 == pc]
        if not cands:
            continue
        target = prev[i] if (prev and i < len(prev)) else lo + 4 * i
        out.append(min(cands, key=lambda m: (abs(m - target), m)))
    return out


def _pad_voice(freq: float, n: int, detune_cents: float) -> np.ndarray:
    """One detuned saw. Harmonics are capped below Nyquist, so nothing aliases
    even at the top of the pad's register."""
    f = freq * 2.0 ** (detune_cents / 1200.0)
    harmonics = max(1, min(10, int((MUSIC_RATE * 0.45) / max(1.0, f))))
    return saw(phase_of(f, n, MUSIC_RATE), harmonics)


def build_music(theme: str) -> "tuple[np.ndarray, dict]":
    spec = MUSIC_SPEC[theme]
    bpm = float(spec["bpm"])
    spb = 60.0 / bpm
    total_beats = BARS * BEATS_PER_BAR
    n = n_samples(total_beats * spb, MUSIC_RATE)
    # crc32, not hash(): Python salts str hashing per process, so a hash()
    # seed would silently produce a different bed on every build. Shipping a
    # patch release has to regenerate byte-identical assets.
    rng = np.random.default_rng(zlib.crc32(theme.encode("utf-8")))

    def at(beat: float) -> int:
        return int(round(beat * spb * MUSIC_RATE))

    bass = np.zeros(n)
    pad = np.zeros(n)
    mel = np.zeros(n)

    chords = list(spec["chords"])
    prev_voices: Optional["list[int]"] = None
    for bar in range(BARS):
        name = chords[bar % len(chords)]
        pcs = chord_pcs(name)
        voices = voice_chord(pcs, prev_voices)
        prev_voices = voices
        start = at(bar * BEATS_PER_BAR)

        # The chord is held longer than its bar so its release overlaps the next
        # one; the last bar's overlap wraps onto sample 0 and becomes the join.
        hold = n_samples(BEATS_PER_BAR * spb * 1.30, MUSIC_RATE)
        for vi, midi in enumerate(voices):
            f = midi_hz(midi)
            voice = (_pad_voice(f, hold, -5.0) + _pad_voice(f, hold, +4.0)
                     + 0.6 * _pad_voice(f, hold, 0.0))
            env = env_adsr(hold, MUSIC_RATE, 0.9 * spb, 0.5 * spb, 0.72, 1.4 * spb)
            add_wrapped(pad, start, voice * env * (0.46 / (1.0 + 0.25 * vi)))

        root_midi = _PC[CHORDS[name][0]] + 12 * (int(spec["bass_oct"]) + 1)
        bn = n_samples(BEATS_PER_BAR * spb * 1.05, MUSIC_RATE)
        bph = phase_of(midi_hz(root_midi), bn, MUSIC_RATE)
        tone = partials(bph, (1.0, 0.26, 0.09, 0.03))
        add_wrapped(bass, start, tone * env_adsr(bn, MUSIC_RATE, 0.035, 0.45 * spb, 0.62, 0.9 * spb))

        # A pickup into the next bar on every other bar: just enough forward
        # motion that eight bars do not feel like a held chord.
        if bar % 2 == 1:
            nxt = chords[(bar + 1) % len(chords)]
            nxt_midi = _PC[CHORDS[nxt][0]] + 12 * (int(spec["bass_oct"]) + 1)
            pn = n_samples(0.55 * spb, MUSIC_RATE)
            pph = phase_of(midi_hz(nxt_midi - 5), pn, MUSIC_RATE)
            add_wrapped(bass, at(bar * BEATS_PER_BAR + 3.4),
                        partials(pph, (0.75, 0.18)) * env_ad(pn, MUSIC_RATE, 0.02, shape=4.0))

    eighth = at(0.5 * BEATS_PER_BAR / 2.0)
    for beat, midi, length in spec["melody"]:
        mn = n_samples(float(length) * spb, MUSIC_RATE)
        ph = phase_of(midi_hz(midi), mn, MUSIC_RATE)
        voice = triangle(ph, 6) + 0.22 * np.sin(ph) + 0.08 * np.sin(2 * ph)
        shaped = voice * env_adsr(mn, MUSIC_RATE, 0.025, 0.30 * spb, 0.55, 0.60 * spb)
        add_wrapped(mel, at(float(beat)), shaped)
        # Echoes are wrapped too, so the motif that lands on the last beat still
        # rings into the top of the loop.
        add_wrapped(mel, at(float(beat)) + eighth, shaped * 0.34)
        add_wrapped(mel, at(float(beat)) + 2 * eighth, shaped * 0.13)

    # Every filter below is circular, so each stage preserves periodicity exactly.
    pad = lowpass(pad, MUSIC_RATE, float(spec["pad_cut"]), order=2.0, circular=True)
    bass = lowpass(bass, MUSIC_RATE, 320.0, order=2.0, circular=True)
    mel = lowpass(mel, MUSIC_RATE, float(spec["pad_cut"]) * 1.6, order=2.0, circular=True)

    air = lowpass(highpass(rng.standard_normal(n), MUSIC_RATE, 900.0, 2.0, circular=True),
                  MUSIC_RATE, 4500.0, 2.0, circular=True)
    air /= max(1e-9, float(np.max(np.abs(air))))

    # An integer number of LFO cycles per loop keeps the movement periodic; a
    # non-integer count would be the one thing in the file that does not line up.
    idx = np.arange(n)
    breathe = 1.0 + 0.11 * np.sin(2.0 * np.pi * 2.0 * idx / n)
    air_lfo = 0.55 + 0.45 * np.sin(2.0 * np.pi * 3.0 * idx / n - 1.1)

    mix = (float(spec["bass_gain"]) * bass
           + float(spec["pad_gain"]) * pad * breathe
           + float(spec["mel_gain"]) * mel
           + float(spec["air"]) * air * air_lfo)

    mix = highpass(mix, MUSIC_RATE, 34.0, 2.0, circular=True)     # kill DC and rumble
    mix = lowpass(mix, MUSIC_RATE, float(spec["bus_cut"]), 2.0, circular=True)
    mix = soft_clip(mix * 0.85)
    mix = normalize(mix, MUSIC_PEAK_DB)

    info = {"bpm": bpm, "bars": BARS, "chords": chords, "seconds": n / MUSIC_RATE}
    return mix, info


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def quantise(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x * 32767.0), -32768, 32767).astype(np.int32)


def loop_seam(mono: np.ndarray) -> "dict[str, float]":
    """Measure the join by literally concatenating the loop to itself.

    A seamless loop is not one whose join step is zero -- that would only be true
    of silence. It is one whose join step is an ordinary step of the underlying
    waveform, i.e. no larger than the steps already inside the file.
    """
    q = quantise(mono)
    doubled = np.concatenate([q, q])
    joint = int(abs(int(doubled[q.size]) - int(doubled[q.size - 1])))
    steps = np.abs(np.diff(q))
    worst = int(steps.max()) if steps.size else 0
    p999 = float(np.percentile(steps, 99.9)) if steps.size else 0.0
    rms = float(np.sqrt(np.mean(steps.astype(np.float64) ** 2))) if steps.size else 0.0
    dbfs = -999.0 if joint == 0 else 20.0 * math.log10(joint / 32768.0)
    return {"join_lsb": joint, "max_step": worst, "p999_step": p999,
            "rms_step": rms, "join_dbfs": dbfs,
            "ratio": (joint / worst) if worst else 0.0}


def peak_dbfs(x: np.ndarray) -> float:
    q = np.abs(quantise(x))
    peak = int(q.max()) if q.size else 0
    return -999.0 if peak == 0 else 20.0 * math.log10(peak / 32768.0)


def loudness(x: np.ndarray, rate: int) -> "dict[str, float]":
    """Peak, RMS, crest factor and the share of energy a phone can reproduce.

    Crest factor is the one that catches bad synthesis: a cue whose peak is a
    single coherent spike has a huge crest, normalises to a quiet-sounding file,
    and gets "fixed" by turning it up until it clips. The audible-share figure
    catches the other classic mistake -- mixing a cue around sub-bass that a
    handset's speaker simply does not produce.
    """
    q = quantise(x).astype(np.float64) / 32768.0
    mono = q if q.ndim == 1 else q.mean(axis=1)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    rms = float(np.sqrt(np.mean(mono ** 2))) if mono.size else 0.0
    spec = np.abs(np.fft.rfft(mono)) ** 2
    freqs = np.fft.rfftfreq(mono.size, 1.0 / rate)
    total = float(spec.sum())
    audible = float(spec[freqs >= 300.0].sum()) / total if total > 0 else 0.0
    pk_db = -999.0 if peak <= 0 else 20.0 * math.log10(peak)
    rms_db = -999.0 if rms <= 0 else 20.0 * math.log10(rms)
    return {"peak_db": pk_db, "rms_db": rms_db, "crest_db": pk_db - rms_db,
            "audible": audible}


def clip_runs(x: np.ndarray, limit: int = 32700) -> int:
    """Longest run of consecutive samples pinned at (or past) full scale."""
    q = np.abs(quantise(x)).ravel()
    hot = q >= limit
    if not hot.any():
        return 0
    best = run = 0
    for v in hot:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


# --------------------------------------------------------------------------
# optional diagnostic plot (build-time only; PIL is never imported at runtime)
# --------------------------------------------------------------------------
def plot(path: str, strips: "list[tuple[str, np.ndarray, int]]",
         seams: "Optional[list[tuple[str, np.ndarray, int]]]" = None) -> None:
    from PIL import Image, ImageDraw  # local import: nothing here ships

    pad, row_h, width = 8, 54, 1180
    label_w = 128
    seam_h = 96
    seams = seams or []
    height = pad + len(strips) * (row_h + pad) + len(seams) * (seam_h + pad)
    img = Image.new("RGB", (width, height), (18, 20, 30))
    dr = ImageDraw.Draw(img)

    for i, (name, data, rate) in enumerate(strips):
        mono = data if data.ndim == 1 else data.mean(axis=1)
        top = pad + i * (row_h + pad)
        mid = top + row_h // 2
        plot_w = width - label_w - pad
        dr.rectangle([label_w, top, width - pad, top + row_h], fill=(26, 29, 44))
        dr.line([(label_w, mid), (width - pad, mid)], fill=(52, 58, 82))
        buckets = np.array_split(mono, plot_w) if mono.size >= plot_w else [mono]
        for bx, chunk in enumerate(buckets):
            if not chunk.size:
                continue
            hi = float(chunk.max()) * (row_h / 2 - 2)
            lo = float(chunk.min()) * (row_h / 2 - 2)
            dr.line([(label_w + bx, mid - hi), (label_w + bx, mid - lo)], fill=(118, 214, 178))
        # Mark the loop join: for music this column should look like any other.
        dr.line([(width - pad - 1, top), (width - pad - 1, top + row_h)], fill=(255, 112, 104))
        dr.text((6, mid - 5), "%s" % name, fill=(220, 226, 240))
        dr.text((6, mid + 6), "%.2fs @%d" % (mono.size / rate, rate), fill=(140, 150, 180))

    # Zoomed join: the last N samples followed by the first N, drawn as one
    # trace. This is the picture the loop has to pass -- if the waveform steps,
    # jumps to silence or changes slope at the red line, the loop clicks.
    span = 360
    base = pad + len(strips) * (row_h + pad)
    for i, (name, data, _rate) in enumerate(seams):
        mono = data if data.ndim == 1 else data.mean(axis=1)
        trace = np.concatenate([mono[-span:], mono[:span]])
        top = base + i * (seam_h + pad)
        mid = top + seam_h // 2
        plot_w = width - label_w - pad
        dr.rectangle([label_w, top, width - pad, top + seam_h], fill=(26, 29, 44))
        dr.line([(label_w, mid), (width - pad, mid)], fill=(52, 58, 82))
        sx = plot_w / float(trace.size)
        amp = (seam_h / 2 - 3) / max(1e-6, float(np.max(np.abs(trace))))
        pts = [(label_w + k * sx, mid - v * amp) for k, v in enumerate(trace)]
        dr.line(pts, fill=(118, 214, 178), width=1)
        jx = label_w + span * sx
        dr.line([(jx, top), (jx, top + seam_h)], fill=(255, 112, 104))
        dr.text((6, mid - 10), "JOIN", fill=(255, 112, 104))
        dr.text((6, mid + 2), "%s" % name, fill=(220, 226, 240))
        dr.text((6, mid + 13), "+-%d samples" % span, fill=(140, 150, 180))

    img.save(path)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------
def generate(plot_path: Optional[str] = None) -> int:
    manifest.ensure_dirs()
    out_dir = manifest.AUDIO
    problems: "list[str]" = []
    strips: "list[tuple[str, np.ndarray, int]]" = []
    seams: "list[tuple[str, np.ndarray, int]]" = []
    index: "dict[str, object]" = {
        "version": INDEX_VERSION,
        "sfx_rate": RATE,
        "music_rate": MUSIC_RATE,
        "sfx": {},
        "music": {},
    }

    print("sound effects  (%d Hz, stereo, peak %.1f dBFS)" % (RATE, SFX_PEAK_DB))
    for name, spec in SFX_SPEC.items():
        variants = []
        for i, pitch in enumerate(spec["pitches"]):
            data = spec["build"](float(pitch), i)
            data = normalize(soft_clip(data), SFX_PEAK_DB)
            fname = sfx_filename(name, i)
            size = write_wav(os.path.join(out_dir, fname), data, RATE)
            variants.append({"file": fname, "pitch": round(float(pitch), 4)})

            run = clip_runs(data)
            if run > 2:
                problems.append("%s: %d consecutive full-scale samples" % (fname, run))
            if peak_dbfs(data) > SFX_PEAK_DB + 0.2:
                problems.append("%s: peak %.2f dBFS over ceiling" % (fname, peak_dbfs(data)))
            if i == 0:
                loud = loudness(data, RATE)
                strips.append((name, data, RATE))
                print("  %-10s %dv %5.0f ms %6.1f kB  peak %5.2f  rms %6.2f  "
                      "crest %4.1f dB  >300Hz %3.0f%%"
                      % (name, len(spec["pitches"]), 1000.0 * len(data) / RATE,
                         size / 1024.0, loud["peak_db"], loud["rms_db"],
                         loud["crest_db"], 100.0 * loud["audible"]))
                if loud["crest_db"] > 22.0:
                    problems.append("%s: crest factor %.1f dB -- the peak is a spike, "
                                    "so the cue will be inaudibly quiet"
                                    % (fname, loud["crest_db"]))
                if loud["audible"] < 0.45:
                    problems.append("%s: only %.0f%% of its energy is above 300 Hz -- "
                                    "a phone speaker will not reproduce it"
                                    % (fname, 100.0 * loud["audible"]))
        index["sfx"][name] = {"pick": spec["pick"], "variants": variants}

    print("\nmusic beds  (%d Hz, mono, peak %.1f dBFS)" % (MUSIC_RATE, MUSIC_PEAK_DB))
    bed_levels: "list[tuple[str, float]]" = []
    for theme in manifest.THEMES:
        mono, info = build_music(theme)
        fname = "music_%s.wav" % theme
        size = write_wav(os.path.join(out_dir, fname), mono, MUSIC_RATE)
        seam = loop_seam(mono)
        loud = loudness(mono, MUSIC_RATE)
        bed_levels.append((theme, loud["rms_db"]))
        strips.append(("music_" + theme, mono, MUSIC_RATE))
        seams.append(("music_" + theme, mono, MUSIC_RATE))
        index["music"][theme] = {
            "file": fname, "seconds": round(info["seconds"], 4),
            "bpm": info["bpm"], "bars": info["bars"], "chords": info["chords"],
        }
        print("  %-6s %3.0f bpm  %-10s %5.2fs %6.1f kB  rms %6.2f dBFS  crest %4.1f dB"
              % (theme, info["bpm"], "-".join(info["chords"]), info["seconds"],
                 size / 1024.0, loud["rms_db"], loud["crest_db"]))
        print("         loop join %d LSB (%.1f dBFS) vs largest internal step %d "
              "(p99.9 %.0f, rms %.0f) -- %.1f%% of an ordinary step"
              % (seam["join_lsb"], seam["join_dbfs"], seam["max_step"],
                 seam["p999_step"], seam["rms_step"], 100.0 * seam["ratio"]))
        if seam["join_lsb"] > seam["max_step"]:
            problems.append("%s: loop join %d LSB exceeds the largest internal step %d"
                            % (fname, seam["join_lsb"], seam["max_step"]))
        if clip_runs(mono) > 2:
            problems.append("%s: sustained full-scale samples" % fname)

    # A theme change cross-fades mid-run, so the beds have to sit at the same
    # loudness. A level jump between two themes is every bit as jarring as a key
    # clash, and it is the kind of thing nobody notices until a player does.
    if bed_levels:
        spread = max(v for _, v in bed_levels) - min(v for _, v in bed_levels)
        print("  level spread across themes: %.2f dB (%s)"
              % (spread, ", ".join("%s %.1f" % (t, v) for t, v in bed_levels)))
        if spread > 1.5:
            problems.append("music beds differ by %.2f dB -- a theme change will "
                            "audibly jump in volume" % spread)

    with open(os.path.join(out_dir, INDEX_NAME), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, sort_keys=True)

    total = 0
    count = 0
    for entry in sorted(os.listdir(out_dir)):
        p = os.path.join(out_dir, entry)
        if os.path.isfile(p):
            total += os.path.getsize(p)
            count += 1
    print("\n%d files in %s" % (count, out_dir))
    print("total size: %d bytes (%.2f MB)" % (total, total / (1024.0 * 1024.0)))

    if plot_path:
        plot(plot_path, strips, seams)
        print("wrote diagnostic plot: %s" % plot_path)

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    print("\nall checks passed")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Synthesise Feather Dash's audio.")
    ap.add_argument("--plot", metavar="PNG", default=None,
                    help="also write a waveform overview for visual inspection")
    args = ap.parse_args(argv)
    return generate(args.plot)


if __name__ == "__main__":
    raise SystemExit(main())
