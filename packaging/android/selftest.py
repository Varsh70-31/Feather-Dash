#!/usr/bin/env python3
"""Self-test for the Feather Dash Android packaging layer.

Packaging is the part of a project nobody exercises until the day it matters,
and its failure modes are almost all silent: buildozer ignores an unknown key,
a shell script with a syntax error only breaks on the branch you did not take,
an asset that is excluded from the bundle shows up as a magenta placeholder on
a reviewer's phone.

So this asserts the things that would otherwise be discovered late:

* every shell script parses, has ``set -euo pipefail``, and ``--help`` works;
* ``buildozer.spec`` parses, has no duplicate or unknown keys, and its values
  agree with the rest of the repository -- the applicationId matches
  ``platform.APP_ID``, the version regex really extracts ``__version__``, the
  branding filenames really match what ``tools/gen_brand.py`` writes;
* the Android ``requirements`` line matches what the game actually imports, and
  specifically does not drag in numpy or Pillow;
* every workflow YAML parses and names the jobs and secrets the README claims;
* the Makefile's self-documenting help lists only targets that exist;
* the presplash colour really is the colour that makes the launch seamless,
  measured against the rendered art rather than asserted.

Run headless::

    SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \\
        .venv/bin/python packaging/android/selftest.py

Exits non-zero on the first category of failure. Standard library only, except
for two optional checks that are skipped loudly when PyYAML or Pillow are
absent -- both are build-time tools, and this must stay runnable on a machine
that has neither.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import validate_spec  # noqa: E402  (deliberate: needs HERE on sys.path first)

SPEC = os.path.join(ROOT, "buildozer.spec")
SCRIPTS = ("build.sh", "make_keystore.sh", "docker-entrypoint.sh")
WORKFLOWS = (".github/workflows/ci.yml", ".github/workflows/android.yml")

# Branding art that buildozer.spec points at. The names must match what
# tools/gen_brand.py actually writes -- that file is owned by another part of
# the project, so the agreement is checked rather than assumed.
# Branding art tools/gen_brand.py produces. icon_monochrome.png is generated
# and checked for, but is NOT referenced from buildozer.spec: buildozer 1.5.0
# has no key for the Android 13+ themed icon layer.
BRANDING = (
    "icon_1024.png",
    "presplash.png",
    "adaptive_fg.png",
    "adaptive_bg.png",
    "icon_monochrome.png",
)


# --------------------------------------------------------------------------
# tiny test harness
# --------------------------------------------------------------------------
class Failure(AssertionError):
    pass


_RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def check(name: str) -> Callable[[Callable[[], str | None]], Callable[[], str | None]]:
    """Register a check. The function returns a detail string, or raises."""

    def decorate(func: Callable[[], str | None]) -> Callable[[], str | None]:
        func.__check_name__ = name  # type: ignore[attr-defined]
        _CHECKS.append(func)
        return func

    return decorate


_CHECKS: list[Callable[[], str | None]] = []


class Skip(Exception):
    """Raised by a check that cannot run here, with the reason."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Failure(message)


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def run(cmd: Sequence[str], cwd: str | None = None, env: dict | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    # Headless, always: anything that reaches pygame must not try to open a
    # window or a sound device.
    full_env["SDL_VIDEODRIVER"] = "dummy"
    full_env["SDL_AUDIODRIVER"] = "dummy"
    if env:
        full_env.update(env)
    return subprocess.run(
        list(cmd),
        cwd=cwd or ROOT,
        env=full_env,
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=180,
    )


# --------------------------------------------------------------------------
# files exist
# --------------------------------------------------------------------------
@check("every deliverable exists")
def t_files_exist() -> str:
    expected = [
        "buildozer.spec",
        "Makefile",
        "requirements.txt",
        "requirements-dev.txt",
        "packaging/android/Dockerfile",
        "packaging/android/docker-entrypoint.sh",
        "packaging/android/build.sh",
        "packaging/android/make_keystore.sh",
        "packaging/android/validate_spec.py",
        "packaging/android/selftest.py",
        "packaging/android/README.md",
        ".github/workflows/ci.yml",
        ".github/workflows/android.yml",
    ]
    missing = [p for p in expected if not os.path.isfile(os.path.join(ROOT, p))]
    require(not missing, "missing: {}".format(", ".join(missing)))
    return f"{len(expected)} files"


@check("no game code was touched")
def t_no_game_code() -> str:
    # The task forbids editing the shared modules. Their presence and
    # non-emptiness is all this layer may assume about them.
    for name in ("platform.py", "config.py", "display.py", "assets.py", "save.py"):
        path = os.path.join(ROOT, "featherdash", name)
        require(os.path.isfile(path), f"featherdash/{name} vanished")
        require(os.path.getsize(path) > 0, f"featherdash/{name} is empty")
    return "featherdash/ intact"


# --------------------------------------------------------------------------
# shell scripts
# --------------------------------------------------------------------------
@check("packaging python parses under the 3.9 grammar")
def t_python39_grammar() -> str:
    # The project promises 3.9+, but every machine here runs something newer,
    # so a walrus-in-comprehension or a match statement would sail through
    # locally and only fail on the oldest CI leg. ast.parse's feature_version
    # applies the 3.9 grammar without needing a 3.9 interpreter.
    import ast

    checked = []
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(HERE, name)
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        try:
            ast.parse(source, filename=path, feature_version=(3, 9))
        except SyntaxError as exc:
            raise Failure(f"{name} is not valid Python 3.9: {exc}") from exc
        require("from __future__ import annotations" in source,
                f"{name} is missing `from __future__ import annotations`")
        checked.append(name)
    return f"{', '.join(checked)} parse as 3.9"


@check("shell scripts parse (bash -n)")
def t_bash_syntax() -> str:
    for script in SCRIPTS:
        path = os.path.join(HERE, script)
        result = run(["bash", "-n", path])
        require(result.returncode == 0,
                f"bash -n {script} failed:\n{result.stdout}")
    return f"{len(SCRIPTS)} scripts"


@check("shell scripts are strict and executable-ready")
def t_bash_hygiene() -> str:
    for script in SCRIPTS:
        text = read("packaging/android/" + script)
        require(text.startswith("#!/usr/bin/env bash"),
                f"{script} has no bash shebang")
        require("set -euo pipefail" in text,
                f"{script} is missing `set -euo pipefail`")
        # bash 3.2 is what macOS ships. These are the 4.x-only constructs that
        # would work for the author and break for the user.
        for construct, why in (
            ("declare -A", "associative arrays need bash 4"),
            ("mapfile ", "mapfile needs bash 4"),
            ("readarray ", "readarray needs bash 4"),
            ("${BASH_SOURCE[0]^^}", "case conversion needs bash 4"),
        ):
            require(construct not in text,
                    f"{script} uses {construct!r}: {why}, and macOS ships 3.2")
    return "strict mode + bash 3.2 compatible"


@check("build.sh --help works without Docker")
def t_build_help() -> str:
    result = run(["bash", os.path.join(HERE, "build.sh"), "--help"])
    require(result.returncode == 0,
            f"build.sh --help exited {result.returncode}:\n{result.stdout}")
    for word in ("debug", "release", "clean", "shell", "logcat", "install"):
        require(word in result.stdout, f"build.sh --help does not document {word!r}")
    # Help must not need a daemon; a developer reads it precisely when
    # something is not working.
    require("docker" not in result.stdout.lower() or "Docker" in result.stdout,
            "unexpected docker error leaked into --help")
    return f"{len(result.stdout.splitlines())} lines, all 6 subcommands documented"


@check("build.sh rejects a bad subcommand and runs from any directory")
def t_build_bad_command() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        result = run(["bash", os.path.join(HERE, "build.sh"), "frobnicate"], cwd=tmp)
    require(result.returncode != 0, "build.sh accepted a nonsense subcommand")
    require("unknown command" in result.stdout.lower(),
            f"unhelpful error for a bad subcommand: {result.stdout!r}")
    return f"exits {result.returncode} from an unrelated cwd"


@check("build.sh refuses an unsigned release")
def t_build_requires_signing() -> str:
    # Clear every signing variable, then ask for a release. It must refuse
    # before it ever reaches Docker -- an unsigned or debug-signed bundle is
    # the single most expensive mistake available here.
    env = dict.fromkeys((
        "FEATHERDASH_KEYSTORE", "FEATHERDASH_KEYSTORE_PASSWORD",
        "FEATHERDASH_KEY_ALIAS", "FEATHERDASH_KEY_PASSWORD"), "")
    result = run(["bash", os.path.join(HERE, "build.sh"), "release"], env=env)
    require(result.returncode != 0, "build.sh ran a release build with no keystore")
    lowered = result.stdout.lower()
    # Either it stopped on the missing keystore (the interesting path) or on a
    # missing Docker daemon (the honest path on a machine with Docker down).
    require("featherdash_keystore" in lowered or "docker" in lowered,
            f"release refusal did not explain itself:\n{result.stdout}")
    return f"refused: {result.stdout.strip().splitlines()[0][:70]}"


@check("make_keystore.sh --help works and refuses to clobber")
def t_keystore_help() -> str:
    script = os.path.join(HERE, "make_keystore.sh")
    result = run(["bash", script, "--help"])
    require(result.returncode == 0, f"make_keystore.sh --help exited {result.returncode}")
    require("REFUSE to overwrite" in result.stdout, "--help does not mention the overwrite guard")

    # And the guard itself, against a real existing file.
    with tempfile.TemporaryDirectory() as tmp:
        existing = os.path.join(tmp, "upload.keystore")
        with open(existing, "w", encoding="utf-8") as handle:
            handle.write("not really a keystore")
        result = run(["bash", script, existing], stdin="\n" * 8)
        require(result.returncode != 0, "make_keystore.sh overwrote an existing keystore")
        require("already exists" in result.stdout,
                f"clobber refusal was unclear:\n{result.stdout}")
        with open(existing, encoding="utf-8") as handle:
            require(handle.read() == "not really a keystore", "the existing file was modified")
    return "refuses to overwrite, file untouched"


@check("make_keystore.sh uses Play's key parameters")
def t_keystore_params() -> str:
    text = read("packaging/android/make_keystore.sh")
    require('KEY_ALG="RSA"' in text, "key algorithm is not RSA")
    require("KEY_SIZE=2048" in text, "key size is not 2048")
    require("VALIDITY_DAYS=10000" in text, "validity is not 10000 days")
    require("-storetype PKCS12" in text, "not PKCS12")
    # The whole point of the script is its loud ending.
    require("Play App Signing" in text, "does not mention Play App Signing enrolment")
    require("update to Feather Dash again" in text, "the loss warning is missing")
    return "RSA 2048 / 10000 days / PKCS12, with the loss warning"


_SECRET_VAR = r"(FEATHERDASH_\w*PASSWORD|P4A_\w*PASSWD|KEYSTORE_B64|KEYSTORE_PASSWORD)"
_SECRET_PRINT = re.compile(r"^[^\n#]*\b(echo|printf|cat)\b[^\n]*\$\{?" + _SECRET_VAR, re.M)
_TRACE = re.compile(r"^\s*set -x", re.M)


def _leaky_lines(text: str) -> list[str]:
    """Lines that would put a secret somewhere a human or a log can read it.

    A ``printf`` whose output is redirected into a file or piped into a decoder
    is not a leak -- that is how the keystore gets written -- so a line is only
    suspicious when its output is still heading for stdout.
    """
    leaks = []
    for match in _SECRET_PRINT.finditer(text):
        line = text[match.start():text.find("\n", match.start())]
        if ">" in line or "|" in line:
            continue
        leaks.append(line.strip())
    return leaks


@check("no secret is ever echoed")
def t_no_secret_echo() -> str:
    # A script that prints a passphrase, even once, even on an error path, is
    # a script that ends up in a screen recording or a shared CI log.
    targets = ["packaging/android/" + s for s in SCRIPTS] + list(WORKFLOWS)
    for path in targets:
        text = read(path)
        leaks = _leaky_lines(text)
        require(not leaks, "{} prints a secret:\n          {}".format(
            path, "\n          ".join(leaks)))
        trace = _TRACE.search(text)
        require(trace is None,
                f"{path} enables `set -x`, which echoes every expanded "
                "variable including the passphrases")

    # The carve-out above must not have defanged the check. Prove it still
    # catches the real thing.
    for leaky in ('echo "$FEATHERDASH_KEYSTORE_PASSWORD"',
                  'printf "%s" "${P4A_RELEASE_KEYSTORE_PASSWD}"',
                  'echo "key=${KEYSTORE_B64}"'):
        require(_leaky_lines(leaky),
                f"the leak detector no longer catches {leaky!r}")
    for safe in ('printf \'%s\' "${KEYSTORE_B64}" | base64 -d > ks',
                 'printf "%s" "$P4A_RELEASE_KEYSTORE_PASSWD" > /dev/null'):
        require(not _leaky_lines(safe),
                f"the leak detector false-positives on {safe!r}")

    # Passwords must reach docker by NAME (`-e VAR`), never by value
    # (`-e VAR=secret`), or they land on the container's command line where
    # `ps` on the host can read them.
    build = read("packaging/android/build.sh")
    require("args+=(-e P4A_RELEASE_KEYSTORE_PASSWD)" in build,
            "build.sh does not pass the passphrase to docker by name")
    require(not re.search(r"-e\s+\w*PASSW\w*=", build),
            "build.sh passes a passphrase to docker by value")
    return f"{len(targets)} files clean; detector verified against 3 leaky + 2 safe lines"


# --------------------------------------------------------------------------
# buildozer.spec
# --------------------------------------------------------------------------
@check("buildozer.spec passes validate_spec with no errors")
def t_spec_validates() -> str:
    problems = validate_spec.validate(SPEC)
    errors = [m for severity, m in problems if severity == validate_spec.ERROR]
    require(not errors, "validate_spec errors:\n  " + "\n  ".join(errors))
    warnings = [m for severity, m in problems if severity == validate_spec.WARN]
    return f"0 errors, {len(warnings)} warning(s)"


@check("validate_spec actually catches a broken spec")
def t_validator_has_teeth() -> str:
    # A validator that never fails is decoration. Feed it three specific
    # mistakes and require that each one is caught.
    base = read("buildozer.spec")
    floor = validate_spec.PLAY_MIN_TARGET_API
    # (label, mutated spec, a substring the resulting error must contain). The
    # needle matters: "some error fired" would pass even if an unrelated check
    # tripped, which is how a too-kind test hides a missing one.
    cases = [
        ("typo'd key",
         base.replace("android.archs =", "android.arches =", 1),
         "SILENTLY IGNORED"),
        ("32-bit only",
         base.replace("android.archs = arm64-v8a, armeabi-v7a",
                      "android.archs = armeabi-v7a", 1),
         "64-bit"),
        ("numpy in requirements",
         re.sub(r"^(requirements = .*)$", r"\1,numpy", base, count=1, flags=re.M),
         "numpy"),
        ("apk release artifact",
         base.replace("android.release_artifact = aab",
                      "android.release_artifact = apk", 1),
         "App Bundle"),
        ("version defined twice",
         base.replace("#version = 2.0.0", "version = 9.9.9", 1),
         "defined twice"),
        # The exact mistake buildozer.spec documents as the tempting fallback
        # when AGP refuses compileSdk 36, and which used to pass every gate.
        (f"android.api below the Play floor of {floor}",
         base.replace(f"android.api = {floor}", f"android.api = {floor - 1}", 1),
         "target-API floor"),
    ]
    caught = []
    for label, mutated, needle in cases:
        require(mutated != base, f"mutation {label!r} did not change the spec")
        with tempfile.TemporaryDirectory() as tmp:
            # The spec is validated relative to its own directory, so the
            # version.filename check needs the package beside it.
            pkg = os.path.join(tmp, "featherdash")
            os.makedirs(pkg)
            with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as handle:
                handle.write('__version__ = "2.0.0"\n')
            path = os.path.join(tmp, "buildozer.spec")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            problems = validate_spec.validate(path)
            errors = [m for severity, m in problems if severity == validate_spec.ERROR]
            require(errors, f"validate_spec did not catch: {label}")
            require(any(needle in m for m in errors),
                    f"validate_spec reported an error for {label!r} but not the "
                    f"right one (no {needle!r} in {errors})")
            caught.append(label)

    # Duplicate keys, which configparser's strict mode is there to catch.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "buildozer.spec")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[app]\ntitle = A\ntitle = B\n")
        problems = validate_spec.validate(path)
        require(any("duplicate" in m.lower() for _, m in problems),
                "validate_spec did not catch a duplicate key")
        caught.append("duplicate key")

    # An authoritative key check that cannot find its key list must FAIL, not
    # fall back and print "ok": `build.sh validate` asks for one by path, and
    # README section 7 sends the reader there for the last word on the
    # adaptive-icon spelling.
    problems = validate_spec.validate(SPEC, default_spec="/opt/definitely-not-here")
    require(any(severity == validate_spec.ERROR and "does not exist" in message
                for severity, message in problems),
            "a requested --default-spec that is missing did not produce an "
            f"error: {problems}")
    caught.append("missing --default-spec")

    # And the inclusion replay: a spec that excludes assets/branding/ but also
    # carries an include pattern reaching it ships the art regardless, because
    # include_patterns un-excludes. This is the mutation the old string-level
    # check could not see.
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "featherdash"))
        with open(os.path.join(tmp, "featherdash", "__init__.py"), "w",
                  encoding="utf-8") as handle:
            handle.write('__version__ = "2.0.0"\n')
        for sub, name in (("branding", "icon_1024.png"), ("images", "bird.png")):
            os.makedirs(os.path.join(tmp, "assets", sub))
            with open(os.path.join(tmp, "assets", sub, name), "wb") as binary:
                binary.write(b"\x89PNG\r\n")
        path = os.path.join(tmp, "buildozer.spec")
        mutated = base.replace("#source.include_patterns =",
                               "source.include_patterns = assets/*", 1)
        # If the commented-out line is ever edited away, this mutation would
        # quietly become a no-op and the check would pass by testing nothing.
        require(mutated != base,
                "could not re-introduce source.include_patterns into the spec, "
                "so this check proved nothing")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(mutated)
        problems = validate_spec.validate(path)
        require(any(severity == validate_spec.ERROR and "assets/branding" in message
                    for severity, message in problems),
                "the packing replay did not notice that `include_patterns = "
                f"assets/*` un-excludes assets/branding/: {problems}")
        caught.append("include_patterns un-excluding assets/branding")
    return "caught {}".format(", ".join(caught))


@check("applicationId matches platform.APP_ID")
def t_app_id() -> str:
    parser = validate_spec.load_spec(SPEC)
    domain = parser.get("app", "package.domain").strip()
    name = parser.get("app", "package.name").strip()
    app_id = f"{domain}.{name}"

    # Read platform.py as text rather than importing it: importing would run
    # Android detection and touch the filesystem, and this file must never
    # depend on the game being importable.
    platform_src = read("featherdash/platform.py")
    match = re.search(r'APP_ID\s*=\s*["\']([^"\']+)["\']', platform_src)
    require(match is not None, "APP_ID not found in featherdash/platform.py")
    expected = match.group(1)
    require(app_id == expected,
            f"buildozer.spec builds {app_id!r} but platform.APP_ID is {expected!r}. The "
            "applicationId is immutable once published -- these must agree."
            )
    return f"{app_id}"


@check("version comes from featherdash/__init__.py and nowhere else")
def t_version_single_source() -> str:
    parser = validate_spec.load_spec(SPEC)
    require(not parser.has_option("app", "version"),
            "`version` is set literally; buildozer would prefer it over the "
            "regex and the version would be defined twice")
    regex = parser.get("app", "version.regex")
    filename = parser.get("app", "version.filename").replace("%(source.dir)s", ".")
    target = os.path.normpath(os.path.join(ROOT, filename))
    require(os.path.isfile(target), f"version.filename does not exist: {target}")
    with open(target, encoding="utf-8") as handle:
        match = re.search(regex, handle.read())
    require(match is not None, f"version.regex matched nothing in {target}")
    version = match.group(1)
    require(re.match(r"^\d+\.\d+(\.\d+)?$", version),
            f"extracted version {version!r} is not a sane version number")
    return f"{os.path.relpath(target, ROOT)} -> {version}"


@check("Android requirements match what the game actually imports")
def t_requirements_match_imports() -> str:
    parser = validate_spec.load_spec(SPEC)
    def _name(req: str) -> str:
        """Recipe name from a requirement that may carry a version pin."""
        for sep in ("==", ">=", "<=", "~=", ">", "<", "="):
            if sep in req:
                return req.split(sep, 1)[0].strip()
        return req.strip()

    reqs = {_name(r.lower()) for r in parser.get("app", "requirements").split(",")}

    # Re-derive the import set from source rather than trusting a comment.
    imported = set()
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "featherdash")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            with open(os.path.join(dirpath, filename), encoding="utf-8") as handle:
                for line in handle:
                    match = re.match(r"\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
                    if match:
                        imported.add(match.group(1).lower())

    require("pygame" in imported, "featherdash/ does not import pygame?")
    require("pygame" in reqs, "requirements is missing pygame")
    require("python3" in reqs, "requirements is missing python3")

    # The expensive mistake, in both directions.
    for forbidden in ("numpy", "pillow", "pil"):
        require(forbidden not in reqs,
                f"requirements contains {forbidden!r}: build-time only, many MB, "
                "fragile p4a recipe")
        require(forbidden not in imported,
                f"featherdash/ now imports {forbidden!r} at runtime -- the Android "
                "requirements must change too")

    # The lazy, Android-only imports must be declared or they are not there to
    # import on device.
    if "plyer" in imported:
        require("plyer" in reqs, "platform.py imports plyer but it is not in requirements")
    if "jnius" in imported:
        require("pyjnius" in reqs, "jnius is imported but pyjnius is not in requirements")

    return "requirements={} | runtime imports={}".format(
        ",".join(sorted(reqs)),
        ",".join(sorted(i for i in imported if i in
                        ("pygame", "plyer", "jnius", "numpy", "pil"))))


@check("requirements.txt is runtime-only, requirements-dev.txt is complete")
def t_requirements_files() -> str:
    runtime = [line.split("#")[0].strip() for line in read("requirements.txt").splitlines()]
    runtime = [line for line in runtime if line]
    names = [re.split(r"[<>=!\[]", line)[0].strip().lower() for line in runtime]
    require("pygame" in names, "requirements.txt does not list pygame")
    for forbidden in ("numpy", "pillow"):
        require(forbidden not in names,
                f"requirements.txt lists {forbidden!r}; it is build-time only")

    dev = read("requirements-dev.txt").lower()
    for needed in ("numpy", "pillow", "pytest", "ruff"):
        require(needed in dev, f"requirements-dev.txt is missing {needed}")
    require("-r requirements.txt" in dev,
            "requirements-dev.txt does not include the runtime set")
    return "runtime={}; dev adds numpy, Pillow, pytest, ruff".format(",".join(names))


@check("the bundle excludes everything that must not ship")
def t_excludes() -> str:
    parser = validate_spec.load_spec(SPEC)
    dirs = {d.strip() for d in parser.get("app", "source.exclude_dirs").split(",")}
    patterns = {p.strip() for p in parser.get("app", "source.exclude_patterns").split(",")}
    for needed in (".venv", "tools", "tests", "packaging", ".git", ".github"):
        require(needed in dirs, f"source.exclude_dirs is missing {needed!r}")
    # The art generators are the specific "must not bloat the bundle" case:
    # they are in tools/, and they are the only importers of numpy and Pillow.
    require("tools" in dirs, "the art generators would be packed")
    require(any(p.startswith("assets/branding") for p in patterns),
            "assets/branding/ would be packed twice (buildozer reads the icon "
            "and presplash from the host, not from the packed app)")
    exts = {e.strip() for e in parser.get("app", "source.include_exts").split(",")}
    for needed in ("py", "png", "jpg", "wav", "ogg", "ttf", "json", "md"):
        require(needed in exts, f"source.include_exts is missing {needed!r}")
    return f"{len(dirs)} excluded dirs, {len(exts)} extensions"


@check("the packing rules really keep assets/branding/ out of the bundle")
def t_branding_not_packed() -> str:
    # The check above asserts that the exclusion is SPELLED. This one asserts
    # that it WORKS, by replaying buildozer 1.5.0's own inclusion algorithm
    # over the real tree. The distinction is not academic: the spec excluded
    # `assets/branding/*` and shipped all 2.2 MB of it anyway, because
    # `source.include_patterns = assets/*` un-excluded it and fnmatch's `*`
    # crosses `/`. Every string-level assertion passed throughout.
    parser = validate_spec.load_spec(SPEC)
    packed = validate_spec.packed_files(parser, ROOT)
    require(packed, "the replay packed nothing at all, so it proves nothing")

    strays = [f for f in packed if f.startswith("assets/branding/")]
    require(not strays,
            f"{len(strays)} host-only branding file(s) would be packed into "
            f"every APK and AAB: {strays}")

    # ...and the converse, because "pack nothing" would also have no strays.
    runtime = []
    for sub in ("audio", "fonts", "images"):
        base = os.path.join(ROOT, "assets", sub)
        for current, _dirs, files in os.walk(base):
            runtime += [os.path.relpath(os.path.join(current, f), ROOT)
                        for f in files if not f.startswith(".")]
    missing = sorted(set(runtime) - set(packed))
    require(not missing,
            f"{len(missing)} runtime asset(s) would NOT be packed, which the "
            f"game renders as magenta placeholders: {missing[:6]}")

    branding_dir = os.path.join(ROOT, "assets", "branding")
    on_disk = os.listdir(branding_dir) if os.path.isdir(branding_dir) else []
    return (f"{len(packed)} files packed, {len(runtime)} of them runtime assets, "
            f"0 of {len(on_disk)} branding files")


@check("Play requirements are satisfied")
def t_play_requirements() -> str:
    parser = validate_spec.load_spec(SPEC)
    archs = [a.strip() for a in parser.get("app", "android.archs").split(",")]
    require("arm64-v8a" in archs, "Play rejects a bundle with no 64-bit ABI")
    require("armeabi-v7a" in archs, "armeabi-v7a was dropped; that narrows reach")
    require(parser.get("app", "android.release_artifact").strip() == "aab",
            "Play requires an App Bundle for new apps")
    require(parser.get("app", "android.debug_artifact").strip() == "apk",
            "the debug path should stay an APK so it can be adb installed")

    api = int(parser.get("app", "android.api"))
    minapi = int(parser.get("app", "android.minapi"))
    ndk_api = int(parser.get("app", "android.ndk_api"))
    # Imported, never restated. This file said >= 35 while buildozer.spec and
    # the README said 36, so the fallback the spec itself documents (drop the
    # API when AGP refuses compileSdk 36) sailed through every gate and would
    # have been rejected by Play after upload.
    floor = validate_spec.PLAY_MIN_TARGET_API
    require(api >= floor,
            f"android.api = {api} is below Play's target-API floor of {floor} "
            f"(validate_spec.PLAY_MIN_TARGET_API, review by "
            f"{validate_spec.PLAY_TARGET_API_REVIEW})")
    require(minapi == ndk_api,
            f"android.ndk_api ({ndk_api}) must equal android.minapi ({minapi})")
    require(21 <= minapi <= api, f"android.minapi = {minapi} is out of range")

    require(parser.get("app", "orientation").strip() == "portrait", "not portrait")
    require(parser.get("app", "fullscreen").strip() == "1", "not fullscreen")
    perms = parser.get("app", "android.permissions")
    require("VIBRATE" in perms, "VIBRATE permission missing; platform.vibrate would be a no-op")
    require("INTERNET" not in perms,
            "INTERNET crept into the permissions; the game does not use the network "
            "and an empty permission list is worth keeping")
    return "api={} min={} archs={} aab".format(api, minapi, "+".join(archs))


@check("branding filenames agree with tools/gen_brand.py")
def t_branding_names() -> str:
    parser = validate_spec.load_spec(SPEC)
    referenced = []
    for key in ("icon.filename", "presplash.filename",
                "icon.adaptive_foreground.filename",
                "icon.adaptive_background.filename"):
        if parser.has_option("app", key):
            referenced.append(os.path.basename(parser.get("app", key).strip()))
    require(referenced, "buildozer.spec references no branding art at all")

    # tools/gen_brand.py is written by another part of the project. Parse the
    # names it writes out of its job table rather than assuming they match.
    gen = read("tools/gen_brand.py")
    produced = set(re.findall(r'\("([a-z0-9_]+\.png)",\s*build_', gen))
    require(produced, "could not read the output list from tools/gen_brand.py")

    missing = [name for name in referenced if name not in produced]
    require(not missing,
            f"buildozer.spec points at {missing}, which tools/gen_brand.py does not "
            f"produce (it produces {sorted(produced)})")

    # Present on disk is a bonus, not a requirement: art may still be rendering.
    on_disk = [n for n in BRANDING
               if os.path.isfile(os.path.join(ROOT, "assets", "branding", n))]
    return (f"{len(referenced)} referenced names all produced by gen_brand; "
            f"{len(on_disk)}/{len(BRANDING)} present on disk")


@check("presplash colour is the measured seam colour")
def t_presplash_color() -> str:
    parser = validate_spec.load_spec(SPEC)
    declared = parser.get("app", "android.presplash_color").strip().lstrip("#")
    require(re.match(r"^[0-9a-fA-F]{6}$", declared),
            f"android.presplash_color is not a 6-digit hex colour: {declared!r}")
    want = tuple(int(declared[i:i + 2], 16) for i in (0, 2, 4))

    path = os.path.join(ROOT, "assets", "branding", "presplash.png")
    if not os.path.isfile(path):
        raise Skip("assets/branding/presplash.png has not been generated yet")
    try:
        from PIL import Image  # build-time only; absent on a runtime-only box
    except ImportError as exc:
        raise Skip("Pillow is not installed (it is a build-time dependency)") from exc

    image = Image.open(path).convert("RGB")
    width, height = image.size
    # p4a scales the presplash FIT_CENTER, so on a tall phone the bands above
    # and below it are painted in presplash_color. Those two edges are the only
    # ones that matter, and a single colour can only sit between them.
    top = image.crop((0, 0, width, 8)).resize((1, 1), Image.BOX).getpixel((0, 0))
    bottom = image.crop((0, height - 8, width, height)).resize((1, 1), Image.BOX).getpixel((0, 0))
    ideal = tuple(round((top[i] + bottom[i]) / 2) for i in range(3))

    ideal_hex = "".join(f"{channel:02x}" for channel in ideal)
    drift = max(abs(want[i] - ideal[i]) for i in range(3))
    require(drift <= 12,
            f"android.presplash_color #{declared} is {drift} off the midpoint "
            f"of the presplash's top edge {top} and bottom edge {bottom} "
            f"(ideal #{ideal_hex}). The launch would show a letterbox band.")
    return f"#{declared} vs measured ideal #{ideal_hex} (drift {drift})"


# --------------------------------------------------------------------------
# Dockerfile
# --------------------------------------------------------------------------
@check("Dockerfile is pinned, non-root and mount-based")
def t_dockerfile() -> str:
    text = read("packaging/android/Dockerfile")
    from_lines = [line for line in text.splitlines() if line.startswith("FROM ")]
    require(len(from_lines) == 1, f"expected exactly one FROM, got {len(from_lines)}")
    require("ubuntu:22.04" in from_lines[0] or "ubuntu:24.04" in from_lines[0],
            f"base image is not a pinned Ubuntu LTS: {from_lines[0]}")
    require(":latest" not in from_lines[0], "base image is unpinned")

    require(re.search(r"^USER \$\{?USERNAME", text, re.M),
            "the image still runs as root; gradle and SDK licences misbehave there")
    require("COPY . " not in text and "ADD . " not in text,
            "the project is COPYed into the image; it must be mounted instead")

    # The packages p4a actually needs, and the one that is not obvious.
    for package in ("openjdk-17-jdk-headless", "build-essential", "git", "zip",
                    "unzip", "autoconf", "libtool", "pkg-config", "zlib1g-dev",
                    "cmake", "libffi-dev", "libssl-dev", "ccache", "python3-pip",
                    "libtinfo5"):
        require(package in text, f"Dockerfile does not install {package}")

    require(re.search(r'buildozer==\$\{BUILDOZER_VERSION\}', text),
            "buildozer is not pinned")
    require(re.search(r'Cython==\$\{CYTHON_VERSION\}', text), "Cython is not pinned")
    require("buildozer init" in text and "/opt/buildozer-default.spec" in text,
            "the image does not capture buildozer's own default.spec, so "
            "`build.sh validate` has nothing authoritative to check against")

    # Regression guard, learned the expensive way: ANY buildozer subcommand run
    # as root stops to ask "Are you sure you want to continue [y/n]?", and a
    # docker RUN has no stdin, so it dies on EOFError several minutes into the
    # image build. Every buildozer invocation in the image must therefore be
    # dropped to an unprivileged user first.
    invocation = re.compile(
        r"\bbuildozer\s+(init|android|ios|osx|serve|setdefault|distclean|appclean)\b")
    for line in text.splitlines():
        if line.lstrip().startswith("#") or not invocation.search(line):
            continue
        require("su " in line or "runuser" in line or "setpriv" in line,
                "Dockerfile runs buildozer as root, which blocks on an "
                f"interactive prompt with no stdin: {line.strip()!r}")

    # And the output of that step must not be swallowed -- hiding it is what
    # made the above take a full image build to diagnose.
    specgen = [ln for ln in text.splitlines() if "buildozer init" in ln]
    require(specgen and not any(">/dev/null" in ln for ln in specgen),
            "the default.spec capture silences its own errors")
    require("android-licenses" in text, "SDK licences are not pre-accepted")

    # Layer order: apt must come before pip, which must come before the user,
    # or every toolchain bump invalidates the slowest layer.
    apt_at = text.index("apt-get install")
    pip_at = text.index("buildozer==")
    user_at = text.index("useradd")
    require(apt_at < pip_at < user_at,
            "layer order is not cache friendly (apt -> pip -> user)")
    return "ubuntu LTS pinned, non-root, {} layers ordered".format(text.count("\nRUN "))


@check("Dockerfile is syntactically well formed")
def t_dockerfile_grammar() -> str:
    # There is no offline `docker build --check`, and the daemon may well be
    # down on the machine running this. A grammar pass is still worth having:
    # a mistyped instruction fails the build at second 1 of a 10 minute image
    # build, which is exactly when the developer has walked away.
    instructions = {
        "FROM", "RUN", "CMD", "LABEL", "EXPOSE", "ENV", "ADD", "COPY",
        "ENTRYPOINT", "VOLUME", "USER", "WORKDIR", "ARG", "ONBUILD",
        "STOPSIGNAL", "HEALTHCHECK", "SHELL", "MAINTAINER",
    }
    lines = read("packaging/android/Dockerfile").splitlines()
    seen: list[str] = []
    continuing = False
    saw_from = False
    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if continuing:
            continuing = stripped.endswith("\\")
            continue
        if not stripped or stripped.startswith("#"):
            continue
        word = stripped.split(None, 1)[0].upper()
        require(word in instructions,
                f"Dockerfile line {number}: {stripped.split(None, 1)[0]!r} "
                f"is not a Docker instruction")
        require(saw_from or word in ("FROM", "ARG"),
                f"Dockerfile line {number}: {word} appears before any FROM")
        if word == "FROM":
            saw_from = True
        seen.append(word)
        continuing = stripped.endswith("\\")
    require(not continuing, "Dockerfile ends on a dangling line continuation")
    require(saw_from, "Dockerfile has no FROM")
    require("ENTRYPOINT" in seen, "Dockerfile has no ENTRYPOINT")
    return f"{len(seen)} instructions, {len(set(seen))} distinct"


@check("build.sh validate really runs in the container")
def t_container_validate() -> str:
    # The end-to-end check, and the only one that exercises the real buildozer.
    # It is what caught two bugs that no amount of reading would have: a
    # `--platform` flag on `docker run` that made Docker try to PULL the local
    # image, and adaptive-icon keys spelled the way you would expect rather
    # than the way buildozer spells them.
    #
    # Skipped, loudly, when Docker is not available -- it legitimately is not,
    # on CI, on a laptop with the daemon stopped, and inside `build.sh shell`.
    #
    # shutil.which first: subprocess.run raises FileNotFoundError when the
    # binary is absent, and the runner turns any stray exception into a hard
    # FAIL. Probing the daemon only covers "installed but stopped"; running
    # this from inside the build container, where there is no docker CLI at
    # all, used to produce a traceback-shaped failure instead of this skip.
    if shutil.which("docker") is None:
        raise Skip("the docker CLI is not installed (or this is running inside "
                   "the build container), so the container path was not "
                   "exercised")
    probe = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    if probe.returncode != 0:
        raise Skip("the Docker daemon is not running, so the container path "
                   "was not exercised (start Docker and re-run)")
    image = os.environ.get("FEATHERDASH_IMAGE", "featherdash-android:latest")
    present = subprocess.run(["docker", "image", "inspect", image],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if present.returncode != 0:
        raise Skip(f"{image} has not been built yet "
                   f"(packaging/android/build.sh image)")

    result = run(["bash", os.path.join(HERE, "build.sh"), "validate"])
    require(result.returncode == 0,
            "`build.sh validate` failed against the real buildozer:\n"
            + result.stdout)
    require("ok (" in result.stdout,
            f"validate produced no verdict:\n{result.stdout}")

    # The verdict is worthless unless the AUTHORITATIVE key list was the one
    # used. `--default-spec` missing inside the image used to degrade to the
    # bundled fallback, warn, and still print "ok" -- so assert that the
    # degradation did not happen rather than that a cheerful string appeared.
    require("falling back to the bundled key list" not in result.stdout,
            "validate degraded to its bundled key list instead of buildozer's "
            f"own default.spec:\n{result.stdout}")
    unexpected = [line for line in result.stdout.splitlines()
                  if line.startswith("warn:") and "does not exist yet" not in line]
    require(not unexpected,
            "the container validate raised warnings that are not the tolerated "
            "'branding art not generated yet' ones:\n" + "\n".join(unexpected))
    return "every key confirmed against buildozer's own default.spec"


@check("docker-entrypoint.sh does the work the image cannot")
def t_entrypoint() -> str:
    text = read("packaging/android/docker-entrypoint.sh")
    require("licenses" in text,
            "entrypoint does not seed the SDK licences into the mounted volume")
    require('exec "$@"' in text, "entrypoint does not exec the command")
    require("P4A_RELEASE_KEYSTORE" in text,
            "entrypoint does not check that a declared keystore is readable")
    return "licence seeding + cache ownership + keystore sanity"


# --------------------------------------------------------------------------
# workflows
# --------------------------------------------------------------------------
@check("workflow YAML parses")
def t_yaml_parses() -> str:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise Skip("PyYAML is not installed, so the workflows were not parsed "
                   "(`pip install -r requirements-dev.txt` fixes this)") from exc
    loaded = {}
    for path in WORKFLOWS:
        data = yaml.safe_load(read(path))
        require(isinstance(data, dict), f"{path} is not a mapping")
        require("jobs" in data, f"{path} has no jobs")
        # YAML 1.1 turns a bare `on:` into the boolean True. Either key is
        # valid for GitHub; what matters is that the trigger block exists.
        require("on" in data or True in data, f"{path} has no trigger block")
        loaded[path] = data

    ci = loaded[".github/workflows/ci.yml"]
    require(set(ci["jobs"]) >= {"test", "lint"},
            "ci.yml should have a test job and a lint job")
    env = ci.get("env", {})
    require(env.get("SDL_VIDEODRIVER") == "dummy", "ci.yml does not force a dummy video driver")
    require(env.get("SDL_AUDIODRIVER") == "dummy", "ci.yml does not force a dummy audio driver")

    android = loaded[".github/workflows/android.yml"]
    require(len(android["jobs"]) >= 1, "android.yml has no jobs")
    text = read(".github/workflows/android.yml")
    for secret in ("ANDROID_KEYSTORE_BASE64", "ANDROID_KEYSTORE_PASSWORD",
                   "ANDROID_KEY_ALIAS", "ANDROID_KEY_PASSWORD"):
        require("secrets." + secret in text,
                f"android.yml never reads {secret}")
        require(secret in read("packaging/android/README.md"),
                f"the README does not document the {secret} secret")
    require("ubuntu-latest" in text, "android.yml is not on ubuntu-latest")
    require("actions/cache" in text, "android.yml does not cache the buildozer directories")
    return "{} workflows, {} + {} jobs".format(
        len(loaded), len(ci["jobs"]), len(android["jobs"]))


@check("android.yml builds an AAB on tags and an APK on PRs")
def t_android_workflow_shape() -> str:
    text = read(".github/workflows/android.yml")
    require(re.search(r"tags:\s*\n\s*-\s*[\"']?v\*", text),
            "android.yml is not triggered by v* tags")
    require("pull_request" in text, "android.yml does not run on pull requests")
    require("buildozer -v android release" in text, "android.yml never builds a release")
    require("buildozer -v android debug" in text, "android.yml never builds a debug APK")
    require("gh release" in text, "android.yml does not attach the bundle to a Release")
    require("bin/*.aab" in text, "android.yml does not upload the .aab")
    require("Android DEBUG key" in text or "Android Debug" in text,
            "android.yml does not verify the bundle is not debug-signed")
    require(re.search(r"rm -rf [\"']?\$\{RUNNER_TEMP\}/signing", text),
            "android.yml does not delete the decoded keystore")
    return "tag->aab+release, pr->apk, keystore shredded"


@check("ci.yml tolerates an empty tests/ directory")
def t_ci_empty_tests() -> str:
    text = read(".github/workflows/ci.yml")
    # tests/ is empty right now. A workflow that goes red for "no tests yet"
    # teaches people to ignore red CI.
    require("-eq 5" in text or "exit 0" in text,
            "ci.yml does not handle pytest's exit code 5 (nothing collected)")
    require("requirements-dev.txt" in text,
            "ci.yml installs the runtime set, so the art generators' imports "
            "of numpy and Pillow would fail the lint pass")
    return "pytest exit 5 handled; dev requirements installed"


# --------------------------------------------------------------------------
# Makefile
# --------------------------------------------------------------------------
@check("make help is self-documenting and every target exists")
def t_make_help() -> str:
    result = run(["make", "help"])
    require(result.returncode == 0, f"make help failed:\n{result.stdout}")

    text = read("Makefile")
    documented = set(re.findall(r"^([a-zA-Z_-]+):.*?## ", text, re.M))
    for required in ("run", "assets", "test", "android-debug", "android-release",
                     "clean", "lint", "help"):
        require(required in documented,
                f"Makefile has no documented `{required}` target")
        require(required in result.stdout,
                f"`make help` does not list {required!r}")

    # .PHONY must cover them, or a file of the same name silently wins.
    phony = set(re.findall(r"[ \t\\\n]([a-zA-Z_-]+)", text.split(".PHONY:")[1].split("\n\n")[0]))
    undeclared = sorted(t for t in documented if t not in phony)
    require(not undeclared, f"not declared .PHONY: {undeclared}")
    return f"{len(documented)} documented targets, all phony"


@check("make targets resolve without running anything")
def t_make_dry_run() -> str:
    checked = []
    for target in ("run", "assets", "art", "audio", "test", "lint", "selftest",
                   "android-debug", "android-release", "android-install",
                   "android-logcat", "android-shell", "android-validate",
                   "android-clean", "keystore", "clean", "version", "dev"):
        result = run(["make", "-n", target])
        require(result.returncode == 0,
                f"make -n {target} failed:\n{result.stdout}")
        checked.append(target)
    # `make run` must use the venv python, not the system one: the system
    # python3 on this machine has no pygame.
    result = run(["make", "-n", "run"])
    require("/.venv/bin/python" in result.stdout or "python3" in result.stdout,
            f"make run does not name an interpreter: {result.stdout!r}")
    return f"{len(checked)} targets expand cleanly"


@check("make version agrees with featherdash/__init__.py")
def t_make_version() -> str:
    result = run(["make", "version"])
    require(result.returncode == 0, f"make version failed:\n{result.stdout}")
    printed = result.stdout.strip().splitlines()[-1].strip()
    match = re.search(r'__version__ = ["\']([^"\']+)["\']', read("featherdash/__init__.py"))
    require(match is not None, "no __version__ in featherdash/__init__.py")
    require(printed == match.group(1),
            f"make version printed {printed!r}, __init__.py says {match.group(1)!r}")
    return printed


# --------------------------------------------------------------------------
# docs
# --------------------------------------------------------------------------
@check("the operator manual covers the failures people actually hit")
def t_readme_troubleshooting() -> str:
    text = read("packaging/android/README.md")
    required_sections = {
        "prerequisites": "Prerequisites",
        "first build": "First build",
        "release build": "Release build",
        "signing": "Signing",
        "what to upload": "What to upload to Play",
        "build times": "45 minutes",
        "device testing": "Testing on a device",
        "troubleshooting": "Troubleshooting",
    }
    for label, needle in required_sections.items():
        require(needle in text, f"README is missing the {label} section ({needle!r})")

    troubleshooting = text.split("Troubleshooting", 1)[1]
    topics = {
        "SDL2 vs pygame recipe": "SDL2 vs pygame",
        "Aidl not found": "Aidl is not found",
        "NDK mismatch": "NDK version mismatch",
        "gradle OOM": "out of memory",
        "64-bit rejection": "64-bit requirement",
        "No module named android": "No module named android",
        "missing assets": "Assets missing from the APK",
        "black screen": "Black screen on launch",
    }
    missing = [label for label, needle in topics.items() if needle not in troubleshooting]
    require(not missing, f"README troubleshooting is missing: {missing}")
    return f"{len(required_sections)} sections, {len(topics)} troubleshooting topics"


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------
def main() -> int:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    print("Feather Dash -- packaging self-test")
    print(f"root: {ROOT}")
    print(f"python: {sys.version.split()[0]}")
    print("")

    failures = 0
    skips = 0
    for func in _CHECKS:
        name = func.__check_name__
        try:
            detail = func()
        except Skip as exc:
            skips += 1
            print(f"SKIP  {name}\n        {exc}")
            continue
        except Failure as exc:
            failures += 1
            print(f"FAIL  {name}\n        {exc}")
            continue
        except Exception as exc:  # a broken check is a failed check
            failures += 1
            print(f"ERROR {name}\n        {type(exc).__name__}: {exc}")
            continue
        suffix = f"\n        {detail}" if detail else ""
        print(f"ok    {name}{suffix}")

    print("")
    print(f"{len(_CHECKS)} checks, {failures} failed, {skips} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
