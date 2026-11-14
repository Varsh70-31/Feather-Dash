"""The play screen: tutorial, run, pause and results.

Everything the player does during a run happens here. The scene owns a `Run`
(the simulation) and translates the events it reports into sound, particles and
camera shake, which keeps the simulation itself free of presentation concerns and
testable without a display.

The four surfaces it draws are deliberately unequal in weight. The HUD is almost
nothing -- one hero number and two quiet corners -- because anything else there
competes with the pillars for the player's attention. The tutorial is two lines.
The pause overlay has a single dominant action. The results panel is the one
place the game allows itself a moment of ceremony. Every size, inset and font
comes from `ui.metrics.metrics_for(vp)` and every edge from `vp.safe_*()`, so
the same code reads correctly in a 320x480 window, on a phone with a camera
cutout, and on a tablet.
"""
from __future__ import annotations

import math
from enum import Enum
from typing import Any

import pygame

from .. import assets, config
from ..display import Viewport
from ..gameplay import Run, RunState
from ..input import Frame
from ..platform import vibrate
from ..save import medal_for
from ..ui import text as ui_text
from ..ui.layout import Box, Column, anchor, safe_box
from ..ui.metrics import Metrics, metrics_for
from ..ui.widgets import Button, IconButton, ScoreCounter, WidgetGroup, draw_frame, draw_icon
from .base import Scene

# --------------------------------------------------------------------------
# timing, in seconds. Entrances are short on purpose: these surfaces interrupt
# a game, and motion the player has to sit through is a tax on the next run.
# --------------------------------------------------------------------------
ENTRANCE = 0.26
PAUSE_FADE = 0.14
READY_FLASH = 0.30
RESULTS_INPUT_DELAY = 0.45

# The dash is taught once flying is in hand, never on top of learning to flap
# and never in the first second of a run.
DASH_HINT_AFTER_FLAPS = 3
DASH_HINT_AFTER_SECONDS = 1.2
DASH_HINT_IN, DASH_HINT_HOLD, DASH_HINT_OUT = 0.18, 2.6, 0.45

# Backdrop darkening, 0-255. Pause stays light enough to read as "the game is
# still there"; results commits further, because nothing behind it matters now.
PAUSE_DIM = 148
RESULTS_DIM = 172

# Proportions of a metric rather than invented absolutes: a secondary action is
# a notch smaller than the primary one, so the hierarchy survives being read in
# a hurry, in sunlight, by someone who cannot tell the two fills apart.
SECONDARY_W = 0.78
SECONDARY_H = 0.84
# Metrics has no entry for an award-sized image, and the medal is the one thing
# on the results panel that has to be big enough to feel like a prize.
MEDAL_OF_ROW = 1.40
# The flexible parts of the results stack shrink together rather than clipping.
SQUEEZE_STEPS = (1.0, 0.84, 0.70, 0.56)
# ...and they aim at a little less than the safe area, because a card that ends
# exactly on the safe edge reads as cropped even when it is not.
FIT_FRACTION = 0.93

_PAUSED = "PAUSED"
_DASH_KEY = "D"


class Phase(Enum):
    TUTORIAL = "tutorial"
    PLAYING = "playing"
    PAUSED = "paused"
    RESULTS = "results"


class GameScene(Scene):
    name = "game"

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.run: Run | None = None
        self.phase = Phase.TUTORIAL
        self.results_time = 0.0
        self.tutorial_time = 0.0
        self.pause_time = 0.0
        self._trail_debt = 0.0
        self.result: dict[str, Any] = {}
        self.counter = ScoreCounter(Box(0, 0, 10, 10), size="mega", outline=4.0)
        self.dash_button: IconButton | None = None
        self.pause_group = WidgetGroup()
        self.results_group = WidgetGroup()
        self.hud_group = WidgetGroup()
        self._built_for: tuple[float, float] | None = None
        self._pause_label = Box()
        # Coaching and dash readiness are presentation state, so they live here
        # and never reach into the simulation.
        self._flaps = 0
        self._dashed = False
        self._hint_time = 0.0
        self._ready_flash = 0.0
        self._was_ready = False
        self._results_key: tuple | None = None
        self._results: dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------
    def enter(self, **kwargs: Any) -> None:
        vp = self.vp
        skin = str(self.app.profile["skin"])
        self.app.world.set_theme(self.app.world.theme_for_score(0), immediate=True)
        scale = float(self.app.profile["speed_scale"])
        if self.run is None:
            self.run = Run(vp, self.app.world, skin=skin, speed_scale=scale)
        else:
            self.run.reset(vp, skin=skin, speed_scale=scale)
        self.phase = Phase.TUTORIAL
        self.tutorial_time = 0.0
        self.results_time = 0.0
        self.pause_time = 0.0
        self.result = {}
        self._flaps = 0
        self._dashed = False
        self._hint_time = 0.0
        self._ready_flash = 0.0
        self._was_ready = False
        self._results_key = None
        if self.app.particles is not None:
            self.app.particles.clear()
        self.app.audio.start_music(self.app.world.theme)
        self._build_ui()

    def leave(self) -> None:
        if self.app.particles is not None:
            self.app.particles.clear()

    def on_resize(self, vp: Viewport) -> None:
        if self.run is not None:
            self.run.on_viewport_changed(vp)
        self._built_for = None
        self._build_ui()
        if self.phase is Phase.RESULTS:
            # Re-place the panel now rather than on the next frame that draws:
            # its buttons must be hittable the instant the device rotates.
            self._results_layout(vp, metrics_for(vp))

    def on_focus_lost(self) -> None:
        # Being backgrounded mid-run must not cost the player their game.
        if self.phase == Phase.PLAYING:
            self._pause()

    # -- interface ---------------------------------------------------------
    def _build_ui(self) -> None:
        vp = self.vp
        key = (vp.world_w, vp.scale)
        if self._built_for == key:
            return
        self._built_for = key
        self._results_key = None          # the panel is measured per viewport
        m = metrics_for(vp)

        # -- HUD: two corners, and nothing else -----------------------------
        # Both controls sit on a dark scrim rather than the pale chip: they have
        # to stay legible over four skies, including a bright midday one.
        self.hud_group.clear()
        self.hud_group.add(IconButton(
            anchor(vp, "top_right", m.icon_button, m.icon_button), "pause",
            on_click=self._pause, backing_style="scrim", min_tap=m.min_tap))
        # The dash takes the opposite corner: low and outboard where a thumb
        # already rests, far enough from pause that neither is hit by accident,
        # and inside the safe area so a gesture bar never covers it.
        d = m.dash_button
        self.dash_button = IconButton(anchor(vp, "bottom_right", d, d), "dash",
                                      on_click=self._dash, backing_style="scrim",
                                      min_tap=m.min_tap)
        self.hud_group.add(self.dash_button)

        # -- pause: Resume dominant, the rest quieter -----------------------
        label_h = ui_text.measure(_PAUSED, _fs(m, "body"), vp)[1]
        col = Column(m.gap)
        # The label carries its own trailing section space, so the stack keeps
        # one spacing rhythm instead of two competing ones.
        col.add(m.button_w, label_h + max(0.0, m.section_gap - m.gap), "label")
        col.add(m.button_w, m.button_h, "resume")
        col.add(m.button_w * SECONDARY_W, m.button_h * SECONDARY_H, "restart")
        col.add(m.button_w * SECONDARY_W, m.button_h * SECONDARY_H, "menu")
        col.place_in(vp, "center")
        self._pause_label = col["label"]
        self.pause_group.clear()
        self.pause_group.add(
            Button(col["resume"], "Resume", icon="play", on_click=self._resume,
                   text_size=_fs(m, "large"), min_tap=m.min_tap),
            Button(col["restart"], "Restart", icon="restart", variant="ghost",
                   on_click=self._restart, text_size=_fs(m, "body"), min_tap=m.min_tap),
            Button(col["menu"], "Menu", icon="home", variant="ghost",
                   on_click=self._to_menu, text_size=_fs(m, "body"), min_tap=m.min_tap),
        )

        # -- results: the actions exist now; their boxes come from the layout,
        # which can only be measured once there is a result to show.
        self.results_group.clear()
        self.results_group.add(
            Button(Box(), "Play Again", icon="restart", on_click=self._restart,
                   text_size=_fs(m, "large"), min_tap=m.min_tap),
            Button(Box(), "Menu", icon="home", variant="ghost", on_click=self._to_menu,
                   text_size=_fs(m, "body"), min_tap=m.min_tap),
        )

    # -- actions -----------------------------------------------------------
    def _dash(self) -> None:
        if self.run is not None and self.phase is Phase.PLAYING:
            self.run.dash()

    def _pause(self) -> None:
        if self.phase != Phase.PLAYING:
            return
        self.phase = Phase.PAUSED
        self.pause_time = 0.0
        self.app.audio.play("ui_tap")
        self.app.audio.duck(0.45, 0.25)

    def _resume(self) -> None:
        if self.phase != Phase.PAUSED:
            return
        self.phase = Phase.PLAYING
        self.app.audio.play("ui_tap")

    def _restart(self) -> None:
        self.app.audio.play("ui_tap")
        self.enter()

    def _to_menu(self) -> None:
        self.app.audio.play("ui_back")
        self.app.go("menu")

    # -- input -------------------------------------------------------------
    def handle(self, frame: Frame) -> None:
        if frame.back:
            if self.phase == Phase.PLAYING:
                self._pause()
            elif self.phase == Phase.PAUSED:
                self._resume()
            else:
                self._to_menu()
            return
        if frame.pause:
            self._pause() if self.phase == Phase.PLAYING else self._resume()
            return

        group = self._active_group()
        consumed = False
        for ptr in frame.down:
            consumed |= group.on_pointer_down(ptr.x, ptr.y)
        for ptr in frame.moved:
            group.on_pointer_move(ptr.x, ptr.y)
        for ptr in frame.up:
            consumed |= group.on_pointer_up(ptr.x, ptr.y)

        if consumed or self.run is None:
            return

        if frame.dash and self.phase is Phase.PLAYING and self.app.profile["dash_enabled"]:
            self._dash()
            return

        if self.phase in (Phase.TUTORIAL, Phase.PLAYING) and frame.flap:
            if self.phase == Phase.TUTORIAL:
                self.phase = Phase.PLAYING
                self.app.profile["seen_tutorial"] = True
            self.run.flap()
        elif (self.phase == Phase.RESULTS and frame.flap
                and self.results_time > RESULTS_INPUT_DELAY):
            # A short delay stops the tap that killed the player from
            # immediately restarting the game before they have read the score.
            self._restart()

    def _active_group(self) -> WidgetGroup:
        if self.phase == Phase.PAUSED:
            return self.pause_group
        if self.phase == Phase.RESULTS:
            return self.results_group
        return self.hud_group

    # -- simulation --------------------------------------------------------
    def step(self, dt: float) -> None:
        if self.run is None or self.phase == Phase.PAUSED:
            return
        if self.app.camera is not None and self.app.camera.frozen:
            return

        self.run.step(dt)
        self._consume_events(self.run.drain())

        if self.phase == Phase.TUTORIAL:
            self.tutorial_time += dt
        elif self.phase == Phase.RESULTS:
            self.results_time += dt

        if self.run.state == RunState.DEAD and self.phase == Phase.PLAYING:
            self._finish()

    def _consume_events(self, ev: Any) -> None:
        audio = self.app.audio
        particles = self.app.particles
        camera = self.app.camera
        bird = self.run.bird if self.run else None

        if ev.flapped:
            self._flaps += 1
            audio.play("flap")
            if particles is not None and bird is not None:
                particles.emit_preset("flap", bird.x - config.BIRD_W * 0.3, bird.y + 12)

        if ev.dashed and bird is not None:
            self._dashed = True          # the coaching line has done its job
            audio.play("dash")
            if particles is not None:
                particles.emit_preset("flap", bird.x - config.BIRD_W * 0.55, bird.y)
            if camera is not None:
                camera.shake(config.DASH_SHAKE)
                camera.kick(-10.0, 0.0)
            if self.app.profile["haptics"]:
                vibrate(0.012)
        if ev.dash_blocked:
            audio.play("ui_back", volume=0.45)

        for pos in ev.score_positions:
            audio.play("score")
            if particles is not None:
                particles.emit_preset("score", *pos)
            if camera is not None:
                camera.shake(config.SHAKE_ON_SCORE)
        if ev.scored:
            self.counter.set_target(self.run.score if self.run else 0)

        for pos in ev.coin_positions:
            audio.play("coin")
            if particles is not None:
                particles.emit_preset("coin", *pos)
        if ev.streak_bonus:
            audio.play("streak")

        if ev.theme_changed:
            audio.set_theme(ev.theme_changed)

        if ev.died:
            audio.play("hit")
            audio.duck(0.35, 0.6)
            if self.app.profile["haptics"]:
                vibrate(0.035)
            if particles is not None and ev.death_position:
                particles.emit_preset("hit", *ev.death_position)
            if camera is not None:
                camera.shake(config.SHAKE_ON_HIT)
                camera.flash((255, 255, 255), config.FLASH_ON_DEATH)
                camera.freeze(config.HIT_STOP_ON_DEATH)
            if self.run is not None and self.run.state == RunState.DYING:
                audio.play("fall")

    def _finish(self) -> None:
        if self.run is None:
            return
        self.phase = Phase.RESULTS
        self.results_time = 0.0
        outcome = self.app.profile.record_run(
            score=self.run.score,
            feathers=self.run.feathers,
            seconds=self.run.elapsed,
            distance=self.run.distance,
        )
        self.result = dict(outcome)
        self.result["score"] = self.run.score
        self.result["feathers"] = self.run.feathers
        self.counter.set_target(self.run.score, from_value=0)
        # Measure the panel before its first frame, so its buttons are hittable
        # from the moment it appears rather than one frame later.
        self._results_layout(self.vp, metrics_for(self.vp))
        if outcome["new_best"]:
            self.app.audio.play("best")
        elif outcome["medal"]:
            self.app.audio.play("medal")

    # -- animation ---------------------------------------------------------
    def update(self, dt: float) -> None:
        self._emit_dash_trail(dt)
        self.counter.update(dt)
        self._active_group().update(dt)
        self._tick_overlays(dt)

    def _tick_overlays(self, dt: float) -> None:
        """Advance the purely visual timers: pause fade, dash bloom, coaching."""
        self.pause_time = self.pause_time + dt if self.phase is Phase.PAUSED else 0.0
        self._ready_flash = max(0.0, self._ready_flash - dt / READY_FLASH)

        ready = self.run is not None and self.run.bird.dash_ready
        if ready and not self._was_ready:
            # One bloom at the moment the dash comes back. A meter that pulsed
            # forever would be noise in the corner of the eye for a whole run.
            self._ready_flash = 1.0
        self._was_ready = ready

        if self.phase is Phase.PLAYING and self._coaching:
            self._hint_time += dt

    @property
    def _coaching(self) -> bool:
        """True while the dash hint still has something to teach."""
        if self._dashed or self.run is None or not self.app.profile["dash_enabled"]:
            return False
        if self._flaps < DASH_HINT_AFTER_FLAPS or self.run.elapsed < DASH_HINT_AFTER_SECONDS:
            return False
        return self._hint_time < DASH_HINT_IN + DASH_HINT_HOLD + DASH_HINT_OUT

    # -- drawing -----------------------------------------------------------
    def draw(self, surface: pygame.Surface, vp: Viewport, alpha: float) -> None:
        camera = self.app.camera
        offset = camera.offset if camera is not None else (0.0, 0.0)

        self.app.world.draw_background(surface, vp, offset)

        # Obstacles live inside the play area only. The backdrop still spans the
        # whole display, so a wide window gains scenery rather than extra
        # warning of what is coming.
        previous_clip = surface.get_clip()
        surface.set_clip(vp.play_rect)
        if self.run is not None:
            self.run.draw(surface, vp, alpha, offset)
        if self.app.particles is not None:
            self.app.particles.draw(surface, vp)
        surface.set_clip(previous_clip)

        self.app.world.draw_ground(surface, vp, offset)

        if self.phase == Phase.TUTORIAL:
            self._draw_tutorial(surface, vp)
        elif self.phase != Phase.RESULTS:
            self._draw_hud(surface, vp)

        if self.phase == Phase.PAUSED:
            self._draw_pause(surface, vp)
        elif self.phase == Phase.RESULTS:
            self._draw_results(surface, vp)

    def _emit_dash_trail(self, dt: float) -> None:
        """A ribbon behind the bird for as long as the dash lasts."""
        run, particles = self.run, self.app.particles
        if run is None or particles is None or not run.bird.dashing:
            return
        self._trail_debt += dt * config.DASH_TRAIL_HZ
        while self._trail_debt >= 1.0:
            self._trail_debt -= 1.0
            particles.emit_preset("trail", run.bird.x - config.BIRD_W * 0.42, run.bird.y)

    # -- HUD ---------------------------------------------------------------
    def _draw_hud(self, surface: pygame.Surface, vp: Viewport) -> None:
        m = metrics_for(vp)
        run = self.run
        score = run.score if run is not None else 0
        feathers = run.feathers if run is not None else 0
        dash_on = bool(self.app.profile["dash_enabled"])
        # Reserved whatever the phase, so pausing cannot change the score's size.
        key_dial = dash_on and not vp.touch
        outline = m.gap * 0.22
        size = _fs(m, "mega")

        # The band the hero number may occupy. A four-figure score must not grow
        # into the corners it shares the top edge with, so it is capped by the
        # room actually left between them rather than trusted to be short.
        left, right = vp.safe_left(), vp.safe_right() - m.icon_button - m.gap
        if key_dial:
            right -= m.icon_button + m.gap
        if feathers and not vp.landscape:
            # Only in portrait does the purse share the top edge with the score;
            # in landscape it sits underneath and costs the band nothing.
            left += (m.hud_icon + m.gap * 1.35
                     + ui_text.measure(str(feathers), _fs(m, "body"), vp)[0])
        room = (right - left) if vp.landscape else 2.0 * min(vp.cx - left, right - vp.cx)
        width = ui_text.number_width(score, size, vp, outline=outline)
        if 0.0 < room < width:
            size *= room / width

        # The score is the hero and the only loud thing on screen. A portrait
        # board reads best with it centred over the lane; a wide board is short
        # through the middle, so there it tucks into the corner the eye is
        # already watching and leaves the play space clear.
        pos = (vp.safe_left(), vp.safe_top()) if vp.landscape else (vp.cx, vp.safe_top())
        align = "left" if vp.landscape else "center"
        box = ui_text.draw_number(surface, vp, score, pos, size,
                                  align=align, valign="top", outline=outline)
        score_right, score_bottom = vp.to_world(box.right, box.bottom)
        score_middle = vp.to_world(box.right, box.centery)[1]

        if feathers:
            # Secondary: small, gold, cornered. It goes under the score when the
            # score already owns that corner, so the two never collide.
            feather_y = score_bottom + m.gap * 0.4 if vp.landscape else vp.safe_top()
            self._draw_feathers(surface, vp, m, vp.safe_left(), feather_y, feathers)

        if self.phase is not Phase.PLAYING:
            return

        charge = run.bird.dash_charge if run is not None else 1.0
        ready = charge >= 1.0
        if self.dash_button is not None:
            # A pointer already has the key binding, so the button would only be
            # clutter there; the dial beside the score says the same thing.
            self.dash_button.visible = dash_on and vp.touch
            self.dash_button.enabled = ready
            self.dash_button.tint = self.app.world.accent if ready else config.COLOR_TEXT
        self.hud_group.draw(surface, vp)
        if not dash_on:
            return

        if vp.touch and self.dash_button is not None:
            control = self.dash_button.rect
            self._draw_dial(surface, vp, control, charge, ready)
        else:
            control = self._key_hint_box(vp, m, score_right, score_middle)
            self._draw_dial(surface, vp, control, charge, ready)
            ui_text.draw_text(surface, vp, _DASH_KEY, control.center, _fs(m, "body"),
                              color=config.COLOR_TEXT if ready else config.COLOR_TEXT_DIM,
                              outline=m.gap * 0.14)
        self._draw_dash_hint(surface, vp, m, control)

    def _draw_feathers(self, surface: pygame.Surface, vp: Viewport, m: Metrics,
                       x: float, y: float, count: int) -> None:
        draw_icon(surface, vp, "feather", Box(x, y, m.hud_icon, m.hud_icon))
        ui_text.draw_text(surface, vp, str(count),
                          (x + m.hud_icon + m.gap * 0.35, y + m.hud_icon * 0.5),
                          _fs(m, "body"), color=config.COLOR_GOLD,
                          align="left", valign="middle", outline=m.gap * 0.14)

    def _key_hint_box(self, vp: Viewport, m: Metrics, right: float, middle: float) -> Box:
        """Where the keyboard dash dial sits: beside the score, clear of pause."""
        size = m.icon_button
        limit = vp.safe_right() - m.icon_button - m.gap - size
        return Box(min(right + m.gap, limit), middle - size * 0.5, size, size)

    def _draw_dial(self, surface: pygame.Surface, vp: Viewport, box: Box,
                   charge: float, ready: bool) -> None:
        """The cooldown ring: a dark track, an accent sweep, a bloom when it lands.

        Ready has to be obvious in peripheral vision, so it is a difference in
        shape -- a complete ring -- and not only a difference in brightness.
        """
        accent = self.app.world.accent
        thickness = box.w * 0.06
        radius = box.w * 0.5 - thickness * 0.5
        center = box.center
        if ready:
            _ring(surface, vp, center, radius, thickness, accent)
        else:
            _ring(surface, vp, center, radius, thickness * 0.5, config.COLOR_SHADOW)
            _ring(surface, vp, center, radius, thickness, accent, sweep=math.tau * charge)
        if self._ready_flash > 0.0:
            grow = 1.0 - self._ready_flash
            _ring(surface, vp, center, radius + box.w * 0.12 * grow,
                  thickness * self._ready_flash, _mix(config.COLOR_TEXT, accent, grow))

    def _draw_dash_hint(self, surface: pygame.Surface, vp: Viewport, m: Metrics,
                        control: Box) -> None:
        """One dim line pointing at the dash control, once flying is in hand.

        It lives on the HUD rather than the tutorial because the tutorial ends
        at the player's very first flap -- which is exactly the moment this hint
        must not appear.
        """
        if not self._coaching:
            return
        t = self._hint_time
        fade = min(1.0, t / DASH_HINT_IN,
                   max(0.0, (DASH_HINT_IN + DASH_HINT_HOLD + DASH_HINT_OUT - t) / DASH_HINT_OUT))
        if fade <= 0.0:
            return
        if vp.touch:
            # Above the button and sharing its right edge, so the line and the
            # thing it describes read as one object.
            text, pos = "Tap to dash", (control.right, control.y - m.gap)
            align, valign = "right", "bottom"
        else:
            text, pos = "Press D to dash", (control.cx, control.bottom + m.gap * 0.6)
            align, valign = "center", "top"
        ui_text.draw_text(surface, vp, text, pos, _fs(m, "small"),
                          color=config.COLOR_TEXT_DIM, align=align, valign=valign,
                          outline=m.gap * 0.14, alpha=int(255 * fade),
                          max_width=safe_box(vp).w)

    # -- tutorial ----------------------------------------------------------
    def _draw_tutorial(self, surface: pygame.Surface, vp: Viewport) -> None:
        m = metrics_for(vp)
        pulse = 0.5 + 0.5 * math.sin(self.tutorial_time * 3.2)
        max_w = max(1.0, safe_box(vp).w - m.gutter * 2)

        head = "TAP TO FLY" if vp.touch else "PRESS SPACE TO FLY"
        body = "Thread the pillars. Collect feathers."
        f_head, f_body = _fs(m, "title"), _fs(m, "small")
        col = Column(m.gap * 0.6)
        col.add(*ui_text.measure(head, f_head, vp, max_width=max_w))
        col.add(*ui_text.measure(body, f_body, vp, max_width=max_w))
        # Centred between the top of the safe area and the hovering bird, so the
        # prompt clears both the cutout and the bird at any aspect ratio.
        boxes = col.place_centered(vp.cx, (vp.safe_top() + config.BIRD_START_Y) * 0.5)

        ui_text.draw_text(surface, vp, head, boxes[0].center, f_head,
                          outline=m.gap * 0.22, alpha=int(190 + 65 * pulse), max_width=max_w)
        ui_text.draw_text(surface, vp, body, boxes[1].center, f_body,
                          color=config.COLOR_TEXT_DIM, outline=m.gap * 0.14, max_width=max_w)

        # A rising chevron under the bird reads as "go up" without words.
        if self.run is not None:
            bob = math.sin(self.tutorial_time * 3.2) * m.gap * 0.5
            arrow = _chevron(vp.ipx(m.icon))
            arrow.set_alpha(int(120 + 100 * pulse))
            pos = vp.to_screen(self.run.bird.x, self.run.bird.y + m.icon * 2.2 + bob)
            surface.blit(arrow, arrow.get_rect(center=pos))

    # -- pause -------------------------------------------------------------
    def _draw_pause(self, surface: pygame.Surface, vp: Viewport) -> None:
        m = metrics_for(vp)
        t = min(1.0, self.pause_time / PAUSE_FADE)
        _dim(surface, int(PAUSE_DIM * t))
        # The word is a label, not a headline: Resume is what should be read.
        ui_text.draw_text(surface, vp, _PAUSED, (vp.cx, self._pause_label.y),
                          _fs(m, "body"), color=config.COLOR_TEXT_DIM,
                          align="center", valign="top", outline=m.gap * 0.14,
                          alpha=int(255 * t))
        self.pause_group.draw(surface, vp)

    # -- results -----------------------------------------------------------
    def _results_layout(self, vp: Viewport, m: Metrics) -> dict[str, Any]:
        """Measure the whole results composition and place it as one group.

        The card is sized from its contents instead of a fixed height, and the
        flexible parts -- the gaps and the medal -- shrink together if the stack
        would ever reach the safe area. That is what keeps it composed on a
        tablet and uncropped in the smallest window the game allows.
        """
        score = int(self.result.get("score", 0))
        best = int(self.app.profile["high_score"])
        feathers = int(self.result.get("feathers", 0))
        medal = self.result.get("medal") or medal_for(score)
        new_best = bool(self.result.get("new_best"))
        key = (vp.world_w, vp.scale, score, best, feathers, medal, new_best)
        if self._results_key == key:
            return self._results
        self._results_key = key

        f_eyebrow, f_score = _fs(m, "small"), _fs(m, "mega")
        f_best, f_name, f_reward = _fs(m, "body"), _fs(m, "tiny"), _fs(m, "large")
        outline = m.gap * 0.18
        pad = m.panel_pad
        inner_w = max(1.0, m.panel_w - pad * 2)

        eyebrow = "NEW BEST" if new_best else "RUN COMPLETE"
        # On a new best the profile already holds this run's score, so a "BEST"
        # line would simply repeat the number above it. Drop the row instead.
        best_text = "" if new_best else f"BEST {best}"
        reward_text = f"+{feathers}"
        name_text = medal.upper() if medal else ""

        h_eyebrow = ui_text.measure(eyebrow, f_eyebrow, vp, max_width=inner_w)[1]
        h_score = ui_text.measure(str(score), f_score, vp, outline=outline)[1]
        h_best = ui_text.measure(best_text, f_best, vp, max_width=inner_w)[1] if best_text else 0.0
        h_name = ui_text.measure(name_text, f_name, vp, max_width=inner_w)[1] if medal else 0.0
        reward_w, reward_h = ui_text.measure(reward_text, f_reward, vp)
        h_reward = max(m.icon, reward_h)

        area = safe_box(vp)
        card = buttons = group = None
        for squeeze in SQUEEZE_STEPS:
            gap, section = m.gap * squeeze, m.section_gap * squeeze
            medal_h = m.row_h * MEDAL_OF_ROW * squeeze
            card = Column(gap)
            card.add(inner_w, h_eyebrow, "eyebrow")
            card.add(inner_w, h_score, "score")
            if best_text:
                card.add(inner_w, h_best, "best")
            if medal:
                card.gap(max(0.0, section - gap * 2))
                card.add(medal_h, medal_h, "medal")
                card.add(inner_w, h_name, "name")
            card.gap(max(0.0, section - gap * 2))
            card.add(inner_w, h_reward, "reward")

            buttons = Column(gap)
            buttons.add(m.button_w, m.button_h, "again")
            buttons.add(m.button_w * SECONDARY_W, m.button_h * SECONDARY_H, "menu")

            group = Column(section)
            group.add(m.panel_w, card.size[1] + pad * 2, "card")
            group.add(m.button_w, buttons.size[1], "buttons")
            if group.size[1] <= area.h * FIT_FRACTION:
                break

        placed = group.place_in(vp, "center")
        card.place(placed[0].x + pad, placed[0].y + pad)
        buttons.place(placed[1].x, placed[1].y)

        out: dict[str, Any] = {
            "card": placed[0], "eyebrow": card["eyebrow"], "score": card["score"],
            "best": card["best"] if best_text else None, "reward": card["reward"],
            "medal": card["medal"] if medal else None,
            "name": card["name"] if medal else None,
            "eyebrow_text": eyebrow, "best_text": best_text, "reward_text": reward_text,
            "name_text": name_text, "medal_key": medal, "new_best": new_best,
            "f_eyebrow": f_eyebrow, "f_best": f_best, "f_name": f_name,
            "f_reward": f_reward, "reward_w": reward_w,
            "actions": [buttons["again"], buttons["menu"]],
        }
        self.counter.size = f_score
        self.counter.outline = outline
        for widget, place in zip(self.results_group.widgets, out["actions"]):
            widget.rect = place.copy()
        self._results = out
        return out

    def _draw_results(self, surface: pygame.Surface, vp: Viewport) -> None:
        m = metrics_for(vp)
        layout = self._results_layout(vp, m)
        t = min(1.0, self.results_time / ENTRANCE)
        alpha = int(255 * min(1.0, t * 1.6))
        # A short lift, not a bounce: the panel should have landed by the time
        # the player has focused on the number.
        rise = (1.0 - _out_cubic(t)) * m.section_gap

        _dim(surface, int(RESULTS_DIM * min(1.0, t * 1.6)))
        draw_frame(surface, vp, layout["card"].moved(0.0, rise), "panel_dark.png",
                   config.PANEL_INSET, "panel_dark", alpha)

        eyebrow = layout["eyebrow"].moved(0.0, rise)
        ui_text.draw_text(surface, vp, layout["eyebrow_text"], eyebrow.center,
                          layout["f_eyebrow"],
                          color=config.COLOR_GOOD if layout["new_best"] else config.COLOR_TEXT_DIM,
                          alpha=alpha, max_width=eyebrow.w)

        self.counter.rect = layout["score"].moved(0.0, rise)
        self.counter.draw(surface, vp)

        if layout["best"] is not None:
            best = layout["best"].moved(0.0, rise)
            ui_text.draw_text(surface, vp, layout["best_text"], best.center, layout["f_best"],
                              color=config.COLOR_TEXT_DIM, alpha=alpha, max_width=best.w)

        if layout["medal"] is not None:
            medal = layout["medal"].moved(0.0, rise)
            img = assets.scaled(f"medal_{layout['medal_key']}.png",
                                vp.px(medal.w), vp.px(medal.h))
            img.set_alpha(alpha)
            surface.blit(img, img.get_rect(center=vp.to_screen(*medal.center)))
            img.set_alpha(255)        # the surface belongs to the asset cache
            name = layout["name"].moved(0.0, rise)
            ui_text.draw_text(surface, vp, layout["name_text"], name.center,
                              layout["f_name"], color=config.COLOR_TEXT_DIM,
                              alpha=alpha, max_width=name.w)

        # The one gold thing on the panel: what the run actually paid out.
        reward = layout["reward"].moved(0.0, rise)
        x = reward.cx - (m.icon + m.gap * 0.5 + layout["reward_w"]) * 0.5
        draw_icon(surface, vp, "feather",
                  Box(x, reward.cy - m.icon * 0.5, m.icon, m.icon), alpha)
        ui_text.draw_text(surface, vp, layout["reward_text"],
                          (x + m.icon + m.gap * 0.5, reward.cy), layout["f_reward"],
                          color=config.COLOR_GOLD, align="left", alpha=alpha)

        for widget, place in zip(self.results_group.widgets, layout["actions"]):
            widget.rect.y = place.y + rise
        self.results_group.draw(surface, vp)


# --------------------------------------------------------------------------
# drawing helpers
# --------------------------------------------------------------------------
def _fs(m: Metrics, key: str) -> float:
    """A world-unit font size from config.FONT_SIZES, scaled for this device."""
    return m.scaled(config.FONT_SIZES[key])


def _out_cubic(t: float) -> float:
    return 1.0 - (1.0 - max(0.0, min(1.0, t))) ** 3


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


def _ring(surface: pygame.Surface, vp: Viewport, center: tuple[float, float],
          radius: float, thickness: float, color: tuple[int, int, int],
          sweep: float = math.tau) -> None:
    """An annulus, or a sector of one, in world units, clockwise from 12 o'clock.

    Sectors are filled polygons rather than `pygame.draw.arc`: arc's thick
    strokes come out ragged at the radii a cooldown meter actually uses, and a
    meter that looks broken reads as a bug in the dash itself.
    """
    if sweep <= 0.0 or radius <= 0.0 or thickness <= 0.0:
        return
    rect = vp.rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
    if rect.w < 3 or rect.h < 3:
        return
    if sweep >= math.tau - 1e-3:
        pygame.draw.circle(surface, color, rect.center, rect.w // 2,
                           max(1, vp.ipx(thickness)))
        return

    outer, inner = radius, max(0.0, radius - thickness)
    steps = max(3, int(sweep / math.tau * 64.0) + 2)
    points = []
    for i in range(steps + 1):
        angle = sweep * i / steps
        points.append(vp.to_screen(center[0] + math.sin(angle) * outer,
                                   center[1] - math.cos(angle) * outer))
    for i in range(steps, -1, -1):
        angle = sweep * i / steps
        points.append(vp.to_screen(center[0] + math.sin(angle) * inner,
                                   center[1] - math.cos(angle) * inner))
    pygame.draw.polygon(surface, color, points)


_chevron_cache: dict[int, pygame.Surface] = {}


def _chevron(size_px: int) -> pygame.Surface:
    """The arrow under the bird, rotated once per size instead of once per frame."""
    size_px = max(2, int(size_px))
    arrow = _chevron_cache.get(size_px)
    if arrow is None:
        arrow = pygame.transform.rotate(
            assets.scaled("icon_play.png", size_px, size_px), 90)
        if len(_chevron_cache) > 8:
            _chevron_cache.clear()
        _chevron_cache[size_px] = arrow
    return arrow


_dim_cache: dict[tuple[int, int], pygame.Surface] = {}


def _dim(surface: pygame.Surface, alpha: int) -> None:
    """Darken the whole screen.

    One opaque layer per surface size, faded with `set_alpha`: keying the cache
    on the alpha as well would allocate a fresh full-screen surface on every
    frame of a fade.
    """
    alpha = max(0, min(255, int(alpha)))
    if alpha <= 0:
        return
    key = (surface.get_width(), surface.get_height())
    layer = _dim_cache.get(key)
    if layer is None:
        layer = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        layer.fill((*config.COLOR_SHADOW, 255))
        if len(_dim_cache) > 4:
            _dim_cache.clear()
        _dim_cache[key] = layer
    layer.set_alpha(alpha)
    surface.blit(layer, (0, 0))
