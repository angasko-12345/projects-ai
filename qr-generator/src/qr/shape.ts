import type { QrStyle } from '../types';
import type { QrMatrix } from './matrix';

export interface QrShapePaths {
  /** Dark body modules, drawn as one path with the nonzero rule. */
  modules: string;
  /** Finder eyes: outer ring, hole and center, drawn with the evenodd rule. */
  eyes: string;
}

function round(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function roundedRectPath(x: number, y: number, size: number, radius: number): string {
  const r = Math.min(round(radius), size / 2);
  const w = round(size - 2 * r);
  return (
    `M${round(x + r)} ${round(y)}h${w}` +
    `a${r} ${r} 0 0 1 ${r} ${r}v${w}` +
    `a${r} ${r} 0 0 1 ${-r} ${r}h${-w}` +
    `a${r} ${r} 0 0 1 ${-r} ${-r}v${-w}` +
    `a${r} ${r} 0 0 1 ${r} ${-r}z`
  );
}

function circlePath(cx: number, cy: number, radius: number): string {
  const r = round(radius);
  return `M${round(cx - r)} ${round(cy)}a${r} ${r} 0 1 0 ${round(2 * r)} 0a${r} ${r} 0 1 0 ${round(-2 * r)} 0z`;
}

// Finder cells are excluded from the body path; the eyes path redraws them styled.
function isFinderCell(row: number, col: number, size: number): boolean {
  return (
    (row < 8 && col < 8) || (row < 8 && col >= size - 8) || (row >= size - 8 && col < 8)
  );
}

function bodyModulePath(style: QrStyle, row: number, col: number): string {
  if (style === 'square') {
    return `M${col} ${row}h1v1h-1z`;
  }
  if (style === 'rounded') {
    return roundedRectPath(col + 0.06, row + 0.06, 0.88, 0.3);
  }
  return circlePath(col + 0.5, row + 0.5, 0.46);
}

function eyePaths(style: QrStyle, x: number, y: number): string[] {
  if (style === 'square') {
    return [
      roundedRectPath(x, y, 7, 0),
      roundedRectPath(x + 1, y + 1, 5, 0),
      roundedRectPath(x + 2, y + 2, 3, 0),
    ];
  }
  if (style === 'rounded') {
    return [
      roundedRectPath(x, y, 7, 1.4),
      roundedRectPath(x + 1, y + 1, 5, 1),
      roundedRectPath(x + 2, y + 2, 3, 1),
    ];
  }
  const cx = x + 3.5;
  const cy = y + 3.5;
  return [circlePath(cx, cy, 3.5), circlePath(cx, cy, 2.5), circlePath(cx, cy, 1.5)];
}

export function buildShapePaths(matrix: QrMatrix, style: QrStyle): QrShapePaths {
  const { size } = matrix;
  const modules: string[] = [];
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      if (matrix.isDark(row, col) && !isFinderCell(row, col, size)) {
        modules.push(bodyModulePath(style, row, col));
      }
    }
  }
  const eyes: string[] = [];
  const origins: [number, number][] = [
    [0, 0],
    [size - 7, 0],
    [0, size - 7],
  ];
  for (const [x, y] of origins) {
    eyes.push(...eyePaths(style, x, y));
  }
  return { modules: modules.join(''), eyes: eyes.join('') };
}
