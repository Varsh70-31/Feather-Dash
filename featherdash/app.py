"""Application shell: the window, the fixed-timestep loop and the scene stack.

The loop is a standard accumulator. Simulation advances in fixed
`config.PHYSICS_DT` slices so the game plays identically on a 60 Hz phone and a
144 Hz monitor, while rendering interpolates between the last two slices so
motion stays smooth at whatever rate the display actually refreshes.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pygame

from . import assets, config
from .display import Display
from .input import InputManager
from .platform import IS_MOBILE, keep_screen_on
from .save import Profile
from .world import World


class _NullAudio:
    """Stand-in so the game runs before -- or without -- the audio layer."""

    def play(self, *args: Any, **kwargs: Any) -> None: ...
    def start_music(self, *args: Any, **kwargs: Any) -> None: ...
    def stop_music(self, *args: Any, **kwargs: Any) -> None: ...
    def set_theme(self, *args: Any, **kwargs: Any) -> None: ...
    def set_sfx_enabled(self, *args: Any, **kwargs: Any) -> None: ...
    def set_music_enabled(self, *args: Any, **kwargs: Any) -> None: ...
    def duck(self, *args: Any, **kwargs: Any) -> None: ...
    def update(self, dt: float) -> None: ...
    def stop_all(self) -> None: ...


class App:
    def __init__(self) -> None:
        self._init_pygame()
        self.display = Display()
        self.input = InputManager()
        self.profile = Profile()
        self.world = World(config.THEMES[1])
        self.audio = self._make_audio()
        self.particles = self._make_particles()
        self.camera = self._make_camera()

        self.running = True
        self.show_fps = bool(self.profile["show_fps"])
        self.clock = pygame.time.Clock()
        self.fps = 0.0
        self._accumulator = 0.0
        self._fps_samples: list[float] = []

        self.scenes: dict[str, Any] = {}
        self.scene: Any = None
        self._pending: tuple[str, dict[str, Any]] | None = None

        keep_screen_on(True)
        self._register_scenes()

    # -- construction ------------------------------------------------------
    def _init_pygame(self) -> None:
        # Ask SDL for a mixer before pygame.init() so the first sound does not
        # allocate a default buffer that is too large and adds audible latency.
        try:
            pygame.mixer.pre_init(config.AUDIO_RATE, -16, 2, config.AUDIO_BUFFER)
        except pygame.error:
            pass
        pygame.init()
        try:
            pygame.mixer.init()
        except pygame.error:
            pass

    def _make_audio(self) -> Any:
        try:
            from .audio import Audio

            return Audio(self.profile)
        except Exception as exc:  # the game must survive a broken audio device
            print(f"[app] audio unavailable ({exc}); continuing without sound")
            return _NullAudio()

    def _make_particles(self) -> Any:
        try:
            from .fx import ParticleSystem

            return ParticleSystem(capacity=420)
        except Exception:
            return None

    def _make_camera(self) -> Any:
        try:
            from .fx import Camera

            return Camera()
        except Exception:
            return None

    def _register_scenes(self) -> None:
        from .scenes.game import GameScene
        from .scenes.menu import MenuScene
        from .scenes.settings import SettingsScene
        from .scenes.shop import ShopScene

        for cls in (MenuScene, GameScene, ShopScene, SettingsScene):
            self.scenes[cls.name] = cls(self)
        self.go("menu")
        self._apply_pending()

    def apply_audio_settings(self) -> None:
        """Push the profile's audio preferences into the mixer.

        Needed because the profile can change without going through a toggle --
        "Reset progress" rewrites every field at once -- and the audio layer
        only reads the profile when it is constructed. Without this the game
        stays muted while the settings screen says sound is on.
        """
        try:
            self.audio.set_sfx_enabled(bool(self.profile["sfx"]))
            self.audio.set_music_enabled(bool(self.profile["music"]))
        except Exception:
            pass

    # -- scenes ------------------------------------------------------------
    def go(self, name: str, **kwargs: Any) -> None:
        """Queue a scene change; it takes effect at the end of the frame."""
        self._pending = (name, kwargs)

    def _apply_pending(self) -> None:
        if self._pending is None:
            return
        name, kwargs = self._pending
        self._pending = None
        target = self.scenes.get(name)
        if target is None:
            return
        if self.scene is not None:
            self.scene.leave()
        self.input.clear()
        self.scene = target
        self.scene.enter(**kwargs)

    # -- main loop ---------------------------------------------------------
    def run(self) -> int:
        try:
            self._loop()
        finally:
            self.profile.flush()
            try:
                self.audio.stop_all()
            except Exception:
                pass
            pygame.quit()
        return 0

    def _loop(self) -> None:
        while self.running:
            frame_time = self.clock.tick(config.TARGET_FPS) / 1000.0
            # A long stall (a breakpoint, the app resuming from background) must
            # not be simulated all at once, or the player dies while away.
            frame_time = min(frame_time, config.MAX_FRAME_TIME)
            self._track_fps(frame_time)

            if self.display.sync():
                self._on_resize()

            frame = self.input.poll(self.display.viewport)
            self._handle_global(frame)
            if not self.running:
                break
            if self.scene is not None:
                self.scene.handle(frame)

            self._accumulator += frame_time
            steps = 0
            while self._accumulator >= config.PHYSICS_DT and steps < 8:
                if self.scene is not None:
                    self.scene.step(config.PHYSICS_DT)
                self._accumulator -= config.PHYSICS_DT
                steps += 1
            if steps >= 8:
                # Fell too far behind to catch up; drop the backlog rather than
                # spiralling into an ever-longer catch-up loop.
                self._accumulator = 0.0

            alpha = self._accumulator / config.PHYSICS_DT if config.PHYSICS_DT else 0.0

            if self.scene is not None:
                self.scene.update(frame_time)
            self.audio.update(frame_time)
            if self.camera is not None:
                self.camera.update(frame_time)
            if self.particles is not None:
                self.particles.update(frame_time)

            self._render(alpha)
            self._apply_pending()

    def _handle_global(self, frame: Any) -> None:
        if frame.quit:
            self.running = False
        if frame.resized:
            self.display.resize(*frame.resized)
            self._on_resize()
        if frame.fullscreen:
            self.display.toggle_fullscreen()
            self._on_resize()
        if frame.debug:
            self.show_fps = not self.show_fps
            self.profile["show_fps"] = self.show_fps
            self.profile.save()
        if frame.focus_lost and self.scene is not None:
            self.scene.on_focus_lost()

    def _on_resize(self) -> None:
        assets.invalidate_scaled()
        try:
            from .ui import text as ui_text

            ui_text.invalidate()
        except Exception:
            pass
        vp = self.display.viewport
        for scene in self.scenes.values():
            scene.on_resize(vp)

    def _render(self, alpha: float) -> None:
        surface = self.display.surface
        vp = self.display.viewport
        if self.scene is not None:
            self.scene.draw(surface, vp, alpha)
        if self.camera is not None:
            try:
                self.camera.draw_overlays(surface, vp)
            except Exception:
                pass
        if self.show_fps:
            self._draw_fps(surface, vp)
        pygame.display.flip()

    # -- diagnostics -------------------------------------------------------
    def _track_fps(self, frame_time: float) -> None:
        if frame_time <= 0:
            return
        self._fps_samples.append(1.0 / frame_time)
        if len(self._fps_samples) > 30:
            self._fps_samples.pop(0)
        self.fps = sum(self._fps_samples) / len(self._fps_samples)

    def _draw_fps(self, surface: pygame.Surface, vp: Any) -> None:
        font = assets.font(vp.px(config.FONT_SIZES["tiny"]))
        text = (f"{self.fps:5.1f} fps  {vp.screen_w}x{vp.screen_h}  "
                f"world {vp.world_w:.0f}x{vp.world_h:.0f}")
        label = font.render(text, True, (255, 255, 255))
        pad = int(vp.px(8))
        bg = pygame.Surface((label.get_width() + pad * 2, label.get_height() + pad), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 140))
        x = int(vp.to_screen(vp.safe_left(), 0)[0])
        y = int(vp.to_screen(0, vp.safe_top())[1])
        surface.blit(bg, (x, y))
        surface.blit(label, (x + pad, y + pad // 2))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if IS_MOBILE:
        os.environ.setdefault("SDL_HINT_ORIENTATIONS", "Portrait")
    app = App()
    return app.run()
