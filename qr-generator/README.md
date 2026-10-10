# QR Generator

A production-ready Android QR code generator built with Expo (React Native + TypeScript).

Generates QR codes from plain text, URLs, email addresses, phone numbers, and Wi-Fi
credentials. Live preview, PNG export, share, copy, dark mode, saved codes, history,
and a premium tier sold through Google Play: Monthly Pro and Yearly Pro
subscriptions plus a Lifetime Pro one-time purchase, unified behind a single
RevenueCat `premium` entitlement.

Everything runs on-device. No accounts, no backend, no analytics, no ads. The
free tier never requires a purchase.

## Features

- 5 input types: Text, URL, Email, Phone, Wi-Fi (with security, hidden SSID, and
  network type options)
- Large live QR preview; the code updates as you type
- Save as PNG (Android Storage Access Framework folder picker), system share sheet,
  copy image to clipboard, clear/reset
- Dark, light, and system theme with persistence
- Free tier: basic generation, PNG export, sharing, 3 saved codes, square style
- Premium tier (entitlement-gated): custom QR/background colors, rounded and dot
  styles, logo overlay, unlimited saves, history
- Premium purchases through Google Play Billing + RevenueCat: Monthly Pro,
  Yearly Pro (highlighted "Best value"), Lifetime Pro (one-time), real localized
  store prices, restore purchases, pending/failure/expiry handling; all three
  plans unlock the same `premium` entitlement
- Honest "purchases are not connected in this build" state until the store
  setup in `BILLING_SETUP.md` is completed; purchases are never faked
- Saved codes, history, and settings stored locally (AsyncStorage): history is
  deduplicated by payload and capped at 100 entries; saved codes are neither
  deduplicated nor capped
- Accessible labels/roles on all controls, Android back closes sheets, works offline
- Free beta mode (opt-in with `BETA_BUILD=1`): premium unlocked for testers,
  purchases switched off, built locally as an APK: see `BETA.md`

## Repository layout

```
qr-generator/
  App.tsx                 # main screen, sheets, export actions, toasts
  app.config.js           # dynamic config: BETA_BUILD=1 opts into beta mode
  app.json                # Expo config (package id, version, permissions, assets)
  BILLING_SETUP.md        # manual Google Play + RevenueCat setup (read before first release)
  BETA.md                 # free beta mode: flag, local APK build, install, GitHub Releases
  eas.json                # production build profile (.aab)
  assets/                 # icon, adaptive icon, splash, favicon
  plugins/
    withBillingLaunchMode.js  # config plugin: MainActivity launchMode=singleTop
  scripts/
    generate_assets.py    # regenerates assets/ (Pillow, deterministic)
    setup_signing.sh      # local release keystore + gradle signing hook
  src/
    billing/              # RevenueCat-backed billing service (real purchases)
    components/           # type selector, form, preview card, action row, locked sections
    data/                 # AsyncStorage persistence (saved, history, settings)
    premium/              # entitlements + premium context/sheet
    qr/                   # pure logic: content parsing/validation, QR matrix, styles
    sheets/               # Customize, Library, Premium sheets
    theme/                # tokens + ThemeContext (system/light/dark)
    ui/                   # toast banner
    util/                 # PNG capture/export helpers
  PRIVACY_POLICY.md       # publish at a public URL for Play Console
  README.md
```

Pure QR/content/entitlement logic lives in leaf modules (`src/qr`, `src/premium`)
with unit tests and no platform I/O.

## Requirements

- Node.js 20+ and npm
- For EAS cloud builds: a free Expo account (`https://expo.dev/signup`)
- For local builds: JDK 17+ (keytool), Android SDK (platform 36, build-tools 36.0.0)

## Commands

### Install and develop

```bash
npm install
npx expo start                # dev server; scan QR with Expo Go
npx expo start --web          # browser preview (development only)
```

### Quality gates

```bash
npm run typecheck             # tsc --noEmit
npm run lint                  # eslint .
npm test                      # jest unit tests
```

### Build the production .aab with EAS (recommended)

EAS signs the bundle with credentials stored in your Expo account, so nothing
sensitive lives in this repository.

```bash
npm install -g eas-cli
eas login                     # or: export EXPO_TOKEN=... for CI
eas build:configure           # once; eas.json already present in this repo
eas build --platform android --profile production
```

The build runs in the cloud and finishes with a downloadable
**`app-release.aab`** (Android App Bundle). The command prints the download URL;
`eas build:list` shows past builds.

Signing (one-time, per Expo account):

```bash
eas credentials
# Android -> com.qrgenerator.app -> Production -> Upload new keystore
#   (or let EAS generate one when prompted on first build)
```

If you use EAS-generated keys, register the resulting upload key in Google Play
(Play Console -> App integrity -> App signing) when first uploading.

### Build the .aab locally (verification without EAS)

```bash
npx expo prebuild -p android --no-install     # generates android/ (gitignored)
bash scripts/setup_signing.sh                 # creates local keystore, prompts twice
# non-interactive: SIGNING_STORE_PASS=... SIGNING_KEY_PASS=... bash scripts/setup_signing.sh

cd android
export ANDROID_HOME=/path/to/android-sdk
export JAVA_HOME=/path/to/jdk-17-or-newer
./gradlew bundleRelease                       # Windows: gradlew.bat bundleRelease
```

Output:

```
android/app/build/outputs/bundle/release/app-release.aab
```

Notes:

- `setup_signing.sh` writes `android/app/release-qr.jks` and
  `android/keystore.properties`; everything under `android/` is gitignored, so no
  keys or passwords are ever committed. It also patches the generated
  `android/app/build.gradle` to read that properties file (marker:
  `QRGEN-LOCAL-SIGNING`); re-run it after any `expo prebuild --clean`.
- On Windows, if the Hermes compiler fails with "permission denied" under the
  system temp directory, point TEMP/TMP at a writable folder first:
  `set TEMP=%CD%\..\.tmp` (from `android/`), same for `TMP`.

### Build the free beta APK (local, no AAB, no cloud)

```bash
# android/ must exist (see the local .aab section above for prebuild/signing)
cd android
export ANDROID_HOME=/path/to/android-sdk
export BETA_BUILD=1
CMAKE_BUILD_PARALLEL_LEVEL=2 sh gradlew --max-workers=3 -Dorg.gradle.parallel=false :app:assembleRelease
```

Output (an installable APK, not an AAB):

```
android/app/build/outputs/apk/release/app-release.apk
```

`BETA_BUILD` is read when Gradle runs: the `expo-constants` task
`createExpoConfig` evaluates `app.config.js` on every build and bakes
`extra.beta.enabled` into the APK at that moment. Set it in the shell that runs
Gradle (PowerShell: `$env:BETA_BUILD="1"`; cmd: `set BETA_BUILD=1`), or use the
eas profile `beta`, whose `env` sets it for cloud builds. Any build without
`BETA_BUILD=1` stays a production build. The flag unlocks premium for testers
and disables every purchase flow. Full details, install steps and GitHub
Releases distribution are in `BETA.md`.

### Environment variables

| Variable | Needed for | Purpose |
|---|---|---|
| `EXPO_TOKEN` | CI / scripted EAS builds | Expo authentication (`eas whoami` to verify) |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | local Gradle build | Android SDK location |
| `JAVA_HOME` | local Gradle build | JDK 17+ used by Gradle and keytool |
| `TEMP` / `TMP` | local Windows build | writable temp dir for the Hermes compiler |
| `BETA_BUILD` | beta APK build (local or eas `beta` profile) | `1` enables beta mode via `app.config.js`; unset/anything else is a production build |
| `SIGNING_STORE_PASS`, `SIGNING_KEY_PASS` | non-interactive `setup_signing.sh` | keystore passwords (never commit values) |

No secret keys are stored in this repository.

## Publishing to Google Play (manual steps)

1. `eas build --platform android --profile production` and download
   `app-release.aab`.
2. In Google Play Console create the app (QR Generator, package
   `com.qrgenerator.app`) with default language and free pricing.
3. App integrity: register the upload key EAS shows (or enable Play App Signing
   and upload the upload key when prompted).
4. Upload the `.aab` to a track (start with Internal testing, then Production).
5. Store listing: title, description, category (Tools/Utilities), screenshots
   (phone), feature graphic, app icon.
6. Content rating questionnaire and Data safety form. What to declare depends
   on the build:
   - beta build (`BETA_BUILD=1`) or billing unconfigured: the app collects no
     data; all content stays on-device.
   - billing enabled: the RevenueCat SDK runs in the app and may collect data
     for its own purposes, so Play Console's Data safety form must declare
     that. Check RevenueCat's current documentation for the exact list before
     answering; do not guess.
7. Privacy policy: host `PRIVACY_POLICY.md` at a public URL and paste the link in
   the Play Console (App content -> Privacy policy).
8. Target API level declaration: the app targets API 36, which satisfies the
   current Play requirement.
9. Premium billing: follow `BILLING_SETUP.md`. The app code (RevenueCat SDK,
   `premium` entitlement, three purchase options, restore) is wired; you must
   still create the Play products/base plans, the RevenueCat project/entitlement/
   offering, and add the `goog_` public SDK key to `app.json`, then rebuild.
   Unconfigured builds show an honest "not connected" notice instead of prices.

## Development notes

- `plugins/withBillingLaunchMode.js` sets `android:launchMode="singleTop"` on
  MainActivity; `expo prebuild` applies it. RevenueCat's docs: the default
  `singleTask` launch mode can cancel a purchase when Android recreates the app
  behind the Google Play payment sheet. The manifest also registers the custom
  URL scheme `qrgenerator://` (from `app.json` `scheme`), but the app has no
  deep-link listeners, so opening such a link does nothing. With that proven
  inert, `singleTop` is safe and required for reliable purchases.
- `app.json` blocks unnecessary permissions; the merged manifest declares
  `INTERNET`, `ACCESS_NETWORK_STATE`, and `com.android.vending.BILLING` (the
  latter two come from the native libraries, not from `app.json`), plus
  explicit removals for storage/audio/overlay permissions some Expo templates
  add.
- Debug tooling (dev menu, Metro, LogBox overlays) does not appear in release
  bundles; release builds do not depend on Expo Go.
- To regenerate app assets: `python scripts/generate_assets.py` (Pillow).
