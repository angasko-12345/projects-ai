import React, { useState } from 'react';
import {
  KeyboardTypeOptions,
  Pressable,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';

import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';
import type { QrDraft, WifiSecurity } from '../types';
import { Chip } from '../ui/Chip';

interface LabeledInputProps {
  label: string;
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  keyboardType?: KeyboardTypeOptions;
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  autoCorrect?: boolean;
  multiline?: boolean;
  secure?: boolean;
}

function LabeledInput({
  label,
  value,
  onChangeText,
  placeholder,
  keyboardType,
  autoCapitalize = 'sentences',
  autoCorrect = true,
  multiline = false,
  secure = false,
}: LabeledInputProps) {
  const { colors } = useTheme();
  const [revealed, setRevealed] = useState(false);
  const hideValue = secure && !revealed;

  return (
    <View style={styles.field}>
      <View style={styles.labelRow}>
        <Text style={[styles.label, { color: colors.textMuted }]}>{label}</Text>
        {secure ? (
          <Pressable
            onPress={() => setRevealed((current) => !current)}
            accessibilityRole="button"
            accessibilityLabel={revealed ? `Hide ${label}` : `Show ${label}`}
            hitSlop={8}
            style={styles.revealButton}
          >
            <Text style={[styles.revealText, { color: colors.accent }]}>
              {revealed ? 'Hide' : 'Show'}
            </Text>
          </Pressable>
        ) : null}
      </View>
      <TextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={colors.textMuted}
        keyboardType={keyboardType}
        autoCapitalize={autoCapitalize}
        autoCorrect={autoCorrect}
        secureTextEntry={hideValue}
        multiline={multiline}
        accessibilityLabel={label}
        style={[
          styles.input,
          multiline && styles.multilineInput,
          { backgroundColor: colors.surfaceMuted, borderColor: colors.border, color: colors.text },
        ]}
      />
    </View>
  );
}

const SECURITY_OPTIONS: { value: WifiSecurity; label: string }[] = [
  { value: 'wpa', label: 'WPA/WPA2' },
  { value: 'wep', label: 'WEP' },
  { value: 'none', label: 'Open' },
];

interface TypeFormProps {
  draft: QrDraft;
  onChange: (next: QrDraft) => void;
  error: string | null;
}

export function TypeForm({ draft, onChange, error }: TypeFormProps) {
  const { colors } = useTheme();
  const wifi = draft.wifi;

  return (
    <View style={styles.form}>
      {draft.type === 'text' ? (
        <LabeledInput
          label="Text"
          value={draft.text}
          onChangeText={(text) => onChange({ ...draft, text })}
          placeholder="Anything you want to share"
          multiline
        />
      ) : null}

      {draft.type === 'url' ? (
        <View style={styles.field}>
          <LabeledInput
            label="Web address"
            value={draft.url}
            onChangeText={(url) => onChange({ ...draft, url })}
            placeholder="example.com"
            keyboardType="web-search"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <Text style={[styles.hint, { color: colors.textMuted }]}>
            https:// is added automatically when you leave out the scheme.
          </Text>
        </View>
      ) : null}

      {draft.type === 'email' ? (
        <LabeledInput
          label="Email address"
          value={draft.email}
          onChangeText={(email) => onChange({ ...draft, email })}
          placeholder="name@example.com"
          keyboardType="email-address"
          autoCapitalize="none"
          autoCorrect={false}
        />
      ) : null}

      {draft.type === 'phone' ? (
        <LabeledInput
          label="Phone number"
          value={draft.phone}
          onChangeText={(phone) => onChange({ ...draft, phone })}
          placeholder="+1 555 123 4567"
          keyboardType="phone-pad"
        />
      ) : null}

      {draft.type === 'wifi' ? (
        <View style={styles.wifiForm}>
          <LabeledInput
            label="Network name"
            value={wifi.ssid}
            onChangeText={(ssid) => onChange({ ...draft, wifi: { ...wifi, ssid } })}
            placeholder="Wi-Fi network name"
            autoCapitalize="none"
            autoCorrect={false}
          />
          {wifi.security !== 'none' ? (
            <LabeledInput
              label="Password"
              value={wifi.password}
              onChangeText={(password) => onChange({ ...draft, wifi: { ...wifi, password } })}
              placeholder="Network password"
              autoCapitalize="none"
              autoCorrect={false}
              secure
            />
          ) : null}
          <View style={styles.field}>
            <Text style={[styles.label, { color: colors.textMuted }]}>Security</Text>
            <View style={styles.securityRow}>
              {SECURITY_OPTIONS.map((option) => (
                <Chip
                  key={option.value}
                  label={option.label}
                  selected={option.value === wifi.security}
                  onPress={() =>
                    onChange({ ...draft, wifi: { ...wifi, security: option.value } })
                  }
                  accessibilityLabel={`Security: ${option.label}`}
                />
              ))}
            </View>
          </View>
          <View style={styles.switchRow}>
            <Text style={[styles.switchLabel, { color: colors.text }]}>Hidden network</Text>
            <Switch
              value={wifi.hidden}
              onValueChange={(hidden) => onChange({ ...draft, wifi: { ...wifi, hidden } })}
              trackColor={{ false: colors.border, true: colors.accent }}
              thumbColor={colors.surface}
              accessibilityLabel="Hidden network"
            />
          </View>
        </View>
      ) : null}

      {error ? (
        <Text accessibilityRole="alert" style={[styles.error, { color: colors.danger }]}>
          {error}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  form: {
    gap: SPACING.md,
  },
  wifiForm: {
    gap: SPACING.md,
  },
  field: {
    gap: SPACING.xs,
  },
  labelRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  label: {
    fontSize: TYPE.label,
    fontWeight: '600',
  },
  revealButton: {
    minHeight: 28,
    justifyContent: 'center',
    paddingHorizontal: SPACING.xs,
  },
  revealText: {
    fontSize: TYPE.caption,
    fontWeight: '700',
  },
  input: {
    minHeight: MIN_TAP_SIZE,
    borderWidth: 1,
    borderRadius: RADIUS.md,
    paddingHorizontal: SPACING.md,
    paddingVertical: SPACING.md,
    fontSize: TYPE.body,
  },
  multilineInput: {
    minHeight: 96,
    textAlignVertical: 'top',
  },
  hint: {
    fontSize: TYPE.caption,
  },
  securityRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: SPACING.sm,
  },
  switchRow: {
    minHeight: MIN_TAP_SIZE,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  switchLabel: {
    fontSize: TYPE.body,
  },
  error: {
    fontSize: TYPE.caption,
    fontWeight: '600',
  },
});
