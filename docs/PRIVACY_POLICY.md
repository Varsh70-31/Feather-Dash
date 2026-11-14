> **Developer note — delete this block before publishing.**
>
> Google Play requires a privacy policy at a **public, reachable URL** for every app, and the
> reviewer will open the link. A Markdown file in a repository is not enough on its own: publish
> this page somewhere that serves it over HTTPS without a login, as plain HTML or rendered
> Markdown. GitHub Pages, a gist rendered through a Pages site, or any static host is fine.
>
> Before you publish it:
>
> 1. Replace every `[...]` placeholder below — the effective date and the contact email.
> 2. Paste the final URL into Play Console under **Policy → App content → Privacy policy**.
> 3. Link the same URL from the game's Settings screen. Play expects the policy to be reachable
>    from inside the app as well as from the listing.
> 4. If you ever add analytics, ads, crash reporting, a leaderboard or any other network feature,
>    this document stops being true. Rewrite it and update the Data Safety form in the same
>    release.

# Privacy Policy for Feather Dash

**Effective date:** `[DD Month YYYY]`
**Applies to:** Feather Dash for Android (package `com.varshraj.featherdash`) and the desktop builds
of the same game.

## The short version

Feather Dash does not collect anything about you. It has no advertising, no analytics, no crash
reporting, no accounts and no network access of any kind. The only thing it saves is your own
progress, in a file on your own device.

There is no server. There is nothing to opt out of, because nothing is ever sent.

## What the game stores

When you play, the game writes a single small file called `profile.json` to its private storage
area. It contains only this:

| Stored value | What it is |
| --- | --- |
| High score | Your best score |
| Feathers | Your current and lifetime feather counts |
| Games played, total score | Counters used for your in-game statistics |
| Best medal | The highest medal you have earned |
| Best run time, total distance | Your longest flight and the distance you have flown |
| Selected bird, unlocked birds | Which bird you are using and which you have unlocked |
| Sound, music, haptics, FPS display | Your settings |
| Game speed | The pace multiplier you chose for the game |
| Dash | Whether the dash move is enabled |
| Tutorial seen | Whether the first-run prompt has been shown |
| Save-format version | A number the game uses to read older saves correctly |

That is the complete list. There is no identifier, no name, no email address, no device ID, no
advertising ID, no location, no contacts, no photos, no microphone or camera access, and no record
of when or how often you play beyond the counters above.

## Where it is stored

The file stays inside the app's own private storage, which only this app can read:

- **Android:** the app's private data directory, provided by the system. Other apps cannot read it.
- **Windows:** `%APPDATA%\FeatherDash\profile.json`
- **macOS:** `~/Library/Application Support/FeatherDash/profile.json`
- **Linux:** `$XDG_DATA_HOME/FeatherDash/profile.json`, or `~/.local/share/FeatherDash/profile.json`

## What the game sends

Nothing. Feather Dash makes no network requests, and contains no advertising, analytics or crash
reporting library that could make one on its behalf. On Android it does not hold the internet
permission, so it could not reach the network even if its code tried.

Your progress is never uploaded, backed up to us, shared, sold, licensed, analysed or combined with
anything else. We never see it, because it never leaves your device.

## Permissions

The Android build requests exactly one permission: **vibrate**, used only for the small haptic tick
when you tap. It is a normal permission, granted when the app is installed, with no prompt — and it
gives the app access to no information whatsoever. You can turn it off with the **Vibration** switch
in Settings.

The app does **not** request internet access, storage, location, contacts, the camera, the
microphone, your phone state, or the advertising identifier. Because it has no internet permission,
it is not technically capable of sending anything anywhere.

If a future version ever needs another permission, this policy and the app's Data Safety
declaration will say so before that version ships.

## Advertising and purchases

There are none. Feather Dash contains no advertising, no third-party ad SDK, no in-app purchases
and no subscriptions. The in-game feathers are earned by playing and cannot be bought with money.

## Children's privacy

Feather Dash is suitable for all ages and is intended to be safe for children to play.

The game does not knowingly collect personal information from anyone, of any age, including
children under 13 (the threshold under the US **Children's Online Privacy Protection Act**,
"COPPA") and children under 16 (the threshold under **Article 8 of the GDPR**, sometimes called
**GDPR-K**, with some EU member states setting it as low as 13).

Because no personal information is ever collected, transmitted or stored off the device, there is
nothing for a parent or guardian to review, correct or ask us to delete from our records — we hold
no records. Deleting the local save file, as described below, removes everything the game has ever
stored.

The game contains no advertising, no third-party SDKs, no social features, no chat, no user-to-user
interaction, no user-generated content and no external links or purchase flows that a child could
be led into.

## Your rights

Privacy laws including the GDPR, the UK GDPR and the CCPA/CPRA give you rights to access, correct,
export and delete the personal data a company holds about you. We hold none, so there is nothing to
request. Your save file is already entirely under your control, on your own device, and you can
delete it at any time without asking us.

## How to delete your data

Any of these removes everything the game has stored:

1. **In the game:** open **Settings** and use **Reset progress**. This clears your scores, feathers,
   unlocks and statistics back to a fresh install.
2. **Uninstall the app.** Android deletes the app's private data directory with it. On desktop,
   delete the folder listed under "Where it is stored" above.
3. **Delete the file directly** at the path for your platform, listed above.

None of these requires contacting us, and none of them can be undone.

## Services outside our control

Feather Dash itself contacts nothing. However, if you installed it from the Google Play Store,
Google collects its own data about that transaction — the install, the device it was installed on,
and anything you choose to share such as a review or a rating. That is handled under
**Google's Privacy Policy** (<https://policies.google.com/privacy>) and the Play Store's own terms,
not this one, and we have no access to it beyond the anonymous, aggregated install and crash
statistics Play shows every developer in its console.

If your device has Google's automatic app backup enabled, Android may include the app's private
files in your own Google account backup. That is a device feature operated by Google for you, not
a transfer to us.

## Changes to this policy

If this policy changes, the new version will be published at the same URL with a new effective
date, and material changes will be noted in the app's release notes. Since the app has no way to
contact you, the published page is the authoritative version.

## Contact

Questions about this policy, or about privacy in the game:

- **Email:** `[your-support-email@example.com]`
- **Developer:** `[Your name or trading name]`
- **Postal address (only if a store or law requires one):** `[optional]`

---

*This policy describes Feather Dash version 2.0.0 and later. It is accurate as of the effective
date above and is maintained alongside the source code in `docs/PRIVACY_POLICY.md`.*
