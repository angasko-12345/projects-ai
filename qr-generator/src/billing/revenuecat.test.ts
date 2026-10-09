import Purchases, { PACKAGE_TYPE, PURCHASES_ERROR_CODE } from 'react-native-purchases';

import { createBillingProvider } from './index';
import { RevenueCatBillingProvider } from './revenuecat';
import type { CustomerInfo, MakePurchaseResult, PurchasesPackage, PurchasesStoreProduct } from 'react-native-purchases';

jest.mock('react-native-purchases', () => {
  const PURCHASES_ERROR_CODE = {
    UNKNOWN_ERROR: '0',
    PURCHASE_CANCELLED_ERROR: '1',
    STORE_PROBLEM_ERROR: '2',
    PURCHASE_NOT_ALLOWED_ERROR: '3',
    PURCHASE_INVALID_ERROR: '4',
    PRODUCT_NOT_AVAILABLE_FOR_PURCHASE_ERROR: '5',
    PRODUCT_ALREADY_PURCHASED_ERROR: '6',
    NETWORK_ERROR: '10',
    INVALID_CREDENTIALS_ERROR: '11',
    OPERATION_ALREADY_IN_PROGRESS_ERROR: '15',
    PAYMENT_PENDING_ERROR: '20',
    CONFIGURATION_ERROR: '23',
    UNSUPPORTED_ERROR: '24',
    PRODUCT_REQUEST_TIMED_OUT_ERROR: '32',
    OFFLINE_CONNECTION_ERROR: '35',
  };
  return {
    __esModule: true,
    default: {
      isConfigured: jest.fn(),
      configure: jest.fn(),
      getOfferings: jest.fn(),
      getCustomerInfo: jest.fn(),
      purchasePackage: jest.fn(),
      restorePurchases: jest.fn(),
      addCustomerInfoUpdateListener: jest.fn(),
      removeCustomerInfoUpdateListener: jest.fn(),
    },
    PURCHASES_ERROR_CODE,
    PACKAGE_TYPE: {
      UNKNOWN: 'UNKNOWN',
      CUSTOM: 'CUSTOM',
      LIFETIME: 'LIFETIME',
      ANNUAL: 'ANNUAL',
      MONTHLY: 'MONTHLY',
    },
    PRODUCT_CATEGORY: {
      NON_SUBSCRIPTION: 'NON_SUBSCRIPTION',
      SUBSCRIPTION: 'SUBSCRIPTION',
      UNKNOWN: 'UNKNOWN',
    },
    PRODUCT_TYPE: {
      AUTO_RENEWABLE_SUBSCRIPTION: 'AUTO_RENEWABLE_SUBSCRIPTION',
    },
  };
});

const mockedPurchases = jest.mocked(Purchases);

interface PackageSeed {
  packageId: string;
  productId: string;
  packageType: PurchasesPackage['packageType'];
  productCategory: string;
  subscriptionPeriod: string | null;
  priceString: string;
  title: string;
}

function makePackage(seed: PackageSeed): PurchasesPackage {
  const product = {
    identifier: seed.productId,
    title: seed.title,
    description: seed.title,
    price: 0,
    priceString: seed.priceString,
    currencyCode: 'USD',
    productCategory: seed.productCategory,
    productType: 'AUTO_RENEWABLE_SUBSCRIPTION',
    subscriptionPeriod: seed.subscriptionPeriod,
    introPrice: null,
    discounts: null,
    pricePerWeek: null,
    pricePerMonth: null,
    pricePerYear: null,
    pricePerWeekString: null,
    pricePerMonthString: null,
    pricePerYearString: null,
    defaultOption: null,
    subscriptionOptions: null,
    presentedOfferingIdentifier: null,
    presentedOfferingContext: null,
    installmentsInfo: null,
  } as unknown as PurchasesStoreProduct;
  return {
    identifier: seed.packageId,
    packageType: seed.packageType,
    product,
    offeringIdentifier: 'default',
    presentedOfferingContext: { offeringIdentifier: 'default', placementIdentifier: null, targetingContext: null },
    webCheckoutUrl: null,
  };
}

const monthlyPackage = makePackage({
  packageId: '$rc_monthly',
  productId: 'qr_pro_monthly:monthly',
  packageType: PACKAGE_TYPE.MONTHLY,
  productCategory: 'SUBSCRIPTION',
  subscriptionPeriod: 'P1M',
  priceString: '$3.99',
  title: 'QR Generator Pro Monthly',
});

const yearlyPackage = makePackage({
  packageId: '$rc_annual',
  productId: 'qr_pro_yearly:yearly',
  packageType: PACKAGE_TYPE.ANNUAL,
  productCategory: 'SUBSCRIPTION',
  subscriptionPeriod: 'P1Y',
  priceString: '$29.99',
  title: 'QR Generator Pro Yearly',
});

const lifetimePackage = makePackage({
  packageId: '$rc_lifetime',
  productId: 'qr_pro_lifetime',
  packageType: PACKAGE_TYPE.LIFETIME,
  productCategory: 'NON_SUBSCRIPTION',
  subscriptionPeriod: null,
  priceString: '$59.99',
  title: 'QR Generator Pro Lifetime',
});

function customerInfoWith(premium: Record<string, unknown> | null): CustomerInfo {
  return {
    entitlements: {
      active: premium ? { premium } : {},
      all: premium ? { premium } : {},
      verification: 'NOT_REQUESTED',
    },
  } as unknown as CustomerInfo;
}

const activePremium = {
  identifier: 'premium',
  isActive: true,
  expirationDate: null,
  productIdentifier: 'qr_pro_lifetime',
  productPlanIdentifier: null,
};

const purchaseResult = (info: CustomerInfo): MakePurchaseResult =>
  ({ productIdentifier: 'qr_pro_monthly:monthly', customerInfo: info, transaction: {} }) as MakePurchaseResult;

const KEY = 'goog_valid_public_key';

describe('RevenueCatBillingProvider', () => {
  let provider: RevenueCatBillingProvider;

  beforeEach(() => {
    provider = new RevenueCatBillingProvider(KEY);
    mockedPurchases.isConfigured.mockResolvedValue(false);
    mockedPurchases.configure.mockReturnValue(undefined);
    mockedPurchases.getOfferings.mockResolvedValue({ all: {}, current: null });
    mockedPurchases.getCustomerInfo.mockResolvedValue(customerInfoWith(null));
    mockedPurchases.purchasePackage.mockResolvedValue(purchaseResult(customerInfoWith(null)));
    mockedPurchases.restorePurchases.mockResolvedValue(customerInfoWith(null));
    mockedPurchases.addCustomerInfoUpdateListener.mockReturnValue(undefined);
    mockedPurchases.removeCustomerInfoUpdateListener.mockReturnValue(true);
  });

  describe('initialization', () => {
    it('configures the SDK with the public key and reports ready', async () => {
      await expect(provider.initialize()).resolves.toBe('ready');
      expect(mockedPurchases.configure).toHaveBeenCalledWith({ apiKey: KEY });
      expect(provider.getConnectionState()).toBe('ready');
    });

    it('does not configure again when the SDK is already configured', async () => {
      mockedPurchases.isConfigured.mockResolvedValue(true);
      await expect(provider.initialize()).resolves.toBe('ready');
      expect(mockedPurchases.configure).not.toHaveBeenCalled();
    });

    it('reports error when the SDK rejects configuration', async () => {
      mockedPurchases.configure.mockImplementation(() => {
        throw new Error('invalid API key');
      });
      await expect(provider.initialize()).resolves.toBe('error');
      expect(provider.getConnectionState()).toBe('error');
    });

    it('reports not_configured when constructed without an API key', async () => {
      const keyless = new RevenueCatBillingProvider('');
      await expect(keyless.initialize()).resolves.toBe('not_configured');
      expect(mockedPurchases.configure).not.toHaveBeenCalled();
    });

    it('never grants premium while billing is unavailable', async () => {
      const keyless = new RevenueCatBillingProvider('');
      await keyless.initialize();
      await expect(keyless.getEntitlement()).resolves.toEqual({ isPremium: false, source: 'none' });
      // SDK failure while configured also resolves to no premium, never true.
      const failing = new RevenueCatBillingProvider(KEY);
      await failing.initialize();
      mockedPurchases.getCustomerInfo.mockRejectedValue(new Error('backend down'));
      await expect(failing.getEntitlement()).resolves.toEqual({ isPremium: false, source: 'none' });
    });
  });

  describe('getOptions', () => {
    it('maps the current offering into monthly, yearly and lifetime options in billing order', async () => {
      await provider.initialize();
      mockedPurchases.getOfferings.mockResolvedValue({
        all: {},
        current: {
          identifier: 'default',
          availablePackages: [lifetimePackage, yearlyPackage, monthlyPackage],
        } as never,
      });

      const options = await provider.getOptions();
      expect(options.map((option) => option.kind)).toEqual(['monthly', 'yearly', 'lifetime']);
      expect(options[0]).toMatchObject({
        packageId: '$rc_monthly',
        productId: 'qr_pro_monthly:monthly',
        priceLabel: '$3.99',
        periodLabel: 'per month',
        billing: 'subscription',
      });
      expect(options[1]).toMatchObject({
        packageId: '$rc_annual',
        priceLabel: '$29.99',
        periodLabel: 'per year',
        billing: 'subscription',
      });
      expect(options[2]).toMatchObject({
        packageId: '$rc_lifetime',
        productId: 'qr_pro_lifetime',
        priceLabel: '$59.99',
        periodLabel: 'One-time purchase',
        billing: 'one_time',
      });
    });

    it('returns an empty list when no current offering exists', async () => {
      await provider.initialize();
      mockedPurchases.getOfferings.mockResolvedValue({ all: {}, current: null });
      await expect(provider.getOptions()).resolves.toEqual([]);
    });

    it('returns an empty list when the SDK is not ready', async () => {
      await expect(provider.getOptions()).resolves.toEqual([]);
      expect(mockedPurchases.getOfferings).not.toHaveBeenCalled();
    });

    it('propagates store failures so the UI can show an error state', async () => {
      await provider.initialize();
      mockedPurchases.getOfferings.mockRejectedValue(new Error('network down'));
      await expect(provider.getOptions()).rejects.toThrow('network down');
    });
  });

  describe('purchase', () => {
    beforeEach(async () => {
      await provider.initialize();
      mockedPurchases.getOfferings.mockResolvedValue({
        all: {},
        current: {
          identifier: 'default',
          availablePackages: [lifetimePackage, yearlyPackage, monthlyPackage],
        } as never,
      });
      await provider.getOptions();
    });

    it.each([
      ['$rc_monthly', 'qr_pro_monthly:monthly'],
      ['$rc_annual', 'qr_pro_yearly:yearly'],
      ['$rc_lifetime', 'qr_pro_lifetime'],
    ])('completes a %s purchase and grants premium', async (packageId, expectedProduct) => {
      mockedPurchases.purchasePackage.mockResolvedValue(
        purchaseResult(customerInfoWith(activePremium)),
      );
      const outcome = await provider.purchase(packageId);
      expect(outcome).toEqual({
        status: 'success',
        entitlement: { isPremium: true, source: 'purchase' },
      });
      const purchased = mockedPurchases.purchasePackage.mock.calls[0][0];
      expect(purchased.identifier).toBe(
        packageId === '$rc_monthly'
          ? '$rc_monthly'
          : packageId === '$rc_annual'
            ? '$rc_annual'
            : '$rc_lifetime',
      );
      expect(purchased.product.identifier).toBe(expectedProduct);
    });

    it('maps user cancellation to cancelled without granting anything', async () => {
      mockedPurchases.purchasePackage.mockRejectedValue({
        code: PURCHASES_ERROR_CODE.PURCHASE_CANCELLED_ERROR,
        message: 'User cancelled',
      });
      await expect(provider.purchase('$rc_monthly')).resolves.toEqual({ status: 'cancelled' });
    });

    it('maps a Play pending order to pending', async () => {
      mockedPurchases.purchasePackage.mockRejectedValue({
        code: PURCHASES_ERROR_CODE.PAYMENT_PENDING_ERROR,
        message: 'Payment pending',
      });
      await expect(provider.purchase('$rc_monthly')).resolves.toEqual({ status: 'pending' });
    });

    it('returns pending when the store accepts payment but the entitlement is not active yet', async () => {
      mockedPurchases.purchasePackage.mockResolvedValue(purchaseResult(customerInfoWith(null)));
      await expect(provider.purchase('$rc_monthly')).resolves.toEqual({ status: 'pending' });
    });

    it('maps network failures to an error', async () => {
      mockedPurchases.purchasePackage.mockRejectedValue({
        code: PURCHASES_ERROR_CODE.NETWORK_ERROR,
        message: 'Network error',
      });
      const outcome = await provider.purchase('$rc_monthly');
      expect(outcome.status).toBe('error');
      if (outcome.status === 'error') {
        expect(outcome.message).toContain('Network problem');
      }
    });

    it('reports unavailable for a package that is not in the current offering', async () => {
      const outcome = await provider.purchase('$rc_removed');
      expect(outcome.status).toBe('unavailable');
      expect(mockedPurchases.purchasePackage).not.toHaveBeenCalled();
    });

    it('rejects a duplicate purchase request while one is in flight', async () => {
      const gate = Promise.withResolvers<MakePurchaseResult>();
      mockedPurchases.purchasePackage.mockImplementation(() => gate.promise);
      const first = provider.purchase('$rc_monthly');
      const second = await provider.purchase('$rc_monthly');
      expect(second).toEqual({
        status: 'error',
        message: 'Another purchase is already in progress.',
      });
      gate.resolve(purchaseResult(customerInfoWith(activePremium)));
      await expect(first).resolves.toMatchObject({ status: 'success' });
      expect(mockedPurchases.purchasePackage).toHaveBeenCalledTimes(1);
    });

    it('reports unavailable when the store no longer sells the product', async () => {
      mockedPurchases.purchasePackage.mockRejectedValue({
        code: PURCHASES_ERROR_CODE.PRODUCT_NOT_AVAILABLE_FOR_PURCHASE_ERROR,
        message: 'Not available',
      });
      const outcome = await provider.purchase('$rc_monthly');
      expect(outcome.status).toBe('unavailable');
    });
  });

  describe('restore', () => {
    it('restores an active entitlement from Google Play', async () => {
      await provider.initialize();
      mockedPurchases.restorePurchases.mockResolvedValue(customerInfoWith(activePremium));
      await expect(provider.restore()).resolves.toEqual({
        status: 'success',
        entitlement: { isPremium: true, source: 'restore' },
      });
    });

    it('reports no purchases when nothing can be restored', async () => {
      await provider.initialize();
      mockedPurchases.restorePurchases.mockResolvedValue(customerInfoWith(null));
      const outcome = await provider.restore();
      expect(outcome.status).toBe('no_purchases');
    });

    it('maps restore network failures to an error', async () => {
      await provider.initialize();
      mockedPurchases.restorePurchases.mockRejectedValue({
        code: PURCHASES_ERROR_CODE.OFFLINE_CONNECTION_ERROR,
        message: 'Offline',
      });
      const outcome = await provider.restore();
      expect(outcome.status).toBe('error');
    });
  });

  describe('entitlement', () => {
    it('reports premium while the entitlement is active', async () => {
      await provider.initialize();
      mockedPurchases.getCustomerInfo.mockResolvedValue(customerInfoWith(activePremium));
      await expect(provider.getEntitlement()).resolves.toEqual({ isPremium: true, source: 'none' });
    });

    it('does not grant premium for an expired subscription', async () => {
      await provider.initialize();
      mockedPurchases.getCustomerInfo.mockResolvedValue({
        entitlements: {
          active: {},
          all: {
            premium: {
              identifier: 'premium',
              isActive: false,
              expirationDate: '2026-09-01T00:00:00Z',
              productIdentifier: 'qr_pro_monthly:monthly',
              productPlanIdentifier: 'monthly',
            },
          },
          verification: 'NOT_REQUESTED',
        },
      } as unknown as CustomerInfo);
      await expect(provider.getEntitlement()).resolves.toEqual({ isPremium: false, source: 'none' });
    });

    it('keeps lifetime access after a subscription expires', async () => {
      await provider.initialize();
      // RevenueCat keeps the non-expiring lifetime grant in `active` even after
      // the subscription that was previously attached to the entitlement ends.
      mockedPurchases.getCustomerInfo.mockResolvedValue(customerInfoWith({
        identifier: 'premium',
        isActive: true,
        expirationDate: null,
        productIdentifier: 'qr_pro_lifetime',
        productPlanIdentifier: null,
      }));
      await expect(provider.getEntitlement()).resolves.toEqual({ isPremium: true, source: 'none' });
    });
  });

  describe('entitlement listener', () => {
    it('forwards entitlement updates and unsubscribes cleanly', async () => {
      await provider.initialize();
      const seen: boolean[] = [];
      const unsubscribe = provider.addEntitlementListener((entitlement) =>
        seen.push(entitlement.isPremium),
      );
      expect(mockedPurchases.addCustomerInfoUpdateListener).toHaveBeenCalledTimes(1);
      const handler = mockedPurchases.addCustomerInfoUpdateListener.mock.calls[0][0];
      handler(customerInfoWith(activePremium));
      handler(customerInfoWith(null));
      expect(seen).toEqual([true, false]);
      unsubscribe();
      expect(mockedPurchases.removeCustomerInfoUpdateListener).toHaveBeenCalledWith(handler);
    });

    it('does nothing before initialization', async () => {
      const unsubscribe = provider.addEntitlementListener(() => undefined);
      expect(mockedPurchases.addCustomerInfoUpdateListener).not.toHaveBeenCalled();
      expect(() => unsubscribe()).not.toThrow();
    });
  });
});

const mockExpoConstants: { expoConfig: { extra?: unknown } | null } = { expoConfig: null };

jest.mock('expo-constants', () => ({
  __esModule: true,
  default: {
    get expoConfig() {
      return mockExpoConstants.expoConfig;
    },
  },
}));

describe('createBillingProvider', () => {
  const setExtra = (extra: unknown) => {
    mockExpoConstants.expoConfig = { extra };
  };

  it('returns the RevenueCat provider for a Google public SDK key', () => {
    setExtra({ revenueCat: { androidPublicKey: ' goog_public_key_123 ' } });
    expect(createBillingProvider().id).toBe('revenuecat');
  });

  it('returns the unconnected provider when no key is configured', () => {
    setExtra({ revenueCat: { androidPublicKey: '' } });
    expect(createBillingProvider().id).toBe('unconnected');
    setExtra({});
    expect(createBillingProvider().id).toBe('unconnected');
  });

  it('refuses to ship anything but a Google public key', () => {
    setExtra({ revenueCat: { androidPublicKey: 'enc_super_secret_rest_key' } });
    expect(createBillingProvider().id).toBe('unconnected');
    setExtra({ revenueCat: { androidPublicKey: 'appl_apple_key' } });
    expect(createBillingProvider().id).toBe('unconnected');
  });

  it('keeps the free tier working without billing', async () => {
    setExtra({});
    const provider = createBillingProvider();
    await expect(provider.getEntitlement()).resolves.toEqual({ isPremium: false, source: 'none' });
    await expect(provider.getOptions()).resolves.toEqual([]);
    await expect(provider.purchase('$rc_monthly')).resolves.toMatchObject({
      status: 'unavailable',
    });
    await expect(provider.restore()).resolves.toMatchObject({ status: 'unavailable' });
  });
});
