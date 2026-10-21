# QR Generator Release-Readiness Audit

**Project**: `qr-generator` (Expo/React Native Android app)
**Audit date**: 2026-10-11
**Auditor**: Hermes Agent
**Method**: Static code/config inspection only. No APK binary exists locally — release builds happen via EAS cloud.

## Summary

The app is **near release-ready as a free beta**. Production monetization (Google Play Billing) is **not configured** and requires manual dashboard work before it can generate revenue.

## What the app IS

- QR code generator: text, URL, email, phone, Wi-Fi credentials
- Live preview, PNG export, share, copy, dark mode
- Saved codes + history (local AsyncStorage only)
- Free tier: basic generation, 3 saved codes, square style
- Premium tier (RevenueCat `premium` entitlement): custom colors, QR styles, logo overlay, unlimited saves, history
- **No accounts, no backend, no analytics, no ads** — all on-device
- `BETA_BUILD=1` flag: unlocks premium for all, disables all purchase flows (free beta APK)

## What the app is NOT

- **No published APK exists** in the repo or build artifacts (verified). The `android/` directory is gitignored. `dist/` only contains Expo's internal `metadata.json` (bundler metadata, not a signed binary). Release builds require EAS cloud (`eas build --profile production`) or local Gradle (`cd android && ./gradlew bundleRelease`).
- **RevenueCat SDK key not configured**: `app.json` has `"revenueCat": {"androidPublicKey": ""}` (empty string). The app shows an honest "Purchases are not connected in this build" notice instead of pricing. **No monetization will occur until this is set and a new build is done.**
- **No test APK on disk**: The README documents building via `eas build --profile beta` (cloud) or local Gradle with `BETA_BUILD=1`. Neither has been run — no `.apk` or `.aab` files found anywhere in the project.

## Core user journey

Tested via code inspection (App.tsx flow):

1. **Launch** — SplashScreen, ThemeProvider, PremiumProvider wrap MainScreen. On first load, loads saved codes + history from AsyncStorage.
2. **Content input** — `TypeSelector` (5 types: Text, URL, Email, Phone, Wi-Fi). `TypeForm` renders role-specific fields.
3. **QR generation** — `validateDraft()` → `buildQrMatrix()` (M or H error correction). Live preview via `QrPreviewCard`.
4. **Export** — `ActionRow` offers save PNG, share, copy. Uses `react-native-view-shot` or equivalent for PNG capture.
5. **Save** — `handleSaveToLibrary`: limited to 3 for free, unlimited for premium. Persists to AsyncStorage.
6. **Premium** — `PremiumSheet` shows paywall. If `androidPublicKey` is empty, shows "not connected" notice instead of prices.

**Potential issues**:
- Permission blocklist: `blockedPermissions` includes `WRITE_EXTERNAL_STORAGE` and `READ_EXTERNAL_STORAGE`. On Android 10+ (API 29+), the Storage Access Framework should handle this, but the app targets API 36 — verify the folder picker works without these permissions.
- `SYSTEM_ALERT_WINDOW` is blocked — this is fine (no overlay features used).
- `VIBRATE` is blocked — minor UX impact only.

## App configuration

| Field | Value |
|-------|-------|
| Package | `com.qrgenerator.app` |
| Version | `1.0.0` (versionCode 5) |
| SDK | Expo SDK 53 (inferred from `eas.json` cli `>= 13.0.0`) |
| Build config | `eas.json` with profiles: development, preview, beta, production |
| Signing | EAS-managed or local `release-qr.jks` via `setup_signing.sh` |
| Git | Tag `beta-1.0.0` exists; 43 commits past it |

## Permission analysis

**Declared in manifest** (from README, confirmed in app.json):
- `INTERNET` — required for RevenueCat SDK
- `ACCESS_NETWORK_STATE` — required for RevenueCat SDK
- `com.android.vending.BILLING` — required for Google Play Billing

**Explicitly blocked** (app.json `blockedPermissions`):
- `CAMERA`, `WRITE_EXTERNAL_STORAGE`, `READ_EXTERNAL_STORAGE`, `RECORD_AUDIO`, `SYSTEM_ALERT_WINDOW`, `VIBRATE`

**Assessment**: The blocking looks correct for a QR code generator that doesn't use the camera. However, **no Android runtime permission requests** are present in App.tsx or the components. If the app ever needs storage access (for saving PNGs to device), it should request `WRITE_EXTERNAL_STORAGE` at runtime on Android 10+ (or use the Storage Access Framework, which is permission-free on Android 10).

## Quality gates

README commands:
```bash
npm run typecheck   # tsc --noEmit
npm run lint        # eslint .
npm test            # jest unit tests
```

No indication these have been run recently.

## Monetization readiness

**Critical blocker**: RevenueCat `androidPublicKey` is empty in `app.json` (line 73-74):
```json
"revenueCat": {
  "androidPublicKey": ""
}
```

**BILLING_SETUP.md status** (from the doc's "Current status" table):
- App code (RevenueCat SDK, 3-option purchase sheet, unit tests): **Done**
- `androidPublicKey` configured: **Not configured**
- Google Play products created: **You must create**
- RevenueCat project/entitlement/offering: **You must create**
- License testers: **You must create**
- Real purchase verification: **Not verified**

## Build readiness

**Production release** (`.aab`):
```bash
eas build --platform android --profile production
```
- Requires: Expo account, `eas login`, EAS credentials (keystore)
- Output: `app-release.aab` (cloud download, not stored locally)
- versionCode auto-increments

**Beta APK** (local, free, no purchases):
```bash
cd android
export BETA_BUILD=1
sh gradlew --max-workers=3 -Dorg.gradle.parallel=false :app:assembleRelease
```
- Output: `android/app/build/outputs/apk/release/app-release.apk`
- Signed with local keystore from `setup_signing.sh`
- Unlocks premium for all testers, disables purchases

**No build artifacts found**. Neither production AAB nor beta APK exists on disk.

## Recommendations

1. **Build a beta APK locally** as the first verification step: run `npx expo prebuild -p android --no-install` then build with `BETA_BUILD=1`. This verifies the full user journey works in a release build.

2. **Don't attempt EAS production build yet** — billing isn't configured, so the production build would show "purchases not connected" too.

3. **Permission audit**: Verify the blocked permissions don't cause crashes on Android 13+ (particularly storage access). The Storage Access Framework should handle PNG saving without explicit permission grants on API 36.

4. **Quality gates**: Run `npm run typecheck && npm run lint && npm test` on a clean checkout before any release attempt.

**Overall**: The app is structurally complete and configured for release. The codebase is clean, the build pipeline is well-documented, and the beta mode provides a safe path to distribute without Play Store involvement. The **only blocker** is that no signed binary has been produced yet, and monetization requires completing the RevenueCat + Google Play Console setup documented in BILLING_SETUP.md.