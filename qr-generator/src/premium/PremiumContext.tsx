import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { createBillingProvider } from '../billing';
import type {
  BillingConnectionState,
  Entitlement,
  PurchaseOption,
  PurchaseOutcome,
} from '../billing/types';

interface PremiumContextValue {
  isPremium: boolean;
  connectionState: BillingConnectionState;
  options: PurchaseOption[];
  loading: boolean;
  loadError: string | null;
  busy: boolean;
  purchase: (packageId: string) => Promise<PurchaseOutcome>;
  restore: () => Promise<PurchaseOutcome>;
  refresh: () => Promise<void>;
}

const PremiumContext = createContext<PremiumContextValue | null>(null);

const LOAD_ERROR_MESSAGE = 'Plans could not be loaded. Check your connection and try again.';

export function PremiumProvider({ children }: { children: React.ReactNode }) {
  const [provider] = useState(createBillingProvider);
  const [isPremium, setIsPremium] = useState(false);
  const [connectionState, setConnectionState] = useState<BillingConnectionState>('not_configured');
  const [options, setOptions] = useState<PurchaseOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const unsubscribeRef = useRef<(() => void) | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [entitlement, storeOptions] = await Promise.allSettled([
        provider.getEntitlement(),
        provider.getOptions(),
      ]);
      // A failed entitlement read never grants premium: it just leaves the
      // current state untouched.
      if (entitlement.status === 'fulfilled') {
        setIsPremium(entitlement.value.isPremium);
      }
      if (storeOptions.status === 'fulfilled') {
        setOptions(storeOptions.value);
        setLoadError(null);
      } else {
        // Keep any previously loaded options; only report that a refresh failed.
        setLoadError(LOAD_ERROR_MESSAGE);
      }
    } finally {
      setLoading(false);
    }
  }, [provider]);

  useEffect(() => {
    let cancelled = false;
    const applyEntitlement = (entitlement: Entitlement) => {
      if (!cancelled) {
        setIsPremium(entitlement.isPremium);
      }
    };
    (async () => {
      const state = await provider.initialize().catch(() => 'error' as const);
      if (cancelled) {
        return;
      }
      setConnectionState(state);
      unsubscribeRef.current = provider.addEntitlementListener(applyEntitlement);
      await refresh();
    })();
    return () => {
      cancelled = true;
      unsubscribeRef.current?.();
      unsubscribeRef.current = null;
    };
  }, [provider, refresh]);

  const applyOutcome = useCallback((outcome: PurchaseOutcome) => {
    if (outcome.status === 'success') {
      setIsPremium(outcome.entitlement.isPremium);
    }
    return outcome;
  }, []);

  const purchase = useCallback(
    async (packageId: string) => {
      setBusy(true);
      try {
        return applyOutcome(await provider.purchase(packageId));
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
    () => ({ isPremium, connectionState, options, loading, loadError, busy, purchase, restore, refresh }),
    [isPremium, connectionState, options, loading, loadError, busy, purchase, restore, refresh],
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
