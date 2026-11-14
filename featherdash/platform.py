"""Platform detection and per-platform paths.

The same code runs on desktop and on Android (packaged with python-for-android),
and those two differ in where it is legal to write files, whether a window can
be resized, and whether a vibrator exists. Everything platform-specific is
funnelled through this module so the rest of the game never has to ask.
"""
from __future__ import annotations

import importlib.util
import os
import sys

APP_NAME = "FeatherDash"
APP_ID = "com.varshraj.featherdash"


def _detect_android() -> bool:
    """True only inside a real python-for-android build.

    Sniffing for an importable `android` module is not enough on its own: any
    directory named `android` next to the game (a packaging folder, say) is
    importable as a namespace package and would make every desktop run claim to
    be a phone. So the module must resolve to an actual file, and we prefer the
    environment variables p4a's bootstrap always sets.
    """
    if os.environ.get("ANDROID_ARGUMENT") or os.environ.get("ANDROID_PRIVATE"):
        return True
    if os.environ.get("ANDROID_ROOT") and os.environ.get("ANDROID_DATA"):
        return True
    try:
        spec = importlib.util.find_spec("android")
    except (ImportError, ValueError):
        return False
    # origin is None for namespace packages -- exactly the false positive above.
    return bool(spec and spec.origin and spec.origin != "namespace")


IS_ANDROID = _detect_android()
IS_IOS = sys.platform == "ios"
IS_MOBILE = IS_ANDROID or IS_IOS
IS_MACOS = sys.platform == "darwin" and not IS_IOS
IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux") and not IS_ANDROID
IS_DESKTOP = not IS_MOBILE

# Running from a PyInstaller one-file bundle unpacks assets to _MEIPASS.
FROZEN = getattr(sys, "frozen", False)
_BUNDLE = getattr(sys, "_MEIPASS", None)


def asset_root() -> str:
    """Directory holding the bundled, read-only `assets/` tree."""
    if _BUNDLE:
        return os.path.join(_BUNDLE, "assets")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "assets")


def data_dir() -> str:
    """Writable directory for the save file, created on demand.

    Android hands the app a private directory via ANDROID_PRIVATE; writing
    anywhere else is denied on modern API levels. Desktop platforms each have
    their own convention, and respecting them keeps the game out of $HOME.
    """
    if IS_ANDROID:
        base = os.environ.get("ANDROID_PRIVATE") or os.path.expanduser("~")
    elif IS_MACOS:
        base = os.path.expanduser("~/Library/Application Support")
    elif IS_WINDOWS:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")

    path = base if IS_ANDROID else os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        # A read-only or sandboxed filesystem must not stop the game booting;
        # the save layer degrades to in-memory-only when the path is unusable.
        path = os.path.join(os.path.expanduser("~"), f".{APP_NAME.lower()}")
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
    return path


def vibrate(seconds: float = 0.02) -> None:
    """Fire a short haptic tick where the platform supports one."""
    if not IS_ANDROID:
        return
    try:
        from plyer import vibrator  # type: ignore

        vibrator.vibrate(seconds)
    except Exception:
        # Haptics are a nicety. A device without a vibrator, or a plyer that
        # failed to import, must never surface as a crash mid-game.
        pass


def keep_screen_on(enabled: bool = True) -> None:
    """Ask Android not to dim the display during play."""
    if not IS_ANDROID:
        return
    try:
        from jnius import autoclass  # type: ignore

        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        params = autoclass("android.view.WindowManager$LayoutParams")
        flag = params.FLAG_KEEP_SCREEN_ON
        if enabled:
            activity.getWindow().addFlags(flag)
        else:
            activity.getWindow().clearFlags(flag)
    except Exception:
        pass
