import type { QrStyle } from '../types';
import { buildQrMatrix, type QrMatrix } from './matrix';
import { buildShapePaths } from './shape';

const STYLES: QrStyle[] = ['square', 'rounded', 'dots'];

/** Dark modules outside the three finder areas, which the body path must cover. */
function countBodyModules(matrix: QrMatrix): number {
  const { size } = matrix;
  let count = 0;
  for (let row = 0; row < size; row += 1) {
    for (let col = 0; col < size; col += 1) {
      const inFinder =
        (row < 8 && col < 8) || (row < 8 && col >= size - 8) || (row >= size - 8 && col < 8);
      if (!inFinder && matrix.isDark(row, col)) {
        count += 1;
      }
    }
  }
  return count;
}

describe('buildShapePaths', () => {
  const matrix = buildQrMatrix('shape test payload', 'M');
  const bodyModules = countBodyModules(matrix);

  it.each(STYLES)('%s: covers every non-finder dark module exactly once', (style) => {
    const paths = buildShapePaths(matrix, style);
    const subpaths = paths.modules.split('z').length - 1;
    expect(subpaths).toBe(bodyModules);
  });

  it.each(STYLES)('%s: draws nine finder subpaths', (style) => {
    const paths = buildShapePaths(matrix, style);
    const starts = paths.eyes.split('M').length - 1;
    expect(starts).toBe(9);
  });

  it('square: emits only straight-line segments in the body', () => {
    const paths = buildShapePaths(matrix, 'square');
    expect(paths.modules).not.toContain('a');
    expect(paths.modules).not.toContain('Q');
  });

  it('rounded and dots: emit curved segments in the body', () => {
    expect(buildShapePaths(matrix, 'rounded').modules).toContain('a');
    expect(buildShapePaths(matrix, 'dots').modules).toContain('a');
  });
});
