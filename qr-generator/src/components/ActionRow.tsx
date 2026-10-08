import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';

interface ActionRowProps {
  canExport: boolean;
  busy: boolean;
  onSave: () => void;
  onShare: () => void;
  onCopy: () => void;
  onClear: () => void;
}

export function ActionRow({ canExport, busy, onSave, onShare, onCopy, onClear }: ActionRowProps) {
  const { colors } = useTheme();
  const exportDisabled = !canExport || busy;

  const actions = [
    {
      key: 'save',
      label: 'Save PNG',
      icon: 'download-outline' as const,
      accessibilityLabel: 'Save QR code as PNG file',
      disabled: exportDisabled,
      onPress: onSave,
    },
    {
      key: 'share',
      label: 'Share',
      icon: 'share-outline' as const,
      accessibilityLabel: 'Share QR code',
      disabled: exportDisabled,
      onPress: onShare,
    },
    {
      key: 'copy',
      label: 'Copy',
      icon: 'copy-outline' as const,
      accessibilityLabel: 'Copy QR code image',
      disabled: exportDisabled,
      onPress: onCopy,
    },
    {
      key: 'clear',
      label: 'Clear',
      icon: 'trash-outline' as const,
      accessibilityLabel: 'Clear all fields',
      disabled: busy,
      onPress: onClear,
    },
  ];

  return (
    <View style={styles.row}>
      {actions.map((action) => (
        <Pressable
          key={action.key}
          onPress={action.onPress}
          disabled={action.disabled}
          accessibilityRole="button"
          accessibilityLabel={action.accessibilityLabel}
          accessibilityState={{ disabled: action.disabled }}
          style={({ pressed }) => [
            styles.button,
            {
              backgroundColor: pressed ? colors.surfaceMuted : colors.surface,
              borderColor: colors.border,
              opacity: action.disabled ? 0.45 : 1,
            },
          ]}
        >
          <Ionicons name={action.icon} size={20} color={colors.text} />
          <Text style={[styles.label, { color: colors.text }]} numberOfLines={1}>
            {action.label}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    gap: SPACING.sm,
  },
  button: {
    flex: 1,
    minHeight: MIN_TAP_SIZE + 4,
    borderWidth: 1,
    borderRadius: RADIUS.md,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
    paddingHorizontal: SPACING.xs,
  },
  label: {
    fontSize: TYPE.caption,
    fontWeight: '600',
  },
});
