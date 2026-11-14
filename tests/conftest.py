"""Shared headless setup.

Every test runs without a real window or audio device, which is also exactly how
CI runs them.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pygame  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _pygame_session():
    pygame.init()
    pygame.display.set_mode((64, 64))
    yield
    pygame.quit()


@pytest.fixture
def tmp_profile(tmp_path, monkeypatch):
    """A Profile backed by a throwaway directory."""
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    return save.Profile()


@pytest.fixture
def vp():
    """A reference 9:16 phone viewport -- the shape the art is composed for."""
    from featherdash.display import compute

    return compute(608, 1080)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A Profile backed by a throwaway directory.

    Named `profile` as well as `tmp_profile` because the subsystem test modules
    were written as standalone scripts that take it as a parameter; exposing it
    as a fixture lets the same file run under pytest and as `python tests/x.py`.
    """
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path))
    return save.Profile()


@pytest.fixture(autouse=True)
def _isolate_save_dir(tmp_path, monkeypatch):
    """No test may touch the real player profile.

    This is not hypothetical: a capture script that skipped the redirect wrote
    291 games and a changed bird into the developer's own save, and the store
    screenshots then showed that bird instead of the one the branding is built
    around. Redirecting here means a test has to work at it to get the real file.
    """
    from featherdash import save

    monkeypatch.setattr(save, "data_dir", lambda: str(tmp_path / "save"))
    yield


@pytest.fixture(autouse=True)
def _reset_global_caches():
    """Give every test the cache state of a freshly launched game.

    The renderer memoises aggressively -- scaled surfaces, rendered glyphs,
    nine-sliced frames, tinted particles -- in module-level dicts, which is
    correct for a game that runs one process for one session but makes tests
    order-dependent: a test that asserts something about cache growth passes
    alone and fails after a test that already filled it. Resetting here is what
    makes a failure mean what it says.
    """
    _invalidate_all()
    yield
    _invalidate_all()


def _invalidate_all() -> None:
    import importlib

    # (module, callable) pairs, each optional: a module may not be importable
    # yet while a subsystem is still being built.
    for mod_name, fn_name in (
        ("featherdash.assets", "invalidate_scaled"),
        ("featherdash.ui.text", "invalidate"),
        ("featherdash.ui.widgets", "invalidate"),
        ("featherdash.ui.metrics", "invalidate"),
        ("featherdash.fx.particles", "invalidate"),
    ):
        try:
            fn = getattr(importlib.import_module(mod_name), fn_name, None)
            if callable(fn):
                fn()
        except Exception:
            pass

    # Caches with no public reset, cleared by name.
    for mod_name, attrs in (
        ("featherdash.entities.pillar", ("_flip_cache", "_glow_cache")),
        ("featherdash.world", ("_bottom_cache",)),
        ("featherdash.scenes.game", ("_dim_cache",)),
    ):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for attr in attrs:
            cache = getattr(mod, attr, None)
            if hasattr(cache, "clear"):
                cache.clear()
