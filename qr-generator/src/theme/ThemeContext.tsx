import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useColorScheme } from 'react-native';

import { loadSettings, saveSettings } from '../data/storage';
import type { ThemeMode } from '../types';
import { darkPalette, lightPalette, type Palette } from './tokens';

interface ThemeContextValue {
  isReady: boolean;
  mode: ThemeMode;
  scheme: 'light' | 'dark';
  colors: Palette;
  setMode: (mode: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const systemScheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>('system');
  const [isReady, setIsReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadSettings().then((settings) => {
      if (!cancelled) {
        setModeState(settings.themeMode);
        setIsReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    saveSettings({ themeMode: next });
  }, []);

  const scheme = mode === 'system' ? (systemScheme === 'dark' ? 'dark' : 'light') : mode;

  const value = useMemo<ThemeContextValue>(
    () => ({
      isReady,
      mode,
      scheme,
      colors: scheme === 'dark' ? darkPalette : lightPalette,
      setMode,
    }),
    [isReady, mode, scheme, setMode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) {
    throw new Error('useTheme must be used inside ThemeProvider');
  }
  return value;
}
