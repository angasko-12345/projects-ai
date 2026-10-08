export type QrType = 'text' | 'url' | 'email' | 'phone' | 'wifi';

export type WifiSecurity = 'wpa' | 'wep' | 'none';

export interface WifiFields {
  ssid: string;
  password: string;
  security: WifiSecurity;
  hidden: boolean;
}

export interface QrDraft {
  type: QrType;
  text: string;
  url: string;
  email: string;
  phone: string;
  wifi: WifiFields;
}

export type QrStyle = 'square' | 'rounded' | 'dots';

export interface QrAppearance {
  foreground: string;
  background: string;
  style: QrStyle;
  logoUri: string | null;
}

export interface SavedCode {
  id: string;
  createdAt: number;
  label: string;
  draft: QrDraft;
  appearance: QrAppearance;
}

export interface HistoryEntry {
  id: string;
  createdAt: number;
  type: QrType;
  label: string;
  payload: string;
  draft: QrDraft;
}

export type ThemeMode = 'system' | 'light' | 'dark';

export interface AppSettings {
  themeMode: ThemeMode;
}

export const EMPTY_DRAFT: QrDraft = {
  type: 'text',
  text: '',
  url: '',
  email: '',
  phone: '',
  wifi: { ssid: '', password: '', security: 'wpa', hidden: false },
};

export const DEFAULT_APPEARANCE: QrAppearance = {
  foreground: '#000000',
  background: '#FFFFFF',
  style: 'square',
  logoUri: null,
};

export const DEFAULT_SETTINGS: AppSettings = { themeMode: 'system' };
