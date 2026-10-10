import Constants from 'expo-constants';

import type { PurchaseOutcome } from '../billing/types';

/**
 * Beta build flag, read from the resolved Expo config: `app.config.js`
 * computes `expo.extra.beta.enabled` from `BETA_BUILD=1` on top of the
 * `app.json` default.
 *
 * A beta build grants every premium feature for free and never offers a
 * purchase: no store is contacted, no price is shown and no transaction can be
 * started. The RevenueCat implementation in `src/billing/` is untouched, so
 * setting this flag back to `false` restores the normal production monetization
 * path (RevenueCat key + Google Play products) with no code change.
 *
 * The default argument is the app config the `expo-constants` gradle script
 * embeds into the APK on every build, so the flag is baked in at build time.
 * Passing the config explicitly keeps this a pure, testable read.
 */
export function isBetaBuildEnabled(
  config: { extra?: unknown } | null | undefined = Constants.expoConfig,
): boolean {
  const extra = config?.extra as { beta?: { enabled?: unknown } } | undefined;
  return extra?.beta?.enabled === true;
}

/**
 * The only answer a beta build ever gives to a purchase or restore request.
 * It is deliberately `unavailable`, never `success`: a beta build must not
 * pretend a payment happened.
 */
export function betaPurchaseDisabledOutcome(): PurchaseOutcome {
  return { status: 'unavailable', message: PURCHASES_DISABLED_MESSAGE };
}

export const PURCHASES_DISABLED_MESSAGE =
  'Purchases are disabled in this beta build. Every premium feature is already unlocked.';

export const BETA_INTRO =
  'This is a free beta build. Every premium feature is unlocked for beta testers, and no purchase is offered here. The app still has no accounts and never sends your QR content anywhere.';

export const BETA_ACTIVE_MESSAGE =
  'Beta build: premium features are unlocked for you at no cost. Purchases are turned off in this build.';

export const BETA_FOOTNOTE =
  'This beta build does not include purchases. Your QR content never leaves your device.';
