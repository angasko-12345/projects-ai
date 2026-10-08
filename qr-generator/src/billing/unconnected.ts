import type {
  BillingConnectionState,
  BillingProvider,
  Entitlement,
  PurchaseOutcome,
  SubscriptionOffer,
} from './types';

export const BILLING_NOT_CONNECTED_MESSAGE =
  'Subscriptions are not connected in this build. Connect Google Play Billing to this app to enable purchases.';

/**
 * Honest stand-in while Play Billing is not wired up: it never reports a
 * purchase as successful and never invents prices. The app stays fully usable
 * without premium.
 */
export class UnconnectedBillingProvider implements BillingProvider {
  readonly id = 'unconnected';

  private connectionState: BillingConnectionState = 'not_configured';

  async initialize(): Promise<BillingConnectionState> {
    this.connectionState = 'not_configured';
    return this.connectionState;
  }

  getConnectionState(): BillingConnectionState {
    return this.connectionState;
  }

  async getOfferings(): Promise<SubscriptionOffer[]> {
    return [];
  }

  async purchase(_sku: string): Promise<PurchaseOutcome> {
    return { status: 'unavailable', message: BILLING_NOT_CONNECTED_MESSAGE };
  }

  async restore(): Promise<PurchaseOutcome> {
    return { status: 'unavailable', message: BILLING_NOT_CONNECTED_MESSAGE };
  }

  async getEntitlement(): Promise<Entitlement> {
    return { isPremium: false, source: 'none' };
  }
}

// Single creation point: return a Play Billing adapter here to enable purchases.
export function createBillingProvider(): BillingProvider {
  return new UnconnectedBillingProvider();
}
