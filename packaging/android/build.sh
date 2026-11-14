#!/usr/bin/env bash
#
# Feather Dash -- the one command you run to get an Android build.
#
# buildozer cannot run on macOS, so every build happens inside the container
# defined by packaging/android/Dockerfile. This script hides that: it finds the
# project, checks Docker is actually up, mounts the caches that turn a 45
# minute cold build into a 3 minute warm one, and tells you where the artifact
# landed.
#
# Device commands (install, logcat) deliberately run on the HOST, because a
# container on macOS cannot see a USB device.
#
# Written for bash 3.2 -- the version Apple ships -- so: no associative arrays,
# no `${var,,}`, no mapfile.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# locate the project, from wherever this was invoked
# ---------------------------------------------------------------------------
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=$(cd -- "${SCRIPT_DIR}/../.." && pwd -P)

IMAGE_NAME="${FEATHERDASH_IMAGE:-featherdash-android:latest}"
CONTAINER_WORKDIR="/workspace"

# Named volumes. These are the whole performance story: ~/.buildozer holds the
# Android SDK, the NDK and the cross-compiled CPython (several GB, ~40 min to
# recreate), and /workspace/.buildozer is mounted as a volume rather than left
# on the bind mount because bind-mount I/O on macOS is several times slower
# than a native volume and the build writes tens of thousands of small files.
VOL_BUILDOZER="${FEATHERDASH_VOL_PREFIX:-featherdash}-buildozer"
VOL_GRADLE="${FEATHERDASH_VOL_PREFIX:-featherdash}-gradle"
VOL_PROJECT_BUILD="${FEATHERDASH_VOL_PREFIX:-featherdash}-projbuild"
VOL_CCACHE="${FEATHERDASH_VOL_PREFIX:-featherdash}-ccache"

BIN_DIR="${PROJECT_ROOT}/bin"
BRANDING_DIR="${PROJECT_ROOT}/assets/branding"

# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_BOLD=$'\033[1m'; C_RED=$'\033[31m'
    C_YEL=$'\033[33m'; C_GRN=$'\033[32m'; C_OFF=$'\033[0m'
else
    C_BOLD=''; C_RED=''; C_YEL=''; C_GRN=''; C_OFF=''
fi

say()  { printf '%s==>%s %s\n' "${C_BOLD}" "${C_OFF}" "$*"; }
ok()   { printf '%s  ok%s %s\n' "${C_GRN}" "${C_OFF}" "$*"; }
warn() { printf '%swarn%s %s\n' "${C_YEL}" "${C_OFF}" "$*" >&2; }
die()  { printf '%sfail%s %s\n' "${C_RED}" "${C_OFF}" "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Feather Dash -- Android build

USAGE
  packaging/android/build.sh <command> [options]

COMMANDS
  debug           Build a debuggable APK and leave it in ./bin
  release         Build a signed App Bundle (.aab) for Google Play
  install         adb install the most recent debug APK onto a connected device
  logcat          Tail the game's log from a connected device
  shell           Open an interactive shell inside the build container
  validate        Check buildozer.spec's keys against the real buildozer
  image           (Re)build the Docker image only
  clean           Delete build output and the per-project build volume
  clean --all     ...and the SDK/NDK/gradle caches too (next build: ~45 min)
  help            This text

RELEASE SIGNING
  `release` needs an upload keystore. Create one with:

      packaging/android/make_keystore.sh

  then export, ideally from a password manager and never from shell history:

      export FEATHERDASH_KEYSTORE=/absolute/path/to/upload.keystore
      export FEATHERDASH_KEYSTORE_PASSWORD='...'
      export FEATHERDASH_KEY_ALIAS=upload
      export FEATHERDASH_KEY_PASSWORD='...'

  The keystore is bind-mounted read-only into the container for the duration of
  the build and the passwords are passed through the environment, so neither is
  ever baked into the image, written to a file, or echoed by this script.

OPTIONS
  -v, --verbose   Stream buildozer at log_level 2 (default)
  -q, --quiet     Quieter buildozer
  --no-cache      Rebuild the Docker image from scratch
  -h, --help      This text

EXAMPLES
  packaging/android/build.sh debug && packaging/android/build.sh install
  packaging/android/build.sh release
  packaging/android/build.sh logcat
EOF
}

# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------
require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        die "Docker is not installed.
     macOS:  brew install --cask docker     (or get Docker Desktop / OrbStack)
     Linux:  your distribution's docker.io / docker-ce package

     There is no way around this: buildozer and python-for-android do not run
     on macOS, so the Android build happens inside a Linux container."
    fi
    if ! docker info >/dev/null 2>&1; then
        die "Docker is installed but the daemon is not responding.

     Start Docker Desktop (or OrbStack, or \`sudo systemctl start docker\`),
     wait for the whale/status icon to settle, and run this again.

     If it is already running, the socket may be wrong -- check:
         docker context ls"
    fi
}

check_branding() {
    # Art is generated by tools/gen_brand.py and may not exist yet. buildozer's
    # own error for a missing icon is a bare traceback, so pre-empt it.
    local missing=""
    local f
    for f in icon_1024.png presplash.png adaptive_fg.png adaptive_bg.png icon_monochrome.png; do
        [ -f "${BRANDING_DIR}/${f}" ] || missing="${missing} ${f}"
    done
    if [ -n "${missing}" ]; then
        warn "branding art is missing from assets/branding/:${missing}"
        warn "generate it with:  make assets        (needs Pillow + numpy)"
        warn "buildozer WILL fail on icon.filename / presplash.filename without it."
        # Not fatal: a developer may be iterating on code with placeholder art,
        # and failing here would be more annoying than letting buildozer speak.
    fi
}

# The Android toolchain is x86_64-only, so the container must be amd64 even on an
# Apple Silicon host (Docker Desktop runs it under Rosetta). Override only if you
# know a native image works for you.
: "${FEATHERDASH_PLATFORM:=linux/amd64}"

ensure_image() {
    local no_cache="${1:-}"
    if [ -n "${no_cache}" ] || ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
        say "building image ${IMAGE_NAME} (first time: 5-10 minutes)"
        local args=""
        [ -n "${no_cache}" ] && args="--no-cache"
        # Pass the host uid/gid so artifacts in ./bin are owned by the human.
        # shellcheck disable=SC2086
        docker build ${args} \
            --platform "${FEATHERDASH_PLATFORM}" \
            --build-arg "USER_ID=$(id -u)" \
            --build-arg "GROUP_ID=$(id -g)" \
            -t "${IMAGE_NAME}" \
            "${SCRIPT_DIR}"
        ok "image ready"
    fi
}

# Common `docker run` arguments. Printed nowhere, so no secret can leak via a
# command echo; the password variables are passed by NAME (-e VAR), which makes
# docker read them from this process's environment instead of placing them on
# the container's command line where `ps` would show them.
docker_run() {
    local interactive="$1"; shift

    # Indexed arrays (bash 3.2 has them; only ASSOCIATIVE arrays need 4.0), so
    # a path containing a space cannot silently split into two arguments.
    local args
    # NO --platform here, deliberately -- it belongs on `docker build` only.
    # Verified against Docker 29 with the containerd image store: passing
    # --platform to `docker run` for a locally built single-platform image
    # makes the daemon treat the local image as a miss and try to PULL it,
    # which fails with "pull access denied ... repository does not exist".
    # The image is already linux/amd64, so there is nothing for docker to
    # choose; it emulates and prints a one-line platform-mismatch warning.
    args=(run --rm)

    if [ "${interactive}" = "tty" ] && [ -t 0 ] && [ -t 1 ]; then
        args+=(-it)
    fi

    args+=(-v "${PROJECT_ROOT}:${CONTAINER_WORKDIR}")
    args+=(-v "${VOL_PROJECT_BUILD}:${CONTAINER_WORKDIR}/.buildozer")
    args+=(-v "${VOL_BUILDOZER}:/home/builder/.buildozer")
    args+=(-v "${VOL_GRADLE}:/home/builder/.gradle")
    args+=(-v "${VOL_CCACHE}:/home/builder/.ccache")

    if [ -n "${FEATHERDASH_KEYSTORE:-}" ]; then
        args+=(-v "${FEATHERDASH_KEYSTORE}:/run/keystore/upload.keystore:ro")
    fi

    # Passed by NAME, never by value: docker then reads each one out of this
    # process's environment, so no passphrase ever appears on the container's
    # command line where `ps` on the host would show it.
    if [ -n "${P4A_RELEASE_KEYSTORE_PASSWD:-}" ]; then
        args+=(-e P4A_RELEASE_KEYSTORE)
        args+=(-e P4A_RELEASE_KEYSTORE_PASSWD)
        args+=(-e P4A_RELEASE_KEYALIAS)
        args+=(-e P4A_RELEASE_KEYALIAS_PASSWD)
    fi

    args+=(-w "${CONTAINER_WORKDIR}")
    args+=("${IMAGE_NAME}")

    mkdir -p "${BIN_DIR}"
    docker "${args[@]}" "$@"
}

report_artifacts() {
    local kind="$1"
    local found=""
    if [ -d "${BIN_DIR}" ]; then
        # Portable to bash 3.2 and to a BSD find: no -printf, no mapfile.
        found=$(find "${BIN_DIR}" -maxdepth 1 -name "*.${kind}" -type f 2>/dev/null | sort)
    fi
    if [ -z "${found}" ]; then
        warn "build reported success but no .${kind} was found in ${BIN_DIR}"
        return 1
    fi
    printf '\n'
    say "artifact:"
    local f
    printf '%s\n' "${found}" | while IFS= read -r f; do
        [ -n "$f" ] || continue
        printf '  %s%s%s  (%s)\n' "${C_BOLD}" "$f" "${C_OFF}" "$(du -h "$f" | cut -f1)"
    done
    printf '\n'
}

# ---------------------------------------------------------------------------
# release signing
# ---------------------------------------------------------------------------
# buildozer reads exactly these four variables and hands them to gradle's
# signing config. Translate the project's friendlier names into them here so
# the developer never has to remember p4a's spelling.
setup_signing() {
    local missing=""
    [ -n "${FEATHERDASH_KEYSTORE:-}" ]          || missing="${missing} FEATHERDASH_KEYSTORE"
    [ -n "${FEATHERDASH_KEYSTORE_PASSWORD:-}" ] || missing="${missing} FEATHERDASH_KEYSTORE_PASSWORD"
    [ -n "${FEATHERDASH_KEY_ALIAS:-}" ]         || missing="${missing} FEATHERDASH_KEY_ALIAS"
    [ -n "${FEATHERDASH_KEY_PASSWORD:-}" ]      || missing="${missing} FEATHERDASH_KEY_PASSWORD"

    if [ -n "${missing}" ]; then
        die "release builds must be signed, and these are not set:${missing}

     Run  packaging/android/make_keystore.sh  if you have no keystore yet,
     then export the four variables (see \`build.sh help\`).

     Refusing to continue: an unsigned or debug-signed bundle is rejected by
     Play, and worse, a bundle signed with a throwaway debug key can never be
     updated."
    fi

    if [ ! -f "${FEATHERDASH_KEYSTORE}" ]; then
        die "keystore not found: ${FEATHERDASH_KEYSTORE}"
    fi

    # Path as seen from INSIDE the container (see keystore_mount above).
    export P4A_RELEASE_KEYSTORE="/run/keystore/upload.keystore"
    export P4A_RELEASE_KEYSTORE_PASSWD="${FEATHERDASH_KEYSTORE_PASSWORD}"
    export P4A_RELEASE_KEYALIAS="${FEATHERDASH_KEY_ALIAS}"
    export P4A_RELEASE_KEYALIAS_PASSWD="${FEATHERDASH_KEY_PASSWORD}"
}

# ---------------------------------------------------------------------------
# host-side device commands
# ---------------------------------------------------------------------------
require_adb() {
    if ! command -v adb >/dev/null 2>&1; then
        die "adb is not on your PATH.

     macOS:  brew install --cask android-platform-tools
     Linux:  your distribution's android-tools-adb package

     adb has to run on the host: a Docker container on macOS cannot see a USB
     device, so there is no in-container alternative."
    fi
}

require_device() {
    require_adb
    # `adb devices` lists a header line plus one line per device; a device that
    # has not had USB debugging authorised shows as "unauthorized".
    local list
    list=$(adb devices | tail -n +2 | grep -v '^[[:space:]]*$' || true)
    if [ -z "${list}" ]; then
        die "no device detected.

     Plug the phone in, enable Developer options (tap Build number 7 times),
     turn on USB debugging, and accept the 'Allow USB debugging?' prompt.
     Then:  adb devices"
    fi
    if printf '%s' "${list}" | grep -q 'unauthorized'; then
        die "device is connected but not authorised -- unlock the phone and
     accept the 'Allow USB debugging?' dialog, then try again."
    fi
}

# The most recently BUILT apk in ./bin -- by modification time, never by name.
#
# `sort | tail -1` was wrong in a way that costs you a whole debugging session:
# version strings are not lexicographic, so with 2.9.0 and 2.10.0 side by side
# it picks 2.9.0. `cmd_install` then runs `adb install -r -d`, and -d exists
# precisely to permit a downgrade, so the stale binary installs with no error
# and you test the wrong build.
#
# `ls -t` behaves identically on macOS and Linux. It is spliced in with
# find's `-exec ... +` rather than piped into `head`, because this script runs
# under `set -o pipefail` inside a `$(...)`: `head` closing the pipe early
# would SIGPIPE `ls` and abort the script.
newest_apk() {
    local listing
    listing=$(find "${BIN_DIR}" -maxdepth 1 -name '*.apk' -type f \
                   -exec ls -t {} + 2>/dev/null) || return 0
    [ -n "${listing}" ] || return 0
    printf '%s\n' "${listing%%$'\n'*}"
}

# Everything newest_apk did NOT pick, newest first. Printed before an install
# so that "I am sure I just built that" has an answer on screen.
other_apks() {
    local listing
    listing=$(find "${BIN_DIR}" -maxdepth 1 -name '*.apk' -type f \
                   -exec ls -t {} + 2>/dev/null) || return 0
    printf '%s\n' "${listing}" | tail -n +2
}

# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
cmd_debug() {
    require_docker
    check_branding
    ensure_image "${NO_CACHE}"
    say "building debug APK (warm cache: a few minutes; cold: 30-60)"
    docker_run notty buildozer "${LOG_FLAG}" android debug
    report_artifacts apk
    say "install it with:  packaging/android/build.sh install"
}

cmd_release() {
    require_docker
    check_branding
    setup_signing
    ensure_image "${NO_CACHE}"
    say "building signed App Bundle for Play"
    docker_run notty buildozer "${LOG_FLAG}" android release
    report_artifacts aab
    cat <<EOF
Next:
  1. https://play.google.com/console  ->  your app  ->  Production (or Internal
     testing first -- always test the real bundle before promoting it).
  2. Upload the .aab above.
  3. If this is the first upload, enrol in Play App Signing when prompted. Do
     it. It is what makes a lost upload key recoverable.
EOF
}

cmd_validate() {
    require_docker
    ensure_image "${NO_CACHE}"
    say "checking buildozer.spec against buildozer's own key list"
    # --default-spec is the whole point of running this in the container: it is
    # the key list the PINNED buildozer generated, and the only thing that can
    # settle the adaptive-icon spellings. If the file is not there (a stale
    # image, FEATHERDASH_IMAGE pointing elsewhere, a renamed layer) the
    # validator now exits 1 rather than degrading to its bundled fallback and
    # printing "ok" -- a check that reports success without running is worse
    # than no check.
    #
    # Deliberately NOT --strict: the only warnings reachable on this path are
    # "branding art has not been generated yet", which is a legitimate state in
    # a fresh clone and says nothing about whether the spec is correct.
    docker_run notty python3 packaging/android/validate_spec.py \
        buildozer.spec --default-spec "/opt/buildozer-default.spec"
}

cmd_shell() {
    require_docker
    ensure_image "${NO_CACHE}"
    say "entering the build container (project is at ${CONTAINER_WORKDIR})"
    docker_run tty bash
}

cmd_image() {
    require_docker
    ensure_image "force"
}

cmd_install() {
    require_device
    local apk others
    apk=$(newest_apk)
    [ -n "${apk}" ] || die "no APK in ${BIN_DIR} -- run: packaging/android/build.sh debug"
    others=$(other_apks)
    if [ -n "${others}" ]; then
        # `adb install -d` will happily downgrade, so picking the wrong file
        # here is silent. Name the losers rather than let it be a mystery.
        warn "more than one APK in ${BIN_DIR}; installing the most recently built:"
        warn "  chosen:  $(basename "${apk}")"
        printf '%s\n' "${others}" | while IFS= read -r stale; do
            [ -n "${stale}" ] && warn "  older:   $(basename "${stale}")"
        done
    fi
    say "installing $(basename "${apk}")"
    # -r reinstall, -d allow a version downgrade (constant during development).
    adb install -r -d "${apk}"
    ok "installed"
}

cmd_logcat() {
    require_device
    say "tailing the game log -- ctrl-c to stop"
    adb logcat -c || true
    # Same filter set as android.logcat_filters in buildozer.spec.
    adb logcat '*:S' python:D PythonActivity:D SDL:D 'SDL/APP:V' AndroidRuntime:E
}

cmd_clean() {
    local all="${1:-}"
    # Paranoia about `rm -rf`: PROJECT_ROOT is derived from `cd && pwd -P` under
    # `set -e` so it cannot be empty, but an empty value here would mean
    # `rm -rf /bin`, and that is not a bug anyone should ever be able to hit.
    case "${PROJECT_ROOT}" in
        ""|"/") die "refusing to clean: PROJECT_ROOT resolved to '${PROJECT_ROOT}'" ;;
    esac
    say "removing build output"
    # ${VAR:?} so that an empty PROJECT_ROOT aborts rather than becoming
    # `rm -rf /bin` (shellcheck SC2115).
    rm -rf "${PROJECT_ROOT:?}/bin" "${PROJECT_ROOT:?}/.buildozer"
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        docker volume rm "${VOL_PROJECT_BUILD}" >/dev/null 2>&1 || true
        ok "dropped per-project build volume"
        if [ "${all}" = "--all" ]; then
            docker volume rm "${VOL_BUILDOZER}" "${VOL_GRADLE}" "${VOL_CCACHE}" >/dev/null 2>&1 || true
            warn "dropped the SDK/NDK/gradle caches -- the next build will take 30-60 minutes"
        fi
    else
        warn "Docker is not running; left the cache volumes alone"
    fi
    ok "clean"
}

# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
NO_CACHE=""
LOG_FLAG="-v"
COMMAND=""
EXTRA=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help|help)   usage; exit 0 ;;
        -v|--verbose)     LOG_FLAG="-v" ;;
        -q|--quiet)       LOG_FLAG="-q" ;;
        --no-cache)       NO_CACHE="force" ;;
        --all)            EXTRA="--all" ;;
        -*)               die "unknown option: $1  (try --help)" ;;
        *)
            if [ -z "${COMMAND}" ]; then
                COMMAND="$1"
            else
                die "unexpected argument: $1  (try --help)"
            fi
            ;;
    esac
    shift
done

[ -n "${COMMAND}" ] || { usage; exit 2; }

case "${COMMAND}" in
    debug)    cmd_debug ;;
    release)  cmd_release ;;
    validate) cmd_validate ;;
    shell)    cmd_shell ;;
    image)    cmd_image ;;
    install)  cmd_install ;;
    logcat)   cmd_logcat ;;
    clean)    cmd_clean "${EXTRA}" ;;
    *)        die "unknown command: ${COMMAND}  (try --help)" ;;
esac
