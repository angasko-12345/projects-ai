# Billing setup: Google Play + RevenueCat

This document covers everything code cannot do: creating store products,
configuring RevenueCat, wiring the public SDK key, and verifying real purchases.
Work through the steps in order; later steps depend on earlier ones.

Sources used: [RevenueCat Google Play product setup](https://www.revenuecat.com/docs/getting-started/entitlements/android-products),
[RevenueCat Expo guide](https://www.revenuecat.com/docs/getting-started/installation/expo),
[RevenueCat configuring the SDK](https://www.revenuecat.com/docs/getting-started/configuring-sdk).

## Current status (2026-10-09)

| Piece | Status |
|---|---|
| App code: RevenueCat SDK integration, `premium` entitlement gating, 3-option purchase sheet, unit tests | **Done** (this repository) |
| `expo.extra.revenueCat.androidPublicKey` in `app.json` | **Not configured** (empty string; app honestly shows "purchases are not connected in this build") |
| Google Play products (`qr_pro_monthly`, `qr_pro_yearly`, `qr_pro_lifetime`) | **You must create** (Play Console) |
| RevenueCat project, app, products, `premium` entitlement, offering | **You must create** (RevenueCat dashboard) |
| License testers, closed test track | **You must create** (Play Console) |
| Real purchase / restore / expiry verification | **Not verified** (requires the items above; unit tests mock the SDK and are not evidence of real purchases) |

## Final identifiers

| Plan shown in app | Play product ID | Play product type | Base plan ID | RevenueCat package | RevenueCat product ID |
|---|---|---|---|---|---|
| Monthly Pro | `qr_pro_monthly` | Subscription | `monthly-autorenewing` (billing period P1M, auto-renewing) | `$rc_monthly` | `qr_pro_monthly:monthly-autorenewing` |
| Yearly Pro | `qr_pro_yearly` | Subscription | `annual-autorenewing` (billing period P1Y, auto-renewing) | `$rc_annual` | `qr_pro_yearly:annual-autorenewing` |
| Lifetime Pro | `qr_pro_lifetime` | One-time product (in-app product, never consumed) | none (not a subscription) | `$rc_lifetime` | `qr_pro_lifetime` |

- Entitlement: **`premium`**. All three products grant it. The app treats this
  entitlement as the only source of Pro access.
- Offering: any offering marked current in RevenueCat that contains the three
  packages above (use offering ID `default` if you have no reason to pick
  another).
- The app does **not** hardcode base plan IDs. It derives monthly / yearly /
  lifetime from the package type and billing period reported by the store, so
  you may rename base plans without an app change (the RevenueCat product ID in
  the table changes accordingly, and RevenueCat handles that mapping).
- Product IDs are permanent: Google reuses neither subscription product IDs nor
  in-app product IDs across apps, even after deletion. Commit to this scheme
  before creating them.

## 1. Create the three products in Google Play Console

Google requires a build (APK/AAB) uploaded to a track before in-app products
can be created, so do step 11's first EAS build and upload first if the Products
menu is locked.

1. Open [Google Play Console](https://play.google.com/console) -> your app
   (`com.qrgenerator.app`).
2. **Monetize -> Subscriptions -> Create subscription**:
   - Product ID `qr_pro_monthly`, name e.g. "QR Generator Pro Monthly".
3. **Monetize -> Subscriptions -> Create subscription**:
   - Product ID `qr_pro_yearly`, name e.g. "QR Generator Pro Yearly".
4. **Monetize -> In-app products -> Create product** (one-time product):
   - Product ID `qr_pro_lifetime`, name e.g. "QR Generator Pro Lifetime".
   - This is a one-time, non-consumable purchase. The app never consumes it,
     so Google keeps it owned permanently (subject to refunds/revocation).

## 2. Configure the monthly and yearly base plans

For each subscription product:

1. Open the subscription -> **Add base plan**.
2. Monthly: billing period **1 month**, renewal type **Auto-renewing**,
   base plan ID `monthly-autorenewing`, a price, then **Activate**.
3. Yearly: billing period **1 year**, renewal type **Auto-renewing**,
   base plan ID `annual-autorenewing`, a price, then **Activate**.
4. Offers (free trials, discounts) are optional and not required by the app.
5. If you want maximum compatibility with older billing clients, mark one base
   plan "Use for deprecated billing methods" (backwards compatible). The
   current RevenueCat Android SDK (used here) supports multiple base plans, so
   this is optional.

## 3. Configure Lifetime as a one-time product

- It is created in step 1's **In-app products** flow, not under Subscriptions.
- Never give it a base plan; it is not a subscription and never renews.
- In the **RevenueCat dashboard** (step 4/7) you must mark `qr_pro_lifetime`
  as **non-consumable**: RevenueCat otherwise automatically consumes the Play
  purchase, which would let the customer buy it again. This is a RevenueCat
  product setting, not a Play Console setting.

## 4. Configure the RevenueCat Android app

1. Create/open a project at [app.revenuecat.com](https://app.revenuecat.com).
2. **Apps -> New app** (or Project settings -> Apps): platform **Google Play**,
   bundle ID `com.qrgenerator.app`.
3. Connect the store: **Project settings -> Google Play**. RevenueCat asks for
   a Google Play **service account JSON key** to link Play Console.
   - Generate it in Play Console -> **Setup -> API access -> service accounts**
     with permissions for the app, download the JSON key, and upload it
     **only** into the RevenueCat dashboard.
   - **Never** commit that JSON key (or any secret) to this repository. The
     repository's `.gitignore` excludes credentials; keep it that way.
4. After the link, RevenueCat imports products from Play (or add them under
   Products -> + with the IDs from the table). Confirm:
   - `qr_pro_monthly:monthly-autorenewing` (subscription)
   - `qr_pro_yearly:annual-autorenewing` (subscription)
   - `qr_pro_lifetime` (one-time) with **non-consumable** set.

## 5. Add the public SDK key to the app configuration

1. RevenueCat **Project settings -> API keys**. Copy the **public** Google SDK
   key, which starts with `goog_`.
2. Put it in `app.json`:

   ```json
   "extra": {
     "revenueCat": { "androidPublicKey": "goog_..." }
   }
   ```

3. Rules:
   - Only `goog_` public keys are accepted; the app rejects anything else
     (including the secret REST key `enc_...`). Secret keys must never be
     embedded in an app bundle or committed anywhere.
   - The key is read from the build's embedded Expo config, so **rebuild**
     after changing it (`eas build ...`), it does not update over the air by
     itself.
   - While the field is empty, the app keeps its honest "purchases are not
     connected in this build" state: no prices, no purchase success, no
     premium.

## 6. Create the `premium` entitlement

RevenueCat **Entitlements -> New entitlement**: identifier **`premium`**.
The app checks exactly this identifier; any other name will not unlock Pro.

## 7. Attach all three products to the entitlement

In the entitlement's product list, attach all three: `qr_pro_monthly...`,
`qr_pro_yearly...`, `qr_pro_lifetime`. Every product that grants Pro access
must be attached here; products missing from this list will never unlock the
app even after a successful payment.

## 8. Create the offering with the three purchase options

RevenueCat **Offerings -> New offering** (ID `default`, set it current):

1. Add package **Monthly** -> product `qr_pro_monthly:monthly-autorenewing`.
2. Add package **Annual** -> product `qr_pro_yearly:annual-autorenewing`.
3. Add package **Lifetime** -> product `qr_pro_lifetime`.
4. Save. Using the predefined Monthly / Annual / Lifetime package types gives
   the `$rc_monthly` / `$rc_annual` / `$rc_lifetime` package IDs; custom
   package IDs also work because the app keys purchases off package ID, not
   the `$rc_*` names.

The app renders whatever the current offering contains: real localized prices
come from Google Play, the yearly option is highlighted with the "Best value"
label, and missing products simply render fewer (or, if nothing is configured,
an honest "plans are not available" state).

## 9. Configure Google Play licensing and test accounts

1. Play Console -> **Settings -> License testing**: add the Google accounts
   that will test purchases (they must join your test track, step 10).
2. License testers get Google's test purchase flow (test payment methods or
   immediately refunded test orders) instead of real charges.
3. RevenueCat **Project settings -> (your app) -> Customer attribution /
   sandbox**: test purchases from license testers show up as regular
   customers; no extra opt-in is needed.

## 10. Make the product configuration available for testing

1. Upload a build to the **Internal testing** track (from step 11) and publish
   the track.
2. License testers accept the tester invitation and install **from Play**
   (sideloaded builds do not see Play's products).
3. Wait until Google shows the products as active on that track, and until
   RevenueCat has synced them (Products page shows them without errors).
4. Confirm the offering from step 8 is marked current.

## 11. Run an EAS build and install it for purchase testing

```bash
cd qr-generator
npx eas-cli login                 # account with this project (owner)
npx eas-cli build --platform android --profile production
# -> downloadable app-release.aab
```

- The production profile builds an `.aab`; download it, then upload it to the
  Internal testing track in Play Console and let the testers install it.
- Real purchases only work in a Play-installed build with the products
  configured above. `Expo Go` and local debug builds cannot perform real Play
  purchases (the SDK's Expo Go preview mode is a UI preview only).
- Local alternative for smoke checks: `./gradlew bundleRelease` in `android/`
  (see README).

## 12. Promote the verified build to production

1. Internal testing -> promote the same artifact to **Production** track
   (Play Console -> Testing -> Internal testing -> Release -> Promote).
2. Re-verify a couple of purchases on the production-track build.
3. Keep license testers configured for future regression testing.

## Verification checklist (run on a Play-installed test build)

- [ ] Premium sheet shows real localized prices for Monthly, Yearly and
      Lifetime (no "not available" box).
- [ ] Monthly purchase -> "Premium is active on this device." after completion.
- [ ] Yearly purchase (after canceling/expiring the monthly test) -> same, with
      the "Best value" label visible before purchase.
- [ ] Lifetime purchase -> same; Google Play shows it as a one-time product.
- [ ] Cancel at the Play payment sheet -> sheet shows nothing granted, premium
      stays off.
- [ ] Force a pending/deferred payment (if your tester region supports it) ->
      "Waiting for Google Play to confirm the purchase." and no early grant;
      premium appears once Google confirms.
- [ ] Kill and reopen the app -> premium persists (entitlement read at startup).
- [ ] Fresh install -> "Restore purchases" brings premium back.
- [ ] Let a subscription expire (or revoke it from Play/RevenueCat tools) ->
      premium is removed; a lifetime purchase is **not** removed by subscription
      expiry.
- [ ] Airplane mode -> purchase fails with a network error message; premium is
      never granted.

Anything on this checklist that needs the dashboards above is **unverified**
until you run it; unit tests in `src/billing/` only cover the mapping logic
against a mocked SDK.

## What the app does without this configuration

- Free tier works exactly as before (3 saved codes, square style, everything
  else offline).
- The Premium sheet shows an honest "purchases are not connected in this
  build" notice. No prices, no purchase buttons that pretend to work, and no
  premium access can be granted by tapping.
