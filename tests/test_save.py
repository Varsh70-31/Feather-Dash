"""Profile persistence, migration and corruption tolerance."""
from __future__ import annotations

import json
import os

import pytest

from featherdash.save import Profile, medal_for


def test_defaults_on_first_run(tmp_profile):
    assert tmp_profile["high_score"] == 0
    assert tmp_profile["feathers"] == 0
    assert tmp_profile["unlocked"] == ["ember"]


def test_run_is_recorded_and_persists(tmp_profile):
    out = tmp_profile.record_run(score=42, feathers=9, seconds=30.0, distance=1234.0)
    assert out["new_best"] and out["medal"] == "silver"

    reloaded = Profile()
    reloaded.path = tmp_profile.path
    reloaded.load()
    assert reloaded["high_score"] == 42
    assert reloaded["feathers"] == 9
    assert reloaded["best_medal"] == "silver"


def test_high_score_only_moves_up(tmp_profile):
    tmp_profile.record_run(score=50, feathers=0, seconds=1.0, distance=1.0)
    out = tmp_profile.record_run(score=10, feathers=0, seconds=1.0, distance=1.0)
    assert not out["new_best"]
    assert tmp_profile["high_score"] == 50


def test_spending_cannot_go_negative(tmp_profile):
    tmp_profile["feathers"] = 100
    assert tmp_profile.spend(60)
    assert not tmp_profile.spend(100)
    assert tmp_profile["feathers"] == 40


def test_corrupt_file_falls_back_to_defaults(tmp_profile):
    tmp_profile.record_run(score=7, feathers=3, seconds=1.0, distance=1.0)
    with open(tmp_profile.path, "w", encoding="utf-8") as fh:
        fh.write("{not json at all")
    recovered = Profile()
    recovered.path = tmp_profile.path
    recovered.load()
    assert recovered["high_score"] == 0


def test_wrong_types_in_file_are_replaced(tmp_profile):
    with open(tmp_profile.path, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "high_score": "lots", "feathers": None}, fh)
    recovered = Profile()
    recovered.path = tmp_profile.path
    recovered.load()
    assert recovered["high_score"] == 0


def test_save_is_atomic_and_leaves_no_temp_files(tmp_profile):
    tmp_profile.record_run(score=5, feathers=1, seconds=1.0, distance=1.0)
    directory = os.path.dirname(tmp_profile.path)
    assert not [n for n in os.listdir(directory) if n.endswith(".tmp")]


def test_unreadable_location_does_not_crash(tmp_path, monkeypatch):
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: "/proc/definitely/not/writable")
    profile = save.Profile()
    profile.record_run(score=3, feathers=1, seconds=1.0, distance=1.0)
    assert profile["high_score"] == 3  # in memory, even though the write failed


def test_medal_thresholds():
    assert medal_for(0) is None
    assert medal_for(9) is None
    assert medal_for(10) == "bronze"
    assert medal_for(99) == "gold"
    assert medal_for(100) == "platinum"
    assert medal_for(10**6) == "diamond"


# --------------------------------------------------------------------------
# hostile profiles
# --------------------------------------------------------------------------
# A profile is plain JSON on the player's device: hand-edited, synced between
# devices, truncated by a kill mid-write. Every one of these used to either
# crash the game at boot or put the UI into a state it cannot render.

HOSTILE = [
    ("negative feathers", {"version": 1, "feathers": -9999}),
    ("absurd high score", {"version": 1, "high_score": 10 ** 18}),
    ("unknown skin", {"version": 1, "skin": "dragon", "unlocked": ["dragon"]}),
    ("locked skin selected", {"version": 1, "skin": "gold", "unlocked": ["ember"]}),
    ("unlocked is a string", {"version": 1, "unlocked": "everything"}),
    ("top-level list", [1, 2, 3]),
    ("null everywhere", {"version": 1, "feathers": None, "skin": None, "unlocked": None}),
    ("bool where int goes", {"version": 1, "high_score": True, "feathers": False}),
    ("float score", {"version": 1, "high_score": 12.7}),
    ("bogus medal", {"version": 1, "best_medal": "unobtainium"}),
    ("duplicate unlocks", {"version": 1, "unlocked": ["gold", "gold", "ember", "nope"], "skin": "gold"}),
]


@pytest.mark.parametrize("label,payload", HOSTILE, ids=[h[0] for h in HOSTILE])
def test_hostile_profile_is_normalised(label, payload, tmp_path, monkeypatch):
    from featherdash import config, save

    path = tmp_path / "profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))

    profile = save.Profile()  # must not raise

    assert profile["skin"] in config.SKIN_KEYS, "an unknown skin would request a missing sprite"
    assert profile["skin"] in profile["unlocked"], "a locked skin must not be selectable"
    assert all(k in config.SKIN_KEYS for k in profile["unlocked"])
    assert len(set(profile["unlocked"])) == len(profile["unlocked"]), "duplicate unlocks"
    assert "ember" in profile["unlocked"], "the starter bird must always be available"
    for key in ("feathers", "high_score", "games_played", "total_score"):
        assert isinstance(profile[key], int) and profile[key] >= 0
    assert profile["best_medal"] is None or profile["best_medal"] in dict(config.MEDAL_THRESHOLDS)


def test_sanitised_profile_boots_the_whole_game(tmp_path, monkeypatch):
    """The shop is where a bad skin would surface; prove it survives one."""
    from featherdash import save

    (tmp_path / "profile.json").write_text(
        json.dumps({"version": 1, "skin": "dragon", "unlocked": None, "feathers": -5}),
        encoding="utf-8")
    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    monkeypatch.setenv("FEATHERDASH_WINDOW", "480x854")

    from featherdash.app import App

    app = App()
    for scene in ("menu", "shop", "game"):
        app.go(scene)
        app._apply_pending()
        app.scene.step(0.016)
        app.scene.update(0.016)
        app.scene.draw(app.display.surface, app.display.viewport, 1.0)
