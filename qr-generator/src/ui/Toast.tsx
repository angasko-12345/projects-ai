import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '../theme/ThemeContext';
import { RADIUS, SPACING, TYPE } from '../theme/tokens';

export interface ToastData {
  text: string;
  tone: 'neutral' | 'success' | 'error';
}

export function ToastBanner({ toast }: { toast: ToastData | null }) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  if (!toast) {
    return null;
  }
  const isError = toast.tone === 'error';
  return (
    <View
      pointerEvents="none"
      accessibilityLiveRegion="polite"
      style={[
        styles.banner,
        {
          bottom: insets.bottom + SPACING.lg,
          backgroundColor: isError ? colors.danger : colors.text,
        },
      ]}
    >
      <Text style={[styles.text, { color: isError ? '#FFFFFF' : colors.background }]}>
        {toast.text}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    position: 'absolute',
    left: SPACING.lg,
    right: SPACING.lg,
    maxWidth: 480,
    alignSelf: 'center',
    borderRadius: RADIUS.md,
    paddingVertical: SPACING.md,
    paddingHorizontal: SPACING.lg,
    zIndex: 100,
    elevation: 4,
  },
  text: {
    fontSize: TYPE.label,
    fontWeight: '600',
    textAlign: 'center',
  },
});
