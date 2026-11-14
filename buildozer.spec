#
# Feather Dash -- python-for-android / buildozer packaging spec.
#
# This file is the single description of the Android build. It is consumed by
# `buildozer` inside the Linux container defined in packaging/android/, and by
# .github/workflows/android.yml. Do not run buildozer on macOS: it is not
# supported there and will fail somewhere deep inside the NDK.
#
#   local build :  packaging/android/build.sh debug      (APK, for a device)
#                  packaging/android/build.sh release    (AAB, for Play)
#   key check   :  packaging/android/build.sh validate   (diffs the keys below
#                                                         against the pinned
#                                                         buildozer's own
#                                                         default.spec)
#
# Every option below is either (a) required, (b) a deliberate departure from a
# buildozer default, or (c) a default restated because getting it wrong is
# expensive. Nothing is here by accident.
#

[app]

# --------------------------------------------------------------------------
# identity
# --------------------------------------------------------------------------
# package.domain + package.name is the applicationId, and the applicationId is
# immutable once the app is live on Play. It MUST stay equal to
# featherdash/platform.py:APP_ID ("com.varshraj.featherdash") -- platform.py
# does not read it from here, so the two are kept in sync by the self-test in
# packaging/android/selftest.py, which fails the build if they drift.
title = Feather Dash
package.name = featherdash
package.domain = com.varshraj

# --------------------------------------------------------------------------
# what goes in the bundle
# --------------------------------------------------------------------------
source.dir = .

# Anything with one of these extensions, anywhere under source.dir that is not
# excluded below, is packed. md is here so LICENSE/attribution text can be
# rendered in-game; add otf here too if a future font is OpenType rather than
# TrueType.
source.include_exts = py,png,jpg,wav,ogg,ttf,json,md,txt

# NO source.include_patterns, deliberately. It reads like a safety net for
# assets/ and is the opposite of one.
#
# buildozer 1.5.0 (buildozer/__init__.py, _copy_application_sources) applies
# exclude_patterns first and then:
#
#     for pattern in include_patterns:
#         if fnmatch(dfn, pattern):
#             is_excluded = False
#
# so include_patterns can only ever UN-exclude. It cannot force a file in: the
# include_exts filter above runs afterwards and is not bypassed. And fnmatch's
# "*" crosses "/", so an innocent `assets/*` reaches
# assets/branding/icon_1024.png and cancels the exclusion below -- putting the
# launcher-icon source art and the 1024x500 Play *listing* feature graphic
# into every APK, where nothing can ever read them.
#
# Measured by replaying buildozer's own loop over this tree:
#   with    assets/*,assets/*/*,assets/*/*/*  ->  193 files, 13,531,282 bytes
#   without                                   ->  187 files, 11,275,897 bytes
# and all 152 runtime asset files are present either way -- include_exts
# already covers png/ttf/wav/json/txt, which is everything under assets/.
#
# That replay is a check rather than a comment: validate_spec.packed_files()
# reimplements the algorithm and check_bundle_contents() fails the build if
# anything under assets/branding/ is packed again.
#source.include_patterns =

# Everything that must NOT ship. Each of these is either large, secret, or
# would change the app's behaviour if it were importable on the device:
#   .venv        -- a macOS-native virtualenv; hundreds of MB and useless on ARM
#   tools        -- the art/audio generators; they import numpy and Pillow,
#                   which are deliberately NOT in requirements (see below)
#   tests        -- never shipped
#   packaging    -- this build system, including the Dockerfile and scripts
#   .github      -- CI config
#   store, docs  -- Play listing copy and screenshots, not app content
#   Images/Sounds/Fonts -- v1 asset trees, superseded by assets/
#   bin, .buildozer     -- build output; packing them would be recursive
#
# NOTE: "android" must never appear as a top-level importable directory here.
# featherdash/platform.py:_detect_android() probes for an importable `android`
# module, and although it rejects namespace packages, a real android/ package
# in the source root would make a desktop run believe it is a phone.
source.exclude_dirs = .venv,venv,tools,tests,packaging,.github,.git,store,docs,Images,Sounds,Fonts,bin,.buildozer,__pycache__,.idea,.vscode,.ruff_cache,.pytest_cache

# assets/branding/ is the launcher icon, the adaptive icon layers and the Play
# listing art. buildozer copies icon.filename / presplash.filename straight off
# the host filesystem into the generated Android resources -- they are NOT read
# out of the packed app directory -- so shipping them a second time inside the
# bundle would add 2.2 MB of dead weight, a third of it a Play *listing*
# feature graphic that no device can display. Excluded on purpose -- and the
# exclusion is verified by replay, not trusted, because a single
# source.include_patterns entry silently cancels it (see above).
source.exclude_patterns = assets/branding/*,*.pyc,*.pyo,*.spec,*.log,*.keystore,*.jks,*.DS_Store,Makefile,requirements-dev.txt

# --------------------------------------------------------------------------
# version -- one source of truth
# --------------------------------------------------------------------------
# `version` is deliberately NOT set. buildozer prefers a literal `version` over
# the regex mechanism, so defining both would silently freeze the version at
# whatever the literal said. The number lives in featherdash/__init__.py and
# nowhere else; bump it there and the APK/AAB follows.
#version = 2.0.0
version.filename = %(source.dir)s/featherdash/__init__.py
version.regex = __version__ = ['"](.*)['"]

# versionCode is derived by p4a from the version string (and offset per ABI so
# that arm64 and armeabi-v7a inside one bundle never collide). Only set
# android.numeric_version if Play ever rejects an upload for a non-increasing
# versionCode -- and then it must only ever go up.
#android.numeric_version = 2000000

# --------------------------------------------------------------------------
# runtime requirements
# --------------------------------------------------------------------------
# Verified by grepping every import under featherdash/ : the game imports only
# the standard library plus pygame, with two lazy, exception-guarded imports --
# `plyer.vibrator` (platform.vibrate) and `jnius.autoclass` (platform.keep_screen_on
# and display._query_safe_insets).
#
# numpy and Pillow are NOT listed and must never be. They are imported only by
# tools/*.py, which does not ship (see source.exclude_dirs). Each would add
# many megabytes and a fragile native p4a recipe to every build.
#
#   python3  CPython itself
#   pygame   the engine; needs the sdl2 bootstrap, set below
#   pyjnius  `from jnius import autoclass` -- screen-on flag and display cutouts
#   plyer    `from plyer import vibrator` -- haptics
#   android  the p4a runtime shim; the sdl2 bootstrap pulls it in anyway, but
#            platform._detect_android() probes for it, so it is named explicitly
# python-for-android's bundled recipes are not mutually consistent out of the
# box: its python3 recipe is on 3.14 while its pygame recipe is still pygame
# 2.1.0 (2021), whose C sources include longintrepr.h -- a header CPython
# removed after 3.12. Building the pair unpinned fails with
#   src_c/_sdl2/sdl2.c: fatal error: 'longintrepr.h' file not found
# roughly forty minutes into a cold build. Pinning CPython to 3.11 is the
# smaller, safer half of the fix; raise it only after confirming the pygame
# recipe has moved on too.
# hostpython3 must be pinned to the SAME version: p4a builds a host CPython to
# run the cross-compile and refuses to start if the two disagree
# ("python3 should have same version as hostpython3").
requirements = python3==3.11.9,hostpython3==3.11.9,pygame,pyjnius,plyer,android

# Overrides p4a's pygame 2.1.0, which cannot compile against any current
# CPython. See packaging/android/recipes/pygame/__init__.py for why.
p4a.local_recipes = ./packaging/android/recipes

# pygame's p4a recipe is built against SDL2 and only works on this bootstrap.
p4a.bootstrap = sdl2

# Reproducibility: `master` tracks upstream python-for-android and WILL
# eventually break a build you did not change. Pin this to a release tag
# (e.g. v2024.01.21) or a commit sha before a release you intend to support,
# and record which one in packaging/android/README.md.
p4a.branch = master

# --------------------------------------------------------------------------
# presentation
# --------------------------------------------------------------------------
orientation = portrait
fullscreen = 1

# Produced by tools/gen_brand.py into assets/branding/. These are read from the
# host at build time; they are not part of the packed app (see exclude_patterns).
# If the art has not been generated yet, buildozer fails with a plain "file not
# found" -- run `make assets` first. packaging/android/build.sh checks for them
# up front and says so in English rather than letting buildozer be cryptic.
icon.filename = %(source.dir)s/assets/branding/icon_1024.png
presplash.filename = %(source.dir)s/assets/branding/presplash.png

# Adaptive (API 26+) launcher icon layers.
#
# These names are NOT guessable and NOT what you would expect -- they are
# `adaptive_foreground`, not `adaptive_icon.foreground`. Confirmed against
# buildozer 1.5.0's own generated default.spec inside the build image, which is
# exactly what `packaging/android/build.sh validate` checks, because an
# unrecognised key here is silently IGNORED rather than rejected: a wrong
# spelling costs you the adaptive icon with no error anywhere. Re-run that
# validate step after any buildozer version bump.
icon.adaptive_foreground.filename = %(source.dir)s/assets/branding/adaptive_fg.png
icon.adaptive_background.filename = %(source.dir)s/assets/branding/adaptive_bg.png

# NO monochrome key: buildozer 1.5.0 has no option for the Android 13+ themed
# icon layer at all (verified against its default.spec -- only the foreground
# and background keys exist). assets/branding/icon_monochrome.png is generated
# and ready; wiring it up needs either a newer buildozer that adds the key, or
# a p4a hook that writes <monochrome> into the generated
# res/mipmap-anydpi-v26/icon.xml. Until then the launcher simply falls back to
# the adaptive pair under a themed-icon setting, which looks fine -- it just
# does not recolour. Do not "fix" this by inventing a key name; it would be
# accepted into the file and do nothing.

# The colour behind the presplash while the Python runtime boots (1-3 s on a
# cold start). p4a scales presplash.png FIT_CENTER and paints everything the
# image does not cover in this colour, so any mismatch is a hard seam on the
# first screen every user ever sees.
#
# A single constant cannot sit on two different edges, so the fix is in the
# art, not here. presplash.png is authored portrait (1080x2160, the app is
# orientation=portrait) so there is barely a letterbox to begin with, and
# gen_brand.build_presplash composites an edge_lock() ramp that drives all four
# borders to exactly one value: FIELD_EDGE, the darkest stop of the brand
# gradient. Declaring that same value is what makes the join disappear.
#
# MEASURED, not guessed -- all four edges sample #100c2c. Re-measure if the art
# changes (tests/../packaging/android/selftest.py checks this automatically):
#     python3 -c "from PIL import Image; im=Image.open('assets/branding/presplash.png').convert('RGB'); w,h=im.size; print(im.crop((0,0,w,8)).resize((1,1)).getpixel((0,0)), im.crop((0,h-8,w,h)).resize((1,1)).getpixel((0,0)))"
android.presplash_color = #100c2c

# A fullscreen theme from the first frame. Without it the activity briefly shows
# the system window background (white on most OEM skins) before SDL takes over.
# NOT ".Fullscreen" -- python-for-android appends that itself. Its manifest
# template reads
#     android:theme="{{args.android_apptheme}}{% if not args.window %}.Fullscreen{% endif %}"
# so with `fullscreen = 1` above, naming the Fullscreen variant here produces
#     android:style/Theme.NoTitleBar.Fullscreen.Fullscreen
# and the build dies in aapt at the very last step, after the full compile.
# DeviceDefault rather than the pre-Holo base theme: both are public in API 36,
# but DeviceDefault follows the device's own styling and is not on a
# deprecation path.
android.apptheme = @android:style/Theme.DeviceDefault.NoActionBar

# Relaunching from the launcher should resume the running game, not stack a
# second activity on top of it (which on p4a means a second Python interpreter).
android.manifest.launch_mode = singleTask

# --------------------------------------------------------------------------
# permissions
# --------------------------------------------------------------------------
# VIBRATE only. It is a normal permission, so there is no runtime prompt, and
# platform.vibrate() already degrades silently on a device without a vibrator.
# Nothing else is requested: no network, no storage, no ads identifier. That is
# worth keeping -- an empty permission list is a selling point on the listing
# and removes a whole class of Play policy declarations.
android.permissions = android.permission.VIBRATE

# --------------------------------------------------------------------------
# API levels
# --------------------------------------------------------------------------
# android.api is compileSdk/targetSdk.
#
# Google Play's rolling rule: new apps and updates to existing apps must target
# an API level released within one year of the latest major Android release.
# The 31-Aug-2026 cut-off therefore requires API 36 (Android 16) for both new
# submissions and updates, so that is what is set here. VERIFY THIS BEFORE EACH
# SUBMISSION -- the threshold moves every August:
#   https://developer.android.com/google/play/requirements/target-sdk
#
# Tradeoff when p4a lags: compileSdk is independent of the NDK, so a high
# android.api does not require a new NDK -- but it does require the matching SDK
# Platform + build-tools, and the Android Gradle Plugin version that p4a pins
# may refuse a compileSdk it does not know ("We recommend using a newer Android
# Gradle Plugin"). If the gradle step fails on that, the options are, best
# first: (1) update p4a (p4a.branch above) to a release that bumps AGP; (2) stay
# on the previous API for internal testing only and do not promote to
# production, because Play will reject the upload outright. Do not "fix" it by
# lowering android.api and shipping -- the rejection is a hard gate.
android.api = 36

# minSdk. 24 (Android 7.0, 2016) covers ~99% of active devices, and everything
# below it costs real money: pre-24 needs a separate OpenSSL/linker story in
# p4a and is where most of the remaining "works on my phone" reports come from.
# ndk_api must equal minapi -- a mismatch produces binaries that dlopen fine on
# the build machine and crash on an old device.
android.minapi = 24
android.ndk_api = 24

# Pinned so a toolchain update never lands silently mid-release. 25b is the
# version p4a has the most mileage on; bump it and the pinned p4a together,
# never one alone.
android.ndk = 25b

# Both ABIs. arm64-v8a is mandatory -- Play rejects a 32-bit-only upload -- and
# armeabi-v7a is ~15 MB of extra bundle for the long tail of cheap devices,
# which for a casual game is exactly the audience. Play serves each device only
# its own ABI out of the AAB, so the download size is unaffected.
# x86_64 is omitted: it is emulator-only and roughly doubles the build time.
android.archs = arm64-v8a, armeabi-v7a

# --------------------------------------------------------------------------
# build behaviour
# --------------------------------------------------------------------------
# Play requires an App Bundle for new applications; the APK path is kept for
# `adb install` onto a real device during development.
android.release_artifact = aab
android.debug_artifact = apk

# Unattended builds (CI, container) cannot answer the licence prompt.
android.accept_sdk_license = True

# Let buildozer keep the SDK current inside the cached volume.
android.skip_update = False

# AndroidX, required by any modern gradle plugin and by p4a's own templates.
android.enable_androidx = True

# ANDROID_PRIVATE -> the app's internal files dir. featherdash/platform.py's
# data_dir() reads exactly that variable to place the save file, so flipping
# this to False would move the save to shared storage and lose every player's
# progress on upgrade. Restated here precisely because it is load-bearing.
android.private_storage = True

# Google auto-backup covers the internal files dir, which is where the save
# lives -- so a player who switches phones keeps their coins, skins and best
# score. The cost is that a determined player can restore an edited save; for a
# single-player game with no leaderboard economy that is the right trade. Set
# android.backup_rules to an XML file if some of the save should be excluded.
android.allow_backup = True

# `build.sh logcat` / `buildozer android logcat` without the rest of the system
# drowning it out. SDL/APP is where pygame's own stdout surfaces.
android.logcat_filters = *:S python:D PythonActivity:D SDL:D SDL/APP:V

# False = let p4a byte-compile and drop the .py sources (the default). That is
# a smaller bundle and a measurably faster cold start, and tracebacks still
# carry file and line numbers -- only the quoted source line is lost. Flip to
# True while chasing a device-only crash, then flip it back.
android.no-byte-compile-python = False

[buildozer]

# 2 = info. Drop to 1 only if the log is genuinely unreadable; nearly every
# "my build fails" question is answered by something at level 2.
log_level = 2

# The container runs as a non-root `builder` user precisely so gradle and the
# SDK licence tooling behave; warn loudly if that ever stops being true.
warn_on_root = 1

# Relative to this file. build.sh shadows ./.buildozer with a Docker named
# volume, because on macOS a bind-mounted build tree is several times slower.
# bin/ stays on the bind mount so finished artifacts appear on the host.
build_dir = ./.buildozer
bin_dir = ./bin
