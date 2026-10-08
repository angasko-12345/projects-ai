export interface Palette {
  background: string;
  surface: string;
  surfaceMuted: string;
  border: string;
  text: string;
  textMuted: string;
  accent: string;
  accentSoft: string;
  onAccent: string;
  danger: string;
  dangerSoft: string;
  success: string;
  backdrop: string;
}

export const lightPalette: Palette = {
  background: '#F3F4F6',
  surface: '#FFFFFF',
  surfaceMuted: '#EAECF0',
  border: '#D8DCE3',
  text: '#14161A',
  textMuted: '#565E6B',
  accent: '#0B6E5F',
  accentSoft: '#DDF0EC',
  onAccent: '#FFFFFF',
  danger: '#A4262C',
  dangerSoft: '#FBE9EA',
  success: '#0F7B3D',
  backdrop: 'rgba(16, 18, 22, 0.45)',
};

export const darkPalette: Palette = {
  background: '#0E1013',
  surface: '#171A1F',
  surfaceMuted: '#1F242B',
  border: '#2A3038',
  text: '#E9ECF1',
  textMuted: '#9BA3AF',
  accent: '#3FBF9F',
  accentSoft: '#12312A',
  onAccent: '#06251F',
  danger: '#F98A84',
  dangerSoft: '#3A1D1D',
  success: '#5BD08A',
  backdrop: 'rgba(0, 0, 0, 0.6)',
};

export const SPACING = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 } as const;

export const RADIUS = { sm: 8, md: 12, lg: 16, pill: 999 } as const;

export const TYPE = { title: 22, heading: 17, body: 16, label: 14, caption: 13 } as const;

export const MIN_TAP_SIZE = 48;
