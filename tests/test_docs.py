"""Self-test for the documentation and the commercial material.

Prose rots silently. These checks make the parts of it that *can* be wrong in a
measurable way fail loudly instead:

* every character-limited store field states its real length, and that length is
  inside the limit Google Play actually enforces;
* every number quoted in the docs still matches the constant it was read from,
  so retuning the game breaks the listing copy rather than quietly invalidating
  it;
* the font licence claims match the bytes in the font's own `name` table;
* the permission and packaging claims match `buildozer.spec`;
* every internal link and cross-document anchor resolves.

Runs under pytest (`make test`) and standalone:

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python tests/test_docs.py

Nothing here imports pygame -- the font is parsed with `struct` -- but the dummy
drivers are set anyway so this file behaves identically however it is invoked.
"""
from __future__ import annotations

import os
import re
import struct
import sys
from typing import Iterable

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from featherdash import config  # noqa: E402
from featherdash.platform import APP_ID  # noqa: E402

# The documents under test.
README = "README.md"
PRIVACY = "docs/PRIVACY_POLICY.md"
SUBMISSION = "docs/PLAY_STORE_SUBMISSION.md"
CREDITS = "docs/CREDITS.md"
ARCHITECTURE = "docs/ARCHITECTURE.md"
LISTING = "store/listing.md"
SCREENSHOTS = "store/screenshots.md"

DOCS = (README, PRIVACY, SUBMISSION, CREDITS, ARCHITECTURE, LISTING, SCREENSHOTS)

# Paths the documents legitimately name before they exist: build outputs, and the
# one licence file `docs/CREDITS.md` exists to demand. These are reported, never
# asserted -- failing on them would make this suite depend on whether somebody
# has run a build.
PENDING_PATHS = (
    "store/screenshots/",   # produced by `make screenshots`
    "bin/",                  # Android build output
)

# Paths the docs name on purpose even though they are gone: the credits file
# records the provenance of the version 1 media precisely because it was deleted,
# and a reader needs to know what used to be there.
REMOVED_PATHS = ("Images/", "Sounds/", "Fonts/")

# `make` targets the docs promise. Anything not in the Makefile is reported.
PENDING_TARGETS = ("screenshots",)

_pending: list[str] = []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def strip_fences(text: str) -> str:
    """Remove fenced code blocks, so inline-backtick scans ignore shell snippets."""
    return re.sub(r"^```.*?^```", "", text, flags=re.S | re.M)


def slug(heading: str) -> str:
    """GitHub's heading-anchor rule: lowercase, drop punctuation, spaces to hyphens."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s).strip("-")


def anchors(text: str) -> set[str]:
    return {slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.*?)\s*$", text, flags=re.M)}


COUNT_HEADING = re.compile(
    r"^#{2,3}\s+\d+\.\s+(?P<name>.+?)\s+—\s+(?P<count>\d+)\s*/\s*(?P<limit>\d+)\s+characters\s*$",
    flags=re.M,
)


def counted_fields(rel: str) -> list[tuple[str, int, int, str]]:
    """(name, stated length, limit, body) for every `## N. X - a / b characters`."""
    text = read(rel)
    out: list[tuple[str, int, int, str]] = []
    for m in COUNT_HEADING.finditer(text):
        fence = re.search(r"^```[a-z]*\n(.*?)\n```", text[m.end():], flags=re.S | re.M)
        assert fence is not None, f"{rel}: '{m.group('name')}' has no fenced block after it"
        out.append((m.group("name"), int(m.group("count")), int(m.group("limit")), fence.group(1)))
    return out


ROW = re.compile(r"^\|\s*`(?P<text>[^`]+)`\s*\|\s*(?P<count>\d+)\s*\|", flags=re.M)


def section(rel: str, heading_prefix: str) -> str:
    """The body of the section whose heading starts with `heading_prefix`."""
    text = read(rel)
    start = text.find(heading_prefix)
    assert start >= 0, f"{rel}: no heading starting {heading_prefix!r}"
    level = len(heading_prefix) - len(heading_prefix.lstrip("#"))
    nxt = re.search(r"^#{1,%d}\s" % level, text[start + len(heading_prefix):], flags=re.M)
    return text[start:start + len(heading_prefix) + (nxt.start() if nxt else len(text))]


def says(rel: str, *needles: str) -> None:
    text = read(rel)
    for needle in needles:
        assert needle in text, f"{rel} should mention {needle!r}"


def font_names(path: str) -> dict[int, str]:
    """Name-table records from a TrueType file, keyed by nameID."""
    with open(path, "rb") as fh:
        data = fh.read()
    count = struct.unpack(">H", data[4:6])[0]
    tables = {}
    for i in range(count):
        off = 12 + i * 16
        tag = data[off:off + 4].decode("latin-1")
        toff, tlen = struct.unpack(">II", data[off + 8:off + 16])
        tables[tag] = (toff, tlen)
    toff, _ = tables["name"]
    _, records, stroff = struct.unpack(">HHH", data[toff:toff + 6])
    out: dict[int, str] = {}
    for i in range(records):
        rec = toff + 6 + i * 12
        pid, _eid, _lid, nid, length, soff = struct.unpack(">HHHHHH", data[rec:rec + 12])
        raw = data[toff + stroff + soff:toff + stroff + soff + length]
        try:
            value = raw.decode("utf-16-be") if pid == 3 else raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
        # Keep the first legible record for each ID; they repeat per platform.
        out.setdefault(nid, value)
    return out


def spec_values() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in read("buildozer.spec").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


# --------------------------------------------------------------------------
# the documents exist and are substantial
# --------------------------------------------------------------------------

def test_every_document_exists() -> None:
    for rel in DOCS:
        path = os.path.join(ROOT, rel)
        assert os.path.isfile(path), f"missing deliverable: {rel}"
        assert os.path.getsize(path) > 2000, f"{rel} is suspiciously short"


def test_documents_have_a_title() -> None:
    for rel in DOCS:
        text = read(rel)
        assert re.search(r"^#\s+\S", text, flags=re.M), f"{rel} has no H1"


# --------------------------------------------------------------------------
# the character limits that decide whether copy can be pasted into a console
# --------------------------------------------------------------------------

def test_listing_fields_state_their_true_length() -> None:
    fields = counted_fields(LISTING)
    names = [name for name, _, _, _ in fields]
    for required in ("App title", "Short description", "Full description"):
        assert any(required in n for n in names), f"{LISTING}: no '{required}' field"
    for name, stated, limit, body in fields:
        assert len(body) == stated, (
            f"{LISTING}: '{name}' says {stated} characters but is {len(body)}"
        )
        assert stated <= limit, f"{LISTING}: '{name}' is {stated}, over the {limit} limit"


def test_listing_limits_are_the_real_play_console_limits() -> None:
    limits = {name: limit for name, _, limit, _ in counted_fields(LISTING)}
    expected = {"App title": 30, "Short description": 80, "Full description": 4000}
    for key, value in expected.items():
        match = [lim for name, lim in limits.items() if key in name]
        assert match and match[0] == value, f"{LISTING}: '{key}' limit should be {value}"
    notes = [lim for name, lim in limits.items() if "Release notes" in name]
    assert notes and notes[0] == 500, f"{LISTING}: release notes limit should be 500"


def test_variant_tables_count_correctly() -> None:
    """Alternate titles and short descriptions are length-limited too."""
    cases = (
        (LISTING, "## 1. App title", 30),
        (LISTING, "## 2. Short description", 80),
    )
    checked = 0
    for rel, heading, limit in cases:
        body = section(rel, heading)
        rows = list(ROW.finditer(body))
        assert rows, f"{rel}: no counted rows under {heading!r}"
        for row in rows:
            text, stated = row.group("text"), int(row.group("count"))
            assert len(text) == stated, (
                f"{rel} {heading}: {text!r} says {stated} characters but is {len(text)}"
            )
            assert stated <= limit, (
                f"{rel} {heading}: {text!r} is {stated}, over the {limit} limit"
            )
            checked += 1
    assert checked >= 6, f"only {checked} variant rows checked"


# --------------------------------------------------------------------------
# the commercial landmine: no competitor's name anywhere in the listing
# --------------------------------------------------------------------------

def test_no_competitor_name_in_anything_player_facing() -> None:
    for rel in (LISTING, SCREENSHOTS, README):
        text = read(rel).lower()
        assert "flappy" not in text, (
            f"{rel} contains 'Flappy' -- it must appear nowhere near the listing"
        )
    assert "flappy" not in APP_ID.lower(), "the package name must not contain it either"
    for name, _, _, body in counted_fields(LISTING):
        assert "flappy" not in body.lower(), f"{name} contains it"


def test_the_risk_is_actually_documented() -> None:
    says(SUBMISSION, "Flappy", "The real risk for this app")


# --------------------------------------------------------------------------
# every number quoted in the copy still matches the code it came from
# --------------------------------------------------------------------------

def test_quoted_numbers_match_the_constants() -> None:
    cases: Iterable[tuple[str, object, object, str]] = (
        ("WORLD_H", config.WORLD_H, 1080, "1080"),
        ("WORLD_W_MIN", config.WORLD_W_MIN, 560, "560"),
        ("WORLD_W_MAX", config.WORLD_W_MAX, 1040, "1040"),
        ("PHYSICS_HZ", config.PHYSICS_HZ, 120.0, "120 Hz"),
        ("RAMP_SCORE", config.RAMP_SCORE, 42, "42 points"),
        ("GAP_START", config.GAP_START, 400.0, "400"),
        ("GAP_END", config.GAP_END, 302.0, "302"),
        ("SPEED_START", config.SPEED_START, 322.0, "322"),
        ("SPEED_END", config.SPEED_END, 476.0, "476"),
        ("THEME_EVERY", config.THEME_EVERY, 12, "12 points"),
        ("COIN_CHANCE", config.COIN_CHANCE, 0.62, "62%"),
        ("BIRD_HIT_RX", config.BIRD_HIT_RX, 30, "30"),
        ("GRAVITY", config.GRAVITY, 4300.0, "4300"),
    )
    readme = read(README)
    for name, actual, expected, in_readme in cases:
        assert actual == expected, (
            f"config.{name} is now {actual!r}; the docs say {expected!r} -- update both"
        )
        assert in_readme in readme, f"README should quote {name} as {in_readme!r}"


def test_themes_medals_and_skins_match_the_copy() -> None:
    assert config.THEMES == ("dawn", "day", "dusk", "night")
    full = [b for n, _, _, b in counted_fields(LISTING) if "Full description" in n][0]
    for theme in config.THEMES:
        assert theme in full.lower(), f"the listing should name the {theme} theme"

    thresholds = dict(config.MEDAL_THRESHOLDS)
    assert thresholds == {"bronze": 10, "silver": 25, "gold": 50,
                          "platinum": 100, "diamond": 200}
    for medal, score in thresholds.items():
        assert f"{medal} at {score}" in full.lower(), f"the listing should state {medal} at {score}"

    assert len(config.SKINS) == 6
    costs = [cost for _, _, cost in config.SKINS]
    assert costs == [0, 150, 300, 600, 1200, 2400]
    for _, label, _ in config.SKINS:
        assert label in full, f"the listing should name the {label} bird"
    readme = read(README)
    assert " / ".join(str(c) for c in costs) in readme, "README should list the unlock costs"


def test_version_is_consistent() -> None:
    import featherdash

    assert featherdash.__version__ == "2.0.0"
    says(LISTING, "version 2.0.0")
    says(PRIVACY, "version 2.0.0")
    assert "2.0.0" in read("buildozer.spec") or "version.filename" in read("buildozer.spec")


def test_package_name_is_consistent() -> None:
    assert APP_ID == "com.varshraj.featherdash"
    for rel in (PRIVACY, SUBMISSION, README):
        says(rel, "com.varshraj")


# --------------------------------------------------------------------------
# the font licence claims, checked against the font binary itself
# --------------------------------------------------------------------------

def test_font_licence_claims_match_the_font() -> None:
    path = os.path.join(ROOT, "assets", "fonts", config.FONT_FILE)
    assert os.path.isfile(path), f"the bundled font is missing: {path}"
    names = font_names(path)

    licence = names.get(13, "")
    assert "SIL Open Font License" in licence, f"unexpected licence in the font: {licence!r}"
    assert "1.1" in licence
    assert "Cadson Demak" in names.get(8, ""), names.get(8, "")

    credits_text = read(CREDITS)
    assert "SIL Open Font License" in credits_text
    assert "Cadson Demak" in credits_text
    assert "Bai Jamjuree" in credits_text
    assert names.get(14, "") in credits_text, "CREDITS should cite the font's own licence URL"


# Phrasings in README.md that assert an *open* OFL obligation. The README is the
# first thing a buyer reads, so it must not contradict CREDITS on the one legal
# question these files exist to answer.
README_OFL_OUTSTANDING = (
    "still has to be added",
    "outstanding action",
    "outstanding actions",
)


def test_ofl_text_claim_matches_reality() -> None:
    """The docs must say the OFL text is missing exactly when it is -- in both places."""
    present = os.path.isfile(os.path.join(ROOT, "assets", "fonts", "OFL.txt"))
    credits_text = read(CREDITS)
    readme_text = read(README)
    demands_it = "There is no OFL licence text in this repository" in credits_text
    readme_says_open = [s for s in README_OFL_OUTSTANDING if s in readme_text]
    if present:
        assert not demands_it, (
            "assets/fonts/OFL.txt now exists -- remove the 'action required' block from "
            "docs/CREDITS.md and record the licence as satisfied"
        )
        assert not readme_says_open, (
            f"assets/fonts/OFL.txt exists, but README.md still calls it open ({readme_says_open}); "
            "README and docs/CREDITS.md now disagree about whether the OFL obligation is met"
        )
        says(README, "assets/fonts/OFL.txt")
    else:
        assert demands_it, (
            "assets/fonts/OFL.txt is missing and docs/CREDITS.md no longer says so"
        )
        assert readme_says_open, (
            "assets/fonts/OFL.txt is missing and README.md no longer says so"
        )
        _pending.append("assets/fonts/OFL.txt -- OFL 1.1 text still to be added (docs/CREDITS.md)")


def test_the_deleted_v1_media_is_actually_deleted() -> None:
    """README and CREDITS both say the undocumented v1 trees are gone; check the disk.

    They are named all over the docs as *removed* (REMOVED_PATHS exempts them from
    the link checker), so nothing else would notice them coming back -- and their
    provenance is exactly what the credits file cannot vouch for.
    """
    back = [d for d in ("Images", "Sounds", "Fonts") if os.path.exists(os.path.join(ROOT, d))]
    assert not back, (
        f"{back} exist again, but README.md and docs/CREDITS.md record them as deleted; "
        "either the deletion was reverted or the docs need to stop claiming it"
    )


def test_originality_claims_are_stated_where_they_matter() -> None:
    for rel in (README, CREDITS):
        text = read(rel).lower()
        assert "original" in text
    says(CREDITS, "MIT", "LGPL 2.1", "zlib")
    # The build-time/runtime split is the reason the APK's licence surface is small.
    says(CREDITS, "NumPy", "Pillow")
    says(README, "NumPy and Pillow")


# --------------------------------------------------------------------------
# packaging claims, checked against buildozer.spec
# --------------------------------------------------------------------------

def test_permission_claims_match_the_spec() -> None:
    spec = spec_values()
    perms = [p.strip() for p in spec.get("android.permissions", "").split(",") if p.strip()]
    assert perms == ["android.permission.VIBRATE"], (
        f"buildozer.spec permissions are {perms}; the docs claim VIBRATE only"
    )
    says(SUBMISSION, "VIBRATE")
    says(PRIVACY, "vibrate")
    assert "INTERNET" not in spec.get("android.permissions", ""), (
        "the listing, the privacy policy and the Data Safety form all claim no network access"
    )


def test_packaging_claims_match_the_spec() -> None:
    spec = spec_values()
    assert spec.get("android.release_artifact") == "aab"
    assert "arm64-v8a" in spec.get("android.archs", "")
    says(SUBMISSION, "aab", "arm64-v8a", "64-bit")
    api = spec.get("android.api", "")
    assert api and f"android.api = {api}" in read(SUBMISSION), (
        f"buildozer.spec targets API {api}; docs/PLAY_STORE_SUBMISSION.md quotes a different one"
    )
    assert f"`android.api = {api}`" in read(ARCHITECTURE) or api in read(ARCHITECTURE)


# "bump it in __init__.py *and* buildozer.spec" -- the instruction that would
# give the project two version strings. Matched over whitespace-flattened text
# because a checklist bullet wraps.
BUMP_BOTH = re.compile(
    r"bump(?:ed)?[^.\n]{0,80}__init__\.py[^.]{0,40}\band\b[^.]{0,40}buildozer\.spec", re.I
)

# `aapt <verb> ... something.aab`. aapt/aapt2 dump wants a binary-XML
# AndroidManifest.xml at the archive root, which is how an APK is laid out; an
# AAB keeps a protobuf manifest at base/manifest/, so the command just errors --
# and an operator can read empty output as "no permissions declared".
AAPT_ON_BUNDLE = re.compile(r"aapt\s+\w+[^`\n]*\.aab")


def test_version_is_single_sourced_as_the_docs_claim() -> None:
    """The docs promise one place to bump the version; hold the spec and the prose to it."""
    assert "version" not in spec_values(), (
        "buildozer.spec now sets a literal `version`, which buildozer prefers over "
        "version.filename/version.regex -- the docs promise featherdash/__init__.py is the only "
        "place to bump, and that is now both false and frozen at whatever the literal says"
    )
    says(SUBMISSION, "version.filename", "version.regex")
    flat = re.sub(r"\s+", " ", read(SUBMISSION) + " " + read(README))
    sends_you_to_the_spec = BUMP_BOTH.findall(flat)
    assert not sends_you_to_the_spec, (
        f"a document still says to bump the version in buildozer.spec as well "
        f"({sends_you_to_the_spec}); the key is commented out there on purpose"
    )


def test_permission_audit_uses_a_tool_that_can_read_the_artifact() -> None:
    """The whole 'no internet' story rests on this command actually running."""
    offenders = [
        f"{rel}: {m.strip()}" for rel in DOCS for m in AAPT_ON_BUNDLE.findall(read(rel))
    ]
    assert not offenders, (
        "aapt cannot read an App Bundle's protobuf manifest -- use "
        "`bundletool dump manifest --bundle=...aab`:\n  " + "\n  ".join(offenders)
    )
    # Both documents that tie a listing claim to this audit must name the tool.
    says(SUBMISSION, "bundletool")
    says(LISTING, "bundletool")


def test_the_font_licence_actually_reaches_the_device() -> None:
    """The OFL's one real obligation, checked end to end rather than asserted."""
    exts = [e.strip() for e in spec_values().get("source.include_exts", "").split(",")]
    credits_text = read(CREDITS)
    if not os.path.isfile(os.path.join(ROOT, "assets", "fonts", "OFL.txt")):
        return  # covered by test_ofl_text_claim_matches_reality
    assert "txt" in exts, (
        "assets/fonts/OFL.txt exists but buildozer.spec's source.include_exts has no `txt`, "
        "so the licence would be dropped from the bundle"
    )
    assert "`source.include_exts`" in credits_text, "CREDITS should record why that matters"


def test_in_app_font_attribution_exists() -> None:
    """CREDITS claims the OFL notice is shown in Settings; it has to be there."""
    settings = os.path.join(ROOT, "featherdash", "scenes", "settings.py")
    if not os.path.isfile(settings):
        _pending.append("featherdash/scenes/settings.py -- the in-app attribution lives here")
        return
    with open(settings, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert "Bai Jamjuree" in source and "Cadson Demak" in source, (
        "docs/CREDITS.md says the font attribution is shown on the Settings screen; it is not"
    )
    assert "OFL" in source or "Open Font" in source


# --------------------------------------------------------------------------
# the privacy policy has to survive a reviewer opening it
# --------------------------------------------------------------------------

def test_privacy_policy_is_complete() -> None:
    says(
        PRIVACY,
        "public",                      # the hosting instruction
        "Effective date",
        "COPPA",
        "GDPR",
        "profile.json",
        "Reset progress",
        "Uninstall the app",
        "Children's privacy",
        "Contact",
    )
    assert "[DD Month YYYY]" in read(PRIVACY), "the effective-date placeholder is gone"
    assert "@example.com" in read(PRIVACY), "the contact placeholder is gone"
    # The policy and the Data Safety form must agree: nothing collected, no ads.
    assert "no advertising" in read(PRIVACY).lower()


def test_privacy_policy_lists_every_saved_field_category() -> None:
    """A policy that under-declares what is stored is worse than no policy."""
    from featherdash import save

    text = read(PRIVACY).lower()
    described = {
        "high_score": "high score",
        "feathers": "feather",
        "games_played": "games played",
        "best_medal": "medal",
        "skin": "bird",
        "unlocked": "unlock",
        "sfx": "sound",
        "music": "music",
        "haptics": "haptic",
        "show_fps": "fps",
        "seen_tutorial": "tutorial",
        "best_run_seconds": "longest",
        "total_distance": "distance",
        "total_score": "total score",
        "total_feathers_earned": "feather",
        "speed_scale": "game speed",
        "dash_enabled": "dash",
        "version": "save-format version",
    }
    for key in save.DEFAULTS:
        assert key in described, f"save key {key!r} is not described in the privacy policy"
        assert described[key] in text, f"the privacy policy never mentions {key!r}"


def test_submission_checklist_covers_the_surprises() -> None:
    says(
        SUBMISSION,
        "USD 25",            # the one-time registration fee
        "closed test",
        "14 days",           # the tester-retention window that sets the launch date
        "12 testers",
        "Data Safety",
        "512 x 512",
        "1024 x 500",
        "320",               # the screenshot side-length bound
        "Internal testing",
        "Production",
        "D-U-N-S",
    )


# --------------------------------------------------------------------------
# links, anchors and promised commands
# --------------------------------------------------------------------------

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
INLINE_PATH = re.compile(r"`([^`\n]*/[^`\n]*)`")
SKIP = ("*", "<", ">", "$", "%", "~", "|", "(", ")", ",", " ", "\\")


def _link_targets(rel: str) -> set[str]:
    """Markdown link destinations, which are relative to the document."""
    out = set()
    for target in LINK.findall(read(rel)):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        head = target.split("#")[0]
        if head:
            out.add(head)
    return out


def _inline_paths(rel: str) -> set[str]:
    """Backticked paths in prose, which are written relative to the repository root."""
    out = set()
    for raw in INLINE_PATH.findall(strip_fences(read(rel))):
        if raw.startswith(("http", "/")) or any(ch in raw for ch in SKIP):
            continue
        out.add(raw)
    return out


def _is_pending(path: str) -> bool:
    return any(path.startswith(p) for p in PENDING_PATHS)


def _is_removed(path: str) -> bool:
    return any(path.startswith(p) for p in REMOVED_PATHS)


def test_internal_links_resolve() -> None:
    broken: list[str] = []
    for rel in DOCS:
        doc_dir = os.path.dirname(os.path.join(ROOT, rel))
        candidates = [(t, doc_dir) for t in _link_targets(rel)]
        candidates += [(t, ROOT) for t in _inline_paths(rel)]
        for target, base in sorted(candidates):
            if os.path.exists(os.path.normpath(os.path.join(base, target))):
                continue
            if _is_pending(target):
                _pending.append(f"{target} -- referenced by {rel}, not generated yet")
                continue
            if _is_removed(target):
                continue
            broken.append(f"{rel} -> {target}")
    assert not broken, "broken internal links:\n  " + "\n  ".join(sorted(set(broken)))


def test_cross_document_anchors_resolve() -> None:
    cache = {rel: anchors(read(rel)) for rel in DOCS}
    broken: list[str] = []
    for rel in DOCS:
        base = os.path.dirname(os.path.join(ROOT, rel))
        for target in LINK.findall(read(rel)):
            if "#" not in target or target.startswith(("http://", "https://")):
                continue
            path, _, frag = target.partition("#")
            if not frag:
                continue
            if path:
                resolved = os.path.relpath(os.path.normpath(os.path.join(base, path)), ROOT)
            else:
                resolved = rel
            if resolved not in cache:
                continue
            if frag not in cache[resolved]:
                broken.append(f"{rel} -> {target}")
    assert not broken, "broken anchors:\n  " + "\n  ".join(broken)


def test_promised_make_targets_exist() -> None:
    makefile = os.path.join(ROOT, "Makefile")
    if not os.path.isfile(makefile):
        _pending.append("Makefile -- referenced by the docs, not present")
        return
    with open(makefile, "r", encoding="utf-8") as fh:
        targets = set(re.findall(r"^([a-zA-Z][\w-]*):", fh.read(), flags=re.M))
    promised: set[str] = set()
    for rel in DOCS:
        text = read(rel)
        # Only commands, never prose: backticked `make x`, or a line of a shell block.
        promised |= set(re.findall(r"`make ([a-z][\w-]*)[`\s]", text))
        for block in re.findall(r"^```.*?\n(.*?)^```", text, flags=re.S | re.M):
            promised |= set(re.findall(r"^make ([a-z][\w-]*)", block, flags=re.M))
    missing = sorted(promised - targets)
    for name in missing:
        if name in PENDING_TARGETS:
            _pending.append(f"make {name} -- promised by the docs, not in the Makefile yet")
    hard = [n for n in missing if n not in PENDING_TARGETS]
    assert not hard, f"docs promise make targets that do not exist: {hard}"


def test_documented_generators_exist() -> None:
    for name in re.findall(r"tools/(gen_\w+\.py)", read(README)):
        assert os.path.isfile(os.path.join(ROOT, "tools", name)), f"README names missing {name}"
    for name in re.findall(r"tests/(\w+\.py)", read(README)):
        assert os.path.isfile(os.path.join(ROOT, "tests", name)), f"README names missing {name}"


def test_controls_match_the_input_map() -> None:
    """The README's control table must not promise a key the game ignores."""
    import pygame

    from featherdash import input as fd_input

    readme = read(README)
    for key, label in (
        (pygame.K_SPACE, "Space"), (pygame.K_UP, "↑"), (pygame.K_w, "W"),
        (pygame.K_RETURN, "Enter"),
    ):
        assert key in fd_input.FLAP_KEYS, f"{label} is documented as flap but is not mapped"
        assert label in readme
    assert pygame.K_ESCAPE in fd_input.BACK_KEYS and "Esc" in readme
    assert pygame.K_AC_BACK in fd_input.BACK_KEYS, "the Android back button must be mapped"
    assert pygame.K_p in fd_input.PAUSE_KEYS and "`P`" in readme
    assert pygame.K_TAB in fd_input.PAUSE_KEYS and "`Tab`" in readme


SHOT_HEADING = re.compile(r"^### ((?:phone_)?(\d{2})_(\w+)\.png)", flags=re.M)


def test_screenshot_spec_is_actionable() -> None:
    text = read(SCREENSHOTS)
    shots = SHOT_HEADING.findall(text)
    assert len(shots) == 8, f"expected 8 specified screenshots, found {len(shots)}"
    assert [n for _, n, _ in shots] == [f"{i:02d}" for i in range(1, 9)]
    for heading, number, name in shots:
        block = section(SCREENSHOTS, f"### {heading}")
        assert "**Caption:**" in block, f"screenshot {number}_{name} has no caption"
        assert "**Screen:**" in block and "**State:**" in block, f"{number}_{name} is vague"
    assert "1080 x 1920" in text and "9:16" in text


def test_screenshot_spec_matches_the_capture_tool() -> None:
    """A spec that names shots the tool does not produce is worse than none."""
    tool = os.path.join(ROOT, "tools", "screenshot.py")
    if not os.path.isfile(tool):
        _pending.append("tools/screenshot.py -- the capture tool the spec describes")
        return
    with open(tool, "r", encoding="utf-8") as fh:
        source = fh.read()
    produced = re.findall(r'\(\s*"(\d{2}_\w+)"\s*,', source)
    specified = [f"{n}_{name}" for _, n, name in SHOT_HEADING.findall(read(SCREENSHOTS))]
    assert produced, "could not read the shot list out of tools/screenshot.py"
    assert produced == specified, (
        f"tools/screenshot.py produces {produced}\nstore/screenshots.md specifies {specified}"
    )


# A capture run is only reproducible if the tool seeds the simulation: the four
# gameplay shots step a live `Run`, whose rng otherwise comes from OS entropy, so
# pillar gaps, coin placement and particle spray are redrawn on every run.
SEEDS_THE_SIM = re.compile(r"random\.Random\(|\bseed\s*=")

# Sentences that promise a reproducible capture. Checked in README.md and
# store/screenshots.md, the two documents that make the promise to an operator.
REPRODUCIBILITY_PROMISES = (
    "steps the simulation deterministically",
    "reproduces the same eight images",
    "Nobody has to get lucky",
)


def test_screenshot_reproducibility_claim_matches_the_tool() -> None:
    """Promise reproducible captures only while the tool actually seeds the run.

    Unseeded, the gameplay shots differ on every capture, which makes the promise
    a trap: an operator re-shooting after a palette change cannot tell a changed
    frame from changed art, and may ship a composition the caption no longer fits.
    Asserted in both directions, so seeding the tool forces the prose to catch up.
    """
    tool = os.path.join(ROOT, "tools", "screenshot.py")
    if not os.path.isfile(tool):
        _pending.append("tools/screenshot.py -- the capture tool the spec describes")
        return
    with open(tool, "r", encoding="utf-8") as fh:
        source = fh.read()
    seeded = bool(SEEDS_THE_SIM.search(source))

    prose = read(README) + read(SCREENSHOTS)
    promises = [p for p in REPRODUCIBILITY_PROMISES if p in prose]
    if seeded:
        assert promises, (
            "tools/screenshot.py now seeds the simulation -- say so in README.md and "
            "store/screenshots.md, and drop the 're-rolled each run' caveat"
        )
    else:
        assert not promises, (
            f"README.md/store/screenshots.md promise reproducible captures {promises}, but "
            "tools/screenshot.py seeds nothing, so the four gameplay shots differ every run"
        )
        says(SCREENSHOTS, "re-rolled")
        says(README, "re-rolled")
        # The tool's own docstring repeats the promise. It is outside this
        # subsystem, so report it instead of failing a suite that cannot fix it.
        if "reproducible between runs" in source:
            _pending.append(
                "tools/screenshot.py -- docstring still calls the set 'reproducible between "
                "runs'; seed the sim (recipe in store/screenshots.md) or reword it"
            )


def _png_size(path: str) -> tuple[int, int]:
    """Width and height from a PNG's IHDR -- no Pillow, which is build-time only."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", head[16:24])


def test_captured_screenshots_match_the_documented_size() -> None:
    out = os.path.join(ROOT, "store", "screenshots")
    if not os.path.isdir(out):
        _pending.append("store/screenshots/ -- not captured yet")
        return
    phone = sorted(f for f in os.listdir(out) if f.startswith("phone_") and f.endswith(".png"))
    if not phone:
        _pending.append("store/screenshots/phone_*.png -- not captured yet")
        return
    assert len(phone) == 8, f"expected 8 phone screenshots, found {len(phone)}"
    for name in phone:
        size = _png_size(os.path.join(out, name))
        assert size == (1080, 1920), f"{name} is {size[0]}x{size[1]}, not the documented 1080x1920"
        # Play's own bounds, independent of what this project chose.
        assert all(320 <= side <= 3840 for side in size), f"{name} is outside Play's size limits"


# --------------------------------------------------------------------------
# standalone runner
# --------------------------------------------------------------------------

def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    width = max(len(n) for n, _ in tests)
    failures = 0
    print(f"documentation self-test -- {len(tests)} checks over {len(DOCS)} documents\n")
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name.ljust(width)}  {exc}")
        except Exception as exc:  # a broken check is as bad as a failing one
            failures += 1
            print(f"ERROR {name.ljust(width)}  {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")

    if _pending:
        print("\npending (documented, not yet produced -- not failures):")
        for item in sorted(set(_pending)):
            print(f"  - {item}")

    print()
    if failures:
        print(f"FAILED: {failures} of {len(tests)} checks")
        return 1
    print(f"PASSED: {len(tests)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
