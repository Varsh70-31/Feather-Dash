"""Persistent player profile.

Writes are atomic (temp file + `os.replace`) because a phone can be killed at
any moment -- including mid-write when the player backgrounds the app on death,
which is exactly when the file is most likely to be saved. A torn save would
cost someone their high score, so the old file survives until the new one is
completely on disk.

A corrupt or unreadable profile degrades to defaults instead of crashing, and
the game still runs (in memory only) on a filesystem it cannot write to.
"""
from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from . import config
from .platform import data_dir

SAVE_VERSION = 1
_FILENAME = "profile.json"

DEFAULTS: dict[str, Any] = {
    "version": SAVE_VERSION,
    "high_score": 0,
    "feathers": 0,
    "total_feathers_earned": 0,
    "games_played": 0,
    "total_score": 0,
    "best_medal": None,
    "skin": "ember",
    "unlocked": ["ember"],
    "sfx": True,
    "music": True,
    "haptics": True,
    "show_fps": False,
    "speed_scale": config.SPEED_SCALE_DEFAULT,
    "dash_enabled": True,
    "seen_tutorial": False,
    "best_run_seconds": 0.0,
    "total_distance": 0.0,
}


def _coerce(value: Any, default: Any) -> Any:
    """Return `value` if it matches the shape of `default`, else `default`.

    `None` always falls back: a null in the file means the field is effectively
    absent, and storing it would push the failure into whatever later tries to
    iterate or add to it.
    """
    if value is None:
        return default
    if default is None:
        return value
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, int):
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if not isinstance(value, type(default)):
        return default
    return value


class Profile:
    """The player's saved state, loaded once and written back on change."""

    def __init__(self) -> None:
        self.path = os.path.join(data_dir(), _FILENAME)
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._readonly = False
        self._dirty = False
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return
        if not isinstance(loaded, dict):
            return
        loaded = self._migrate(loaded)
        for key, default in DEFAULTS.items():
            self._data[key] = _coerce(loaded.get(key, default), default)
        self._sanitise()

    def _sanitise(self) -> None:
        """Force the profile into a state the game can actually render.

        A profile is a plain JSON file on the player's device: it gets
        hand-edited, synced between devices, and truncated by a kill mid-write.
        Everything downstream (the shop index, the sprite filename, the feather
        counter) assumes these invariants, so they are established once here
        rather than defended at every use site.
        """
        known = set(config.SKIN_KEYS)

        unlocked = self._data.get("unlocked")
        if not isinstance(unlocked, list):
            unlocked = []
        # Drop skins that no longer exist, de-duplicate, keep the catalogue order
        # so the shop carousel is never in a surprising sequence.
        unlocked = [k for k in config.SKIN_KEYS if k in set(unlocked) & known]
        if "ember" not in unlocked:
            unlocked.insert(0, "ember")
        self._data["unlocked"] = unlocked

        # A skin that is unknown would ask the loader for a sprite that does not
        # exist; one that is merely locked would hand out an unearned reward.
        skin = self._data.get("skin")
        if skin not in known or skin not in unlocked:
            self._data["skin"] = "ember"

        for key in ("high_score", "feathers", "total_feathers_earned",
                    "games_played", "total_score"):
            value = self._data.get(key)
            self._data[key] = max(0, int(value)) if isinstance(value, (int, float)) else 0
        for key in ("best_run_seconds", "total_distance"):
            value = self._data.get(key)
            self._data[key] = max(0.0, float(value)) if isinstance(value, (int, float)) else 0.0

        if self._data.get("best_medal") not in dict(config.MEDAL_THRESHOLDS):
            self._data["best_medal"] = None

        # Snap to the nearest offered preset: a hand-edited 2.73 would otherwise
        # leave the settings screen with nothing highlighted.
        scale = self._data.get("speed_scale")
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or scale != scale:
            scale = config.SPEED_SCALE_DEFAULT
        scale = max(config.SPEED_SCALE_MIN, min(config.SPEED_SCALE_MAX, float(scale)))
        self._data["speed_scale"] = min(
            (s for s, _ in config.SPEED_PRESETS), key=lambda s: abs(s - scale))

    def _migrate(self, loaded: dict[str, Any]) -> dict[str, Any]:
        """Bring an older save forward. Keyed on `version` so it stays additive."""
        version = loaded.get("version", 0)
        if version < 1:
            # v0 was the original single-session high score with no profile.
            loaded["high_score"] = int(loaded.get("high_score", 0) or 0)
        loaded["version"] = SAVE_VERSION
        return loaded

    def save(self) -> bool:
        if self._readonly:
            return False
        try:
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".profile-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, separators=(",", ":"))
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._dirty = False
            return True
        except (OSError, TypeError, ValueError):
            self._readonly = True
            return False

    def flush(self) -> None:
        if self._dirty:
            self.save()

    # -- access ------------------------------------------------------------
    def __getitem__(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self._dirty = True

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def toggle(self, key: str) -> bool:
        value = not bool(self._data.get(key, False))
        self[key] = value
        self.save()
        return value

    # -- gameplay events ---------------------------------------------------
    def record_run(self, score: int, feathers: int, seconds: float, distance: float) -> dict[str, bool]:
        """Fold one finished run into the profile. Returns what was newly achieved."""
        new_best = score > int(self["high_score"])
        if new_best:
            self["high_score"] = int(score)
        self["games_played"] = int(self["games_played"]) + 1
        self["total_score"] = int(self["total_score"]) + int(score)
        self["feathers"] = int(self["feathers"]) + int(feathers)
        self["total_feathers_earned"] = int(self["total_feathers_earned"]) + int(feathers)
        if seconds > float(self["best_run_seconds"]):
            self["best_run_seconds"] = float(seconds)
        self["total_distance"] = float(self["total_distance"]) + float(distance)

        medal = medal_for(score)
        improved_medal = False
        if medal and _medal_rank(medal) > _medal_rank(self["best_medal"]):
            self["best_medal"] = medal
            improved_medal = True

        self.save()
        return {"new_best": new_best, "medal": medal, "new_medal": improved_medal}

    def spend(self, amount: int) -> bool:
        if int(self["feathers"]) < amount:
            return False
        self["feathers"] = int(self["feathers"]) - amount
        self.save()
        return True

    def unlock(self, skin: str) -> None:
        unlocked = list(self["unlocked"])
        if skin not in unlocked:
            unlocked.append(skin)
            self["unlocked"] = unlocked
            self.save()

    def is_unlocked(self, skin: str) -> bool:
        return skin in self["unlocked"]

    @property
    def average_score(self) -> float:
        games = int(self["games_played"])
        return (int(self["total_score"]) / games) if games else 0.0

    def reset(self) -> None:
        """Wipe progress back to a fresh install (exposed in Settings)."""
        self._data = dict(DEFAULTS)
        self._data["unlocked"] = ["ember"]
        self._sanitise()
        self.save()


# --------------------------------------------------------------------------
# medals
# --------------------------------------------------------------------------
_MEDAL_ORDER = [name for name, _ in reversed(config.MEDAL_THRESHOLDS)]


def _medal_rank(medal: str | None) -> int:
    if not medal:
        return -1
    try:
        return _MEDAL_ORDER.index(medal)
    except ValueError:
        return -1


def medal_for(score: int) -> str | None:
    """Highest medal earned at `score`, or None below the first threshold."""
    for name, threshold in config.MEDAL_THRESHOLDS:
        if score >= threshold:
            return name
    return None
