import * as ImagePicker from 'expo-image-picker';
import React, { useState } from 'react';
import { Image, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { LockedSection } from '../components/LockedSection';
import { isHexColor } from '../qr/content';
import { checkColors, type ColorCheck } from '../qr/contrast';
import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';
import type { QrAppearance, QrStyle } from '../types';
import { Chip } from '../ui/Chip';
import { Sheet } from '../ui/Sheet';
import type { ToastData } from '../ui/Toast';

const FOREGROUND_PRESETS = [
  '#000000',
  '#1F2937',
  '#B91C1C',
  '#1D4ED8',
  '#15803D',
  '#7C3AED',
  '#B45309',
  '#0F766E',
];

const BACKGROUND_PRESETS = ['#FFFFFF', '#FEF3C7', '#ECFDF5', '#EFF6FF', '#FEE2E2', '#F5F3FF'];

const STYLE_OPTIONS: { value: QrStyle; label: string }[] = [
  { value: 'square', label: 'Square' },
  { value: 'rounded', label: 'Rounded' },
  { value: 'dots', label: 'Dots' },
];

const CHECK_MESSAGES: Record<Exclude<ColorCheck, 'ok'>, string> = {
  'low-contrast': 'Contrast is too low for a scannable code.',
  inverted: 'The code must be darker than the background.',
};

interface HexColorFieldProps {
  label: string;
  value: string;
  presets: string[];
  colorRole: 'foreground' | 'background';
  pairColor: string;
  onCommit: (color: string) => void;
  notify: (text: string, tone?: ToastData['tone']) => void;
}

// Callers remount this field with key={value}, so a freshly committed color
// resets the typed text without syncing through an effect.
function HexColorField({
  label,
  value,
  presets,
  colorRole,
  pairColor,
  onCommit,
  notify,
}: HexColorFieldProps) {
  const { colors } = useTheme();
  const [text, setText] = useState(value);
  const [pairError, setPairError] = useState<string | null>(null);

  const commit = (raw: string) => {
    const candidate = raw.trim();
    if (!isHexColor(candidate)) {
      notify('Use a color in #RRGGBB form, like #1A2B3C.', 'error');
      setText(value);
      return;
    }
    const next = candidate.toUpperCase();
    const check =
      colorRole === 'foreground' ? checkColors(next, pairColor) : checkColors(pairColor, next);
    if (check !== 'ok') {
      setPairError(CHECK_MESSAGES[check]);
      setText(value);
      return;
    }
    setPairError(null);
    onCommit(next);
  };

  return (
    <View style={styles.block}>
      <Text style={[styles.blockLabel, { color: colors.textMuted }]}>{label}</Text>
      <View style={styles.swatchRow}>
        {presets.map((preset) => {
          const selected = preset.toUpperCase() === value.toUpperCase();
          return (
            <Pressable
              key={preset}
              onPress={() => commit(preset)}
              accessibilityRole="button"
              accessibilityLabel={`Color ${preset}`}
              accessibilityState={{ selected }}
              style={[
                styles.swatch,
                {
                  backgroundColor: preset,
                  borderColor: selected ? colors.text : colors.border,
                  borderWidth: selected ? 2 : 1,
                },
              ]}
            />
          );
        })}
      </View>
      <View style={styles.hexRow}>
        <Text style={[styles.hexCaption, { color: colors.textMuted }]}>Hex</Text>
        <TextInput
          value={text}
          onChangeText={setText}
          onEndEditing={() => commit(text)}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="default"
          accessibilityLabel={`${label} hex value`}
          placeholder="#000000"
          placeholderTextColor={colors.textMuted}
          style={[
            styles.hexInput,
            { backgroundColor: colors.surfaceMuted, borderColor: colors.border, color: colors.text },
          ]}
        />
      </View>
      {pairError ? (
        <Text accessibilityRole="alert" style={[styles.error, { color: colors.danger }]}>
          {pairError}
        </Text>
      ) : null}
    </View>
  );
}

interface CustomizeSheetProps {
  visible: boolean;
  onClose: () => void;
  isPremium: boolean;
  appearance: QrAppearance;
  onChange: (patch: Partial<QrAppearance>) => void;
  onUnlock: () => void;
  notify: (text: string, tone?: ToastData['tone']) => void;
}

export function CustomizeSheet({
  visible,
  onClose,
  isPremium,
  appearance,
  onChange,
  onUnlock,
  notify,
}: CustomizeSheetProps) {
  const { colors } = useTheme();

  const pickLogo = async () => {
    try {
      const result = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        quality: 0.8,
      });
      if (result.canceled) {
        return;
      }
      const asset = result.assets[0];
      if (asset) {
        onChange({ logoUri: asset.uri });
      }
    } catch {
      notify('The image could not be opened.', 'error');
    }
  };

  const styleControls = (
    <View style={styles.block}>
      <Text style={[styles.blockLabel, { color: colors.textMuted }]}>Style</Text>
      <View style={styles.chipRow}>
        {STYLE_OPTIONS.map((option) => (
          <Chip
            key={option.value}
            label={option.label}
            selected={option.value === appearance.style}
            onPress={() => onChange({ style: option.value })}
            accessibilityLabel={`QR style: ${option.label}`}
          />
        ))}
      </View>
    </View>
  );

  const logoControls = (
    <View style={styles.block}>
      <Text style={[styles.blockLabel, { color: colors.textMuted }]}>Center image</Text>
      {appearance.logoUri ? (
        <View style={styles.logoRow}>
          <Image
            source={{ uri: appearance.logoUri }}
            style={[styles.logoThumb, { borderColor: colors.border }]}
            accessibilityLabel="Current logo"
          />
          <Pressable
            onPress={pickLogo}
            accessibilityRole="button"
            accessibilityLabel="Choose a different logo"
            style={[styles.logoButton, { borderColor: colors.border, backgroundColor: colors.surface }]}
          >
            <Text style={[styles.logoButtonText, { color: colors.text }]}>Change</Text>
          </Pressable>
          <Pressable
            onPress={() => onChange({ logoUri: null })}
            accessibilityRole="button"
            accessibilityLabel="Remove logo"
            style={[styles.logoButton, { borderColor: colors.danger, backgroundColor: colors.surface }]}
          >
            <Text style={[styles.logoButtonText, { color: colors.danger }]}>Remove</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable
          onPress={pickLogo}
          accessibilityRole="button"
          accessibilityLabel="Choose a logo image"
          style={[styles.logoButton, { borderColor: colors.border, backgroundColor: colors.surface }]}
        >
          <Text style={[styles.logoButtonText, { color: colors.text }]}>Choose image</Text>
        </Pressable>
      )}
      <Text style={[styles.caption, { color: colors.textMuted }]}>
        A logo raises the QR error correction, so very long content may not fit.
      </Text>
    </View>
  );

  return (
    <Sheet visible={visible} title="Customize" onClose={onClose}>
      <View style={styles.stack}>
        {isPremium ? (
          styleControls
        ) : (
          <LockedSection
            title="Style"
            detail="Square is included. Rounded and dots are premium."
            onUnlock={onUnlock}
          >
            {styleControls}
          </LockedSection>
        )}

        {isPremium ? (
          <HexColorField
            key={`code-${appearance.foreground}`}
            label="Code color"
            value={appearance.foreground}
            presets={FOREGROUND_PRESETS}
            colorRole="foreground"
            pairColor={appearance.background}
            onCommit={(foreground) => onChange({ foreground })}
            notify={notify}
          />
        ) : (
          <LockedSection
            title="Code color"
            detail="Recolor the QR modules with your own hex values."
            onUnlock={onUnlock}
          >
            <HexColorField
              key={`code-${appearance.foreground}`}
              label="Code color"
              value={appearance.foreground}
              presets={FOREGROUND_PRESETS}
              colorRole="foreground"
              pairColor={appearance.background}
              onCommit={(foreground) => onChange({ foreground })}
              notify={notify}
            />
          </LockedSection>
        )}

        {isPremium ? (
          <HexColorField
            key={`background-${appearance.background}`}
            label="Background color"
            value={appearance.background}
            presets={BACKGROUND_PRESETS}
            colorRole="background"
            pairColor={appearance.foreground}
            onCommit={(background) => onChange({ background })}
            notify={notify}
          />
        ) : (
          <LockedSection
            title="Background color"
            detail="Pick the canvas color behind the code."
            onUnlock={onUnlock}
          >
            <HexColorField
              key={`background-${appearance.background}`}
              label="Background color"
              value={appearance.background}
              presets={BACKGROUND_PRESETS}
              colorRole="background"
              pairColor={appearance.foreground}
              onCommit={(background) => onChange({ background })}
              notify={notify}
            />
          </LockedSection>
        )}

        {isPremium ? (
          logoControls
        ) : (
          <LockedSection
            title="Logo"
            detail="Place one of your images in the center of the code."
            onUnlock={onUnlock}
          >
            {logoControls}
          </LockedSection>
        )}
      </View>
    </Sheet>
  );
}

const styles = StyleSheet.create({
  stack: {
    gap: SPACING.lg,
  },
  error: {
    fontSize: TYPE.caption,
    fontWeight: '600',
  },
  block: {
    gap: SPACING.sm,
  },
  blockLabel: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  chipRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.sm,
  },
  swatchRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.sm,
  },
  swatch: {
    width: 40,
    height: 40,
    borderRadius: RADIUS.sm,
  },
  hexRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
  },
  hexCaption: {
    fontSize: TYPE.caption,
    fontWeight: '600',
  },
  hexInput: {
    flex: 1,
    minHeight: MIN_TAP_SIZE - 8,
    borderWidth: 1,
    borderRadius: RADIUS.sm,
    paddingHorizontal: SPACING.md,
    fontSize: TYPE.body,
  },
  logoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
    flexWrap: 'wrap',
  },
  logoThumb: {
    width: 56,
    height: 56,
    borderRadius: RADIUS.sm,
    borderWidth: 1,
  },
  logoButton: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: SPACING.lg,
    borderWidth: 1,
    borderRadius: RADIUS.md,
  },
  logoButtonText: {
    fontSize: TYPE.label,
    fontWeight: '600',
  },
  caption: {
    fontSize: TYPE.caption,
  },
});
