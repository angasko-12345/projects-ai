import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import type { PurchaseOutcome } from '../billing/types';
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

export function PremiumSheet({ visible, onClose, notify }: PremiumSheetProps) {
  const { colors } = useTheme();
  const { isPremium, offers, busy, purchase, restore } = usePremium();
  const [notice, setNotice] = useState<{ text: string; tone: 'neutral' | 'error' } | null>(null);

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
      setNotice({ text: 'Waiting for Google Play to confirm the purchase.', tone: 'neutral' });
      return;
    }
    setNotice({
      text: outcome.message,
      tone: outcome.status === 'error' ? 'error' : 'neutral',
    });
  };

  const handlePurchase = async (sku: string) => {
    applyOutcome(await purchase(sku));
  };

  const handleRestore = async () => {
    applyOutcome(await restore());
  };

  return (
    <Sheet visible={visible} title="QR Generator Premium" onClose={onClose}>
      <View style={styles.stack}>
        <Text style={[styles.intro, { color: colors.textMuted }]}>
          Unlock custom colors, QR styles, logos, unlimited saves and history. Subscriptions are
          handled by Google Play. The app has no accounts and collects no personal data.
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
        ) : offers.length > 0 ? (
          <View style={styles.offerList}>
            {offers.map((offer) => (
              <Pressable
                key={offer.sku}
                onPress={() => handlePurchase(offer.sku)}
                disabled={busy}
                accessibilityRole="button"
                accessibilityLabel={`Subscribe to ${offer.title} for ${offer.priceLabel}`}
                style={({ pressed }) => [
                  styles.offerButton,
                  {
                    backgroundColor: colors.accent,
                    opacity: busy ? 0.5 : pressed ? 0.85 : 1,
                  },
                ]}
              >
                <Text style={[styles.offerTitle, { color: colors.onAccent }]}>{offer.title}</Text>
                <Text style={[styles.offerPrice, { color: colors.onAccent }]}>
                  {offer.priceLabel} · {offer.periodLabel}
                </Text>
              </Pressable>
            ))}
          </View>
        ) : (
          <View style={[styles.infoBox, { backgroundColor: colors.surfaceMuted }]}>
            <Ionicons name="information-circle-outline" size={18} color={colors.textMuted} />
            <Text style={[styles.infoText, { color: colors.textMuted }]}>
              Plans appear here once Google Play Billing is connected to this build.
            </Text>
          </View>
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
            { borderColor: colors.border, backgroundColor: pressed ? colors.surfaceMuted : colors.surface, opacity: busy ? 0.5 : 1 },
          ]}
        >
          {busy ? (
            <ActivityIndicator size="small" color={colors.text} />
          ) : (
            <Text style={[styles.restoreText, { color: colors.text }]}>Restore purchases</Text>
          )}
        </Pressable>

        <Text style={[styles.footnote, { color: colors.textMuted }]}>
          Cancel anytime in Google Play. Purchases never share your QR content.
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
  offerList: {
    gap: SPACING.sm,
  },
  offerButton: {
    minHeight: MIN_TAP_SIZE,
    borderRadius: RADIUS.md,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
    paddingVertical: SPACING.sm,
  },
  offerTitle: {
    fontSize: TYPE.body,
    fontWeight: '700',
  },
  offerPrice: {
    fontSize: TYPE.caption,
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
