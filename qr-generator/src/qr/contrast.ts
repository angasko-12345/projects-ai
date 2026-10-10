import { DEFAULT_APPEARANCE, type QrAppearance } from '../types';
import { isHexColor } from './content';

export type ColorCheck = 'ok' | 'low-contrast' | 'inverted';

const SHORT_HEX_PATTERN = /^#[0-9a-fA-F]{3}$/;

// Returns [r, g, b] 0-255, or null when the color is not #RRGGBB/#RGB.
function parseHex(color: string): [number, number, number] | null {
  const value = color.trim();
  if (isHexColor(value)) {
    return [
      parseInt(value.slice(1, 3), 16),
      parseInt(value.slice(3, 5), 16),
      parseInt(value.slice(5, 7), 16),
    ];
  }
  if (SHORT_HEX_PATTERN.test(value)) {
    const [r, g, b] = value.slice(1).split('');
    return [parseInt(r + r, 16), parseInt(g + g, 16), parseInt(b + b, 16)];
  }
  return null;
}

function channelLuminance(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

// WCAG 2.x relative luminance; unparseable colors count as black (0) so
// checkColors can reject the pair before any ratio math.
export function relativeLuminance(color: string): number {
  const rgb = parseHex(color);
  if (!rgb) {
    return 0;
  }
  const [r, g, b] = rgb.map(channelLuminance);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(fg: string, bg: string): number {
  const fgLum = relativeLuminance(fg);
  const bgLum = relativeLuminance(bg);
  const [high, low] = fgLum >= bgLum ? [fgLum, bgLum] : [bgLum, fgLum];
  return (high + 0.05) / (low + 0.05);
}

export function checkColors(fg: string, bg: string): ColorCheck {
  // Malformed input is never an acceptable pair, and the status type has no
  // value for it, so it reports the blocking outcome.
  if (!parseHex(fg) || !parseHex(bg)) {
    return 'low-contrast';
  }
  if (contrastRatio(fg, bg) < 3) {
    return 'low-contrast';
  }
  if (relativeLuminance(fg) > relativeLuminance(bg)) {
    return 'inverted';
  }
  return 'ok';
}

// Falls back to the default colors when a stored pair is unacceptable;
// style and logo are preserved and nothing is written back to storage.
export function sanitizeAppearance(appearance: QrAppearance): QrAppearance {
  if (checkColors(appearance.foreground, appearance.background) === 'ok') {
    return appearance;
  }
  return {
    ...appearance,
    foreground: DEFAULT_APPEARANCE.foreground,
    background: DEFAULT_APPEARANCE.background,
  };
}
