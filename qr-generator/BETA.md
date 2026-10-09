# Free beta build

How QR Generator ships as a zero-budget public beta: premium is unlocked for
everyone, purchases are switched off, and the APK is built locally with the
existing Android toolchain. No paid service, no cloud build, no new dependency.

## What the flag does

`app.json`:

```json
"extra": {
  "revenueCat": { "androidPublicKey": "" },
  "beta": { "enabled": true }
}
```

`expo.extra.beta.enabled` is read by `src/premium/beta.ts`
(`isBetaBuildEnabled()`), which the premium layer consults:

| Area | Beta build (`true`) | Production build (`false`) |
|---|---|---|
| Premium access | Granted from the build config on startup | From the RevenueCat `premium` entitlement |
| Purchase options | Never loaded, always an empty list | Loaded from the current RevenueCat offering |
| `purchase()` / `restore()` | Returns an honest `unavailable` outcome, never `success` | Real Google Play purchase / restore |
| Billing initialization | Skipped entirely (no store contact) | RevenueCat SDK configured and connected |
| Premium screen | Explains the beta, no prices, no buttons | Price cards, renewal terms, restore |

This is configuration, not a simulated transaction: the app never claims a
payment happened, never invents a price, and never contacts a store in a beta
build. Every existing premium feature (custom colors, QR styles, logo overlay,
unlimited saves, history) simply unlocks for beta testers.

`src/billing/` is unchanged. The RevenueCat provider, entitlement mapping and
error handling are exactly what production uses.

## Going back to production monetization

1. Set `expo.extra.beta.enabled` to `false`.
2. Put the RevenueCat public SDK key in `expo.extra.revenueCat.androidPublicKey`
   (the `goog_...` value).
3. Rebuild.

See `BILLING_SETUP.md` for the Google Play and RevenueCat dashboard work that
goes with it. The beta flag and the key can coexist; while beta is `true`, beta
wins and no purchase is offered.

## Build the beta APK locally

```bash
cd qr-generator
npx expo prebuild -p android --no-install   # only if android/ is missing
bash scripts/setup_signing.sh               # only if android/ was regenerated

cd android
export ANDROID_HOME=/d/Android/Sdk          # or your SDK path
export JAVA_HOME="/c/Program Files/Java/jdk-22"
CMAKE_BUILD_PARALLEL_LEVEL=2 sh gradlew --max-workers=3 -Dorg.gradle.parallel=false :app:assembleRelease
```

An APK, not an AAB, is produced:

```
qr-generator/android/app/build/outputs/apk/release/app-release.apk
```

Release builds are signed with the local keystore created by
`scripts/setup_signing.sh` (`android/app/release-qr.jks`, gitignored). Nothing
under `android/` is ever committed.

On Windows, if the Hermes compiler fails with a "permission denied" under the
system temp directory, set `TEMP`/`TMP` to a writable folder first.

## Install on a phone

1. Copy `app-release.apk` to the device (USB, cloud link, QR, whatever).
2. On the device: **Settings → Apps → Special access → Install unknown apps**
   → allow your file manager/browser (Android blocks APKs from outside Play by
   default).
3. Open the APK and confirm the install.
4. First run: the Premium screen shows the beta notice and every premium
   feature is unlocked.

Alternatively, straight from the build machine:

```bash
adb install -r qr-generator/android/app/build/outputs/apk/release/app-release.apk
```

## Distribute with GitHub Releases

From the repository root (or use the web UI):

```bash
# one-time: name the file so the version is obvious
cp qr-generator/android/app/build/outputs/apk/release/app-release.apk \
   QR-Generator-beta-1.0.0.apk

gh release create beta-1.0.0 QR-Generator-beta-1.0.0.apk \
  --title "QR Generator Beta 1.0.0" \
  --notes "Free beta build. Premium features are unlocked for testers; purchases are disabled. Not signed by Google Play."
```

Web UI: **Releases → Draft a new release → choose a tag (e.g. `beta-1.0.0`) →
attach `app-release.aab`/`app-release.apk` → Publish**.

Notes for testers:

- The APK is self-signed with a local key, so Android will ask them to allow
  installs from unknown sources. That is expected outside Google Play.
- This build is **not** on Google Play; Play purchase/restore paths are not
  part of it.
- Rebuild with `beta.enabled = false` before any future Play release.

## Checks before you ship a beta build

```bash
npm run typecheck
npm run lint
npm test
```

Then confirm the flag is actually baked into the APK (the `expo-constants`
gradle script regenerates `app.config` on every build):

```bash
grep -o '"beta"' android/app/build/intermediates/assets/release/mergeReleaseAssets/app.config
```
