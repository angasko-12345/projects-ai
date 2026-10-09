import { getRevenueCatAndroidApiKey } from './config';
import { RevenueCatBillingProvider } from './revenuecat';
import { UnconnectedBillingProvider } from './unconnected';
import type { BillingProvider } from './types';

/**
 * Single creation point: a valid RevenueCat Google public SDK key in the Expo
 * config enables the real Play Billing provider; without one the app keeps
 * the honest unconnected state (no prices, no purchases, no premium).
 */
export function createBillingProvider(): BillingProvider {
  const apiKey = getRevenueCatAndroidApiKey();
  return apiKey ? new RevenueCatBillingProvider(apiKey) : new UnconnectedBillingProvider();
}
