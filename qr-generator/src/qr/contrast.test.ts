import { checkColors, contrastRatio, relativeLuminance, sanitizeAppearance } from './contrast';
import { DEFAULT_APPEARANCE } from '../types';

describe('relativeLuminance', () => {
  it('is 1 for white and 0 for black', () => {
    expect(relativeLuminance('#FFFFFF')).toBeCloseTo(1, 10);
    expect(relativeLuminance('#000000')).toBeCloseTo(0, 10);
  });

  it('matches the published value for mid-gray', () => {
    expect(relativeLuminance('#808080')).toBeCloseTo(0.2159, 3);
  });

  it('treats shorthand and uppercase hex the same', () => {
    expect(relativeLuminance('#FFF')).toBeCloseTo(1, 10);
    expect(relativeLuminance('#abcdef')).toBe(relativeLuminance('#ABCDEF'));
  });

  it('counts unparseable colors as black', () => {
    expect(relativeLuminance('red')).toBe(0);
    expect(relativeLuminance('#GGHHII')).toBe(0);
  });
});

describe('contrastRatio', () => {
  it('is 21 for black on white', () => {
    expect(contrastRatio('#000000', '#FFFFFF')).toBeCloseTo(21, 9);
  });

  it('is 1 for a color against itself', () => {
    expect(contrastRatio('#1A2B3C', '#1A2B3C')).toBe(1);
  });

  it('is symmetric', () => {
    expect(contrastRatio('#767676', '#FFFFFF')).toBeCloseTo(
      contrastRatio('#FFFFFF', '#767676'),
      10,
    );
  });

  it('matches the known AA-boundary gray pair', () => {
    // #767676 on white is the classic 4.54:1 "just passes AA" value.
    expect(contrastRatio('#767676', '#FFFFFF')).toBeCloseTo(4.54, 2);
  });

  it('handles shorthand hex', () => {
    expect(contrastRatio('#000', '#FFF')).toBeCloseTo(21, 9);
  });
});

describe('checkColors', () => {
  it('accepts black on white', () => {
    expect(checkColors('#000000', '#FFFFFF')).toBe('ok');
  });

  it('accepts a known mid-gray pair with enough contrast', () => {
    expect(checkColors('#767676', '#FFFFFF')).toBe('ok');
  });

  it('flags low contrast when the ratio is below 3', () => {
    expect(checkColors('#999999', '#FFFFFF')).toBe('low-contrast');
    expect(checkColors('#CCCCCC', '#FFFFFF')).toBe('low-contrast');
  });

  it('flags a same-color pair as low contrast', () => {
    expect(checkColors('#777777', '#777777')).toBe('low-contrast');
  });

  it('flags inversion when light sits on dark with enough contrast', () => {
    expect(checkColors('#FFFFFF', '#000000')).toBe('inverted');
    expect(checkColors('#DDDDDD', '#000000')).toBe('inverted');
  });

  it('rejects malformed colors', () => {
    expect(checkColors('red', '#FFFFFF')).toBe('low-contrast');
    expect(checkColors('#000000', 'blue')).toBe('low-contrast');
    expect(checkColors('#12345', '#FFFFFF')).toBe('low-contrast');
    expect(checkColors('#GGHHII', '#FFFFFF')).toBe('low-contrast');
  });

  it('treats uppercase, lowercase and shorthand hex alike', () => {
    expect(checkColors('#1d4ed8', '#FFFFFF')).toBe('ok');
    expect(checkColors('#1D4ED8', '#FFFFFF')).toBe('ok');
    expect(checkColors('#FFF', '#000000')).toBe('inverted');
    expect(checkColors('#fff', '#000000')).toBe('inverted');
    expect(checkColors('#000', '#FFF')).toBe('ok');
  });
});

describe('sanitizeAppearance', () => {
  it('keeps an acceptable appearance untouched', () => {
    const appearance = {
      foreground: '#1D4ED8',
      background: '#FFFFFF',
      style: 'dots' as const,
      logoUri: 'file:///logo.png',
    };
    expect(sanitizeAppearance(appearance)).toBe(appearance);
  });

  it('falls back to default colors for an invalid pair', () => {
    const appearance = {
      foreground: '#FFFFFF',
      background: '#000000',
      style: 'rounded' as const,
      logoUri: 'file:///logo.png',
    };
    const clean = sanitizeAppearance(appearance);
    expect(clean.foreground).toBe(DEFAULT_APPEARANCE.foreground);
    expect(clean.background).toBe(DEFAULT_APPEARANCE.background);
    expect(clean.style).toBe('rounded');
    expect(clean.logoUri).toBe('file:///logo.png');
  });

  it('falls back for a malformed stored color', () => {
    const clean = sanitizeAppearance({
      ...DEFAULT_APPEARANCE,
      foreground: 'not-a-color',
    });
    expect(clean.foreground).toBe(DEFAULT_APPEARANCE.foreground);
    expect(clean.background).toBe(DEFAULT_APPEARANCE.background);
  });
});
