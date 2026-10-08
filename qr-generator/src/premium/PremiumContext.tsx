import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { createBillingProvider } from '../billing/unconnected';
import type {
  BillingConnectionState,
  PurchaseOutcome,
  SubscriptionOffer,
} from '../billing/types';

interface PremiumContextValue {
  isPremium: boolean;
  connectionState: BillingConnectionState;
  offers: SubscriptionOffer[];
  busy: boolean;
  purchase: (sku: string) => Promise<PurchaseOutcome>;
  restore: () => Promise<PurchaseOutcome>;
}

const PremiumContext = createContext<PremiumContextValue | null>(null);

export function PremiumProvider({ children }: { children: React.ReactNode }) {
  const [provider] = useState(createBillingProvider);
  const [isPremium, setIsPremium] = useState(false);
  const [connectionState, setConnectionState] = useState<BillingConnectionState>('not_configured');
  const [offers, setOffers] = useState<SubscriptionOffer[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    provider
      .initialize()
      .then(async (state) => {
        const [entitlement, offerings] = await Promise.all([
          provider.getEntitlement(),
          provider.getOfferings(),
        ]);
        if (!cancelled) {
          setConnectionState(state);
          setIsPremium(entitlement.isPremium);
          setOffers(offerings);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setConnectionState('error');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [provider]);

  const applyOutcome = useCallback((outcome: PurchaseOutcome) => {
    if (outcome.status === 'success') {
      setIsPremium(outcome.entitlement.isPremium);
    }
    return outcome;
  }, []);

  const purchase = useCallback(
    async (sku: string) => {
      setBusy(true);
      try {
        return applyOutcome(await provider.purchase(sku));
      } finally {
        setBusy(false);
      }
    },
    [provider, applyOutcome],
  );

  const restore = useCallback(async () => {
    setBusy(true);
    try {
      return applyOutcome(await provider.restore());
    } finally {
      setBusy(false);
    }
  }, [provider, applyOutcome]);

  const value = useMemo<PremiumContextValue>(
    () => ({ isPremium, connectionState, offers, busy, purchase, restore }),
    [isPremium, connectionState, offers, busy, purchase, restore],
  );

  return <PremiumContext.Provider value={value}>{children}</PremiumContext.Provider>;
}

export function usePremium(): PremiumContextValue {
  const value = useContext(PremiumContext);
  if (!value) {
    throw new Error('usePremium must be used inside PremiumProvider');
  }
  return value;
}
