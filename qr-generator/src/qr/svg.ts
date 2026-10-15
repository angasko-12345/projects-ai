import type { QrAppearance } from '../types';
import { isHexColor } from './content';
import type { QrMatrix } from './matrix';
import { buildShapePaths } from './shape';

/** Quiet-zone width in modules; must match the on-screen preview (QrPreviewCard). */
export const SVG_QUIET_ZONE = 4;
/** Intrinsic size of an exported SVG in CSS pixels when no size is given. */
export const DEFAULT_SVG_SIZE = 1024;

// Logo plate and image sizes as a fraction of the drawing area. These mirror
// the absolute overlay sizes used in QrPreviewCard so the SVG matches the
// preview that gets captured to PNG.
const LOGO_PLATE_RATIO = 0.24;
const LOGO_IMAGE_RATIO = 0.18;

export interface QrSvgOptions {
  matrix: QrMatrix;
  appearance: QrAppearance;
  /** Intrinsic width/height of the document; the viewBox stays in module units. */
  sizePx?: number;
  /** Optional self-contained image (data URI) drawn inside the central plate. */
  logoHref?: string | null;
}

function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (char) => {
    switch (char) {
      case '&':
        return '&amp;';
      case '<':
        return '&lt;';
      case '>':
        return '&gt;';
      case '"':
        return '&quot;';
      default:
        return '&apos;';
    }
  });
}

// Three-decimal rounding matches buildShapePaths and keeps output byte-stable.
function num(value: number): string {
  return String(Math.round(value * 1000) / 1000);
}

function safeColor(value: string, fallback: string): string {
  return isHexColor(value) ? value : fallback;
}

function normalizedSize(value: number | undefined): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? Math.round(value)
    : DEFAULT_SVG_SIZE;
}

// Serializes a QR matrix and appearance to a standalone SVG document. Colors
// are validated and fall back to black/white so the exported code stays
// scannable, and the body/eye paths come straight from the shared shape logic.
export function buildQrSvg({ matrix, appearance, sizePx, logoHref }: QrSvgOptions): string {
  const { size } = matrix;
  const viewSize = size + SVG_QUIET_ZONE * 2;
  const min = -SVG_QUIET_ZONE;
  const { modules, eyes } = buildShapePaths(matrix, appearance.style);
  const foreground = safeColor(appearance.foreground, '#000000');
  const background = safeColor(appearance.background, '#FFFFFF');
  const canvas = normalizedSize(sizePx);

  const parts = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${canvas}" height="${canvas}" viewBox="${min} ${min} ${viewSize} ${viewSize}" role="img" aria-label="QR code">`,
    `<rect x="${min}" y="${min}" width="${viewSize}" height="${viewSize}" fill="${background}"/>`,
    `<path d="${modules}" fill="${foreground}"/>`,
    `<path d="${eyes}" fill="${foreground}" fill-rule="evenodd"/>`,
  ];

  if (logoHref) {
    const center = size / 2;
    const plate = viewSize * LOGO_PLATE_RATIO;
    const image = viewSize * LOGO_IMAGE_RATIO;
    const plateOffset = center - plate / 2;
    const imageOffset = center - image / 2;
    const href = escapeXml(logoHref);
    parts.push(
      `<rect x="${num(plateOffset)}" y="${num(plateOffset)}" width="${num(plate)}" height="${num(plate)}" fill="${background}"/>`,
      `<image href="${href}" xlink:href="${href}" x="${num(imageOffset)}" y="${num(imageOffset)}" width="${num(image)}" height="${num(image)}" preserveAspectRatio="xMidYMid meet"/>`,
    );
  }

  parts.push('</svg>');
  return parts.join('\n');
}
