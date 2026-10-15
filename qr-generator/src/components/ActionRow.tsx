import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';

interface ActionButton {
  key: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  accessibilityLabel: string;
  disabled: boolean;
  onPress: () => void;
}

interface ActionRowProps {
  canExport: boolean;
  busy: boolean;
  onSave: () => void;
  onShare: () => void;
  onCopy: () => void;
  onClear: () => void;
  onSaveSvg?: () => void;
  onShareSvg?: () => void;
}

export function ActionRow({
  canExport,
  busy,
  onSave,
  onShare,
  onCopy,
  onClear,
  onSaveSvg,
  onShareSvg,
}: ActionRowProps) {
  const { colors } = useTheme();
  const exportDisabled = !canExport || busy;

  const actions: ActionButton[] = [
    {
      key: 'save',
      label: 'Save PNG',
      icon: 'download-outline',
      accessibilityLabel: 'Save QR code as PNG file',
      disabled: exportDisabled,
      onPress: onSave,
    },
    {
      key: 'share',
      label: 'Share',
      icon: 'share-outline',
      accessibilityLabel: 'Share QR code',
      disabled: exportDisabled,
      onPress: onShare,
    },
    {
      key: 'copy',
      label: 'Copy',
      icon: 'copy-outline',
      accessibilityLabel: 'Copy QR code image',
      disabled: exportDisabled,
      onPress: onCopy,
    },
    {
      key: 'clear',
      label: 'Clear',
      icon: 'trash-outline',
      accessibilityLabel: 'Clear all fields',
      disabled: busy,
      onPress: onClear,
    },
  ];

  // The vector row appears only when handlers are supplied, so existing
  // screens keep the same single row of PNG actions.
  const svgActions: ActionButton[] = [];
  if (onSaveSvg) {
    svgActions.push({
      key: 'save-svg',
      label: 'Save SVG',
      icon: 'document-outline',
      accessibilityLabel: 'Save QR code as SVG file',
      disabled: exportDisabled,
      onPress: onSaveSvg,
    });
  }
  if (onShareSvg) {
    svgActions.push({
      key: 'share-svg',
      label: 'Share SVG',
      icon: 'share-social-outline',
      accessibilityLabel: 'Share QR code as SVG',
      disabled: exportDisabled,
      onPress: onShareSvg,
    });
  }

  const renderButton = (action: ActionButton) => (
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
  );

  return (
    <View style={styles.stack}>
      <View style={styles.row}>{actions.map(renderButton)}</View>
      {svgActions.length > 0 ? <View style={styles.row}>{svgActions.map(renderButton)}</View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  stack: {
    gap: SPACING.sm,
  },
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
