import type { QrDraft, WifiFields } from '../types';

export const MAX_CONTENT_LENGTH = 1000;
export const MAX_SSID_LENGTH = 32;
export const MIN_WIFI_PASSWORD_LENGTH = 8;
export const MAX_WIFI_PASSWORD_LENGTH = 63;

export type ContentState =
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ok'; payload: string };

const EMAIL_PATTERN = /^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$/;
const URL_SCHEME_PATTERN = /^[a-z][a-z0-9+.-]*:\/\//i;
const SCHEME_PREFIX_PATTERN = /^([a-z][a-z0-9+.-]*):/i;
const PHONE_ALLOWED_PATTERN = /^\+?[\d\s\-().]+$/;
const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

const TOO_LONG_MESSAGE = `Too much content. Keep it under ${MAX_CONTENT_LENGTH} characters.`;

const INVALID_URL_MESSAGE = 'Enter a valid web address.';

// Only web schemes are allowed in the URL field. These stay rejected even
// when the tail after the colon looks like a port number (e.g. "tel:123"),
// which is how host:port input is otherwise allowed to pass as schemeless.
const DISALLOWED_URL_SCHEMES: Record<string, true> = {
  javascript: true,
  file: true,
  content: true,
  ftp: true,
  data: true,
  intent: true,
  mailto: true,
  tel: true,
  sms: true,
};

export function normalizeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!URL_SCHEME_PATTERN.test(trimmed)) {
    return `https://${trimmed}`;
  }
  return trimmed;
}

function urlHost(url: string): string {
  const afterScheme = url.split('://')[1] ?? '';
  return afterScheme.split(/[/?#]/)[0] ?? '';
}

export function escapeWifiValue(value: string): string {
  return value.replace(/([\\;,:"])/g, '\\$1');
}

export function buildWifiPayload(wifi: WifiFields): string {
  const security = wifi.security === 'none' ? 'nopass' : wifi.security === 'wep' ? 'WEP' : 'WPA';
  const ssid = escapeWifiValue(wifi.ssid.trim());
  let payload = `WIFI:T:${security};S:${ssid};`;
  if (wifi.security !== 'none' && wifi.password.length > 0) {
    payload += `P:${escapeWifiValue(wifi.password)};`;
  }
  if (wifi.hidden) {
    payload += 'H:true;';
  }
  return `${payload};`;
}

export function isHexColor(value: string): boolean {
  return HEX_COLOR_PATTERN.test(value);
}

function ok(payload: string): ContentState {
  if (payload.length > MAX_CONTENT_LENGTH) {
    return { status: 'error', message: TOO_LONG_MESSAGE };
  }
  return { status: 'ok', payload };
}

function validateText(value: string): ContentState {
  const trimmed = value.trim();
  if (!trimmed) {
    return { status: 'empty' };
  }
  return ok(trimmed);
}

function validateUrl(value: string): ContentState {
  const trimmed = value.trim();
  if (!trimmed) {
    return { status: 'empty' };
  }
  if (/\s/.test(trimmed)) {
    return { status: 'error', message: INVALID_URL_MESSAGE };
  }
  const schemeMatch = SCHEME_PREFIX_PATTERN.exec(trimmed);
  if (schemeMatch) {
    const scheme = schemeMatch[1].toLowerCase();
    const rest = trimmed.slice(schemeMatch[0].length);
    const isWebScheme = scheme === 'http' || scheme === 'https';
    const looksLikePort = /^\d/.test(rest);
    if (scheme in DISALLOWED_URL_SCHEMES || (!isWebScheme && !looksLikePort)) {
      return { status: 'error', message: INVALID_URL_MESSAGE };
    }
  }
  const normalized = normalizeUrl(trimmed);
  const host = urlHost(normalized);
  if (!host) {
    return { status: 'error', message: INVALID_URL_MESSAGE };
  }
  return ok(normalized);
}

function validateEmail(value: string): ContentState {
  const trimmed = value.trim();
  if (!trimmed) {
    return { status: 'empty' };
  }
  if (!EMAIL_PATTERN.test(trimmed)) {
    return { status: 'error', message: 'Enter a valid email address.' };
  }
  return ok(`mailto:${trimmed}`);
}

function validatePhone(value: string): ContentState {
  const trimmed = value.trim();
  if (!trimmed) {
    return { status: 'empty' };
  }
  const digits = trimmed.replace(/\D/g, '');
  if (!PHONE_ALLOWED_PATTERN.test(trimmed) || digits.length < 7) {
    return { status: 'error', message: 'Enter a valid phone number.' };
  }
  const tel = trimmed.replace(/[^\d+]/g, '');
  return ok(`tel:${tel}`);
}

function validateWifi(wifi: WifiFields): ContentState {
  const ssid = wifi.ssid.trim();
  if (!ssid) {
    return { status: 'empty' };
  }
  if (ssid.length > MAX_SSID_LENGTH) {
    return { status: 'error', message: `Network name must be ${MAX_SSID_LENGTH} characters or fewer.` };
  }
  if (wifi.security !== 'none') {
    if (!wifi.password) {
      return { status: 'empty' };
    }
    if (wifi.password.length < MIN_WIFI_PASSWORD_LENGTH) {
      return {
        status: 'error',
        message: `Password must be at least ${MIN_WIFI_PASSWORD_LENGTH} characters.`,
      };
    }
    if (wifi.password.length > MAX_WIFI_PASSWORD_LENGTH) {
      return {
        status: 'error',
        message: `Password must be ${MAX_WIFI_PASSWORD_LENGTH} characters or fewer.`,
      };
    }
  }
  return ok(buildWifiPayload(wifi));
}

export function validateDraft(draft: QrDraft): ContentState {
  switch (draft.type) {
    case 'text':
      return validateText(draft.text);
    case 'url':
      return validateUrl(draft.url);
    case 'email':
      return validateEmail(draft.email);
    case 'phone':
      return validatePhone(draft.phone);
    case 'wifi':
      return validateWifi(draft.wifi);
  }
}

export function contentLabel(draft: QrDraft): string {
  switch (draft.type) {
    case 'text':
      return draft.text.trim().replace(/\s+/g, ' ').slice(0, 48);
    case 'url':
      return normalizeUrl(draft.url).slice(0, 48);
    case 'email':
      return draft.email.trim().slice(0, 48);
    case 'phone':
      return draft.phone.trim().slice(0, 48);
    case 'wifi':
      return draft.wifi.ssid.trim().slice(0, 48) || 'Wi-Fi';
  }
}
