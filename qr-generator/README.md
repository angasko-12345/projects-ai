# QR Generator

A production-ready Android QR code generator built with Expo (React Native + TypeScript).

Generates QR codes from plain text, URLs, email addresses, phone numbers, and Wi-Fi
credentials. Live preview, PNG export, share, copy, dark mode, saved codes, history,
and a premium tier designed so Google Play Billing can be connected later without
rewriting the app.

Everything runs on-device. No accounts, no backend, no analytics, no ads.

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
- Billing isolated behind `src/billing/` with an honest "unavailable" state until
  Google Play Billing is connected; purchases are never faked
- Saved codes, history, and settings stored locally (AsyncStorage), capped and
  deduplicated
- Accessible labels/roles on all controls, Android back closes sheets, works offline

## Repository layout

```
qr-generator/
  App.tsx                 # main screen, sheets, export actions, toasts
  app.json                # Expo config (package id, version, permissions, assets)
  eas.json                # production build profile (.aab)
  assets/                 # icon, adaptive icon, splash, favicon
  scripts/
    generate_assets.py    # regenerates assets/ (Pillow, deterministic)
    setup_signing.sh      # local release keystore + gradle signing hook
  src/
    billing/              # billing abstraction (Play Billing connect point)
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

### Environment variables

| Variable | Needed for | Purpose |
|---|---|---|
| `EXPO_TOKEN` | CI / scripted EAS builds | Expo authentication (`eas whoami` to verify) |
| `ANDROID_HOME` / `ANDROID_SDK_ROOT` | local Gradle build | Android SDK location |
| `JAVA_HOME` | local Gradle build | JDK 17+ used by Gradle and keytool |
| `TEMP` / `TMP` | local Windows build | writable temp dir for the Hermes compiler |
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
6. Content rating questionnaire and Data safety form: this app collects nothing
   and stores all content on-device; answer accordingly.
7. Privacy policy: host `PRIVACY_POLICY.md` at a public URL and paste the link in
   the Play Console (App content -> Privacy policy).
8. Target API level declaration: the app targets API 36, which satisfies the
   current Play requirement.
9. If you enable premium subscriptions later: create the subscription products in
   Play Console, connect `src/billing/` to `react-native-iap` (or Google Play
   Billing directly), and keep `BILLING_NOT_CONNECTED_MESSAGE` behavior for builds
   where billing is not configured.

Set a real contact email in `PRIVACY_POLICY.md` before publishing.

## Development notes

- `app.json` blocks unnecessary permissions; the manifest ships with `INTERNET`
  only (plus explicit removals for storage/audio/overlay permissions some Expo
  templates add).
- Debug tooling (dev menu, Metro, LogBox overlays) does not appear in release
  bundles; release builds do not depend on Expo Go.
- To regenerate app assets: `python scripts/generate_assets.py` (Pillow).
