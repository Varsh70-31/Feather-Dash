"""The runtime constants in `config` must match the authoring contract in `tools/manifest`.

They are deliberately duplicated -- `tools/` is excluded from the Android bundle --
so this test is what stops them drifting apart and shipping a game that asks for
sprites the generators never produced.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import manifest  # noqa: E402

from featherdash import config  # noqa: E402


def test_themes_match() -> None:
    assert tuple(config.THEMES) == tuple(manifest.THEMES)


def test_frame_counts_match() -> None:
    assert config.BIRD_FRAMES == manifest.BIRD_FRAMES
    assert config.COIN_FRAMES == manifest.COIN_FRAMES


def test_skins_match() -> None:
    assert tuple(s[0] for s in manifest.BIRD_SKINS) == config.SKIN_KEYS
    costs = {s[0]: s[-1] for s in manifest.BIRD_SKINS}
    assert {k: c for k, _, c in config.SKINS} == costs
    names = {s[0]: s[1] for s in manifest.BIRD_SKINS}
    assert {k: n for k, n, _ in config.SKINS} == names


def test_ui_constants_match() -> None:
    assert config.PANEL_INSET == manifest.PANEL_INSET
    assert config.BUTTON_INSET == manifest.BUTTON_INSET
    assert tuple(config.ICONS) == tuple(manifest.ICONS)
    assert tuple(config.MEDALS) == tuple(manifest.MEDALS)
    assert tuple(config.PARTICLE_SPRITES) == tuple(manifest.PARTICLES.keys())


def test_medal_thresholds_match() -> None:
    assert dict(config.MEDAL_THRESHOLDS) == manifest.MEDAL_THRESHOLDS


def test_world_dimensions_match() -> None:
    assert config.WORLD_H == manifest.WORLD_H
    assert config.WORLD_W_MIN == manifest.WORLD_W_MIN
    assert config.WORLD_W_MAX == manifest.WORLD_W_MAX
    assert config.GROUND_H == manifest.GROUND_DRAW_H
    assert config.GROUND_ART_H == manifest.GROUND_ART_H


def test_sky_furniture_matches() -> None:
    """The sun and the stars are placed by the runtime but authored by the tools.

    Both sides have to agree on where the sun is and how big it is drawn, or the
    disc stops sitting in its own glow.
    """
    assert config.SUN_POS == manifest.SUN_POS
    assert config.SUN_DRAW == manifest.SUN_DRAW
    assert config.STAR_ALPHA == manifest.STAR_ALPHA
    assert set(config.SUN_POS) == set(manifest.THEMES)
    assert set(config.STAR_ALPHA) <= set(manifest.THEMES)


def test_far_layer_height_preserves_its_tile_width() -> None:
    """Cropping the unreachable bottom off far_*.png must not rescale it.

    The layer's world width follows from the source aspect ratio, so trimming
    rows silently widens every mountain unless the height moves with it.
    """
    src_w, src_h = manifest.LAYER_FAR
    tile_w = config.LAYER_FAR_H * src_w / src_h
    assert abs(tile_w - 983.0) < 1.5, tile_w


def test_runtime_never_imports_build_only_modules() -> None:
    """No module under `featherdash/` may import numpy, Pillow or tools/.

    They are not shipped in the APK, so such an import is a guaranteed crash on
    device rather than a mere size regression.
    """
    import re

    banned = re.compile(r"^\s*(?:import|from)\s+(numpy|PIL|manifest|artlib|tools)\b", re.M)
    offenders = []
    pkg = os.path.join(ROOT, "featherdash")
    for dirpath, _, filenames in os.walk(pkg):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="utf-8") as fh:
                for match in banned.finditer(fh.read()):
                    offenders.append(f"{os.path.relpath(path, ROOT)}: imports {match.group(1)}")
    assert not offenders, "build-only imports found in runtime code:\n" + "\n".join(offenders)


if __name__ == "__main__":
    import traceback

    failures = 0
    for fn_name, fn in sorted(globals().items()):
        if fn_name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {fn_name}")
            except AssertionError:
                failures += 1
                print(f"  FAIL {fn_name}")
                traceback.print_exc()
    print("contract:", "OK" if not failures else f"{failures} FAILURES")
    sys.exit(1 if failures else 0)
