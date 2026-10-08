export type BillingConnectionState = 'not_configured' | 'ready' | 'error';

export interface SubscriptionOffer {
  sku: string;
  title: string;
  priceLabel: string;
  periodLabel: string;
}

export interface Entitlement {
  isPremium: boolean;
  source: 'purchase' | 'restore' | 'none';
}

export type PurchaseOutcome =
  | { status: 'success'; entitlement: Entitlement }
  | { status: 'cancelled' }
  | { status: 'pending' }
  | { status: 'unavailable'; message: string }
  | { status: 'error'; message: string };

/**
 * The only billing surface the app talks to. A Google Play Billing adapter
 * implements this interface; no UI or state code changes when one is added.
 */
export interface BillingProvider {
  readonly id: string;
  initialize(): Promise<BillingConnectionState>;
  getConnectionState(): BillingConnectionState;
  getOfferings(): Promise<SubscriptionOffer[]>;
  purchase(sku: string): Promise<PurchaseOutcome>;
  restore(): Promise<PurchaseOutcome>;
  getEntitlement(): Promise<Entitlement>;
}
