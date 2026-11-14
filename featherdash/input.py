"""Unified input.

One tap is one tap whether it arrives as a spacebar, a mouse click, a finger or a
gamepad button, so every scene asks the same questions of this module rather than
inspecting raw pygame events.

The subtle part is touch: SDL synthesises mouse events from touches on Android, so
a single tap arrives twice -- once as FINGERDOWN and once as a MOUSEBUTTONDOWN
flagged `touch=True`. Acting on both would double every flap. Those synthetic
mouse events are dropped here, in one place, instead of in each scene.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pygame

from .display import Viewport

# Keys that mean "flap" / "confirm". Several, because players reach for different ones.
FLAP_KEYS = frozenset({pygame.K_SPACE, pygame.K_UP, pygame.K_w, pygame.K_RETURN, pygame.K_KP_ENTER})
BACK_KEYS = frozenset({pygame.K_ESCAPE, pygame.K_AC_BACK, pygame.K_BACKSPACE})
PAUSE_KEYS = frozenset({pygame.K_p, pygame.K_TAB})
# Dash sits on keys a thumb or a right hand reaches without leaving the flap key.
DASH_KEYS = frozenset({pygame.K_d, pygame.K_RIGHT, pygame.K_LSHIFT, pygame.K_RSHIFT, pygame.K_e})
# Only events that mean the player genuinely cannot see or reach the game.
_FOCUS_LOST_EVENTS = frozenset(
    getattr(pygame, name)
    for name in ("WINDOWFOCUSLOST", "WINDOWMINIMIZED", "WINDOWHIDDEN",
                 "APP_WILLENTERBACKGROUND", "APP_DIDENTERBACKGROUND")
    if hasattr(pygame, name)
)

# Xbox/PlayStation-style mapping: A/Cross to flap, B/Circle to go back, Start to pause.
PAD_FLAP = frozenset({0})
PAD_BACK = frozenset({1})
PAD_PAUSE = frozenset({6, 7, 9})
PAD_DASH = frozenset({2, 3})      # X/Y on an Xbox pad, Square/Triangle on a DualSense


@dataclass
class Pointer:
    """A single active touch or mouse cursor, in world units."""

    ident: int
    x: float
    y: float
    start_x: float
    start_y: float
    pressed: bool = True
    moved: bool = False


@dataclass
class Frame:
    """What the player did since the last frame."""

    flap: bool = False          # a fresh press this frame, not a held key
    dash: bool = False
    back: bool = False
    pause: bool = False
    fullscreen: bool = False
    debug: bool = False
    quit: bool = False
    resized: tuple[int, int] | None = None
    focus_lost: bool = False
    down: list[Pointer] = field(default_factory=list)
    up: list[Pointer] = field(default_factory=list)
    moved: list[Pointer] = field(default_factory=list)

    @property
    def any_press(self) -> bool:
        return self.flap or bool(self.down)


class InputManager:
    """Translates a frame of pygame events into a `Frame`."""

    # A mouse id that cannot collide with SDL's touch finger ids.
    MOUSE_ID = -1

    def __init__(self) -> None:
        self.pointers: dict[int, Pointer] = {}
        self._touch_seen = False
        self._pads: list[pygame.joystick.Joystick] = []
        self._init_pads()

    def _init_pads(self) -> None:
        try:
            pygame.joystick.init()
            self._pads = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
            for pad in self._pads:
                pad.init()
        except pygame.error:
            self._pads = []

    def poll(self, vp: Viewport) -> Frame:
        frame = Frame()

        for event in pygame.event.get():
            kind = event.type

            if kind == pygame.QUIT:
                frame.quit = True

            elif kind == pygame.KEYDOWN:
                if event.key in FLAP_KEYS:
                    frame.flap = True
                elif event.key in DASH_KEYS:
                    frame.dash = True
                elif event.key in BACK_KEYS:
                    frame.back = True
                elif event.key in PAUSE_KEYS:
                    frame.pause = True
                elif event.key == pygame.K_F11 or (
                    event.key == pygame.K_f and (event.mod & pygame.KMOD_ALT)
                ):
                    frame.fullscreen = True
                elif event.key == pygame.K_F3:
                    frame.debug = True
                elif event.key == pygame.K_q and (event.mod & (pygame.KMOD_CTRL | pygame.KMOD_META)):
                    frame.quit = True

            elif kind == pygame.VIDEORESIZE:
                frame.resized = (event.w, event.h)

            elif kind in _FOCUS_LOST_EVENTS:
                # Losing focus (a phone call, the app switcher, minimising) must
                # pause the run rather than let the player die while they are not
                # looking. Deliberately NOT pygame.ACTIVEEVENT: SDL raises that
                # for mouse enter/leave as well as input focus, so honouring it
                # would pause the game whenever the cursor left the window.
                frame.focus_lost = True

            # -- touch --------------------------------------------------
            elif kind == pygame.FINGERDOWN:
                self._touch_seen = True
                wx, wy = vp.to_world(event.x * vp.screen_w, event.y * vp.screen_h)
                ptr = Pointer(int(event.finger_id), wx, wy, wx, wy)
                self.pointers[ptr.ident] = ptr
                frame.down.append(ptr)
                frame.flap = True

            elif kind == pygame.FINGERMOTION:
                ptr = self.pointers.get(int(event.finger_id))
                if ptr is not None:
                    ptr.x, ptr.y = vp.to_world(event.x * vp.screen_w, event.y * vp.screen_h)
                    ptr.moved = True
                    frame.moved.append(ptr)

            elif kind == pygame.FINGERUP:
                ptr = self.pointers.pop(int(event.finger_id), None)
                if ptr is not None:
                    ptr.x, ptr.y = vp.to_world(event.x * vp.screen_w, event.y * vp.screen_h)
                    ptr.pressed = False
                    frame.up.append(ptr)

            # -- mouse --------------------------------------------------
            elif kind == pygame.MOUSEBUTTONDOWN:
                if self._is_synthetic(event) or event.button != 1:
                    continue
                wx, wy = vp.to_world(*event.pos)
                ptr = Pointer(self.MOUSE_ID, wx, wy, wx, wy)
                self.pointers[ptr.ident] = ptr
                frame.down.append(ptr)
                frame.flap = True

            elif kind == pygame.MOUSEMOTION:
                if self._is_synthetic(event):
                    continue
                ptr = self.pointers.get(self.MOUSE_ID)
                if ptr is not None:
                    ptr.x, ptr.y = vp.to_world(*event.pos)
                    ptr.moved = True
                    frame.moved.append(ptr)

            elif kind == pygame.MOUSEBUTTONUP:
                if self._is_synthetic(event) or event.button != 1:
                    continue
                ptr = self.pointers.pop(self.MOUSE_ID, None)
                if ptr is not None:
                    ptr.x, ptr.y = vp.to_world(*event.pos)
                    ptr.pressed = False
                    frame.up.append(ptr)

            # -- gamepad ------------------------------------------------
            elif kind == pygame.JOYBUTTONDOWN:
                if event.button in PAD_FLAP:
                    frame.flap = True
                elif event.button in PAD_DASH:
                    frame.dash = True
                elif event.button in PAD_BACK:
                    frame.back = True
                elif event.button in PAD_PAUSE:
                    frame.pause = True

            elif kind in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
                self._init_pads()

        return frame

    def _is_synthetic(self, event: pygame.event.Event) -> bool:
        """True for a mouse event SDL fabricated from a touch.

        pygame exposes `event.touch` for this. Older SDL builds omit the
        attribute, so having seen any real finger event is used as a fallback
        signal that this platform is touch-driven.
        """
        if getattr(event, "touch", False):
            return True
        return self._touch_seen

    def clear(self) -> None:
        """Forget held pointers -- used when a scene changes under the finger."""
        self.pointers.clear()
