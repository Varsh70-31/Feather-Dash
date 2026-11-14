#!/usr/bin/env python3
"""Static checks on ``buildozer.spec`` before a 45-minute build finds out.

buildozer parses its spec with configparser and reads the options it knows
about. An option it does *not* know about is silently ignored -- there is no
warning, no error, nothing. A typo, or a key that was renamed between buildozer
versions, therefore costs you a wrong icon, an unsigned bundle or a rejected
upload, discovered at the far end of a long build or, worse, in a store review.

This module closes that hole three ways:

* **duplicates** -- configparser in strict mode rejects a key defined twice,
  which is otherwise last-one-wins and invisible.
* **unknown keys** -- every key is checked against buildozer's own generated
  ``default.spec`` when one is available (the Docker image captures the pinned
  buildozer's at ``/opt/buildozer-default.spec``), falling back to a curated
  list for a bare checkout with no container.
* **Play readiness** -- the handful of values Google actually enforces: a
  64-bit ABI, an App Bundle artifact, the target API floor
  (``PLAY_MIN_TARGET_API``), and a version that is defined exactly once.
* **what actually gets packed** -- buildozer's own inclusion algorithm is
  replayed over the real tree (``packed_files``), so the bundle's contents are
  asserted as an effect rather than as a pattern string. ``include_patterns``
  *un-excludes*, and ``fnmatch``'s ``*`` crosses ``/``, which is how a spec
  that excluded ``assets/branding/*`` shipped it anyway.

Standard library only, Python 3.9+. It must run in CI, in the build container
and on a developer's laptop with nothing installed.

Usage::

    python3 packaging/android/validate_spec.py buildozer.spec
    python3 packaging/android/validate_spec.py buildozer.spec \\
        --default-spec /opt/buildozer-default.spec
"""
from __future__ import annotations

import argparse
import configparser
import os
import re
import sys
from collections.abc import Iterable, Sequence
from fnmatch import fnmatch

# --------------------------------------------------------------------------
# severities
# --------------------------------------------------------------------------
ERROR = "error"
WARN = "warn"

Problem = tuple[str, str]  # (severity, message)

# --------------------------------------------------------------------------
# Play policy, in one place
# --------------------------------------------------------------------------
# Google Play's rolling target-API rule: an upload must target an API level
# released within one year of the latest major Android release, and the
# threshold moves every 31 August. The 31-Aug-2026 cut-off requires API 36
# (Android 16), which is what buildozer.spec sets and what README section 8
# documents.
#
# This constant is the ONE definition. packaging/android/selftest.py imports it
# rather than restating the number, because the three of them drifted once
# already: the spec said 36, the validator warned below 35, and the self-test
# required >= 35 -- so dropping the spec to 35 (which the spec itself documents
# as the fallback when AGP refuses compileSdk 36) passed every check and would
# have been rejected by Play hours after upload.
#
# It is an ERROR, not a warning. Play's gate is a hard rejection, so a build
# that cannot be published is a failed build.
PLAY_MIN_TARGET_API = 36

# When the number above must be re-checked against the published rule. Raising
# PLAY_MIN_TARGET_API means moving this date with it.
PLAY_TARGET_API_REVIEW = "2027-08-31"
PLAY_TARGET_API_URL = "https://developer.android.com/google/play/requirements/target-sdk"


# --------------------------------------------------------------------------
# the key catalogue
# --------------------------------------------------------------------------
# Fallback only. The authoritative list is whatever the *pinned* buildozer
# generates with `buildozer init`, which is why the Docker image captures it --
# see --default-spec. This list exists so the check still does something useful
# on a laptop with no container, and it is deliberately a superset: a false
# "unknown key" is more annoying than a missed one, and the container check is
# the one that gates a release.
_FALLBACK_KEYS: set[str] = {
    # identity / sources
    "title", "package.name", "package.domain",
    "source.dir", "source.include_exts", "source.exclude_exts",
    "source.include_patterns", "source.exclude_patterns",
    "source.exclude_dirs",
    # version
    "version", "version.regex", "version.filename",
    # requirements
    "requirements", "garden_requirements",
    # presentation
    "icon.filename", "presplash.filename", "orientation", "fullscreen",
    "services",
    # android: build
    "android.api", "android.minapi", "android.ndk", "android.ndk_api",
    "android.sdk", "android.ndk_path", "android.sdk_path", "android.ant_path",
    "android.archs", "android.skip_update", "android.accept_sdk_license",
    "android.release_artifact", "android.debug_artifact",
    "android.numeric_version", "android.no-byte-compile-python",
    "android.copy_libs", "android.enable_androidx",
    # android: manifest / runtime
    "android.permissions", "android.apptheme", "android.entrypoint",
    "android.manifest.launch_mode", "android.manifest.intent_filters",
    "android.manifest.orientation", "android.manifest_placeholders",
    "android.allow_backup", "android.backup_rules", "android.private_storage",
    "android.presplash_color", "android.presplash_lottie",
    "android.meta_data", "android.uses_library", "android.home_app",
    "android.extra_manifest_xml",
    "android.extra_manifest_application_arguments",
    "android.service_class_name", "android.activity_class_name",
    "android.wakelock",
    # android: gradle / extra inputs
    "android.add_src", "android.add_jars", "android.add_aars",
    "android.add_resources", "android.add_assets", "android.add_activities",
    "android.add_compile_options", "android.add_gradle_repositories",
    "android.add_packaging_options", "android.gradle_dependencies",
    "android.gradle_repositories", "android.library_references",
    "android.whitelist", "android.blacklist_src", "android.whitelist_src",
    # android: tooling
    "android.logcat_filters", "android.logcat_pid_only", "android.adb_args",
    "android.p4a_dir",
    # p4a
    "p4a.url", "p4a.fork", "p4a.branch", "p4a.commit", "p4a.source_dir",
    "p4a.local_recipes", "p4a.hook", "p4a.bootstrap", "p4a.port",
    "p4a.setup_py", "p4a.extra_args",
    # osx / ios -- unused here but valid keys
    "osx.python_version", "osx.kivy_version",
    # [buildozer]
    "log_level", "warn_on_root", "build_dir", "bin_dir",
}

# Keys whose spelling genuinely differs between buildozer versions. Flagged as
# a warning rather than an error when checking against the fallback list, and
# resolved properly when --default-spec is supplied.
#
# The adaptive-icon names are the cautionary tale this whole module exists for:
# the plausible-looking `icon.adaptive_icon.foreground.filename` is wrong, and
# buildozer 1.5.0 actually spells it `icon.adaptive_foreground.filename` with
# no monochrome equivalent at all. Nothing warned about it; the spec just
# quietly built a square icon.
_VERSION_DEPENDENT: set[str] = {
    "icon.adaptive_foreground.filename",
    "icon.adaptive_background.filename",
    "icon.adaptive_monochrome.filename",
    "icon.adaptive_icon.foreground",
    "icon.adaptive_icon.background",
    "icon.adaptive_icon.monochrome",
    "icon.adaptive_icon.foreground.filename",
    "icon.adaptive_icon.background.filename",
    "icon.adaptive_icon.monochrome.filename",
}

# Prefixes under which buildozer accepts arbitrary suffixes.
_KEY_PREFIXES: tuple[str, ...] = ("requirements.source.", "ios.", "osx.")

# Packages that must never reach an Android build: build-time only, large, and
# each one a fragile native p4a recipe.
_FORBIDDEN_REQUIREMENTS: set[str] = {"numpy", "pillow", "pil", "scipy", "matplotlib"}

_KEY_LINE = re.compile(r"^\s*#?\s*([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*=")


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def load_spec(path: str) -> configparser.RawConfigParser:
    """Parse a buildozer.spec, raising on anything buildozer would mis-handle.

    RawConfigParser, not ConfigParser: buildozer values contain ``%(source.dir)s``
    and a ``#141032`` colour, both of which the interpolating parser mangles or
    rejects. ``strict=True`` is the point of the exercise -- it turns a silently
    last-one-wins duplicate key into an exception.
    """
    parser = configparser.RawConfigParser(strict=True)
    # buildozer's keys are case-sensitive in spirit (and all lowercase in
    # practice); the default optionxform would quietly fold `android.API`.
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    with open(path, encoding="utf-8") as handle:
        parser.read_file(handle, source=path)
    return parser


def known_keys_from_default_spec(path: str) -> set[str]:
    """Every option name buildozer's own generated default.spec mentions.

    Most of default.spec is commented out, so a plain configparser pass would
    see almost nothing; the keys are harvested line by line with the leading
    ``#`` stripped. This is the authoritative catalogue for the exact buildozer
    version that generated the file.
    """
    keys: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.lstrip().startswith("["):
                continue
            match = _KEY_LINE.match(line)
            if match:
                keys.add(match.group(1))
    return keys


def spec_keys(parser: configparser.RawConfigParser) -> list[tuple[str, str]]:
    """(section, key) for every option actually set."""
    out: list[tuple[str, str]] = []
    for section in parser.sections():
        for key in parser.options(section):
            out.append((section, key))
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def _is_known(key: str, known: set[str]) -> bool:
    if key in known:
        return True
    return any(key.startswith(prefix) for prefix in _KEY_PREFIXES)


def check_keys(
    parser: configparser.RawConfigParser,
    known: set[str],
    authoritative: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    for section, key in spec_keys(parser):
        if _is_known(key, known):
            continue
        if key in _VERSION_DEPENDENT and not authoritative:
            problems.append((
                WARN,
                f"[{section}] {key}: this key's spelling varies between buildozer "
                "versions and could not be confirmed without a default.spec. "
                "Run `packaging/android/build.sh validate` to settle it."
                ,
            ))
            continue
        problems.append((
            ERROR,
            f"[{section}] {key}: not a key this buildozer understands -- it will be "
            "SILENTLY IGNORED. Check the spelling.",
        ))
    return problems


def _get(parser: configparser.RawConfigParser, key: str, default: str = "") -> str:
    for section in parser.sections():
        if parser.has_option(section, key):
            return parser.get(section, key).strip()
    return default


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def check_play_requirements(parser: configparser.RawConfigParser) -> list[Problem]:
    """The values Google enforces at upload time."""
    problems: list[Problem] = []

    archs = _split_list(_get(parser, "android.archs"))
    if not archs:
        problems.append((ERROR, "android.archs is unset; Play needs an explicit 64-bit ABI."))
    elif not any(a in ("arm64-v8a", "x86_64") for a in archs):
        problems.append((
            ERROR,
            "android.archs = {} has no 64-bit ABI. Play rejects 32-bit-only "
            "uploads outright.".format(", ".join(archs)),
        ))

    artifact = _get(parser, "android.release_artifact").lower()
    if artifact != "aab":
        problems.append((
            ERROR,
            "android.release_artifact = {!r}; Play requires an App Bundle "
            "(aab) for new applications.".format(artifact or "<unset>"),
        ))

    api = _get(parser, "android.api")
    if not api.isdigit():
        problems.append((ERROR, "android.api must be an integer target API level."))
    else:
        minapi = _get(parser, "android.minapi")
        ndk_api = _get(parser, "android.ndk_api")
        if minapi.isdigit() and int(minapi) > int(api):
            problems.append((
                ERROR,
                f"android.minapi ({minapi}) is above android.api ({api}).",
            ))
        if minapi.isdigit() and ndk_api.isdigit() and minapi != ndk_api:
            problems.append((
                ERROR,
                f"android.ndk_api ({ndk_api}) must equal android.minapi ({minapi}); a "
                "mismatch produces binaries that load on your build machine "
                "and crash on an older device.",
            ))
        if int(api) < PLAY_MIN_TARGET_API:
            problems.append((
                ERROR,
                f"android.api = {api} is below Play's target-API floor of "
                f"{PLAY_MIN_TARGET_API}; the upload would be REJECTED. If gradle "
                "refuses this compileSdk, update p4a rather than lowering the "
                f"API. Re-check the rule by {PLAY_TARGET_API_REVIEW}: "
                f"{PLAY_TARGET_API_URL}",
            ))

    return problems


def check_version_single_source(parser: configparser.RawConfigParser, root: str) -> list[Problem]:
    """The version must be defined exactly once, and must actually resolve."""
    problems: list[Problem] = []
    literal = None
    for section in parser.sections():
        if parser.has_option(section, "version"):
            literal = parser.get(section, "version").strip()
    regex = _get(parser, "version.regex")
    filename = _get(parser, "version.filename")

    if literal and regex:
        problems.append((
            ERROR,
            "both `version` and `version.regex` are set. buildozer prefers the "
            "literal, so the regex is dead and the version is defined twice.",
        ))
        return problems

    if not literal and not regex:
        problems.append((
            ERROR,
            "no version: set either `version`, or `version.regex` with "
            "`version.filename`.",
        ))
        return problems

    if regex and not filename:
        problems.append((ERROR, "version.regex is set but version.filename is not."))
        return problems

    if regex:
        resolved = filename.replace("%(source.dir)s", _get(parser, "source.dir", "."))
        target = os.path.normpath(os.path.join(root, resolved))
        if not os.path.isfile(target):
            problems.append((ERROR, f"version.filename does not exist: {target}"))
            return problems
        with open(target, encoding="utf-8") as handle:
            text = handle.read()
        try:
            match = re.search(regex, text)
        except re.error as exc:
            problems.append((ERROR, f"version.regex does not compile: {exc}"))
            return problems
        if not match:
            problems.append((
                ERROR,
                f"version.regex {regex!r} matches nothing in {target}; the build would "
                "have no version.",
            ))
        elif not match.groups():
            problems.append((ERROR, "version.regex has no capture group."))

    return problems


def check_requirements(parser: configparser.RawConfigParser) -> list[Problem]:
    problems: list[Problem] = []
    raw = [r.lower() for r in _split_list(_get(parser, "requirements"))]
    if not raw:
        problems.append((ERROR, "requirements is empty."))
        return problems
    # A requirement may carry a version pin ("python3==3.11.9"); every check
    # below is about which recipe is present, so compare on the name alone.
    reqs = [_requirement_name(r) for r in raw]
    if "python3" not in reqs:
        problems.append((ERROR, "requirements is missing python3."))
    if "python3" in reqs and "hostpython3" in reqs:
        pins = {_requirement_name(r): _requirement_pin(r) for r in raw}
        if pins.get("python3") != pins.get("hostpython3"):
            problems.append((
                ERROR,
                "python3 and hostpython3 are pinned to different versions "
                f"({pins.get('python3') or 'unpinned'} vs "
                f"{pins.get('hostpython3') or 'unpinned'}). python-for-android "
                "refuses to start when they disagree.",
            ))
    for bad in sorted(_FORBIDDEN_REQUIREMENTS.intersection(reqs)):
        problems.append((
            ERROR,
            f"requirements contains {bad!r}, which is a BUILD-TIME dependency of "
            "tools/ only. It is many megabytes and a fragile native recipe; "
            "nothing imported at game runtime needs it.",
        ))
    if "kivy" in reqs and "pygame" in reqs:
        problems.append((WARN, "kivy and pygame both requested; they fight over SDL2."))
    return problems


def _requirement_name(req: str) -> str:
    """The recipe name from a requirement that may carry a version pin."""
    for sep in ("==", ">=", "<=", "~=", ">", "<", "="):
        if sep in req:
            return req.split(sep, 1)[0].strip()
    return req.strip()


def _requirement_pin(req: str) -> str:
    """The version a requirement is pinned to, or "" when it is unpinned."""
    if "==" in req:
        return req.split("==", 1)[1].strip()
    return ""


def check_apptheme(parser: configparser.RawConfigParser) -> list[Problem]:
    """`fullscreen = 1` makes p4a append `.Fullscreen` to the theme itself.

    Naming the Fullscreen variant in the spec as well yields
    `Theme.NoTitleBar.Fullscreen.Fullscreen`, which aapt cannot resolve -- and
    it fails at the very last step, after the entire native compile, so the
    cost of not catching it here is a whole build.
    """
    problems: list[Problem] = []
    theme = _get(parser, "android.apptheme", "")
    fullscreen = _get(parser, "fullscreen", "1").strip()
    if theme and fullscreen not in ("0", "false", "False") and theme.endswith(".Fullscreen"):
        problems.append((
            ERROR,
            f"android.apptheme is {theme!r} while fullscreen is on. "
            "python-for-android appends '.Fullscreen' to the theme, so this "
            "becomes '...Fullscreen.Fullscreen' and aapt fails. Drop the "
            "suffix here, or set fullscreen = 0.",
        ))
    return problems


def check_referenced_files(parser: configparser.RawConfigParser, root: str) -> list[Problem]:
    """Icons and presplash are read off the host at build time, not packed."""
    problems: list[Problem] = []
    source_dir = _get(parser, "source.dir", ".")
    for key in ("icon.filename", "presplash.filename",
                "icon.adaptive_foreground.filename",
                "icon.adaptive_background.filename",
                "icon.adaptive_monochrome.filename",
                "icon.adaptive_icon.foreground", "icon.adaptive_icon.background"):
        value = _get(parser, key)
        if not value:
            continue
        resolved = value.replace("%(source.dir)s", source_dir)
        target = os.path.normpath(os.path.join(root, resolved))
        if not os.path.isfile(target):
            # A warning, not an error: branding art is generated by
            # tools/gen_brand.py and may legitimately not exist yet in a fresh
            # clone or while art is still being produced in parallel.
            problems.append((
                WARN,
                f"{key} -> {target} does not exist yet. Run `make assets` before a "
                "real build; buildozer's own error for this is a bare "
                "traceback.",
            ))
    return problems


def check_excludes(parser: configparser.RawConfigParser) -> list[Problem]:
    """Things that must not end up inside the shipped bundle."""
    problems: list[Problem] = []
    dirs = set(_split_list(_get(parser, "source.exclude_dirs")))
    for needed in (".venv", "tools", "tests", "packaging", ".git"):
        if needed not in dirs:
            problems.append((
                ERROR,
                f"source.exclude_dirs is missing {needed!r}; it would be packed into "
                "the bundle.",
            ))
    return problems


# --------------------------------------------------------------------------
# what actually gets packed
# --------------------------------------------------------------------------
# Directories under assets/ whose contents the game loads at runtime through
# featherdash/assets.py. Every file in them must reach the bundle; a missing
# one is a magenta placeholder on a reviewer's phone, which is the single
# failure this check exists to make impossible.
_RUNTIME_ASSET_DIRS: tuple[str, ...] = ("assets/audio", "assets/fonts", "assets/images")

# Host-only art: the launcher icon layers, the presplash and the Play listing
# feature graphic. buildozer reads icon.filename / presplash.filename off the
# host filesystem when it generates the Android resources, so a copy inside the
# packed app is dead weight that nothing on the device can open.
_HOST_ONLY_ASSET_DIR = "assets/branding"


def packed_files(parser: configparser.RawConfigParser, root: str) -> list[str]:
    """Replay buildozer's own inclusion algorithm and return what it would pack.

    A transcription of ``Buildozer._copy_application_sources`` from buildozer
    1.5.0 (``buildozer/__init__.py``), deliberately faithful rather than tidy,
    including the parts that surprise people:

    * ``exclude_patterns`` is applied first and ``include_patterns`` then
      *un-excludes* -- an include pattern is not "also pack this", it is
      "never mind the exclusion";
    * ``fnmatch``'s ``*`` crosses ``/``, so ``assets/*`` reaches
      ``assets/branding/icon_1024.png``;
    * the extension filter runs last and is never bypassed, so no pattern can
      force a file in;
    * a file whose name begins with ``.`` is skipped wherever it is.

    Asserting on the *result* of this is the only honest way to test the
    packing rules. Checking that a pattern string is present in the spec tests
    nothing: the string that was supposed to keep assets/branding/ out was
    present, and 2.2 MB of it shipped anyway.
    """
    source_dir = os.path.realpath(
        os.path.join(root, _get(parser, "source.dir", ".")))
    include_exts = [e.lower() for e in _split_list(_get(parser, "source.include_exts"))]
    exclude_exts = [e.lower() for e in _split_list(_get(parser, "source.exclude_exts"))]
    exclude_dirs = [d.lower() for d in _split_list(_get(parser, "source.exclude_dirs"))]
    exclude_pats = [p.lower() for p in _split_list(_get(parser, "source.exclude_patterns"))]
    include_pats = [p.lower() for p in _split_list(_get(parser, "source.include_patterns"))]

    out: list[str] = []
    for current, subdirs, files in os.walk(source_dir, followlinks=True):
        relative = current[len(source_dir) + 1:]
        # buildozer tests every component of the ABSOLUTE path for a leading
        # dot; the relative path is used here because the build runs with the
        # project bind-mounted at /workspace, where the absolute prefix has no
        # dotted component. Pruning is safe (and keeps .venv out of the walk)
        # precisely because this rule fires before any pattern can rescue it.
        if any(part.startswith(".") for part in relative.split(os.sep)):
            subdirs[:] = []
            continue

        filtered_root = relative.lower()
        if filtered_root:
            filtered_root += "/"
            is_excluded = False
            for exclude_dir in exclude_dirs:
                if not exclude_dir.endswith("/"):
                    exclude_dir += "/"
                if filtered_root.startswith(exclude_dir):
                    is_excluded = True
                    break
            if not is_excluded:
                for pattern in exclude_pats:
                    if fnmatch(filtered_root, pattern):
                        is_excluded = True
                        break
            for pattern in include_pats:
                if fnmatch(filtered_root, pattern):
                    is_excluded = False
                    break
            if is_excluded:
                continue

        for name in files:
            if name.startswith("."):
                continue
            # buildozer lowercases the bare filename but NOT the filename it
            # joins onto a subdirectory. Reproduced rather than corrected:
            # the question here is what buildozer does, not what it should do.
            candidate = os.path.join(filtered_root, name) if filtered_root else name.lower()
            is_excluded = False
            for pattern in exclude_pats:
                if fnmatch(candidate, pattern):
                    is_excluded = True
                    break
            for pattern in include_pats:
                if fnmatch(candidate, pattern):
                    is_excluded = False
                    break
            if is_excluded:
                continue

            ext = os.path.splitext(name)[1]
            if ext:
                ext = ext[1:].lower()
                if include_exts and ext not in include_exts:
                    continue
                if exclude_exts and ext in exclude_exts:
                    continue

            out.append(os.path.join(relative, name) if relative else name)
    return sorted(out)


def check_bundle_contents(parser: configparser.RawConfigParser, root: str) -> list[Problem]:
    """Assert on what the packing rules DO, not on how they are spelled."""
    problems: list[Problem] = []
    source_dir = os.path.join(root, _get(parser, "source.dir", "."))
    if not os.path.isdir(os.path.join(source_dir, "assets")):
        # A spec being validated outside its own tree (a fixture, a CI matrix
        # copy); there is nothing to replay against.
        return problems

    packed = packed_files(parser, root)
    strays = [f for f in packed if f.startswith(_HOST_ONLY_ASSET_DIR + os.sep)]
    if strays:
        wasted = 0
        for stray in strays:
            try:
                wasted += os.path.getsize(os.path.join(source_dir, stray))
            except OSError:
                pass
        problems.append((
            ERROR,
            "{} file(s) under {}/ ({:,} bytes) would be packed into the bundle, "
            "where nothing can read them: {}. source.exclude_patterns says they "
            "are excluded, but an include_patterns entry un-excludes whatever it "
            "matches and fnmatch's `*` crosses `/`.".format(
                len(strays), _HOST_ONLY_ASSET_DIR, wasted,
                ", ".join(os.path.basename(s) for s in strays[:4])
                + (", ..." if len(strays) > 4 else ""),
            ),
        ))

    packed_set = set(packed)
    for asset_dir in _RUNTIME_ASSET_DIRS:
        on_disk = []
        full = os.path.join(source_dir, asset_dir)
        for current, _subdirs, files in os.walk(full):
            for name in files:
                if name.startswith("."):
                    continue
                on_disk.append(
                    os.path.relpath(os.path.join(current, name), source_dir))
        if not on_disk:
            # Art and audio are generated and may not exist yet; that is the
            # generators' problem, not the spec's.
            continue
        missing = sorted(set(on_disk) - packed_set)
        if missing:
            problems.append((
                ERROR,
                "{} of {} file(s) under {}/ would NOT be packed, and the game "
                "renders a missing image as a magenta placeholder: {}".format(
                    len(missing), len(on_disk), asset_dir,
                    ", ".join(missing[:4]) + (", ..." if len(missing) > 4 else ""),
                ),
            ))
    return problems


def validate(spec_path: str, default_spec: str | None = None) -> list[Problem]:
    """Run every check. Returns problems; an empty list means the spec is good."""
    root = os.path.dirname(os.path.abspath(spec_path)) or "."
    problems: list[Problem] = []

    try:
        parser = load_spec(spec_path)
    except configparser.DuplicateOptionError as exc:
        return [(ERROR, "duplicate key -- buildozer would silently keep the "
                        f"last one: {exc}")]
    except configparser.Error as exc:
        return [(ERROR, f"{spec_path} does not parse: {exc}")]

    if default_spec and os.path.isfile(default_spec):
        known = known_keys_from_default_spec(default_spec)
        authoritative = True
    else:
        if default_spec:
            # ERROR, not WARN. Asking for a default.spec is asking for the
            # authoritative key list -- the only thing that can settle the
            # adaptive-icon spellings, which is the whole reason this module
            # exists. Quietly answering with the bundled fallback and printing
            # "ok" is the silent degradation the check was written to prevent:
            # it confirms nothing while looking exactly like a confirmation.
            # An *unrequested* fallback (no --default-spec at all, as on a
            # laptop or in CI) stays a warning: nobody claimed otherwise.
            return [(
                ERROR,
                f"--default-spec {default_spec} does not exist, so the key check "
                "could only fall back to the bundled list -- which cannot confirm "
                "version-dependent spellings such as "
                "icon.adaptive_foreground.filename. Refusing to report `ok` on a "
                "check that did not run. Rebuild the image "
                "(packaging/android/build.sh image --no-cache) or drop "
                "--default-spec to accept the fallback knowingly.",
            )]
        known = set(_FALLBACK_KEYS)
        authoritative = False

    problems.extend(check_keys(parser, known, authoritative))
    problems.extend(check_play_requirements(parser))
    problems.extend(check_version_single_source(parser, root))
    problems.extend(check_requirements(parser))
    problems.extend(check_apptheme(parser))
    problems.extend(check_referenced_files(parser, root))
    problems.extend(check_excludes(parser))
    problems.extend(check_bundle_contents(parser, root))
    return problems


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def summarise(problems: Iterable[Problem]) -> dict[str, int]:
    counts = {ERROR: 0, WARN: 0}
    for severity, _ in problems:
        counts[severity] = counts.get(severity, 0) + 1
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a buildozer.spec before spending an hour on a build.",
    )
    parser.add_argument("spec", nargs="?", default="buildozer.spec")
    parser.add_argument(
        "--default-spec",
        default=os.environ.get("BUILDOZER_DEFAULT_SPEC"),
        help="buildozer's own generated default.spec, used as the authoritative "
             "key list (the build image puts one at /opt/buildozer-default.spec)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="treat warnings as failures",
    )
    args = parser.parse_args(argv)

    if not os.path.isfile(args.spec):
        sys.stderr.write(f"validate_spec: no such file: {args.spec}\n")
        return 2

    problems = validate(args.spec, args.default_spec)
    for severity, message in problems:
        stream = sys.stderr if severity == ERROR else sys.stdout
        stream.write(f"{severity}: {message}\n")

    counts = summarise(problems)
    if counts[ERROR]:
        sys.stderr.write(
            f"\n{counts[ERROR]} error(s), {counts[WARN]} warning(s) in {args.spec}\n")
        return 1
    if counts[WARN] and args.strict:
        sys.stderr.write(f"\n{counts[WARN]} warning(s), --strict\n")
        return 1
    sys.stdout.write(
        f"{args.spec}: ok ({counts[WARN]} warning(s))\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
