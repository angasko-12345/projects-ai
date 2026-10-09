export type BillingConnectionState = 'not_configured' | 'ready' | 'error';

/**
 * What the option bills for. Derived from store data, never hardcoded:
 * `monthly`/`yearly` are auto-renewing subscriptions, `lifetime` is a
 * one-time purchase, `other` is any other configured period.
 */
export type PurchaseKind = 'monthly' | 'yearly' | 'lifetime' | 'other';

export interface PurchaseOption {
  /** RevenueCat package identifier within the current offering; passed to `purchase()`. */
  packageId: string;
  /** Store product identifier (Google Play subscriptions include `:basePlanId`). */
  productId: string;
  /** Product title as reported by the store. */
  title: string;
  /** Localized price from the store, e.g. "$4.99". Never hardcoded. */
  priceLabel: string;
  /** Billing disclosure: "per month", "per year", or "One-time purchase". */
  periodLabel: string;
  kind: PurchaseKind;
  billing: 'subscription' | 'one_time';
}

export interface Entitlement {
  isPremium: boolean;
  source: 'purchase' | 'restore' | 'none';
}

export type PurchaseOutcome =
  | { status: 'success'; entitlement: Entitlement }
  | { status: 'cancelled' }
  | { status: 'pending' }
  | { status: 'no_purchases'; message: string }
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
  /** Load available purchase options; rejects when the store cannot be reached. */
  getOptions(): Promise<PurchaseOption[]>;
  purchase(packageId: string): Promise<PurchaseOutcome>;
  restore(): Promise<PurchaseOutcome>;
  getEntitlement(): Promise<Entitlement>;
  /**
   * Subscribe to entitlement changes (purchase confirmed later, renewal,
   * cancellation, restart). Returns an unsubscribe function.
   */
  addEntitlementListener(listener: (entitlement: Entitlement) => void): () => void;
}
