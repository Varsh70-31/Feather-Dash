# Screenshot specification

The eight phone screenshots for the Play listing, what each one has to show, and the caption to put
on it.

They are **captured, not played**. [`tools/screenshot.py`](../tools/screenshot.py) boots the real
game at store resolution, forces each scene into a specific state, steps the simulation a fixed
number of times and saves the frame. An art change is re-photographed in thirty seconds, and the
listing always shows the real renderer rather than a mock-up.

```bash
.venv/bin/python tools/screenshot.py              # the eight phone shots
.venv/bin/python tools/screenshot.py --tablet     # ...and the tablet set
.venv/bin/python tools/screenshot.py --windowed   # watch it happen
```

A `make screenshots` wrapper around that command is the obvious next line in the Makefile; it is
not there yet.

## Reproducibility — read before you re-shoot

**Four of the eight shots are re-rolled on every run.** The scene, the resolution, the demo profile
and the step count are all fixed, but the gameplay simulation's RNG is not seeded
(`Run.__init__` takes `seed=None`, and nothing in the capture tool supplies one), so pillar gap
centres, coin placement and particle spray come from OS entropy each time.

| Shot | Between two runs of the tool |
| --- | --- |
| `01_menu`, `06_results`, `07_shop`, `08_settings` | Byte-identical. Safe to regenerate blind. |
| `02_day`, `03_dusk`, `04_night`, `05_dawn` | Different pixels every run — different pillar positions, a different bird height, sometimes a composition that no longer matches the caption written for it. |

Measured by capturing the full set twice into separate directories and comparing MD5s; the first
four matched, the last four did not.

What that means in practice:

- After re-shooting, **look at the four gameplay frames** and check each against the **State** line
  in its section below, which is the acceptance criterion for that shot, not a promise about what
  the tool emits. Re-run if the bird is tucked behind a pillar, if no pillar pair is in frame, or
  if the framing fights the caption. A re-run is thirty seconds.
- Do not treat a changed gameplay PNG as a regression in the art. Only the other four carry that
  signal.
- Commit the four gameplay shots you actually chose. They are listing assets, not build output.

**To make all eight reproducible**, seed the simulation inside the capture tool — in `_advance`,
straight after `app.go(scene_name)` / `app._apply_pending()`:

```python
SEED = 0xFEA7
if run is not None:
    run.rng = random.Random(SEED)
    run.reset()          # re-spawns the opening pillars from the seeded rng
if app.particles is not None:
    app.particles._rng = random.Random(SEED)
```

Both classes already accept a `seed` argument; the capture tool reaches past it because `App` owns
the construction of each, and the point is to seed without editing the game.

That change belongs in [`tools/screenshot.py`](../tools/screenshot.py) rather than in this
document, so the recipe is recorded here instead of applied. Make it, and this section and the
README paragraph have to change with it: the `test_screenshot_reproducibility_claim_matches_the_tool`
check in `tests/test_docs.py` fails while the prose and the tool disagree, in either direction.

## Output contract

| Property | Value | Why |
| --- | --- | --- |
| Phone size | 1080 x 1920 px (9:16) | Play accepts 320–3840 px per side and 16:9 to 9:16; 1080 x 1920 is the modal phone and needs no upscaling. |
| Tablet size | 1600 x 2560 px (10:16) | Covers both the 7-inch and 10-inch slots. |
| Format | PNG | Lossless; Play's per-image limit is 8 MB and these land near 0.7 MB. |
| Count | 8 | The maximum Play shows; 2 is the minimum it accepts. |
| Naming | `store/screenshots/<phone\|tablet>_NN_name.png` | Play orders screenshots by upload order, and a sorted filename makes that order reproducible. |
| Captions | **Not burned in by the tool** | Play has no caption field, so a caption has to be part of the image. Add them as a post-step if you want them — the copy is below. |

Every shot is taken against the same demo profile, so the numbers on screen tell a consistent
story: best 128, 940 feathers banked, 61 games played, platinum as the best medal, and four of the
six birds unlocked. It reads as a played game rather than a fresh install with every counter at
zero.

Caption style, if you add them: one line, sentence case, under about 40 characters, set in Bai
Jamjuree Bold over a translucent band in the same position in all eight. A caption that jumps
around between images reads as a collage of unrelated apps.

## The eight shots

Shots 1 and 2 do most of the work — they are the pair Play shows inside search results, so they
must say "flying game, tap to play" without being read. The rest are for someone who has already
opened the listing and is scrolling.

### phone_01_menu.png — Title screen

- **Screen:** menu, settled after 180 steps, dawn sky.
- **State:** the logo and wordmark, `BEST 128`, the bird idling in frame, the Play and Birds
  buttons, the feather balance top left, the settings gear top right, and "Tap anywhere to play"
  along the bottom. The world keeps scrolling behind the menu, so even the still frame looks alive.
- **Caption:** `One tap. That's the whole game.`

### phone_02_day.png — Mid-run, day

- **Screen:** play scene, score **14**, 11 feathers, bot flying.
- **State:** the day theme (score 14 lands in the second theme band), a pillar pair in frame with
  the bird threading toward the gap, the big score numeral at the top, the pause button top right.
- **Caption:** `Tap to climb. Let go to fall.`
- **Why:** this is the shot that explains the game. If only two images were allowed, it would be
  this one and the title.

### phone_03_dusk.png — Mid-run, dusk

- **Screen:** play scene, score **27**, 22 feathers.
- **State:** the dusk palette — violet pillars with a lit rim on each cap, pink clouds, the sun
  going down behind the ridge, the first stars out. Check for several pillar pairs and all four
  parallax bands in frame; re-run if the frame is empty of pillars.
- **Caption:** `The sky turns as your score climbs.`
- **Why:** the scenery shot. Keep the bird small in the frame; this one is selling the world.

### phone_04_night.png — Mid-run, night

- **Screen:** play scene, score **39**, 31 feathers.
- **State:** the night theme with its cool blue accent on the HUD, moon and stars, tighter gaps than
  the earlier shots because the difficulty curve has run.
- **Caption:** `Dawn. Day. Dusk. Night.`

### phone_05_dash.png — The dash

- **Screen:** play scene on the dawn theme, score **2**, mid-dash.
- **State:** the bird in the middle of a dash, its trail and shockwave still on screen, the dash
  button on cooldown at the bottom right.
- **Caption:** `One move to master.`
- **Why:** the dash is what separates this from every other tap-to-fly game in the category, and it
  is invisible in a still frame unless the shot is taken mid-burst. Shown on the dawn theme so the
  set still opens on the sky a new player sees first.

### phone_06_results.png — Run complete, with a medal

- **Screen:** results panel over a dimmed play scene.
- **State:** `RUN COMPLETE`, score **57** against `BEST 128`, the **gold** medal with its label,
  `+44` feathers earned, and the Play Again / Menu buttons.
- **Caption:** `Five medals. Bronze to diamond.`
- **Why:** the retention hook. Gold rather than diamond — diamond at 200 reads as unreachable.

### phone_07_shop.png — Choose your bird

- **Screen:** shop, `CHOOSE YOUR BIRD`.
- **State:** Ember on the carousel with `Selected`, Sky peeking in from the right, the six page
  dots, `4 of 6 unlocked`, and the 940-feather balance in the header.
- **Caption:** `Six birds. Earned, never sold.`
- **Why:** answers "is this a free-to-play money pit?" before the question is asked. Check that no
  currency symbol or price tag appears anywhere in the frame.

### phone_08_settings.png — What the app does not do

- **Screen:** settings.
- **State:** the Sound effects, Music and Vibration toggles, the Reset progress row, and the
  version line.
- **Caption:** `No ads. No tracking. No internet.`
- **Why:** the differentiator in a genre full of interstitial ads, and the claim is verifiable in
  the listing's Data Safety section, so it earns a whole screenshot.

## Which ones to upload

All eight, in file order. Play shows them in the order they are uploaded, and that order —
title, gameplay, world, world, world, reward, progression, trust — walks a browsing player through
the pitch. If you ever need to cut to four: `01_menu`, `02_day`, `06_results`, `07_shop`.

## Rules the images must not break

- **No fake chrome.** No invented ratings, no "Editor's Choice"-style badges, no Google Play
  branding, no award claims, no download counts. Play's metadata policy rejects all of it.
- **No other game's name**, in the images or in the captions. See
  [PLAY_STORE_SUBMISSION.md](../docs/PLAY_STORE_SUBMISSION.md#8-the-real-risk-for-this-app).
- **No magenta.** [`featherdash/assets.py`](../featherdash/assets.py) renders a magenta box for any
  image it cannot load, by design. If magenta appears in a capture, an asset is missing from
  `assets/images/` — regenerate it, never retouch the PNG.
- **No device frames** around the gameplay. Play already presents the images in a phone-shaped
  carousel; a second bezel just shrinks the art.
- **Nothing a 3-year-old rating would not allow**, since that is the rating being applied for.

## Tablet screenshots

Phone screenshots alone leave the app flagged as not optimised for large screens, which costs
visibility in the tablet and Chromebook listings. `--tablet` captures the same eight states at
1600 x 2560; upload them under both the 7-inch and 10-inch tabs.

Because the game is authored in world units and the viewport widens with the aspect ratio
([`featherdash/display.py`](../featherdash/display.py)), the tablet captures show genuinely more
scenery rather than a stretched phone frame — which is exactly the thing worth proving on that tab.
