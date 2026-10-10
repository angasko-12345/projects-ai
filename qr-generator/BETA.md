# Free beta build

How QR Generator ships as a zero-budget public beta: premium is unlocked for
everyone, purchases are switched off, and the APK is built locally with the
existing Android toolchain. No paid service, no cloud build, no new dependency.

## What the flag does

`app.json` keeps the fail-safe default:

```json
"extra": {
  "revenueCat": { "androidPublicKey": "" },
  "beta": { "enabled": false }
}
```

`app.config.js` overrides that default whenever the config is evaluated:
`extra.beta.enabled` is true only when the environment variable `BETA_BUILD`
is exactly `1`. Unset, `0`, `true`, or any other value leaves it `false`.
The flag is baked in by the `expo-constants` Gradle task `createExpoConfig`,
which re-evaluates `app.config.js` on every Gradle build, so the value comes
from the shell that runs Gradle, not from editing `app.json`.

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

1. Stop setting `BETA_BUILD=1`. The checked-in default is `false` and neither
   the `production` nor the `preview` eas profile sets the variable, so any
   normal build is already a production build.
2. Put the RevenueCat public SDK key in `expo.extra.revenueCat.androidPublicKey`
   (the `goog_...` value).
3. Rebuild.

See `BILLING_SETUP.md` for the Google Play and RevenueCat dashboard work that
goes with it. The beta flag and the key can coexist; while beta is on
(`BETA_BUILD=1`), beta wins and no purchase is offered.

## Build the beta APK locally

```bash
cd qr-generator
npx expo prebuild -p android --no-install   # only if android/ is missing
bash scripts/setup_signing.sh               # only if android/ was regenerated

cd android
export ANDROID_HOME=/d/Android/Sdk          # or your SDK path
export JAVA_HOME="/c/Program Files/Java/jdk-22"
export BETA_BUILD=1                         # read by Gradle, not by prebuild
CMAKE_BUILD_PARALLEL_LEVEL=2 sh gradlew --max-workers=3 -Dorg.gradle.parallel=false :app:assembleRelease
```

`BETA_BUILD` must be set in the shell that runs Gradle: `createExpoConfig`
evaluates `app.config.js` during the build and writes the resolved config into
the APK. Windows PowerShell: `$env:BETA_BUILD="1"`; cmd: `set BETA_BUILD=1`.
For a cloud build use the dedicated profile instead, which sets the variable in
its `env`: `eas build --platform android --profile beta`. A build without
`BETA_BUILD=1` is a production build (purchases active, no free premium).

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
- Rebuild without `BETA_BUILD=1` (any non-beta profile) before any future Play
  release.

## Checks before you ship a beta build

```bash
npm run typecheck
npm run lint
npm test
```

Then confirm the flag resolves the way you expect (`app.config.js` is evaluated
by the `expo-constants` Gradle task `createExpoConfig` on every build):

```bash
# prints false with the variable unset, true only for BETA_BUILD=1
node -e "const c=require('./app.config')({config:require('./app.json').expo});console.log(c.extra.beta.enabled)"
BETA_BUILD=1 node -e "const c=require('./app.config')({config:require('./app.json').expo});console.log(c.extra.beta.enabled)"
```
