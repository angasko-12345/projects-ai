import { Ionicons } from '@expo/vector-icons';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type View as RNView,
} from 'react-native';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';

import { ActionRow } from './src/components/ActionRow';
import { QrPreviewCard, type PreviewState } from './src/components/QrPreviewCard';
import { TypeForm } from './src/components/TypeForm';
import { TypeSelector } from './src/components/TypeSelector';
import {
  loadHistory,
  loadSavedCodes,
  makeId,
  prependHistory,
  writeHistory,
  writeSavedCodes,
} from './src/data/storage';
import { contentLabel, validateDraft } from './src/qr/content';
import { sanitizeAppearance } from './src/qr/contrast';
import { QrEncodingError, buildQrMatrix } from './src/qr/matrix';
import { canSaveCode, effectiveAppearance } from './src/premium/entitlements';
import { PremiumProvider, usePremium } from './src/premium/PremiumContext';
import { CustomizeSheet } from './src/sheets/CustomizeSheet';
import { LibrarySheet } from './src/sheets/LibrarySheet';
import { PremiumSheet } from './src/sheets/PremiumSheet';
import { ThemeProvider, useTheme } from './src/theme/ThemeContext';
import { RADIUS, SPACING, TYPE } from './src/theme/tokens';
import {
  EMPTY_DRAFT,
  type HistoryEntry,
  type QrAppearance,
  type QrDraft,
  type SavedCode,
  type ThemeMode,
} from './src/types';
import { ToastBanner, type ToastData } from './src/ui/Toast';
import { captureQrPng, copyQrPng, saveQrPng, shareQrPng } from './src/util/qrExport';

SplashScreen.preventAutoHideAsync();

type SheetName = 'customize' | 'library' | 'premium' | null;

interface ToolbarButtonProps {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  locked: boolean;
  onPress: () => void;
}

function ToolbarButton({ label, icon, locked, onPress }: ToolbarButtonProps) {
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={label}
      style={({ pressed }) => [
        styles.toolbarButton,
        { backgroundColor: pressed ? colors.surfaceMuted : colors.surface, borderColor: colors.border },
      ]}
    >
      <Ionicons name={icon} size={20} color={locked ? colors.accent : colors.text} />
      <Text style={[styles.toolbarLabel, { color: colors.text }]}>{label}</Text>
      {locked ? <Ionicons name="lock-closed" size={13} color={colors.accent} /> : null}
    </Pressable>
  );
}

function MainScreen() {
  const { colors, scheme, mode, setMode, isReady } = useTheme();
  const { isPremium } = usePremium();

  const [draft, setDraft] = useState<QrDraft>(EMPTY_DRAFT);
  const [appearance, setAppearance] = useState<QrAppearance>({
    style: 'square',
    foreground: '#000000',
    background: '#FFFFFF',
    logoUri: null,
  });
  // Free users see and export the default appearance; the stored one is kept
  // as-is and activates with premium.
  const renderedAppearance = effectiveAppearance(isPremium, appearance);
  const [sheet, setSheet] = useState<SheetName>(null);
  const [toast, setToast] = useState<ToastData | null>(null);
  const [saved, setSaved] = useState<SavedCode[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);

  const captureViewRef = useRef<RNView>(null);
  const toastTimerRef = useRef<number | null>(null);
  const lastSaveDirectoryRef = useRef<string | null>(null);

  const showToast = useCallback((text: string, tone: ToastData['tone'] = 'neutral') => {
    clearTimeout(toastTimerRef.current);
    setToast({ text, tone });
    toastTimerRef.current = setTimeout(() => setToast(null), 2800);
  }, []);

  useEffect(() => {
    if (!isReady) {
      return;
    }
    SplashScreen.hideAsync().catch(() => undefined);
  }, [isReady]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadSavedCodes(), loadHistory()]).then(([loadedSaved, loadedHistory]) => {
      if (cancelled) {
        return;
      }
      setSaved(loadedSaved);
      setHistory(loadedHistory);
      setHydrated(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (hydrated) {
      writeSavedCodes(saved);
    }
  }, [saved, hydrated]);

  useEffect(() => {
    if (hydrated) {
      writeHistory(history);
    }
  }, [history, hydrated]);

  useEffect(() => () => clearTimeout(toastTimerRef.current), []);

  const validation = useMemo(() => validateDraft(draft), [draft]);

  const preview = useMemo<PreviewState>(() => {
    if (validation.status === 'empty') {
      return { kind: 'empty' };
    }
    if (validation.status === 'error') {
      return { kind: 'error', message: validation.message };
    }
    try {
      return {
        kind: 'ready',
        matrix: buildQrMatrix(validation.payload, renderedAppearance.logoUri ? 'H' : 'M'),
      };
    } catch (error) {
      return {
        kind: 'error',
        message:
          error instanceof QrEncodingError
            ? error.message
            : 'This content cannot be turned into a QR code.',
      };
    }
  }, [validation, renderedAppearance.logoUri]);

  const payload = validation.status === 'ok' ? validation.payload : null;

  useEffect(() => {
    if (!payload) {
      return;
    }
    const timer = setTimeout(() => {
      const entry: HistoryEntry = {
        id: makeId(),
        createdAt: Date.now(),
        type: draft.type,
        label: contentLabel(draft),
        payload,
        draft: { ...draft, wifi: { ...draft.wifi } },
      };
      setHistory((current) => prependHistory(current, entry));
    }, 1500);
    return () => clearTimeout(timer);
  }, [payload, draft]);

  const cycleTheme = () => {
    const next: ThemeMode = mode === 'system' ? 'light' : mode === 'light' ? 'dark' : 'system';
    setMode(next);
  };

  const themeIcon: keyof typeof Ionicons.glyphMap =
    mode === 'system' ? 'contrast-outline' : mode === 'light' ? 'sunny-outline' : 'moon-outline';

  const runExport = async (task: () => Promise<void>) => {
    if (exportBusy || preview.kind !== 'ready') {
      return;
    }
    setExportBusy(true);
    try {
      await task();
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Something went wrong.', 'error');
    } finally {
      setExportBusy(false);
    }
  };

  const handleSavePng = () =>
    runExport(async () => {
      const image = await captureQrPng(captureViewRef);
      const result = await saveQrPng(image, lastSaveDirectoryRef.current);
      if (result.status === 'saved') {
        lastSaveDirectoryRef.current = result.directoryUri;
        showToast('QR code saved as PNG.', 'success');
      } else if (result.status === 'error') {
        showToast(result.message, 'error');
      }
    });

  const handleShare = () =>
    runExport(async () => {
      const image = await captureQrPng(captureViewRef);
      const result = await shareQrPng(image);
      if (result.status === 'unavailable' || result.status === 'error') {
        showToast(result.message, 'error');
      }
    });

  const handleCopy = () =>
    runExport(async () => {
      const image = await captureQrPng(captureViewRef);
      await copyQrPng(image);
      showToast('QR code image copied.', 'success');
    });

  const handleClear = () => {
    setDraft(EMPTY_DRAFT);
  };

  const handleSaveToLibrary = () => {
    if (validation.status !== 'ok') {
      showToast('Build a valid QR code first.', 'error');
      return;
    }
    if (!canSaveCode(isPremium, saved.length)) {
      showToast('Free plan saves up to 3 codes. Go premium for unlimited.', 'neutral');
      return;
    }
    const code: SavedCode = {
      id: makeId(),
      createdAt: Date.now(),
      label: contentLabel(draft),
      draft: { ...draft, wifi: { ...draft.wifi } },
      appearance: { ...appearance },
    };
    setSaved((current) => [code, ...current]);
    showToast('Code saved to your library.', 'success');
  };

  const handleLoadSaved = (code: SavedCode) => {
    setDraft({ ...code.draft, wifi: { ...code.draft.wifi } });
    setAppearance(sanitizeAppearance({ ...code.appearance }));
    setSheet(null);
    showToast('Saved code loaded.', 'success');
  };

  const handleLoadHistory = (entry: HistoryEntry) => {
    setDraft({ ...entry.draft, wifi: { ...entry.draft.wifi } });
    setSheet(null);
    showToast('History entry loaded.', 'success');
  };

  const handleDeleteSaved = (id: string) => {
    setSaved((current) => current.filter((code) => code.id !== id));
  };

  const handleClearHistory = () => {
    setHistory([]);
    showToast('History cleared.', 'neutral');
  };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }} edges={['top', 'left', 'right']}>
      <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
      <View style={styles.container}>
        <View style={styles.headerRow}>
          <Text style={[styles.appTitle, { color: colors.text }]}>QR Generator</Text>
          <View style={styles.headerActions}>
            <Pressable
              onPress={cycleTheme}
              accessibilityRole="button"
              accessibilityLabel={`Theme: ${mode}`}
              accessibilityHint="Switches between system, light and dark"
              hitSlop={8}
              style={styles.iconButton}
            >
              <Ionicons name={themeIcon} size={22} color={colors.text} />
            </Pressable>
            <Pressable
              onPress={() => setSheet('premium')}
              accessibilityRole="button"
              accessibilityLabel={isPremium ? 'Premium active' : 'Open premium plans'}
              hitSlop={8}
              style={styles.iconButton}
            >
              <Ionicons
                name={isPremium ? 'star' : 'star-outline'}
                size={22}
                color={isPremium ? colors.accent : colors.text}
              />
            </Pressable>
          </View>
        </View>

        <ScrollView
          contentContainerStyle={styles.scrollContent}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          <TypeSelector value={draft.type} onChange={(type) => setDraft((current) => ({ ...current, type }))} />

          <View
            style={[
              styles.card,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
          >
            <TypeForm
              draft={draft}
              onChange={setDraft}
              error={validation.status === 'error' ? validation.message : null}
            />
          </View>

          <QrPreviewCard
            preview={preview}
            appearance={renderedAppearance}
            captureViewRef={captureViewRef}
          />

          <ActionRow
            canExport={preview.kind === 'ready'}
            busy={exportBusy}
            onSave={handleSavePng}
            onShare={handleShare}
            onCopy={handleCopy}
            onClear={handleClear}
          />

          <View style={styles.toolbarRow}>
            <ToolbarButton
              label="Customize"
              icon="color-palette-outline"
              locked={!isPremium}
              onPress={() => setSheet('customize')}
            />
            <ToolbarButton
              label="Library"
              icon="bookmark-outline"
              locked={false}
              onPress={() => setSheet('library')}
            />
          </View>

          <Text style={[styles.footnote, { color: colors.textMuted }]}>
            Everything stays on your device. No account, no tracking.
          </Text>
        </ScrollView>
      </View>

      <ToastBanner toast={toast} />

      <CustomizeSheet
        visible={sheet === 'customize'}
        onClose={() => setSheet(null)}
        isPremium={isPremium}
        appearance={appearance}
        onChange={(patch) => setAppearance((current) => ({ ...current, ...patch }))}
        onUnlock={() => setSheet('premium')}
        notify={showToast}
      />
      <LibrarySheet
        visible={sheet === 'library'}
        onClose={() => setSheet(null)}
        isPremium={isPremium}
        saved={saved}
        history={history}
        canSaveNow={validation.status === 'ok'}
        onSaveCurrent={handleSaveToLibrary}
        onLoadSaved={handleLoadSaved}
        onDeleteSaved={handleDeleteSaved}
        onLoadHistory={handleLoadHistory}
        onClearHistory={handleClearHistory}
        onUnlock={() => setSheet('premium')}
      />
      <PremiumSheet visible={sheet === 'premium'} onClose={() => setSheet(null)} notify={showToast} />
    </SafeAreaView>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <PremiumProvider>
          <MainScreen />
        </PremiumProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    width: '100%',
    maxWidth: 560,
    alignSelf: 'center',
  },
  headerRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: SPACING.lg,
    paddingVertical: SPACING.sm,
  },
  headerActions: {
    flexDirection: 'row',
    gap: SPACING.xs,
  },
  iconButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  appTitle: {
    fontSize: TYPE.title,
    fontWeight: '800',
  },
  scrollContent: {
    padding: SPACING.lg,
    gap: SPACING.lg,
    paddingBottom: SPACING.xl * 2,
  },
  card: {
    borderWidth: 1,
    borderRadius: RADIUS.lg,
    padding: SPACING.md,
    gap: SPACING.md,
  },
  toolbarRow: {
    flexDirection: 'row',
    gap: SPACING.md,
  },
  toolbarButton: {
    flex: 1,
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: SPACING.sm,
    borderWidth: 1,
    borderRadius: RADIUS.md,
  },
  toolbarLabel: {
    fontSize: TYPE.label,
    fontWeight: '700',
  },
  footnote: {
    fontSize: TYPE.caption,
    textAlign: 'center',
  },
});
