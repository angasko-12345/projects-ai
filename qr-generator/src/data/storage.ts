import AsyncStorage from '@react-native-async-storage/async-storage';

import type { AppSettings, HistoryEntry, SavedCode } from '../types';
import { DEFAULT_SETTINGS } from '../types';

const SETTINGS_KEY = '@qrgen:v1:settings';
const SAVED_KEY = '@qrgen:v1:saved';
const HISTORY_KEY = '@qrgen:v1:history';

export const HISTORY_LIMIT = 100;

export function makeId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

async function readList<T>(key: string): Promise<T[]> {
  try {
    const raw = await AsyncStorage.getItem(key);
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

async function writeList(key: string, entries: unknown[]): Promise<void> {
  try {
    await AsyncStorage.setItem(key, JSON.stringify(entries));
  } catch {
    // Storage failures must not crash the app; the in-memory state stays authoritative.
  }
}

export async function loadSettings(): Promise<AppSettings> {
  try {
    const raw = await AsyncStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      return DEFAULT_SETTINGS;
    }
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && 'themeMode' in parsed) {
      const themeMode = (parsed as AppSettings).themeMode;
      if (themeMode === 'system' || themeMode === 'light' || themeMode === 'dark') {
        return { themeMode };
      }
    }
  } catch {
    // Fall through to defaults.
  }
  return DEFAULT_SETTINGS;
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  try {
    await AsyncStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Settings stay applied for this session even if persistence fails.
  }
}

export function loadSavedCodes(): Promise<SavedCode[]> {
  return readList<SavedCode>(SAVED_KEY);
}

export function writeSavedCodes(entries: SavedCode[]): Promise<void> {
  return writeList(SAVED_KEY, entries);
}

export function loadHistory(): Promise<HistoryEntry[]> {
  return readList<HistoryEntry>(HISTORY_KEY);
}

/** Newest first, deduplicated by payload, capped at HISTORY_LIMIT. */
export function prependHistory(history: HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  const withoutDuplicate = history.filter((existing) => existing.payload !== entry.payload);
  return [entry, ...withoutDuplicate].slice(0, HISTORY_LIMIT);
}

export function writeHistory(entries: HistoryEntry[]): Promise<void> {
  return writeList(HISTORY_KEY, entries);
}
