import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { PurchaseKind, PurchaseOption, PurchaseOutcome } from '../billing/types';
import { PREMIUM_FEATURES } from '../premium/entitlements';
import { usePremium } from '../premium/PremiumContext';
import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';
import { Sheet } from '../ui/Sheet';
import type { ToastData } from '../ui/Toast';

interface PremiumSheetProps {
  visible: boolean;
  onClose: () => void;
  notify: (text: string, tone?: ToastData['tone']) => void;
}

const PLAN_LABELS: Record<PurchaseKind, string> = {
  monthly: 'Monthly Pro',
  yearly: 'Yearly Pro',
  lifetime: 'Lifetime Pro',
  other: '',
};

export function PremiumSheet({ visible, onClose, notify }: PremiumSheetProps) {
  const { colors } = useTheme();
  const {
    isPremium,
    connectionState,
    options,
    loading,
    loadError,
    busy,
    purchase,
    restore,
    refresh,
  } = usePremium();
  const [notice, setNotice] = useState<{ text: string; tone: 'neutral' | 'error' } | null>(null);
  const [pendingPackage, setPendingPackage] = useState<string | null>(null);
  const [wasVisible, setWasVisible] = useState(visible);

  // Clear stale outcome feedback when the sheet is (re)opened, adjusting state
  // during render instead of in an effect.
  if (visible !== wasVisible) {
    setWasVisible(visible);
    setNotice(null);
  }

  useEffect(() => {
    if (visible) {
      refresh();
    }
  }, [visible, refresh]);

  const applyOutcome = (outcome: PurchaseOutcome) => {
    if (outcome.status === 'success') {
      notify('Premium unlocked.', 'success');
      onClose();
      return;
    }
    if (outcome.status === 'cancelled') {
      setNotice(null);
      return;
    }
    if (outcome.status === 'pending') {
      setNotice({
        text: 'Waiting for Google Play to confirm the purchase. Premium unlocks once the payment is confirmed.',
        tone: 'neutral',
      });
      return;
    }
    if (outcome.status === 'no_purchases' || outcome.status === 'unavailable') {
      setNotice({ text: outcome.message, tone: 'neutral' });
      return;
    }
    setNotice({ text: outcome.message, tone: 'error' });
  };

  const handlePurchase = async (packageId: string) => {
    setPendingPackage(packageId);
    try {
      applyOutcome(await purchase(packageId));
    } finally {
      setPendingPackage(null);
    }
  };

  const handleRestore = async () => {
    applyOutcome(await restore());
  };

  const renderOption = (option: PurchaseOption) => {
    const highlighted = option.kind === 'yearly';
    const displayName = option.kind === 'other' ? option.title : PLAN_LABELS[option.kind];
    const renewal =
      option.billing === 'one_time'
        ? 'No recurring charges.'
        : option.kind === 'monthly'
          ? 'Renews every month until canceled. Cancel anytime in Google Play.'
          : option.kind === 'yearly'
            ? 'Renews every year until canceled. Cancel anytime in Google Play.'
            : 'Auto-renews until canceled. Cancel anytime in Google Play.';
    const action =
      option.kind === 'monthly'
        ? 'Subscribe monthly'
        : option.kind === 'yearly'
          ? 'Subscribe yearly'
          : option.billing === 'one_time'
            ? 'Buy lifetime'
            : 'Subscribe';
    const isPending = pendingPackage === option.packageId;

    return (
      <View
        key={option.packageId}
        style={[
          styles.optionCard,
          {
            backgroundColor: colors.surface,
            borderColor: highlighted ? colors.accent : colors.border,
            borderWidth: highlighted ? 2 : 1,
          },
        ]}
      >
        <View style={styles.optionHeader}>
          <Text style={[styles.optionTitle, { color: colors.text }]}>{displayName}</Text>
          {highlighted ? (
            <View style={[styles.bestValue, { backgroundColor: colors.accent }]}>
              <Text style={[styles.bestValueText, { color: colors.onAccent }]}>Best value</Text>
            </View>
          ) : null}
        </View>
        <View style={styles.priceRow}>
          <Text style={[styles.price, { color: colors.text }]}>{option.priceLabel}</Text>
          <Text style={[styles.period, { color: colors.textMuted }]}>{option.periodLabel}</Text>
        </View>
        <Text style={[styles.renewal, { color: colors.textMuted }]}>{renewal}</Text>
        <Pressable
          onPress={() => handlePurchase(option.packageId)}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel={`${action} ${displayName} for ${option.priceLabel} (${option.periodLabel})`}
          accessibilityHint={renewal}
          style={({ pressed }) => [
            styles.optionButton,
            highlighted
              ? { backgroundColor: colors.accent, borderColor: colors.accent }
              : { backgroundColor: pressed ? colors.surfaceMuted : colors.surface, borderColor: colors.border },
            { opacity: busy ? 0.5 : pressed ? 0.85 : 1 },
          ]}
        >
          {isPending ? (
            <ActivityIndicator size="small" color={highlighted ? colors.onAccent : colors.text} />
          ) : (
            <Text
              style={[
                styles.optionButtonText,
                { color: highlighted ? colors.onAccent : colors.text },
              ]}
            >
              {action}
            </Text>
          )}
        </Pressable>
      </View>
    );
  };

  const renderUnavailable = () => {
    if (loading && options.length === 0) {
      return (
        <View style={[styles.infoBox, { backgroundColor: colors.surfaceMuted }]}>
          <ActivityIndicator size="small" color={colors.textMuted} />
          <Text style={[styles.infoText, { color: colors.textMuted }]}>
            Loading plans from Google Play...
          </Text>
        </View>
      );
    }
    if (connectionState === 'not_configured') {
      return (
        <View style={[styles.infoBox, { backgroundColor: colors.surfaceMuted }]}>
          <Ionicons name="information-circle-outline" size={18} color={colors.textMuted} />
          <Text style={[styles.infoText, { color: colors.textMuted }]}>
            Purchases are not connected in this build. Every free feature keeps working.
          </Text>
        </View>
      );
    }
    if (connectionState === 'error') {
      return (
        <View style={[styles.infoBox, { backgroundColor: colors.dangerSoft }]}>
          <Ionicons name="alert-circle-outline" size={18} color={colors.danger} />
          <Text style={[styles.infoText, { color: colors.danger }]}>
            Billing could not be started. Please try again later.
          </Text>
        </View>
      );
    }
    if (loadError) {
      return (
        <View style={[styles.infoBox, { backgroundColor: colors.dangerSoft }]}>
          <Ionicons name="alert-circle-outline" size={18} color={colors.danger} />
          <Text style={[styles.infoText, { color: colors.danger }]}>{loadError}</Text>
        </View>
      );
    }
    if (connectionState === 'ready') {
      return (
        <View style={[styles.infoBox, { backgroundColor: colors.surfaceMuted }]}>
          <Ionicons name="information-circle-outline" size={18} color={colors.textMuted} />
          <Text style={[styles.infoText, { color: colors.textMuted }]}>
            Plans are not available right now. They will appear here once the products for this
            app are configured in Google Play.
          </Text>
        </View>
      );
    }
    return null;
  };

  return (
    <Sheet visible={visible} title="QR Generator Premium" onClose={onClose}>
      <View style={styles.stack}>
        <Text style={[styles.intro, { color: colors.textMuted }]}>
          Unlock custom colors, QR styles, logos, unlimited saves and history. Purchases are
          handled by Google Play. The app has no accounts and never sees your QR content.
        </Text>

        <View style={styles.featureList}>
          {PREMIUM_FEATURES.map((feature) => (
            <View key={feature.id} style={styles.featureRow}>
              <Ionicons name="checkmark-circle" size={20} color={colors.accent} />
              <View style={styles.featureText}>
                <Text style={[styles.featureTitle, { color: colors.text }]}>{feature.title}</Text>
                <Text style={[styles.featureDetail, { color: colors.textMuted }]}>
                  {feature.detail}
                </Text>
              </View>
            </View>
          ))}
        </View>

        {isPremium ? (
          <View style={[styles.infoBox, { backgroundColor: colors.accentSoft }]}>
            <Ionicons name="star" size={18} color={colors.accent} />
            <Text style={[styles.infoText, { color: colors.text }]}>
              Premium is active on this device.
            </Text>
          </View>
        ) : options.length > 0 ? (
          <View style={styles.optionList}>{options.map(renderOption)}</View>
        ) : (
          renderUnavailable()
        )}

        {notice ? (
          <View
            style={[
              styles.infoBox,
              {
                backgroundColor: notice.tone === 'error' ? colors.dangerSoft : colors.surfaceMuted,
              },
            ]}
          >
            <Ionicons
              name={notice.tone === 'error' ? 'alert-circle-outline' : 'information-circle-outline'}
              size={18}
              color={notice.tone === 'error' ? colors.danger : colors.textMuted}
            />
            <Text
              style={[
                styles.infoText,
                { color: notice.tone === 'error' ? colors.danger : colors.textMuted },
              ]}
            >
              {notice.text}
            </Text>
          </View>
        ) : null}

        <Pressable
          onPress={handleRestore}
          disabled={busy}
          accessibilityRole="button"
          accessibilityLabel="Restore purchases"
          style={({ pressed }) => [
            styles.restoreButton,
            {
              borderColor: colors.border,
              backgroundColor: pressed ? colors.surfaceMuted : colors.surface,
              opacity: busy ? 0.5 : 1,
            },
          ]}
        >
          {busy && !pendingPackage ? (
            <ActivityIndicator size="small" color={colors.text} />
          ) : (
            <Text style={[styles.restoreText, { color: colors.text }]}>Restore purchases</Text>
          )}
        </Pressable>

        <Text style={[styles.footnote, { color: colors.textMuted }]}>
          Subscriptions renew until canceled. Manage or cancel anytime in Google Play. Purchases
          never share your QR content.
        </Text>
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  stack: {
    gap: SPACING.lg,
  },
  intro: {
    fontSize: TYPE.label,
    lineHeight: 20,
  },
  featureList: {
    gap: SPACING.md,
  },
  featureRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: SPACING.sm,
  },
  featureText: {
    flex: 1,
    gap: 2,
  },
  featureTitle: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  featureDetail: {
    fontSize: TYPE.caption,
  },
  infoBox: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: SPACING.sm,
    padding: SPACING.md,
    borderRadius: RADIUS.md,
  },
  infoText: {
    flex: 1,
    fontSize: TYPE.caption,
  },
  optionList: {
    gap: SPACING.sm,
  },
  optionCard: {
    borderRadius: RADIUS.lg,
    padding: SPACING.md,
    gap: SPACING.sm,
  },
  optionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
  },
  optionTitle: {
    fontSize: TYPE.heading,
    fontWeight: '700',
    flexShrink: 1,
  },
  bestValue: {
    borderRadius: RADIUS.sm,
    paddingHorizontal: SPACING.sm,
    paddingVertical: 2,
  },
  bestValueText: {
    fontSize: TYPE.caption,
    fontWeight: '700',
  },
  priceRow: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: SPACING.sm,
  },
  price: {
    fontSize: TYPE.heading,
    fontWeight: '700',
  },
  period: {
    fontSize: TYPE.caption,
  },
  renewal: {
    fontSize: TYPE.caption,
    lineHeight: 18,
  },
  optionButton: {
    minHeight: MIN_TAP_SIZE,
    marginTop: SPACING.xs,
    borderRadius: RADIUS.md,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: SPACING.md,
  },
  optionButtonText: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  restoreButton: {
    minHeight: MIN_TAP_SIZE,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: RADIUS.md,
  },
  restoreText: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  footnote: {
    fontSize: TYPE.caption,
    textAlign: 'center',
  },
});
