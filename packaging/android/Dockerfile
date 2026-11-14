# syntax=docker/dockerfile:1
#
# Feather Dash -- reproducible Android build image.
#
# buildozer / python-for-android do not run on macOS. This image is the only
# way the developer builds locally. It is also close enough to the GitHub
# Actions runner that a failure here reproduces there and vice versa.
#
# The project is MOUNTED at /workspace, never COPYed in. Copying would make
# every source edit invalidate the image and force a 30-60 minute cold rebuild
# of the SDK, NDK and CPython-for-ARM toolchain.
#
#   build : docker build -t featherdash-android packaging/android
#   use   : packaging/android/build.sh debug        (does the above for you)
#
# Layer order below is deliberate: slow-and-stable first (apt, then pip, then
# the user), so editing anything near the bottom rebuilds in seconds.

# Pinned to a specific LTS, not :latest. 22.04 rather than 24.04 on purpose:
# it ships Python 3.10, and python-for-android still reaches for `distutils`
# in a few recipes, which 3.12 removed outright. Pin by digest instead of tag
# if you need byte-for-byte reproducibility across months.
# --platform is NOT optional on Apple Silicon. Google ships the Android NDK and
# the SDK build-tools (aapt2, aidl, the clang toolchain) as Linux **x86_64**
# binaries only -- there is no aarch64 Linux NDK. A natively-arm64 container
# therefore installs a toolchain it cannot execute, and the build dies deep
# inside p4a with an exec-format error after ~40 wasted minutes. Pinning amd64
# makes Docker Desktop run the image under Rosetta, which is slower but works.
# On an Intel machine or a CI runner this line is a no-op.
FROM --platform=linux/amd64 ubuntu:22.04

LABEL org.opencontainers.image.title="Feather Dash Android build" \
      org.opencontainers.image.description="buildozer + python-for-android toolchain" \
      org.opencontainers.image.source="https://github.com/Varsh70-31/Flap.py"

# Pinned toolchain versions. Bump these deliberately, one at a time, and
# rebuild the image; never let them float.
ARG BUILDOZER_VERSION=1.5.0
ARG CYTHON_VERSION=0.29.36
ARG VIRTUALENV_VERSION=20.26.6

# Match the host user so files written into the bind mount are not root-owned.
# (Harmless on Docker Desktop for macOS, which maps ownership for you; load
# bearing on Linux, where root-owned bin/ output is a daily annoyance.)
ARG USER_ID=1000
ARG GROUP_ID=1000
ARG USERNAME=builder

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --------------------------------------------------------------------------
# OS packages
# --------------------------------------------------------------------------
# Grouped by why they are here, because "the internet said so" lists rot.
#
#   openjdk-17  -- Android Gradle Plugin 8.x refuses anything older, and
#                  refuses anything newer than 21. 17 is the safe middle.
#   build-essential/autoconf/automake/libtool/m4/pkg-config/cmake
#               -- p4a builds every native recipe from source, in-container.
#   zlib1g-dev/libffi-dev/libssl-dev/libbz2-dev/liblzma-dev/libsqlite3-dev
#               -- CPython-for-Android's own extension modules.
#   libtinfo5   -- the single most common cause of "Aidl is not found": older
#                  Android build-tools binaries are linked against ncurses 5,
#                  which 22.04 no longer installs by default. 24.04 has dropped
#                  this package entirely, which is a second reason to stay here.
#   libltdl-dev/gettext/autopoint -- needed by a handful of p4a recipes.
#   ccache      -- second build of the same recipe set is dramatically faster.
#   android-tools-adb -- for `buildozer android logcat` from inside the
#                  container over TCP; USB devices are NOT visible to a
#                  container on macOS, so build.sh runs device commands on the
#                  host instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        wget \
        git \
        openssh-client \
        unzip \
        zip \
        file \
        patch \
        sudo \
        less \
        openjdk-17-jdk-headless \
        build-essential \
        autoconf \
        automake \
        libtool \
        libtool-bin \
        libltdl-dev \
        m4 \
        gettext \
        autopoint \
        pkg-config \
        cmake \
        ninja-build \
        zlib1g-dev \
        libffi-dev \
        libssl-dev \
        libbz2-dev \
        liblzma-dev \
        libsqlite3-dev \
        libreadline-dev \
        libncurses-dev \
        libncursesw5-dev \
        libtinfo5 \
        ccache \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        python3-setuptools \
        python3-wheel \
        android-tools-adb \
    && rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------
# Python build tooling
# --------------------------------------------------------------------------
# Cython is pinned below 3.0: several python-for-android recipes still use
# pre-3.0 Cython semantics and fail to compile against 3.x. This is the single
# most common "it built last month" breakage, so it is pinned, not floated.
RUN python3 -m pip install --upgrade "pip<25" setuptools wheel \
    && python3 -m pip install \
        "buildozer==${BUILDOZER_VERSION}" \
        "Cython==${CYTHON_VERSION}" \
        "virtualenv==${VIRTUALENV_VERSION}" \
        sh

# Capture the pinned buildozer's OWN generated default.spec. This is the
# authoritative list of valid keys for this exact version, and
# packaging/android/validate_spec.py diffs ../../buildozer.spec against it --
# which is how a typo'd or renamed key (buildozer silently ignores both) gets
# caught at build time instead of in a store review.
#
# It runs as `nobody`, not as root. Any buildozer subcommand run as root stops
# to ask "Are you sure you want to continue [y/n]?", and in a docker RUN there
# is no stdin to answer with, so it dies on EOFError. `nobody` rather than the
# build user below because this layer must not depend on the USER_ID build
# arg -- that differs between a laptop (501) and CI (1000), and making this
# layer depend on it would throw away the expensive pip cache on every machine.
#
# Deliberately NOT silenced: hiding this command's output is how the root
# problem above stayed invisible in the first place.
#
# The two greps fail the image build rather than ship a default.spec that is
# empty or truncated. validate_spec's authoritative mode degrades silently to
# its bundled fallback list if this file is unusable, and a silent degradation
# in a correctness check is worse than no check at all.
RUN install -d -m 0777 /tmp/specgen \
    && su -s /bin/sh nobody -c "cd /tmp/specgen && HOME=/tmp/specgen buildozer init" \
    && cp /tmp/specgen/buildozer.spec /opt/buildozer-default.spec \
    && rm -rf /tmp/specgen \
    && chmod 0444 /opt/buildozer-default.spec \
    && grep -q '^\[app\]' /opt/buildozer-default.spec \
    && grep -q '^package.name' /opt/buildozer-default.spec \
    && echo "captured $(grep -c '=' /opt/buildozer-default.spec) option lines from buildozer ${BUILDOZER_VERSION}"

# --------------------------------------------------------------------------
# non-root build user
# --------------------------------------------------------------------------
# buildozer refuses to run as root without --warn-on-root overrides, gradle's
# daemon and cache behave badly under root, and `sdkmanager --licenses` writes
# acceptance files that then belong to root and are unreadable next run.
RUN if getent group "${GROUP_ID}" >/dev/null; then \
        groupmod -n "${USERNAME}" "$(getent group "${GROUP_ID}" | cut -d: -f1)"; \
    else \
        groupadd -g "${GROUP_ID}" "${USERNAME}"; \
    fi \
    && if getent passwd "${USER_ID}" >/dev/null; then \
        usermod -l "${USERNAME}" -d "/home/${USERNAME}" -m \
                "$(getent passwd "${USER_ID}" | cut -d: -f1)"; \
    else \
        useradd -m -u "${USER_ID}" -g "${GROUP_ID}" -s /bin/bash "${USERNAME}"; \
    fi \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${USERNAME}" \
    && chmod 0440 "/etc/sudoers.d/${USERNAME}" \
    && mkdir -p "/home/${USERNAME}/.buildozer" "/home/${USERNAME}/.gradle" "/home/${USERNAME}/.ccache" \
    && chown -R "${USER_ID}:${GROUP_ID}" "/home/${USERNAME}"

# --------------------------------------------------------------------------
# pre-accepted Android SDK licences
# --------------------------------------------------------------------------
# These live in the IMAGE, not in ~/.buildozer, because ~/.buildozer is a
# mounted volume at run time and anything baked into that path would be
# shadowed. The entrypoint copies them into the SDK on first run.
#
# The files are SHA1 hashes of the licence texts -- the exact same thing
# `sdkmanager --licenses` writes after you hold down "y". They contain no
# secrets. buildozer's android.accept_sdk_license=True covers the same ground;
# this just removes the download-and-prompt round trip from a cold build.
RUN mkdir -p /opt/android-licenses \
    && printf '\n24333f8a63b6825ea9c5514f83c2829b004d1fee\n8933bad161af4178b1185d1a37fbf41ea5269c55\nd56f5187479451eabf01fb78af6dfcb131a6481e\n' \
        > /opt/android-licenses/android-sdk-license \
    && printf '\n84831b9409646a918e30573bab4c9c91346d8abd\n504667f4c0de7af1a06de9f4b1727b84351f2910\n' \
        > /opt/android-licenses/android-sdk-preview-license \
    && printf '\n859f317696f67ef3d7f30a50a5560e7834b43903\n' \
        > /opt/android-licenses/android-sdk-arm-dbt-license \
    && printf '\nd975f751698a77b662f1254ddbeed3901e976f5a\n' \
        > /opt/android-licenses/intel-android-extra-license \
    && printf '\n33b6a2b64607f11b759f320ef9dff4ae5c47d97a\n' \
        > /opt/android-licenses/google-gdk-license \
    && printf '\ne9acab5b5fbb560a72cfaecce8946896ff6aab9d\n' \
        > /opt/android-licenses/mips-android-sysimage-license \
    && chmod -R a+r /opt/android-licenses

# JAVA_HOME cannot be written literally: the path is arch-suffixed
# (java-17-openjdk-arm64 on Apple Silicon, -amd64 on Intel or a CI runner).
# Resolve it once at image build time behind a stable symlink.
RUN ln -s "$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")" /opt/java17 \
    && test -x /opt/java17/bin/javac

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER ${USERNAME}
WORKDIR /workspace

ENV HOME=/home/${USERNAME} \
    JAVA_HOME=/opt/java17 \
    ANDROID_SDK_ROOT=/home/${USERNAME}/.buildozer/android/platform/android-sdk \
    ANDROID_HOME=/home/${USERNAME}/.buildozer/android/platform/android-sdk \
    USE_CCACHE=1 \
    CCACHE_DIR=/home/${USERNAME}/.ccache \
    PATH=/home/${USERNAME}/.local/bin:/usr/lib/ccache:/usr/local/bin:/usr/bin:/bin \
    BUILDOZER_DEFAULT_SPEC=/opt/buildozer-default.spec \
    ANDROID_LICENSE_SEED=/opt/android-licenses \
    GRADLE_OPTS="-Xmx4g -Dorg.gradle.daemon=false -Dorg.gradle.jvmargs=-Xmx4g -Dfile.encoding=UTF-8"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["bash"]
