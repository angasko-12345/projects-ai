import AsyncStorage from '@react-native-async-storage/async-storage';

import { EMPTY_DRAFT, DEFAULT_SETTINGS, type HistoryEntry } from '../types';
import { HISTORY_LIMIT, loadSavedCodes, loadSettings, prependHistory, saveSettings } from './storage';

function entry(payload: string): HistoryEntry {
  return {
    id: `id-${payload}`,
    createdAt: 1,
    type: 'text',
    label: payload,
    payload,
    draft: EMPTY_DRAFT,
  };
}

beforeEach(async () => {
  await AsyncStorage.clear();
});

describe('prependHistory', () => {
  it('puts the newest entry first', () => {
    const result = prependHistory([entry('a'), entry('b')], entry('c'));
    expect(result.map((item) => item.payload)).toEqual(['c', 'a', 'b']);
  });

  it('moves a repeated payload to the front instead of duplicating it', () => {
    const result = prependHistory([entry('a'), entry('b')], entry('a'));
    expect(result.map((item) => item.payload)).toEqual(['a', 'b']);
    expect(result).toHaveLength(2);
  });

  it('caps the list at the history limit', () => {
    const many = Array.from({ length: HISTORY_LIMIT }, (_, index) => entry(`item-${index}`));
    const result = prependHistory(many, entry('newest'));
    expect(result).toHaveLength(HISTORY_LIMIT);
    expect(result[0].payload).toBe('newest');
    expect(result[result.length - 1].payload).toBe(`item-${HISTORY_LIMIT - 2}`);
  });
});

describe('settings persistence', () => {
  it('returns defaults when nothing is stored', async () => {
    await expect(loadSettings()).resolves.toEqual(DEFAULT_SETTINGS);
  });

  it('round-trips the theme mode', async () => {
    await saveSettings({ themeMode: 'dark' });
    await expect(loadSettings()).resolves.toEqual({ themeMode: 'dark' });
  });

  it('falls back to defaults on corrupt data', async () => {
    await AsyncStorage.setItem('@qrgen:v1:settings', '{not json');
    await expect(loadSettings()).resolves.toEqual(DEFAULT_SETTINGS);
  });

  it('ignores unknown theme modes from older or newer versions', async () => {
    await AsyncStorage.setItem('@qrgen:v1:settings', JSON.stringify({ themeMode: 'neon' }));
    await expect(loadSettings()).resolves.toEqual(DEFAULT_SETTINGS);
  });
});

describe('saved codes persistence', () => {
  it('returns an empty list when the stored JSON is corrupt', async () => {
    await AsyncStorage.setItem('@qrgen:v1:saved', '[');
    await expect(loadSavedCodes()).resolves.toEqual([]);
  });
});
