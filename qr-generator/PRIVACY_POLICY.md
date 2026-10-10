# Privacy Policy — QR Generator

Effective date: October 9, 2026

QR Generator (package name `com.qrgenerator.app`) is a offline-first utility app.
This policy explains what the app does with your data. The short version: it does
not collect any.

## What the app does not do

- No accounts and no user registration
- No servers or backend services operated by the app
- No analytics, crash reporters, tracking SDKs, or advertising SDKs
- No transmission of your QR code contents, saved codes, history, input text, or
  images to anyone
- No collection of personal data (billing exposes only an anonymous app
  identifier and purchase/entitlement state, described below)
- No sharing of data with third parties other than the billing providers
  (Google Play Billing and RevenueCat) described under "Payments" below

## Data that stays on your device

The following is stored locally in the app's private storage on your device and is
removed if you clear the app's data or uninstall the app:

- The text you type and the QR codes you generate
- QR codes you save to your library and your generation history
- App settings, such as your theme preference
- Any image you choose from your photos to place inside a QR code

Nothing in this list leaves your device.

## Permissions

- Photos: only used if you tap the logo/image option and pick a picture yourself.
  The image is processed on-device.
- The app includes the `INTERNET` permission because it is built with React
  Native tooling and because purchases talk to Google Play Billing and
  RevenueCat; your QR content is never sent anywhere and every feature except
  purchases works fully offline.
- The Android share sheet, clipboard copy, and the "save as PNG" folder picker are
  triggered only by your own taps and operate on-device or through Android system
  components.

## Payments (premium)

Premium plans (auto-renewing monthly and yearly subscriptions and a lifetime
one-time purchase) are sold through Google Play. Google Play handles the
payment; the app never sees or stores your payment card or billing details.
Purchases are tied to your Google Play account and are restored through the
app's "Restore purchases" action.

Entitlement state (whether Premium is active on this device) is managed by
RevenueCat, a third-party subscription infrastructure service. When billing is
enabled in a build, the app sends RevenueCat only an anonymous app-user
identifier together with the status of your purchases and entitlements. It
never sends your QR code contents, saved codes, Wi-Fi credentials, settings,
or images to RevenueCat or any other service.

In builds where Google Play Billing is not yet configured, the app shows an
honest notice and does not process any purchases.

## Children

The app is a general-audience utility and does not knowingly collect data from
anyone, including children.

## Changes

If this policy changes, the updated version will be published with the app listing
on Google Play.

## Contact

Questions about this policy: qrgenerator.support@proton.me
