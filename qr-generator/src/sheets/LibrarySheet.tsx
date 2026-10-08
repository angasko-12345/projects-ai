import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { LockedSection } from '../components/LockedSection';
import { FREE_SAVED_CODE_LIMIT } from '../premium/entitlements';
import { useTheme } from '../theme/ThemeContext';
import { MIN_TAP_SIZE, RADIUS, SPACING, TYPE } from '../theme/tokens';
import type { HistoryEntry, SavedCode } from '../types';
import { Sheet } from '../ui/Sheet';

interface LibrarySheetProps {
  visible: boolean;
  onClose: () => void;
  isPremium: boolean;
  saved: SavedCode[];
  history: HistoryEntry[];
  canSaveNow: boolean;
  onSaveCurrent: () => void;
  onLoadSaved: (code: SavedCode) => void;
  onDeleteSaved: (id: string) => void;
  onLoadHistory: (entry: HistoryEntry) => void;
  onClearHistory: () => void;
  onUnlock: () => void;
}

type Tab = 'saved' | 'history';

export function LibrarySheet({
  visible,
  onClose,
  isPremium,
  saved,
  history,
  canSaveNow,
  onSaveCurrent,
  onLoadSaved,
  onDeleteSaved,
  onLoadHistory,
  onClearHistory,
  onUnlock,
}: LibrarySheetProps) {
  const { colors } = useTheme();
  const [tab, setTab] = useState<Tab>('saved');

  const saveLimitReached = !isPremium && saved.length >= FREE_SAVED_CODE_LIMIT;
  const saveDisabled = !canSaveNow || saveLimitReached;

  let saveHint = `${saved.length} of ${FREE_SAVED_CODE_LIMIT} saved`;
  if (!canSaveNow) {
    saveHint = 'Build a valid QR code first.';
  } else if (saveLimitReached) {
    saveHint = 'Free plan saves up to 3 codes.';
  } else if (isPremium) {
    saveHint = `${saved.length} saved`;
  }

  const confirmClearHistory = () => {
    Alert.alert('Clear history?', 'This removes every history entry on this device.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Clear', style: 'destructive', onPress: onClearHistory },
    ]);
  };

  const savedTab = (
    <View style={styles.tabStack}>
      <Pressable
        onPress={onSaveCurrent}
        disabled={saveDisabled}
        accessibilityRole="button"
        accessibilityLabel="Save current QR code to library"
        accessibilityState={{ disabled: saveDisabled }}
        style={({ pressed }) => [
          styles.primaryButton,
          {
            backgroundColor: colors.accent,
            opacity: saveDisabled ? 0.45 : pressed ? 0.85 : 1,
          },
        ]}
      >
        <Ionicons name="bookmark" size={18} color={colors.onAccent} />
        <Text style={[styles.primaryButtonText, { color: colors.onAccent }]}>Save current code</Text>
      </Pressable>
      <View style={styles.hintRow}>
        <Text style={[styles.hint, { color: colors.textMuted }]}>{saveHint}</Text>
        {saveLimitReached ? (
          <Pressable
            onPress={onUnlock}
            accessibilityRole="button"
            accessibilityLabel="See premium plans"
            hitSlop={8}
          >
            <Text style={[styles.hintLink, { color: colors.accent }]}>See plans</Text>
          </Pressable>
        ) : null}
      </View>

      {saved.length === 0 ? (
        <View style={styles.emptyBox}>
          <Ionicons name="bookmark-outline" size={44} color={colors.textMuted} />
          <Text style={[styles.emptyText, { color: colors.textMuted }]}>
            No saved codes yet. Save a code to reopen it here later.
          </Text>
        </View>
      ) : (
        <View style={styles.list}>
          {saved.map((code) => (
            <View key={code.id} style={[styles.row, { borderColor: colors.border, backgroundColor: colors.surface }]}>
              <Pressable
                onPress={() => onLoadSaved(code)}
                accessibilityRole="button"
                accessibilityLabel={`Load saved code ${code.label}`}
                style={styles.rowMain}
              >
                <Text style={[styles.rowLabel, { color: colors.text }]} numberOfLines={1}>
                  {code.label}
                </Text>
                <Text style={[styles.rowDate, { color: colors.textMuted }]}>
                  {new Date(code.createdAt).toLocaleString()}
                </Text>
              </Pressable>
              <Pressable
                onPress={() => onDeleteSaved(code.id)}
                accessibilityRole="button"
                accessibilityLabel={`Delete saved code ${code.label}`}
                hitSlop={8}
                style={styles.rowAction}
              >
                <Ionicons name="trash-outline" size={20} color={colors.danger} />
              </Pressable>
            </View>
          ))}
        </View>
      )}
    </View>
  );

  const historyTab = !isPremium ? (
    <LockedSection
      title="QR history"
      detail="Premium reopens the codes you built earlier, stored only on this device."
      onUnlock={onUnlock}
    />
  ) : (
    <View style={styles.tabStack}>
      {history.length === 0 ? (
        <View style={styles.emptyBox}>
          <Ionicons name="time-outline" size={44} color={colors.textMuted} />
          <Text style={[styles.emptyText, { color: colors.textMuted }]}>
            History is empty. Codes you build show up here automatically.
          </Text>
        </View>
      ) : (
        <>
          <View style={styles.list}>
            {history.map((entry) => (
              <Pressable
                key={entry.id}
                onPress={() => onLoadHistory(entry)}
                accessibilityRole="button"
                accessibilityLabel={`Load history entry ${entry.label}`}
                style={[styles.row, styles.rowFull, { borderColor: colors.border, backgroundColor: colors.surface }]}
              >
                <View style={styles.rowMain}>
                  <Text style={[styles.rowLabel, { color: colors.text }]} numberOfLines={1}>
                    {entry.label}
                  </Text>
                  <Text style={[styles.rowDate, { color: colors.textMuted }]}>
                    {new Date(entry.createdAt).toLocaleString()}
                  </Text>
                </View>
                <Ionicons name="arrow-forward" size={18} color={colors.textMuted} />
              </Pressable>
            ))}
          </View>
          <Pressable
            onPress={confirmClearHistory}
            accessibilityRole="button"
            accessibilityLabel="Clear history"
            style={({ pressed }) => [
              styles.dangerButton,
              { borderColor: colors.danger, opacity: pressed ? 0.7 : 1 },
            ]}
          >
            <Text style={[styles.dangerButtonText, { color: colors.danger }]}>Clear history</Text>
          </Pressable>
        </>
      )}
    </View>
  );

  return (
    <Sheet visible={visible} title="Library" onClose={onClose}>
      <View style={styles.tabBar}>
        <Pressable
          onPress={() => setTab('saved')}
          accessibilityRole="button"
          accessibilityLabel="Saved codes tab"
          accessibilityState={{ selected: tab === 'saved' }}
          style={[
            styles.tabButton,
            { borderColor: tab === 'saved' ? colors.accent : colors.border },
          ]}
        >
          <Text
            style={[
              styles.tabText,
              { color: tab === 'saved' ? colors.accent : colors.textMuted },
            ]}
          >
            Saved
          </Text>
        </Pressable>
        <Pressable
          onPress={() => setTab('history')}
          accessibilityRole="button"
          accessibilityLabel="History tab"
          accessibilityState={{ selected: tab === 'history' }}
          style={[
            styles.tabButton,
            { borderColor: tab === 'history' ? colors.accent : colors.border },
          ]}
        >
          <Text
            style={[
              styles.tabText,
              { color: tab === 'history' ? colors.accent : colors.textMuted },
            ]}
          >
            History
          </Text>
        </Pressable>
      </View>
      {tab === 'saved' ? savedTab : historyTab}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  tabBar: {
    flexDirection: 'row',
    gap: SPACING.sm,
    marginBottom: SPACING.lg,
  },
  tabButton: {
    flex: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: RADIUS.md,
  },
  tabText: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  tabStack: {
    gap: SPACING.md,
  },
  primaryButton: {
    minHeight: MIN_TAP_SIZE,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: SPACING.sm,
    borderRadius: RADIUS.md,
  },
  primaryButtonText: {
    fontSize: TYPE.body,
    fontWeight: '700',
  },
  hintRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: SPACING.sm,
    flexWrap: 'wrap',
  },
  hint: {
    fontSize: TYPE.caption,
  },
  hintLink: {
    fontSize: TYPE.caption,
    fontWeight: '700',
  },
  emptyBox: {
    alignItems: 'center',
    justifyContent: 'center',
    gap: SPACING.md,
    paddingVertical: SPACING.xl,
  },
  emptyText: {
    fontSize: TYPE.label,
    textAlign: 'center',
    paddingHorizontal: SPACING.lg,
  },
  list: {
    gap: SPACING.sm,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    borderWidth: 1,
    borderRadius: RADIUS.md,
    paddingLeft: SPACING.md,
  },
  rowFull: {
    paddingRight: SPACING.md,
  },
  rowMain: {
    flex: 1,
    paddingVertical: SPACING.md,
    paddingRight: SPACING.sm,
    gap: 2,
  },
  rowLabel: {
    fontSize: TYPE.body,
    fontWeight: '600',
  },
  rowDate: {
    fontSize: TYPE.caption,
  },
  rowAction: {
    width: MIN_TAP_SIZE,
    height: MIN_TAP_SIZE,
    alignItems: 'center',
    justifyContent: 'center',
  },
  dangerButton: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderRadius: RADIUS.md,
  },
  dangerButtonText: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
});
