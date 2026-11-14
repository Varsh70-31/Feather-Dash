# Feather Dash — Google Play store listing copy

Paste-ready copy for the **Main store listing** page in Play Console.

Every length-limited field below is in a fenced block, so what you copy is exactly what is
counted — no stray Markdown, no smart quotes added by a word processor. The count beside each
heading is the real length of the block, verified by `tests/test_docs.py`; if you edit the copy,
re-run that test and update the count.

Fields are listed in the order Play Console asks for them.

---

## 1. App title — 27 / 30 characters

```text
Feather Dash: One Tap Flyer
```

Why this one: the brand comes first so it is still legible when Play truncates the title under the
icon, and "One Tap" is the phrase people actually search for in this genre. It carries no
competitor's name, which is the whole point (see
[PLAY_STORE_SUBMISSION.md](../docs/PLAY_STORE_SUBMISSION.md#8-the-real-risk-for-this-app)).

Alternates, if you want to A/B the title later in Store listing experiments:

| Title | Characters |
| --- | --- |
| `Feather Dash` | 12 |
| `Feather Dash: Sky Run` | 21 |
| `Feather Dash - Arcade Fly` | 25 |

---

## 2. Short description — 74 / 80 characters

```text
One tap to fly. Four skies, six birds, five medals. No ads, ever. Offline.
```

This is the line shown in search results and above the Install button, so it leads with the verb,
names the content in numbers, and closes on the two things that differentiate the app in a crowded
genre. Alternates:

| Short description | Characters |
| --- | --- |
| `A one-tap arcade flyer. Four skies, six birds, no ads, no tracking.` | 67 |
| `Tap to fly, glide the gaps, chase the medal. No ads, no tracking, offline.` | 74 |
| `One-tap arcade flyer with hand-made art. No ads, no tracking, plays offline.` | 76 |

---

## 3. Full description — 2384 / 4000 characters

```text
One tap to climb. Let go to fall. That is the whole game, and it is harder than it sounds.

Feather Dash is a hand-built arcade flyer about nerve and rhythm. Guide your bird through the gaps between stone pillars, collect feathers, and watch the sky roll from dawn to day to dusk to night as your score climbs. Miss once and the run is over. One more go.

WHY IT FEELS GOOD
• Forgiving on purpose. The hitbox is deliberately smaller than the bird, so a near miss reads as a near miss instead of a cheap death.
• The same game on every device. Physics run on a fixed 120 Hz timestep, so a 60 Hz phone and a 120 Hz tablet play identically.
• Fits your screen properly. The game adapts to any shape of display, from tall phones to tablets, with no black bars and no stretched art.
• A curve, not a wall. Gaps tighten and the world speeds up over your first 42 points, then holds steady and lets skill decide the rest.
• A move worth mastering. The dash is a short forward burst on a cooldown. It gets you through a gap you were going to miss, or into a pillar you were going to clear. It does not make you invincible.

WHAT IS IN IT
• Four skies, dawn to day to dusk to night, that cross-fade as you score.
• Six birds to unlock with the feathers you collect: Ember, Sky, Mint, Violet, Gold and Shadow.
• Eight game speeds, from Relaxed at half pace to Insane at four times. Set it to whatever you want the game to be.
• Five medals to chase: bronze at 10, silver at 25, gold at 50, platinum at 100, diamond at 200.
• Stats kept on your device: high score, longest run, games played, feathers earned, distance flown.
• Sound, music and haptics, each switchable on its own.

NO ADS. NO TRACKING. NO ACCOUNT.
Feather Dash shows no advertising, sells nothing inside the app, and never connects to the internet. Every feather is earned by playing. Your scores live in one small file on your device and go nowhere else. Nothing to sign up for, nothing to opt out of, nothing collected.

PLAYS ANYWHERE
• Fully offline, on a plane or underground.
• Small download, light on the battery, no background services.
• One hand, one thumb, no tutorial to sit through.

Every drawing in Feather Dash was made for Feather Dash. The art is produced by original code written for this game rather than bought from an asset pack, which is why all of it looks like one thing.

Tap to start. Beat your best.
```

Notes on the copy:

- **Bullets are `•` (U+2022), not Markdown `-`.** Play Console renders the full description as near-plain
  text; `-` reads as a dash, `•` reads as a list.
- **Keyword-aware, not keyword-stuffed.** "arcade", "one tap", "offline", "flyer", "unlock" and the
  bird/medal names all appear once in natural sentences. Play ranks on the title and short
  description far more than on repetition in the body, and repetition risks a spam flag.
- **Every number is checkable in the code.** 120 Hz is `config.PHYSICS_HZ`, 42 points is
  `config.RAMP_SCORE`, the medal thresholds are `config.MEDAL_THRESHOLDS`, the six birds are
  `config.SKINS`. If you retune the game, fix this copy too.

---

## 4. Release notes, version 2.0.0 — 493 / 500 characters

```text
2.0.0 is a complete rebuild.

• A new hand-made world with four skies that change as you score
• Six unlockable birds and five medals to chase
• Physics rebuilt on a fixed timestep: the same feel at 60, 90 and 120 Hz
• Proper support for every screen shape, with no black bars and no stretched art
• Score and buttons kept clear of notches, cutouts and gesture bars
• Still no ads, no tracking and no internet access

Thank you for playing. If something feels wrong, tell me and I will fix it.
```

The 500-character limit is **per language**, and it applies to what you type into the "What's new
in this release?" box on the release page.

---

## 5. Categorisation

| Field | Value |
| --- | --- |
| App or game | Game |
| Category | **Arcade** |
| Tags (choose up to 5) | Arcade, Casual, One-handed, Offline, Single player |
| Email address | *(your public support email — it is shown on the listing)* |
| Website | *(optional; the privacy policy URL is a separate, required field)* |
| Phone | *(optional, and it is public — leave it blank)* |

"Arcade" over "Casual": the genre's top charts are where the audience browses, and the game is a
reflex game, not an idle one. You can change the category after launch without a new release.

---

## 6. Content rating

Answer the IARC questionnaire honestly and you should land on the lowest rating in every region.
For this app the answers are all "no": no violence, no sexuality, no profanity, no controlled
substances, no gambling or simulated gambling, no user-to-user interaction, no user-generated
content, no sharing of location or personal information, no digital purchases.

Expected outcome: **ESRB Everyone / PEGI 3 / USK 0 / IARC 3+**.

The questionnaire is the legal rating instrument — never guess an answer to get a lower rating.
The detailed walkthrough is in
[PLAY_STORE_SUBMISSION.md](../docs/PLAY_STORE_SUBMISSION.md#5-content-rating-questionnaire).

---

## 7. Graphic assets

Sizes, formats and the capture plan live in [screenshots.md](screenshots.md) and
[PLAY_STORE_SUBMISSION.md](../docs/PLAY_STORE_SUBMISSION.md#7-store-listing-assets). In short:

| Asset | Requirement | Source |
| --- | --- | --- |
| App icon | 512 x 512 PNG, 32-bit, under 1 MB | `assets/branding/icon_1024.png`, resized to 512 |
| Feature graphic | 1024 x 500 PNG or JPEG | `assets/branding/feature_graphic.png` |
| Phone screenshots | 2–8, 9:16, 1080 x 1920 | `store/screenshots/` |
| Tablet screenshots | needed for tablet eligibility | `store/screenshots/` at tablet aspect |

---

## 8. Claims audit — read before you publish

Three sentences in the copy above are promises about the *build*, not just the code. The source
makes no network calls and reads no identifiers, but a packaging mistake can still make these
untrue. Verify each one against the APK/AAB you are actually uploading:

| Claim in the listing | What must be true in the build |
| --- | --- |
| "never connects to the internet" | The merged manifest declares **no** `android.permission.INTERNET`. `bundletool dump manifest --bundle=bin/*-release.aab --xpath=/manifest/uses-permission/@android:name` should print `android.permission.VIBRATE` and nothing else — see [PLAY_STORE_SUBMISSION.md](../docs/PLAY_STORE_SUBMISSION.md#2-build-requirements). `aapt` cannot read an `.aab`; use it only on a debug `.apk`. |
| "no tracking" | No `com.google.android.gms.permission.AD_ID` and no analytics library is linked in. |
| "sells nothing inside the app" | No Play Billing library, and "Contains ads" and "In-app purchases" are both set to **No**. |
| "nothing collected" | The Data Safety form says *no data collected and no data shared* — matching [PRIVACY_POLICY.md](../docs/PRIVACY_POLICY.md). |

If any of those stops being true, the listing copy, the Data Safety form and the privacy policy all
have to change together. A listing that contradicts the Data Safety declaration is a policy
violation, not a typo.

---

## 9. Translations

Launch in `en-GB` or `en-US` only. Machine-translated listings in a dozen locales read as spam,
earn one-star reviews in those locales, and every one of them is another surface you must keep in
sync with the Data Safety answers. Add a locale when you have a reason to, and translate the
screenshots' captions when you do.
