# Shipping Feather Dash to Google Play

Everything needed to turn this pygame project into a signed Android App Bundle.

**buildozer does not run on macOS.** Not "is awkward on" — python-for-android
shells out to Linux toolchains and fails in the NDK. The developer machine is a
Mac, so every Android build happens inside the container defined by
[`Dockerfile`](Dockerfile), driven by [`build.sh`](build.sh). That is not an
optional convenience; it is the only local path.

```
packaging/android/
  Dockerfile             the Linux build image (Ubuntu 22.04 + JDK 17 + buildozer)
  docker-entrypoint.sh   per-run setup: SDK licences, cache ownership
  build.sh               the command you actually type
  make_keystore.sh       creates the Play upload key (run once, ever)
  validate_spec.py       static checks on ../../buildozer.spec
  selftest.py            proves this packaging layer is internally consistent
  README.md              this file
```

---

## 1. Prerequisites

| | |
|---|---|
| **Docker** | Docker Desktop or OrbStack. ~20 GB free disk. |
| **adb** (optional) | `brew install --cask android-platform-tools` — only for installing onto a real phone. Runs on the **host**: a container on macOS cannot see a USB device. |
| **Play Console account** | $25, one time. |
| **Branding art** | `assets/branding/*.png`. Generate with `make assets` if absent. |

No JDK, Android SDK or NDK on the host. They live in the image and in a Docker
volume.

---

## 2. First build

```sh
make android-debug          # or: packaging/android/build.sh debug
```

The first run does three slow things, in order:

1. Builds the Docker image — **5–10 minutes**.
2. Downloads the Android SDK, the NDK and python-for-android — **5–10 minutes**.
3. Cross-compiles CPython, SDL2, pygame and every dependency, twice (arm64 and
   armv7) — **25–45 minutes**.

Total for a cold build: **roughly 45 minutes to an hour.** Go and do something
else. Subsequent builds reuse all of that through named Docker volumes and take
**2–5 minutes**.

The APK lands in `bin/`, and `build.sh` prints its exact path and size.

```sh
make android-install        # adb install -r -d the most recently BUILT APK
make android-logcat         # tail the game's log
```

"Most recently built" means by modification time, not by filename: `2.10.0`
sorts before `2.9.0`, and `adb install -d` exists to permit a downgrade, so
picking the wrong one would be silent. If `bin/` holds more than one APK,
`build.sh install` names the one it chose and the ones it skipped.

### What the caches are

| volume | holds | cold cost |
|---|---|---|
| `featherdash-buildozer` | Android SDK, NDK, p4a checkout | ~40 min |
| `featherdash-projbuild` | the per-project p4a distribution | ~25 min |
| `featherdash-gradle` | gradle dependency cache | ~3 min |
| `featherdash-ccache` | compiled object cache | speeds up recipe rebuilds |

`build.sh clean` drops the project build volume. `build.sh clean --all` drops
everything, which means the next build is cold again. Reach for the first one
first.

The per-project build directory is a *volume* mounted over `/workspace/.buildozer`
rather than a plain directory on the bind mount, because macOS bind-mount I/O is
several times slower than a native volume and this build writes tens of
thousands of small files.

---

## 3. Signing

### Create the upload key — once, ever

```sh
make keystore               # or: packaging/android/make_keystore.sh
```

RSA 2048, 10 000 days, PKCS12. It prompts for the certificate fields and for a
passphrase (keytool asks on its own TTY, so the passphrase never touches your
shell history), writes to `~/.featherdash/upload.keystore`, chmods it `600`, and
refuses to overwrite anything.

> **If you lose that file or its passphrase, you can never update Feather Dash
> again.** Google cannot sign for you. The only path forward is a new app under
> a new package name, with every player losing their save, your reviews and
> your install base.
>
> So, today:
>
> 1. **Enrol in Play App Signing** at the first upload. Google then holds the
>    real app signing key and your keystore becomes only an *upload* key — and
>    a lost upload key can be reset through Play Console support. Without
>    enrolment there is no reset.
> 2. Back the file up somewhere that is not this laptop. A password manager's
>    file attachment, plus an encrypted archive in cloud storage. **Never a git
>    repository**, private ones included.
> 3. Put the passphrase and the alias in the same password manager entry.
> 4. `echo '*.keystore' >> .gitignore` and `echo '*.jks' >> .gitignore`.

### Release build

```sh
export FEATHERDASH_KEYSTORE="$HOME/.featherdash/upload.keystore"
export FEATHERDASH_KEYSTORE_PASSWORD='…'
export FEATHERDASH_KEY_ALIAS=upload
export FEATHERDASH_KEY_PASSWORD='…'

make android-release
```

The keystore is bind-mounted **read-only** into the container for the duration
of the build; the passwords are passed by variable *name*, so docker reads them
from your shell rather than putting them on the container's command line where
`ps` would show them. Nothing is baked into the image and nothing is echoed.

`build.sh` refuses to start a release build with any of the four unset — an
unsigned bundle is rejected by Play, and a bundle signed with a throwaway debug
key can never be updated.

Output: `bin/featherdash-<version>-release.aab`.

---

## 4. What to upload to Play

Upload the **`.aab`**, not an APK. Play has required App Bundles for new apps
since August 2021.

1. Play Console → your app → **Internal testing** → Create new release.
   Always put the real bundle through internal testing before production; it is
   the only way to catch a signing or ABI problem on a real device.
2. Upload `bin/*.aab`.
3. Accept **Play App Signing** if prompted. Do it. See the warning above.
4. Promote to Production when it runs.

From `assets/branding/`, the Play listing also wants:

| asset | file |
|---|---|
| App icon, 512×512 | downscale `icon_1024.png` |
| Feature graphic, 1024×500 | `feature_graphic.png` |
| Phone screenshots, ≥2 | capture from a device |

The adaptive launcher icon (`adaptive_fg.png` / `adaptive_bg.png`) is consumed
by `buildozer.spec` and baked into the generated Android resources — nothing to
upload separately. `icon_monochrome.png` is **not**: buildozer 1.5.0 has no key
for the Android 13+ themed layer, so it is generated and waiting but not yet
wired up. See §7, "the icon is a square", for what that costs and what wiring
it up needs.

None of these files ship *inside* the bundle. buildozer reads them off the host
filesystem when it generates the resources, and `source.exclude_patterns` keeps
`assets/branding/` out of the packed app — see §7 for why that exclusion is
verified by replay rather than trusted.

### Versioning

The version has exactly one home: `__version__` in `featherdash/__init__.py`.
`buildozer.spec` reads it with `version.regex`, so there is nothing to keep in
sync. Bump it, tag, push:

```sh
make version                # what the build will use
git tag v2.0.1 && git push --tags
```

The tag triggers `.github/workflows/android.yml`, which builds the signed AAB
and attaches it to a GitHub Release.

---

## 5. Testing on a device

```sh
make android-debug
make android-install
make android-logcat
```

Enable Developer options first (Settings → About phone → tap **Build number**
seven times), then USB debugging, then accept the "Allow USB debugging?" prompt
when you plug in.

To test the *release* bundle rather than a debug APK, install
[`bundletool`](https://github.com/google/bundletool) and:

```sh
bundletool build-apks --bundle=bin/*.aab --output=/tmp/fd.apks \
  --ks="$FEATHERDASH_KEYSTORE" --ks-key-alias="$FEATHERDASH_KEY_ALIAS"
bundletool install-apks --apks=/tmp/fd.apks
```

This is worth doing at least once per release: it is the only local way to
exercise the exact artifact Play will serve, including the per-ABI split.

---

## 6. Continuous integration

- **`.github/workflows/ci.yml`** — every push: byte-compiles on 3.9 and 3.12,
  validates `buildozer.spec`, runs the packaging self-test, runs `pytest`
  headless, runs ruff and `bash -n`.
- **`.github/workflows/android.yml`** — debug APK on every pull request, signed
  AAB on every `v*` tag, attached to a GitHub Release.

Four repository secrets are needed for the release path (Settings → Secrets and
variables → Actions):

| secret | value |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | the keystore, base64, one line |
| `ANDROID_KEYSTORE_PASSWORD` | store passphrase |
| `ANDROID_KEY_ALIAS` | `upload` |
| `ANDROID_KEY_PASSWORD` | key passphrase |

Produce the first without printing it to a scrollback buffer:

```sh
base64 < ~/.featherdash/upload.keystore | tr -d '\n' | pbcopy    # macOS
base64 -w0 ~/.featherdash/upload.keystore | xclip -selection c    # Linux
```

---

## 7. Troubleshooting

These are the failures people actually hit with python-for-android and pygame,
in roughly descending order of how often they bite.

### `Aidl is not found`

Older Android build-tools binaries are linked against **ncurses 5**, which
modern distributions no longer ship.

- In the container: already handled — the image installs `libtinfo5`, which is
  one of the reasons it is pinned to Ubuntu **22.04** rather than 24.04, where
  the package no longer exists.
- In CI: the `libtinfo5` step in `android.yml` tries three routes — plain
  `apt`, then a temporarily added **jammy universe** pocket, then an immutable
  `snapshot.ubuntu.com` URL — and then checks `ldconfig` and **fails the job**
  if the library is still absent. It deliberately does not pin an
  `archive.ubuntu.com` pool URL: the pool keeps only the current version of a
  package, so a pinned pool URL rots by design. The previous one
  (`6.3-2ubuntu0.1`) had already 404'd, which meant the fallback never ran and
  the job died forty minutes later with the very error this step prevents.
- If you see it anyway, the SDK is half-downloaded. `build.sh clean --all` and
  rebuild.

### NDK version mismatch / `No such file or directory: .../toolchains/llvm/...`

`android.ndk` in `buildozer.spec` and the NDK actually inside the cache volume
have diverged, usually because the pin was bumped without clearing the cache.

```sh
packaging/android/build.sh clean --all
packaging/android/build.sh debug
```

Never bump `android.ndk` and `p4a.branch` in the same commit: when it breaks you
will not know which one did it.

### Gradle out of memory — `Expiring Daemon because JVM heap space is exhausted`

The default gradle heap cannot merge two ABIs. `GRADLE_OPTS=-Xmx4g` with the
daemon disabled is set in both the image and the CI workflow. If it still
happens, give Docker Desktop more memory (Settings → Resources → Memory ≥ 8 GB),
or build one ABI at a time by temporarily trimming `android.archs`.

### `Your app currently targets API level N and must target at least …`

Play rejected the upload. Raise `android.api` in `buildozer.spec`, **and**
`PLAY_MIN_TARGET_API` in `packaging/android/validate_spec.py`, which is the one
definition of the floor — `selftest.py` imports it rather than restating it, so
the two cannot drift apart again. The requirement moves every August; the next
review date lives beside the constant as `PLAY_TARGET_API_REVIEW`. Check
<https://developer.android.com/google/play/requirements/target-sdk>. If gradle
then refuses the new compileSdk ("We recommend using a newer Android Gradle
Plugin"), update `p4a.branch` to a release that bumps AGP. Do **not** lower
`android.api` back down and reupload; the rejection is a hard gate, and
`validate_spec.py` now reports a below-floor `android.api` as an **error**
rather than a warning for exactly that reason.

### `This release is not compliant with the Google Play 64-bit requirement`

`android.archs` lost `arm64-v8a`. It must include a 64-bit ABI. `validate_spec.py`
fails the build for this before you spend an hour on it.

### `fatal error: 'longintrepr.h' file not found`

python-for-android's own recipes are not mutually consistent out of the box. It
pins pygame to **2.1.0** (October 2021), whose C sources `#include
<longintrepr.h>` — a header CPython moved into `Include/cpython/` in 3.11 — while
its python3 recipe is on **3.14**. Built with p4a's defaults the pair cannot
compile, and the failure arrives roughly forty minutes into a cold build.

This project fixes it in two places, both already applied:

* `buildozer.spec` pins `python3==3.11.9` **and** `hostpython3==3.11.9`. Both are
  required: p4a builds a host interpreter to drive the cross-compile and refuses
  to start when the two versions disagree (`python3 should have same version as
  hostpython3`).
* `packaging/android/recipes/pygame/` overrides the recipe to pygame **2.6.1**,
  the version the game is developed and tested against, wired up through
  `p4a.local_recipes`.

If you ever bump the Python pin, check the pygame recipe's version first: the
constraint runs the other way too, and an old pygame will not build against a
new CPython.

### `Compiling Cython files` → `You need cython`

pygame 2.1.0's extensions were plain C. From 2.2 onward several — the whole
`_sdl2` package — are Cython sources compiled at build time, and p4a's upstream
recipe only installs `setuptools` into the host interpreter. The local recipe
adds Cython to `hostpython_prerequisites`, which is the list p4a pip-installs
into that interpreter. Note it must go there, not into the container image: the
host interpreter p4a builds is not the system python3.

### SDL2 vs pygame recipe conflicts

pygame on p4a needs the **sdl2 bootstrap** (`p4a.bootstrap = sdl2`), and it
cannot coexist with kivy, which wants its own SDL2 build. Symptoms are duplicate
`libSDL2.so` link errors or a recipe that insists on rebuilding forever.

- Keep `kivy` out of `requirements`. `validate_spec.py` warns if both appear.
- If a half-built SDL2 is wedged, the per-project volume is the thing to drop:
  `build.sh clean`, then rebuild.

### `No module named android`

The `android` module is supplied by the p4a bootstrap and exists **only inside a
real build**. Seeing this on desktop means something imported it unguarded.

`featherdash/platform.py` is the single place allowed to probe for it, and it
does so with `importlib.util.find_spec`, rejecting namespace packages so that a
directory merely *named* `android` beside the game cannot fool it. Keep it that
way, and never create a top-level `android/` directory in the source root.

Inside a build, this error instead means `android` is missing from
`requirements` in `buildozer.spec` — it is listed there explicitly.

### Assets missing from the APK — the game boots to magenta placeholders

`featherdash/assets.py` returns a magenta placeholder for a missing image, so a
packaging mistake shows up as magenta rather than a crash. That is a *packing*
problem, not an art problem.

```sh
unzip -l bin/*.apk | grep assets/ | head
```

If `assets/` is absent or thin, check in `buildozer.spec`:

- `source.include_exts` covers the extension (`png,jpg,wav,ogg,ttf,json,md`) —
  this is what actually pulls assets in;
- the file is not caught by `source.exclude_dirs` / `source.exclude_patterns` —
  note that `assets/branding/*` is excluded on purpose, because buildozer reads
  the icon and presplash off the host filesystem and packing them again adds
  2.2 MB of dead weight, including a Play *listing* feature graphic that no
  device can display.

There is no `source.include_patterns`, and adding one is a trap rather than a
fix. An include pattern does not force a file in — the extension filter runs
afterwards and is not bypassed — it only *un-excludes*, and `fnmatch`'s `*`
crosses `/`, so `assets/*` reaches `assets/branding/icon_1024.png` and cancels
the exclusion above. That is not a theory: the spec carried
`source.include_patterns = assets/*,assets/*/*,assets/*/*/*` and shipped all six
branding files in every APK while the spec, this document and the self-test all
said they were excluded.

`validate_spec.packed_files()` now replays buildozer's inclusion algorithm over
the real tree and `make check` fails if anything under `assets/branding/` would
be packed, or if any file under `assets/audio`, `assets/fonts` or
`assets/images` would not. The assertion is on the resulting file list, not on
the spelling of a pattern.

### Black screen on launch

The presplash disappears and nothing replaces it. In order of likelihood:

1. **Python crashed during import.** The log says so. `make android-logcat`,
   then launch — look for the traceback between `SDL/APP` lines.
2. **A missing asset at module import time.** `assets.py` tolerates missing
   images; code that opens a file directly does not.
3. **The display was never flipped.** A `pygame.display.set_mode()` that threw
   leaves a black window and no error.
4. **Wrong bootstrap.** Without `p4a.bootstrap = sdl2`, pygame has no window to
   draw into.

Always reproduce with `logcat` before changing anything — a black screen is a
symptom with a dozen causes and guessing is expensive here.

### The build "succeeds" but the icon is a square, not the adaptive one

buildozer **silently ignores** a spec key it does not recognise — no warning,
no error. The adaptive-icon keys are the trap: they are

```ini
icon.adaptive_foreground.filename
icon.adaptive_background.filename
```

and **not** `icon.adaptive_icon.foreground.filename`, which is what everyone
writes first. Run:

```sh
make android-validate
```

which diffs every key in `buildozer.spec` against the key list generated by the
exact buildozer pinned in the image, and names any that will be ignored. Do
this after every buildozer version bump.

If that key list is missing from the image — a stale image, a renamed layer, a
`FEATHERDASH_IMAGE` pointing somewhere else — the command now **fails** instead
of quietly falling back to the validator's own bundled list and printing `ok`.
A check that reports success without having run is worse than no check, and
this is the one command the paragraph above tells you to trust.

Related: buildozer 1.5.0 has **no key at all** for the Android 13+ themed
monochrome icon layer. `assets/branding/icon_monochrome.png` is generated and
waiting, but it is not referenced from `buildozer.spec`, because an invented
key name would be accepted into the file and do nothing. Wiring it up needs a
newer buildozer, or a p4a hook that writes `<monochrome>` into the generated
`res/mipmap-anydpi-v26/icon.xml`.

### Docker says the daemon is not running

`build.sh` already says this in English. Start Docker Desktop or OrbStack and
wait for the status icon to settle. If it *is* running, check `docker context ls`
— a stale context points at a socket that no longer exists.

---

## 7a. Verified build

A debug APK has been built and inspected from this exact configuration, on an
Apple Silicon machine through the Docker image in this directory:

| | |
| --- | --- |
| Artifact | `bin/featherdash-2.0.0-arm64-v8a_armeabi-v7a-debug.apk`, 43 MB |
| Package | `com.varshraj.featherdash` (matches `featherdash/platform.py::APP_ID`) |
| Version | `2.0.0`, read from `featherdash/__init__.py` by `version.regex` |
| compileSdk / targetSdk | 36 |
| Native code | `arm64-v8a`, `armeabi-v7a` — satisfies Play's 64-bit requirement |
| Permissions | `android.permission.VIBRATE`, and nothing else |
| Icon | `res/mipmap-anydpi-v26/icon.xml` — the adaptive icon, not a flat square |
| Payload | 201 files: the `featherdash` package, 129 images, 31 audio files, both fonts and `OFL.txt` |
| Excluded | `tools/`, `tests/`, `packaging/`, `store/`, `docs/`, `assets/branding/` |

Worth knowing about the timings: a genuinely cold build (no SDK, no NDK, nothing
cached) took about 50 minutes here, dominated by compiling CPython and SDL2
twice — once per architecture — under x86 emulation. A rebuild that only
repackages takes under a minute. The emulation is unavoidable on Apple Silicon;
see the `--platform` note in the Dockerfile.

To reproduce the inspection:

```bash
unzip -o bin/*.apk -d /tmp/apk 'assets/*'
mkdir -p /tmp/payload && tar xf /tmp/apk/assets/private.tar -C /tmp/payload
chmod -R u+rwX /tmp/payload        # p4a writes restrictive directory modes
find /tmp/payload -type f | wc -l
```

## 8. Reproducibility

Pinned, deliberately:

| | where | value |
|---|---|---|
| base image | `Dockerfile` | `ubuntu:22.04` |
| JDK | `Dockerfile` | OpenJDK 17 |
| buildozer | `Dockerfile`, `android.yml` | `1.5.0` |
| Cython | `Dockerfile`, `android.yml` | `0.29.36` (3.x breaks p4a recipes) |
| NDK | `buildozer.spec` | `25b` |
| target API | `buildozer.spec` | `36` |
| min API | `buildozer.spec` | `24` |

**`p4a.branch` is the one thing still floating** (`master`). Pin it to a release
tag before any release you intend to support, and record which tag here. An
unpinned p4a will eventually break a build you did not change.
