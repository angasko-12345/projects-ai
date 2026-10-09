import Constants from 'expo-constants';

/**
 * RevenueCat public SDK key for the Google Play app, read from the Expo
 * config (`app.json` -> `expo.extra.revenueCat.androidPublicKey`).
 *
 * Only Google public keys (`goog_...`) are accepted: a secret REST key
 * (`enc_...`) must never ship inside an app bundle, so anything that is not
 * a Google public key is treated as "not configured".
 */
export function getRevenueCatAndroidApiKey(): string | null {
  const extra = Constants.expoConfig?.extra as
    | { revenueCat?: { androidPublicKey?: unknown } }
    | undefined;
  const raw = extra?.revenueCat?.androidPublicKey;
  if (typeof raw !== 'string') {
    return null;
  }
  const trimmed = raw.trim();
  if (!/^goog_[A-Za-z0-9_-]+$/.test(trimmed)) {
    return null;
  }
  return trimmed;
}
