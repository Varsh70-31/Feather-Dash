#!/usr/bin/env bash
#
# Container entrypoint for the Feather Dash Android build image.
#
# Everything here is work that cannot be baked into the image because it has to
# land inside the mounted cache volumes, which shadow whatever the image put at
# those paths. Keep it fast: it runs before every single build.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# seed the Android SDK licence acceptances
# ---------------------------------------------------------------------------
# ~/.buildozer is a named volume, so the licence files baked into the image at
# /opt/android-licenses are invisible from inside the SDK tree until copied.
# Without them a cold build stalls on an interactive prompt no one can answer,
# or burns several minutes re-running sdkmanager --licenses.
seed_licenses() {
    local seed="${ANDROID_LICENSE_SEED:-/opt/android-licenses}"
    local sdk="${ANDROID_SDK_ROOT:-}"
    [ -d "$seed" ] || return 0
    [ -n "$sdk" ] || return 0

    # Critically: do NOT create $sdk. buildozer decides whether to download the
    # SDK with a bare `if file_exists(sdk_dir): return` -- so creating the
    # directory just to drop licences in it convinces buildozer the SDK is
    # already installed, and the build dies later with
    #   "sdkmanager path ... does not exist, sdkmanager is not installed".
    # On a cold build there is nothing to seed and nothing to break: the spec
    # sets android.accept_sdk_license, so buildozer accepts them itself. From
    # the second build onward the directory exists and this is a pure speed-up.
    [ -d "$sdk" ] || return 0
    mkdir -p "$sdk/licenses" 2>/dev/null || return 0
    local f name
    for f in "$seed"/*; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        # Never clobber a licence the SDK manager itself wrote: its hash list
        # is authoritative for the SDK version actually installed.
        if [ ! -f "$sdk/licenses/$name" ]; then
            cp "$f" "$sdk/licenses/$name" 2>/dev/null || true
        fi
    done
}

# ---------------------------------------------------------------------------
# make the cache volumes writable
# ---------------------------------------------------------------------------
# A freshly created named volume is owned by root. Fix it once, quietly, rather
# than letting buildozer fail 40 seconds in with a bare EACCES.
fix_perms() {
    local d
    for d in "$HOME/.buildozer" "$HOME/.gradle" "$HOME/.ccache" /workspace/.buildozer /workspace/bin; do
        [ -d "$d" ] || continue
        if [ ! -w "$d" ]; then
            sudo chown -R "$(id -u):$(id -g)" "$d" 2>/dev/null || true
        fi
    done
}

fix_perms
seed_licenses

# Reject the one mistake that silently produces an unsigned release: buildozer
# reads the keystore passphrase from P4A_RELEASE_KEYSTORE_PASSWD, and if the
# keystore path is set but unreadable it falls back to a debug signature and
# says so only deep in the log.
if [ -n "${P4A_RELEASE_KEYSTORE:-}" ] && [ ! -r "${P4A_RELEASE_KEYSTORE}" ]; then
    echo "entrypoint: P4A_RELEASE_KEYSTORE points at '${P4A_RELEASE_KEYSTORE}'," >&2
    echo "            which is not readable inside the container." >&2
    echo "            The mount is probably missing or the path is a host path." >&2
    exit 78  # EX_CONFIG
fi

exec "$@"
