import { QrEncodingError, buildQrMatrix, utf8Bytes } from './matrix';

describe('utf8Bytes', () => {
  it('encodes ASCII directly', () => {
    expect(utf8Bytes('A')).toEqual([65]);
    expect(utf8Bytes('AB')).toEqual([65, 66]);
  });

  it('encodes two-byte characters', () => {
    expect(utf8Bytes('é')).toEqual([0xc3, 0xa9]);
  });

  it('encodes three-byte characters', () => {
    expect(utf8Bytes('日')).toEqual([0xe6, 0x97, 0xa5]);
  });

  it('encodes surrogate pairs as four-byte sequences', () => {
    expect(utf8Bytes('😀')).toEqual([0xf0, 0x9f, 0x98, 0x80]);
  });

  it('mixes ascii and multi-byte characters', () => {
    expect(utf8Bytes('Aé')).toEqual([65, 0xc3, 0xa9]);
  });
});

describe('buildQrMatrix', () => {
  it('produces a version 1 matrix for short content', () => {
    const matrix = buildQrMatrix('hello', 'M');
    expect(matrix.size).toBe(21);
  });

  it('draws the standard finder pattern in the top-left corner', () => {
    const matrix = buildQrMatrix('hello', 'M');
    expect(matrix.isDark(0, 0)).toBe(true);
    expect(matrix.isDark(1, 1)).toBe(false);
    expect(matrix.isDark(3, 3)).toBe(true);
  });

  it('builds deterministically for the same input', () => {
    const first = buildQrMatrix('stable input', 'M');
    const second = buildQrMatrix('stable input', 'M');
    expect(first.size).toBe(second.size);
    for (let row = 0; row < first.size; row += 1) {
      for (let col = 0; col < first.size; col += 1) {
        expect(first.isDark(row, col)).toBe(second.isDark(row, col));
      }
    }
  });

  it('handles multi-byte content without throwing', () => {
    const matrix = buildQrMatrix('héllo 日本語 😀', 'M');
    expect(matrix.size).toBeGreaterThanOrEqual(21);
  });

  it('raises a typed error when the content cannot fit', () => {
    expect(() => buildQrMatrix('x'.repeat(4000), 'H')).toThrow(QrEncodingError);
  });
});
