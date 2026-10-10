import Purchases, {
  PACKAGE_TYPE,
  PRODUCT_CATEGORY,
  PURCHASES_ERROR_CODE,
  type CustomerInfo,
  type PurchasesError,
  type PurchasesPackage,
} from 'react-native-purchases';

import type {
  BillingConnectionState,
  BillingProvider,
  Entitlement,
  PurchaseKind,
  PurchaseOption,
  PurchaseOutcome,
} from './types';

export const PREMIUM_ENTITLEMENT_ID = 'premium';

const PURCHASES_NOT_READY_MESSAGE = 'Purchases are unavailable right now. Close this screen and try again.';

const OFFER_UNAVAILABLE_MESSAGE = 'This option is not available from Google Play right now.';

/** ISO 8601 billing periods we show verbatim; anything else gets a generic label. */
const PERIOD_LABELS: Record<string, string> = {
  P1W: 'per week',
  P1M: 'per month',
  P3M: 'per 3 months',
  P6M: 'per 6 months',
  P1Y: 'per year',
};

const KIND_ORDER: Record<PurchaseKind, number> = { monthly: 0, yearly: 1, lifetime: 2, other: 3 };

function kindOf(pkg: PurchasesPackage): PurchaseKind {
  const product = pkg.product;
  if (
    pkg.packageType === PACKAGE_TYPE.LIFETIME ||
    product.productCategory === PRODUCT_CATEGORY.NON_SUBSCRIPTION
  ) {
    return 'lifetime';
  }
  if (pkg.packageType === PACKAGE_TYPE.MONTHLY || product.subscriptionPeriod === 'P1M') {
    return 'monthly';
  }
  if (pkg.packageType === PACKAGE_TYPE.ANNUAL || product.subscriptionPeriod === 'P1Y') {
    return 'yearly';
  }
  return 'other';
}

function entitlementOf(customerInfo: CustomerInfo): Entitlement {
  const premium = customerInfo.entitlements.active[PREMIUM_ENTITLEMENT_ID];
  return { isPremium: Boolean(premium && premium.isActive), source: 'none' };
}

function mapSdkError(error: unknown, fallback: string): PurchaseOutcome {
  const code = (error as Partial<PurchasesError> | null | undefined)?.code;
  switch (code) {
    case PURCHASES_ERROR_CODE.PURCHASE_CANCELLED_ERROR:
      return { status: 'cancelled' };
    case PURCHASES_ERROR_CODE.PAYMENT_PENDING_ERROR:
      return { status: 'pending' };
    case PURCHASES_ERROR_CODE.PRODUCT_NOT_AVAILABLE_FOR_PURCHASE_ERROR:
    case PURCHASES_ERROR_CODE.UNSUPPORTED_ERROR:
      return { status: 'unavailable', message: OFFER_UNAVAILABLE_MESSAGE };
    case PURCHASES_ERROR_CODE.PRODUCT_ALREADY_PURCHASED_ERROR:
      return {
        status: 'error',
        message: 'This purchase is already owned on your Google Play account. Use Restore purchases.',
      };
    case PURCHASES_ERROR_CODE.NETWORK_ERROR:
    case PURCHASES_ERROR_CODE.OFFLINE_CONNECTION_ERROR:
    case PURCHASES_ERROR_CODE.PRODUCT_REQUEST_TIMED_OUT_ERROR:
      return { status: 'error', message: 'Network problem. Check your connection and try again.' };
    case PURCHASES_ERROR_CODE.STORE_PROBLEM_ERROR:
      return { status: 'error', message: 'Google Play reported a problem. Please try again later.' };
    case PURCHASES_ERROR_CODE.INVALID_CREDENTIALS_ERROR:
    case PURCHASES_ERROR_CODE.CONFIGURATION_ERROR:
      return { status: 'unavailable', message: 'Purchases are not set up correctly for this build.' };
    case PURCHASES_ERROR_CODE.OPERATION_ALREADY_IN_PROGRESS_ERROR:
      return { status: 'error', message: 'Another purchase is already in progress.' };
    default:
      return { status: 'error', message: fallback };
  }
}

/**
 * RevenueCat-backed Play Billing provider. All Pro access flows through the
 * `premium` entitlement: Google Play handles payment, RevenueCat resolves the
 * entitlement, and this class only maps store state onto the app's
 * `BillingProvider` surface. A billing failure can only ever leave the user
 * without premium, never with it.
 */
export class RevenueCatBillingProvider implements BillingProvider {
  readonly id = 'revenuecat';

  private connectionState: BillingConnectionState = 'not_configured';
  private readonly packages = new Map<string, PurchasesPackage>();
  private purchaseInFlight = false;

  constructor(private readonly apiKey: string) {}

  async initialize(): Promise<BillingConnectionState> {
    if (!this.apiKey) {
      this.connectionState = 'not_configured';
      return this.connectionState;
    }
    try {
      if (!(await Purchases.isConfigured())) {
        Purchases.configure({ apiKey: this.apiKey });
      }
      this.connectionState = 'ready';
    } catch {
      this.connectionState = 'error';
    }
    return this.connectionState;
  }

  getConnectionState(): BillingConnectionState {
    return this.connectionState;
  }

  async getOptions(): Promise<PurchaseOption[]> {
    if (this.connectionState !== 'ready') {
      return [];
    }
    const offerings = await Purchases.getOfferings();
    this.packages.clear();
    const offering = offerings.current;
    if (!offering) {
      return [];
    }
    const options = offering.availablePackages.map((pkg): PurchaseOption => {
      const kind = kindOf(pkg);
      const isOneTime = pkg.product.productCategory === PRODUCT_CATEGORY.NON_SUBSCRIPTION;
      return {
        packageId: pkg.identifier,
        productId: pkg.product.identifier,
        title: pkg.product.title,
        priceLabel: pkg.product.priceString,
        periodLabel: isOneTime
          ? 'One-time purchase'
          : (pkg.product.subscriptionPeriod
              ? PERIOD_LABELS[pkg.product.subscriptionPeriod]
              : undefined) ?? 'per billing period',
        kind,
        billing: isOneTime ? 'one_time' : 'subscription',
      };
    });
    for (const pkg of offering.availablePackages) {
      this.packages.set(pkg.identifier, pkg);
    }
    return options.sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]);
  }

  async purchase(packageId: string): Promise<PurchaseOutcome> {
    if (this.connectionState !== 'ready') {
      return { status: 'unavailable', message: PURCHASES_NOT_READY_MESSAGE };
    }
    if (this.purchaseInFlight) {
      return { status: 'error', message: 'Another purchase is already in progress.' };
    }
    let pkg = this.packages.get(packageId);
    if (!pkg) {
      try {
        await this.getOptions();
      } catch {
        // Falls through to the availability check below.
      }
      pkg = this.packages.get(packageId);
    }
    if (!pkg) {
      return { status: 'unavailable', message: OFFER_UNAVAILABLE_MESSAGE };
    }
    this.purchaseInFlight = true;
    try {
      const result = await Purchases.purchasePackage(pkg);
      const entitlement = entitlementOf(result.customerInfo);
      if (entitlement.isPremium) {
        return { status: 'success', entitlement: { ...entitlement, source: 'purchase' } };
      }
      // Store accepted the payment but the entitlement is not active yet
      // (Play pending order or delayed provisioning); never grant early.
      return { status: 'pending' };
    } catch (error) {
      return mapSdkError(error, 'The purchase could not be completed. Please try again.');
    } finally {
      this.purchaseInFlight = false;
    }
  }

  async restore(): Promise<PurchaseOutcome> {
    if (this.connectionState !== 'ready') {
      return { status: 'unavailable', message: PURCHASES_NOT_READY_MESSAGE };
    }
    try {
      const customerInfo = await Purchases.restorePurchases();
      const entitlement = entitlementOf(customerInfo);
      if (entitlement.isPremium) {
        return { status: 'success', entitlement: { ...entitlement, source: 'restore' } };
      }
      return {
        status: 'no_purchases',
        message: 'No active purchases were found for this Google Play account.',
      };
    } catch (error) {
      return mapSdkError(error, 'Purchases could not be restored. Please try again.');
    }
  }

  async getEntitlement(): Promise<Entitlement> {
    if (this.connectionState !== 'ready') {
      return { isPremium: false, source: 'none' };
    }
    // Read failures propagate as rejections: a failed read must never be
    // turned into an answer, so no caller can mistake failure for "no premium".
    return entitlementOf(await Purchases.getCustomerInfo());
  }

  addEntitlementListener(listener: (entitlement: Entitlement) => void): () => void {
    if (this.connectionState !== 'ready') {
      return () => undefined;
    }
    const handler = (customerInfo: CustomerInfo) => listener(entitlementOf(customerInfo));
    Purchases.addCustomerInfoUpdateListener(handler);
    return () => {
      Purchases.removeCustomerInfoUpdateListener(handler);
    };
  }
}
