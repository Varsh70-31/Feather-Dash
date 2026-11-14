# Shipping Feather Dash to Google Play

An operator checklist, in the order you will actually do it. Each step says what Play asks, what
the answer is **for this app**, and where the answer comes from.

Budget roughly **three to four weeks** from a standing start to a live listing, almost all of it
spent waiting: identity verification takes days, and a new personal developer account must run a
14-day closed test before it is allowed to publish to production at all. Step 1 explains that,
because it is the part that surprises people and it is the part that sets your launch date.

Nothing here is legal advice, and Play's requirements move. Where a number is likely to have
changed since this was written, the step says so and points at the page that holds the current
value.

---

## 1. Developer account

1. **Register** at <https://play.google.com/console/signup> with a Google account you control and
   intend to keep. The account name becomes your public developer name on every listing.
2. **Pay the one-time registration fee: USD 25.** It is charged once, not per app and not per year.
3. **Choose the account type carefully — it is hard to change later.**
   - *Personal*: fastest to open, and what a solo developer usually wants.
   - *Organisation*: requires a **D-U-N-S number** for your legal entity, which can take a week or
     two to obtain and verify. Choose this only if you are publishing as a registered company.
4. **Complete identity verification.** Google asks for your legal name, address, phone number and a
   government photo ID, and will not let you publish until it passes. Allow a few days. The address
   you verify must match your ID.
5. **Read this before you plan a launch date.** A **personal** developer account created after
   13 November 2023 cannot publish straight to production. Google requires you to:
   - run a **closed test** of your app,
   - with at least **12 testers** who opt in and **stay opted in continuously for 14 days**,
   - and then **apply for production access**, which is a reviewed application asking about your
     app and your testing.

   Twelve real people must accept the tester invitation and remain in the test for two unbroken
   weeks. If a tester opts out on day 10, the clock does not simply continue — line up more than
   twelve. The tester count was lowered from 20 since the rule launched and may change again, so
   confirm the current number on the **Production access** page in Console before you start
   recruiting. Organisation accounts are exempt from this requirement.

   Practically: start the closed test **first**, on the day your build is good enough to be played,
   and finish the store listing while the 14 days run.

---

## 2. Build requirements

These are properties of the artifact you upload, not of the listing, and getting one wrong means a
rejected upload rather than a rejected review. They all live in
[`buildozer.spec`](../buildozer.spec) at the repository root, and **all of them are already set
correctly** — this table is what to re-check before each release, not a list of work.

| Requirement | Value in `buildozer.spec` | Notes |
| --- | --- | --- |
| **Target API level** | `android.api = 36` | Play requires new apps and updates to target a recent API level, with the deadline landing around **31 August each year**. That meant API 35 (Android 15) for the 2025 deadline and API 36 (Android 16) after the 2026 one. Confirm against <https://developer.android.com/google/play/requirements/target-sdk> before every release — this is the one value that silently expires. |
| **Minimum API level** | `android.minapi = 24` (Android 7.0) | python-for-android's SDL2 bootstrap needs a reasonably modern NDK target; going lower buys almost no real devices. |
| **64-bit** | `android.archs = arm64-v8a, armeabi-v7a` | Every APK/AAB containing native code must include 64-bit libraries, and the Python runtime and SDL are native code. An arm-only build is rejected at upload. |
| **Format** | `android.release_artifact = aab` | New apps must ship as an App Bundle, not an APK. |
| **Package name** | `com.varshraj` + `featherdash` | Must equal `APP_ID` in `featherdash/platform.py`. **It can never be changed** once published, and it must not contain any other game's name. This one does not — see step 8. |
| **Version** | read from `featherdash/__init__.py` | `version.filename` + `version.regex` single-source it from `__version__`. Bump it there and nowhere else. |
| **Version code** | derived by buildozer | Every upload, including a replaced test build, needs a strictly higher one. If you ever need to force it, set `android.numeric_version` explicitly and only ever increase it. |
| **Permissions** | `VIBRATE`, and nothing else | The haptic tick on tap. It is a *normal* permission: granted at install, no runtime prompt, no access to any data. Do **not** add `INTERNET` — the game makes no network calls, and an unused internet permission contradicts your own listing and makes the Data Safety answers harder to defend. |

**Audit the built artifact before you upload it**, because a dependency can inject a permission you
did not ask for:

```bash
# The AAB you upload. An App Bundle keeps its manifest as protobuf at
# base/manifest/AndroidManifest.xml, not as binary XML at the archive root, so
# aapt cannot read it -- use bundletool (github.com/google/bundletool).
bundletool dump manifest --bundle=bin/*-release.aab \
    --xpath=/manifest/uses-permission/@android:name

# A debug APK. aapt/aapt2 from the Android SDK build-tools does read these.
aapt dump permissions bin/*-debug.apk
```

Glob rather than type the filename: `android.archs` puts the ABIs into it, so the artifact is
`featherdash-<version>-arm64-v8a_armeabi-v7a-release.aab`, not `featherdash-<version>-release.aab`.

Expect exactly one line: `android.permission.VIBRATE`. An **empty result is not a pass** — it
usually means the command did not read the archive. If `INTERNET` or
`com.google.android.gms.permission.AD_ID` appears, something pulled in a dependency you did not
intend — remove it, or your Data Safety and "no advertising ID" declarations become false.

`make android-validate` additionally checks every key in `buildozer.spec` against the real
buildozer's key list, which catches a typo'd setting that would otherwise be silently ignored.

**Then test on a real device**, not only the emulator:

- the hardware **back** button exits to the menu and then out of the app, rather than killing it
  mid-run (`featherdash/input.py` maps `K_AC_BACK`);
- backgrounding the app mid-run **pauses** it instead of letting the player die in the app switcher;
- rotation and the system bars appearing do not break the layout (`Display.sync()` handles the
  resize);
- on a device with a camera cutout, the score and buttons stay clear of it;
- no magenta boxes anywhere — those are `featherdash/assets.py` telling you an asset is missing
  from the bundle.

---

## 3. Create the app in Play Console

**All apps → Create app.**

| Field | Value |
| --- | --- |
| App name | `Feather Dash: One Tap Flyer` (30-character limit; see [store/listing.md](../store/listing.md)) |
| Default language | English (United Kingdom) or (United States) — pick one and stay with it |
| App or game | **Game** |
| Free or paid | **Free**. A free app can never be switched to paid afterwards; paid can become free. |
| Declarations | Confirm the Developer Program Policies and US export laws |

Console then shows a long task list. Steps 4 to 7 below are that list, in the order it is least
painful to do them.

---

## 4. Data Safety form

**Policy → App content → Data safety.** This is a legal declaration, it is shown on your listing,
and a mismatch between it and the app's actual behaviour is one of the most common suspension
reasons. For Feather Dash every answer is the simple one.

The distinction that matters: Play defines **collection** as user data *transmitted off the
device*. Feather Dash writes a save file and reads it back locally, and that is explicitly **not**
collection. So:

| Question | Answer |
| --- | --- |
| Does your app collect or share any of the required user data types? | **No** |
| Is all of the user data collected by your app encrypted in transit? | *Not asked* (follows from "No") |
| Do you provide a way for users to request that their data is deleted? | *Not asked* (follows from "No") |
| Does your app use the Advertising ID? | **No** |
| Have you had an independent security review? | No (optional; leave unanswered) |

The form covers data collected by **any third-party SDK inside your app**, not just your own code.
Feather Dash links no SDKs at all — the only runtime dependency is pygame — so there is nothing to
disclose.

You must still supply a **privacy policy URL**, even declaring no collection. Publish
[PRIVACY_POLICY.md](PRIVACY_POLICY.md) at a public HTTPS URL first and paste the link here.

---

## 5. Content rating questionnaire

**Policy → App content → Content rating.** The questionnaire is operated by IARC and issues the
real, legally recognised ratings. Answer it honestly; never shade an answer to chase a lower
rating, and never skip it — an unrated app cannot be published.

Category: **Game**. Then, for this app:

| Topic | Answer |
| --- | --- |
| Violence, realistic or cartoon | No |
| Sexuality or nudity | No |
| Profanity or crude humour | No |
| Controlled substances | No |
| Gambling, real or simulated | No (the feather economy has no randomised reward and no money) |
| Horror or fear themes | No |
| User-to-user interaction, chat, or sharing | No |
| User-generated content | No |
| Shares user location | No |
| Allows purchase of digital goods | No |
| Shares personal information | No |
| Digital purchases / loot boxes | No |

**Expected rating:** ESRB **Everyone**, PEGI **3**, USK **0**, IARC **3+**, ClassInd **L**.

Your in-game share button, if it opens the system share sheet with a score, is *not* user-to-user
interaction within the app — but if you later add anything that lets players send each other
content, the questionnaire must be retaken.

---

## 6. Target audience, ads, and the declarations

**Policy → App content.** Work down the list; most of it is three clicks.

**Target audience and content.** You choose age groups. This matters more than it looks:

- Selecting **13 and over** keeps you out of the **Families** programme and its extra
  requirements, and is the simplest first release. The game remains available to everyone — age
  targeting is about who Play markets it to, not who may install it.
- Selecting any group **under 13** opts you into the Families policy: additional content review,
  stricter ads rules (you have no ads, so that part is free), a mandatory privacy policy (you have
  one), and a declaration that the app is designed for children.

Feather Dash genuinely qualifies for the Families route — no ads, no data collection, no external
links, no purchases — so the choice is about how much review you want on your first submission.
**Recommended for release 1: 13 and over**, then move it if you want the Family-friendly badge.

Also confirm you are **not** appealing to children if you select 13+: your icon, screenshots and
description must not be child-directed if you have declared otherwise. The copy in
[store/listing.md](../store/listing.md) is age-neutral.

**Ads.** "Does your app contain ads?" → **No**. This puts "Contains ads" off your listing. If you
ever add an ad network, this answer, the Data Safety form and the privacy policy all change in the
same release.

**App access.** → **All functionality is available without special access.** There is no login, no
region lock and no paywall, so the reviewer needs no credentials. Saying nothing here stalls the
review while someone asks you how to get in.

**Government apps** → No. **Financial features** → None. **Health apps** → No. **News app** → No.
**COVID-19 contact tracing and status apps** → No, if the question is still listed for your app.

**Advertising ID** → declare that the app does not use it, consistent with step 4.

---

## 7. Store listing assets

**Grow → Store presence → Main store listing.** Copy comes from
[store/listing.md](../store/listing.md); the capture plan is in
[store/screenshots.md](../store/screenshots.md).

| Asset | Exact requirement | Where it comes from |
| --- | --- | --- |
| **App icon** | **512 x 512 px**, 32-bit PNG with alpha, under 1 MB | `assets/branding/icon_1024.png` from `tools/gen_brand.py`, resized to 512 |
| **Feature graphic** | **1024 x 500 px**, PNG or JPEG, no alpha | `assets/branding/feature_graphic.png` |
| **Phone screenshots** | **2 to 8**, PNG or JPEG, up to 8 MB each, each side 320–3840 px, aspect ratio between 16:9 and 9:16 | `store/screenshots/`, captured at 1080 x 1920 |
| **7-inch tablet screenshots** | Up to 8, same rules | Optional, but needed for tablet eligibility |
| **10-inch tablet screenshots** | Up to 8, same rules | Optional, but needed for tablet eligibility |
| **App name** | **30 characters** maximum | 27 used |
| **Short description** | **80 characters** maximum | 74 used |
| **Full description** | **4000 characters** maximum | 2068 used |
| **Release notes** | **500 characters** maximum, per language | 493 used |

Notes that cost people a resubmission:

- The icon must be exactly 512 x 512. Play does not resize it for you, and a 1024 upload is
  rejected outright.
- The feature graphic has **no transparency**. An alpha channel renders as black.
- Without tablet screenshots your app is flagged as not optimised for large screens, which hurts
  its ranking on tablets and Chromebooks. Feather Dash genuinely adapts to those shapes
  (`featherdash/display.py`), so this is free visibility — take the captures.
- The icon, screenshots and description must not contain Play Store badges, awards, prices,
  "free", download counts, invented ratings, or any call-to-action graphics. Play's metadata
  policy rejects all of them.

---

## 8. The real risk for this app

This is the step to take seriously. Everything else on this page is paperwork; this one decides
whether the listing survives.

The one-tap flyer genre is saturated with clones of a single famous 2013 game, and Google actively
removes apps that impersonate it. Removal is usually by automated detection of the *name and
metadata*, not the gameplay — the genre itself is entirely legal, and mechanics are not
copyrightable. What gets apps pulled is trading on someone else's name.

**Rules for this listing, without exception:**

1. **Never use the word "Flappy"** — not in the app title, the short or full description, the
   release notes, the screenshots, the feature graphic, the package name, the keystore alias, the
   AAB filename, your developer name, or any support page you link to. There is no safe use of it,
   including "better than", "inspired by" or "not a Flappy clone". Automated metadata matching does
   not read the sentence around it.
2. **Never name any other game or developer** in the listing. No "like X", no comparison tables.
3. **Do not use another game's art style as a selling point.** Do not describe the bird as
   pixel-art, do not mimic the green pipe silhouette, do not use that game's colour scheme.
4. **Ship original art.** Every image in this project is produced by the generators in `tools/` and
   is an original work — see [CREDITS.md](CREDITS.md). That is your actual defence if anyone
   complains: you can show the source code that drew each pixel.
5. **Use the original name consistently.** "Feather Dash", the package `com.varshraj.featherdash`,
   and the feather/pillar vocabulary are all yours and none of them read as a clone.

Already compliant, and worth keeping that way: the package name contains no competitor's name, the
in-game obstacles are called *pillars* rather than pipes, the currency is *feathers*, and the
listing copy in [store/listing.md](../store/listing.md) names no other product.

A secondary risk in this genre is **discovery**, not removal: there are thousands of these games
and almost all of them are ad-stuffed. The listing's angle — no ads, no tracking, no internet
access, original art — is the only thing that makes it stand out in that crowd, which is why it is
in the short description rather than buried at the bottom of the long one.

---

## 9. App signing and release tracks

**Signing.** Play App Signing is mandatory for app bundles. You generate an **upload key** and
Google holds the **app signing key**. Create the upload keystore once, ever:

```bash
make keystore            # packaging/android/make_keystore.sh
```

It writes to `~/.featherdash/upload.keystore` by default — deliberately **outside** the repository,
because a keystore inside a working tree is one `git add -A` away from being published forever. It
uses RSA 2048 with a 10,000-day validity, which is what Play requires of an upload key. Then:

```bash
export FEATHERDASH_KEYSTORE=/absolute/path/to/upload.keystore
export FEATHERDASH_KEYSTORE_PASSWORD='...'
export FEATHERDASH_KEY_ALIAS=upload
export FEATHERDASH_KEY_PASSWORD='...'
make android-release
```

The keystore is bind-mounted read-only into the build container and the passwords are passed
through the environment, so neither is baked into an image or written to disk.

**Back the keystore and its passwords up somewhere you will still have in five years.** Losing the
upload key means requesting a key reset from Google and waiting; being on Play App Signing is the
only reason that is recoverable at all. Never commit the keystore or its passwords.

**Tracks**, in the order you will use them:

| Track | Who sees it | What it is for |
| --- | --- | --- |
| **Internal testing** | Up to 100 testers you list by email; available within minutes | Checking that the AAB installs, launches and does not crash on a real phone. Start here, always. |
| **Closed testing** | A tester list or a Google Group you control | The 14-day, 12-tester requirement from step 1 runs here. Also where you find the bugs that only appear on hardware you do not own. |
| **Open testing** | Anyone with the link; appears in Play with a "Early access" label | Optional. Useful for a wider device sweep before production, and a way to gather reviews. |
| **Production** | Everyone | Needs production access granted (step 1) for a new personal account. |

Promote a build between tracks rather than rebuilding it, so what reaches production is the exact
artifact that was tested. Use a **staged rollout** for production — start at 20% — so a crash on
some chipset you have never seen can be halted instead of recalled.

---

## 10. Review timeline, and what rejection looks like

- **Internal testing:** available in minutes to a few hours; essentially unreviewed.
- **First production review on a new account:** commonly **several days**, and official guidance
  allows up to **7 days or longer**. New developer accounts are reviewed more strictly than
  established ones, so build the extra week into your plan.
- **Later updates:** usually hours to a couple of days.
- **A rejection** arrives as an email and a notice in Console naming the policy clause. Fix it,
  increase the version code, and resubmit — a rejection is not a strike against the account, but
  repeated rejections for the same clause are.

The common first-timer rejections, all of which the steps above are designed to avoid: a privacy
policy URL that 404s or needs a login; Data Safety answers that contradict the app's permissions; a
listing that names or imitates another product; screenshots that are not of the app; and the
target-API requirement being out of date on the uploaded bundle.

---

## Pre-flight checklist

Run this immediately before you promote to production.

- [ ] Version bumped in `featherdash/__init__.py` **only** — buildozer reads it through
      `version.filename` / `version.regex`, and `buildozer.spec`'s own `version` key is commented
      out on purpose. Confirm `make version` prints the new number, and that the version code will
      be higher than the last upload
- [ ] AAB built in release mode, signed with the upload key, 64-bit included
- [ ] `bundletool dump manifest` on the final `.aab` prints `VIBRATE` and nothing else
      (`aapt dump permissions` for a debug `.apk`; aapt cannot read a bundle)
- [ ] Installed from the Play internal track on a real phone and played for five minutes with no
      magenta placeholder art, no crash on rotation, and a clean back-button exit
- [ ] Privacy policy live at a public HTTPS URL, linked in Console **and** from the Settings screen
- [ ] Data Safety: no data collected, no data shared, no advertising ID
- [ ] Content rating questionnaire completed and a rating issued
- [ ] Target audience, ads (No), and app access (no special access) all set
- [ ] Listing copy within every character limit, checked against [store/listing.md](../store/listing.md)
- [ ] Icon exactly 512 x 512; feature graphic exactly 1024 x 500 with no alpha
- [ ] At least 4 phone screenshots uploaded, plus tablet screenshots if you want tablet reach
- [ ] The word "Flappy" appears **nowhere** in the listing, the metadata or the package
- [ ] Keystore and passwords backed up off this machine
- [ ] Staged rollout configured rather than 100% on day one
