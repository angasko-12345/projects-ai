import type { QrAppearance, QrStyle } from '../types';
import { buildQrMatrix } from './matrix';
import { buildShapePaths } from './shape';
import { DEFAULT_SVG_SIZE, SVG_QUIET_ZONE, buildQrSvg } from './svg';

const APPEARANCE: QrAppearance = {
  foreground: '#000000',
  background: '#FFFFFF',
  style: 'square',
  logoUri: null,
};

const matrix = buildQrMatrix('https://example.com/svg-test', 'M');
const viewSize = matrix.size + SVG_QUIET_ZONE * 2;

interface ParsedPath {
  d: string;
  fill: string;
  fillRule: string | null;
}

function parsePaths(svg: string): ParsedPath[] {
  return Array.from(svg.matchAll(/<path d="([^"]*)" fill="([^"]*)"(?: fill-rule="([^"]*)")?\s*\/>/g)).map(
    (match) => ({ d: match[1], fill: match[2], fillRule: match[3] ?? null }),
  );
}

describe('buildQrSvg document', () => {
  it('emits a standalone XML document with the SVG namespace', () => {
    const svg = buildQrSvg({ matrix, appearance: APPEARANCE });
    expect(svg.startsWith('<?xml version="1.0" encoding="UTF-8"?>')).toBe(true);
    expect(svg).toContain('xmlns="http://www.w3.org/2000/svg"');
    expect(svg).toContain('<svg ');
    expect(svg.endsWith('</svg>')).toBe(true);
  });

  it('defaults the intrinsic size and honours an explicit size', () => {
    expect(buildQrSvg({ matrix, appearance: APPEARANCE })).toContain(
      `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${DEFAULT_SVG_SIZE}" height="${DEFAULT_SVG_SIZE}"`,
    );
    expect(buildQrSvg({ matrix, appearance: APPEARANCE, sizePx: 300 })).toContain('width="300" height="300"');
  });

  it('falls back to the default size for invalid size input', () => {
    for (const sizePx of [Number.NaN, -5, 0, Number.POSITIVE_INFINITY]) {
      expect(buildQrSvg({ matrix, appearance: APPEARANCE, sizePx })).toContain(
        `width="${DEFAULT_SVG_SIZE}" height="${DEFAULT_SVG_SIZE}"`,
      );
    }
  });

  it('keeps the preview quiet zone in the viewBox', () => {
    const svg = buildQrSvg({ matrix, appearance: APPEARANCE });
    expect(svg).toContain(`viewBox="-4 -4 ${viewSize} ${viewSize}"`);
    expect(svg).toContain(
      `<rect x="-4" y="-4" width="${viewSize}" height="${viewSize}" fill="#FFFFFF"/>`,
    );
  });
});

describe('buildQrSvg shapes and colors', () => {
  it.each<QrStyle>(['square', 'rounded', 'dots'])(
    '%s: reuses the shared shape paths for body and eyes',
    (style) => {
      const svg = buildQrSvg({ matrix, appearance: { ...APPEARANCE, style } });
      const paths = parsePaths(svg);
      const expected = buildShapePaths(matrix, style);

      expect(paths).toHaveLength(2);
      expect(paths[0].d).toBe(expected.modules);
      expect(paths[0].fillRule).toBeNull();
      expect(paths[1].d).toBe(expected.eyes);
      expect(paths[1].fillRule).toBe('evenodd');
    },
  );

  it('applies the appearance foreground to both paths', () => {
    const svg = buildQrSvg({ matrix, appearance: { ...APPEARANCE, foreground: '#1D4ED8' } });
    expect(parsePaths(svg).every((path) => path.fill === '#1D4ED8')).toBe(true);
  });

  it('falls back to black and white when a color is not a hex value', () => {
    const svg = buildQrSvg({
      matrix,
      appearance: { ...APPEARANCE, foreground: 'rgb(0,0,0)', background: 'not-a-color' },
    });
    expect(svg).toContain('fill="#FFFFFF"/>'); // background rect fallback
    expect(parsePaths(svg).every((path) => path.fill === '#000000')).toBe(true);
  });
});

describe('buildQrSvg logo handling', () => {
  const logo = 'data:image/png;base64,AAAA';

  it('omits the plate and image when no logo is supplied', () => {
    const svg = buildQrSvg({ matrix, appearance: APPEARANCE });
    expect(svg).not.toContain('<image');
  });

  it('embeds the logo image and a background plate when supplied', () => {
    const svg = buildQrSvg({ matrix, appearance: APPEARANCE, logoHref: logo });
    expect(svg).toContain(`<image href="${logo}" xlink:href="${logo}"`);
    expect(svg).toContain('preserveAspectRatio="xMidYMid meet"');
    // A second background-colored rect sits behind the image plate.
    const plateRects = svg.match(/<rect[^>]*fill="#FFFFFF"\/>/g) ?? [];
    expect(plateRects).toHaveLength(2);
  });

  it('ignores an empty logo reference', () => {
    expect(buildQrSvg({ matrix, appearance: APPEARANCE, logoHref: '' })).not.toContain('<image');
    expect(buildQrSvg({ matrix, appearance: APPEARANCE, logoHref: null })).not.toContain('<image');
  });

  it('XML-escapes the logo reference', () => {
    const svg = buildQrSvg({
      matrix,
      appearance: APPEARANCE,
      logoHref: 'data:image/svg+xml,<svg a="1">&amp;</svg>',
    });
    expect(svg).toContain('&lt;svg a=&quot;1&quot;&gt;&amp;amp;&lt;/svg&gt;');
    expect(svg).not.toContain('<svg a=');
  });
});

describe('buildQrSvg determinism', () => {
  it('produces identical output for identical input', () => {
    const first = buildQrSvg({ matrix, appearance: APPEARANCE, sizePx: 512, logoHref: 'data:image/png;base64,AAAA' });
    const second = buildQrSvg({ matrix, appearance: APPEARANCE, sizePx: 512, logoHref: 'data:image/png;base64,AAAA' });
    expect(first).toBe(second);
  });
});
