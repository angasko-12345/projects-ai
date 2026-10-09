import type {
  BillingConnectionState,
  BillingProvider,
  Entitlement,
  PurchaseOption,
  PurchaseOutcome,
} from './types';

export const BILLING_NOT_CONNECTED_MESSAGE =
  'Purchases are not connected in this build. Connect Google Play Billing to this app to enable purchases.';

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

  async getOptions(): Promise<PurchaseOption[]> {
    return [];
  }

  async purchase(_packageId: string): Promise<PurchaseOutcome> {
    return { status: 'unavailable', message: BILLING_NOT_CONNECTED_MESSAGE };
  }

  async restore(): Promise<PurchaseOutcome> {
    return { status: 'unavailable', message: BILLING_NOT_CONNECTED_MESSAGE };
  }

  async getEntitlement(): Promise<Entitlement> {
    return { isPremium: false, source: 'none' };
  }

  addEntitlementListener(_listener: (entitlement: Entitlement) => void): () => void {
    return () => undefined;
  }
}
