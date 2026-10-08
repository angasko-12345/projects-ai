import React from 'react';
import { ScrollView, StyleSheet } from 'react-native';

import { SPACING } from '../theme/tokens';
import type { QrType } from '../types';
import { Chip } from '../ui/Chip';

const OPTIONS: { value: QrType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'url', label: 'URL' },
  { value: 'email', label: 'Email' },
  { value: 'phone', label: 'Phone' },
  { value: 'wifi', label: 'Wi-Fi' },
];

interface TypeSelectorProps {
  value: QrType;
  onChange: (type: QrType) => void;
}

export function TypeSelector({ value, onChange }: TypeSelectorProps) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.row}
    >
      {OPTIONS.map((option) => (
        <Chip
          key={option.value}
          label={option.label}
          selected={option.value === value}
          onPress={() => onChange(option.value)}
          accessibilityLabel={`${option.label} QR type`}
        />
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  row: {
    gap: SPACING.sm,
    paddingVertical: SPACING.xs,
    paddingHorizontal: SPACING.xs,
  },
});
