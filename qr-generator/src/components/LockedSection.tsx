import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useTheme } from '../theme/ThemeContext';
import { RADIUS, SPACING, TYPE } from '../theme/tokens';

interface LockedSectionProps {
  title: string;
  detail: string;
  onUnlock: () => void;
  children?: React.ReactNode;
}

/** Shows premium controls greyed out so free users see exactly what they get. */
export function LockedSection({ title, detail, onUnlock, children }: LockedSectionProps) {
  const { colors } = useTheme();
  return (
    <View
      style={[
        styles.container,
        { borderColor: colors.border, backgroundColor: colors.surfaceMuted },
      ]}
    >
      <View style={styles.headerRow}>
        <View style={styles.textColumn}>
          <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
          <Text style={[styles.detail, { color: colors.textMuted }]}>{detail}</Text>
        </View>
        <View style={[styles.badge, { backgroundColor: colors.accentSoft }]}>
          <Ionicons name="lock-closed" size={12} color={colors.accent} />
          <Text style={[styles.badgeText, { color: colors.accent }]}>Premium</Text>
        </View>
      </View>
      {children ? (
        <View
          style={styles.preview}
          pointerEvents="none"
          importantForAccessibility="no-hide-descendants"
          accessibilityElementsHidden
        >
          {children}
        </View>
      ) : null}
      <Pressable
        onPress={onUnlock}
        accessibilityRole="button"
        accessibilityLabel={`Unlock ${title}`}
        style={({ pressed }) => [styles.unlock, { backgroundColor: colors.accent, opacity: pressed ? 0.8 : 1 }]}
      >
        <Ionicons name="star" size={16} color={colors.onAccent} />
        <Text style={[styles.unlockText, { color: colors.onAccent }]}>See premium plans</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    borderWidth: 1,
    borderRadius: RADIUS.md,
    padding: SPACING.md,
    gap: SPACING.md,
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: SPACING.sm,
  },
  textColumn: {
    flex: 1,
    gap: 2,
  },
  title: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  detail: {
    fontSize: TYPE.caption,
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: SPACING.sm,
    paddingVertical: 4,
    borderRadius: RADIUS.pill,
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '700',
  },
  preview: {
    opacity: 0.45,
  },
  unlock: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: SPACING.sm,
    borderRadius: RADIUS.md,
  },
  unlockText: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
});
