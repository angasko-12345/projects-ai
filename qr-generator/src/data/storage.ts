import AsyncStorage from '@react-native-async-storage/async-storage';

import type { AppSettings, HistoryEntry, QrType, SavedCode } from '../types';
import { DEFAULT_SETTINGS } from '../types';

const SETTINGS_KEY = '@qrgen:v1:settings';
const SAVED_KEY = '@qrgen:v1:saved';
const HISTORY_KEY = '@qrgen:v1:history';

export const HISTORY_LIMIT = 100;

export function makeId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

async function readList<T>(key: string): Promise<T[]> {
  const raw = await AsyncStorage.getItem(key);
  if (!raw) {
    return [];
  }
  const parsed: unknown = JSON.parse(raw);
  if (!Array.isArray(parsed)) {
    throw new Error(`Stored list ${key} is not an array.`);
  }
  return parsed as T[];
}

async function writeList(key: string, entries: unknown[]): Promise<void> {
  // Propagate errors so the UI can distinguish a persisted change from an
  // in-memory-only change. Callers must not report durable success on failure.
  await AsyncStorage.setItem(key, JSON.stringify(entries));
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

export async function loadHistory(): Promise<HistoryEntry[]> {
  const entries = await readList<HistoryEntry>(HISTORY_KEY);
  // Older app versions automatically retained Wi-Fi payloads (including
  // passwords). Remove those on upgrade before exposing history to the UI.
  const safeEntries = entries.filter((entry) => entry.type !== 'wifi');
  if (safeEntries.length !== entries.length) {
    await writeList(HISTORY_KEY, safeEntries);
  }
  return safeEntries;
}

/** Wi-Fi credentials are intentionally never retained in automatic history. */
export function shouldRecordHistory(type: QrType): boolean {
  return type !== 'wifi';
}

/** Newest first, deduplicated by payload, capped at HISTORY_LIMIT. */
export function prependHistory(history: HistoryEntry[], entry: HistoryEntry): HistoryEntry[] {
  const withoutDuplicate = history.filter((existing) => existing.payload !== entry.payload);
  return [entry, ...withoutDuplicate].slice(0, HISTORY_LIMIT);
}

export function writeHistory(entries: HistoryEntry[]): Promise<void> {
  return writeList(HISTORY_KEY, entries);
}
