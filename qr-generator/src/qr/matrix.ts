/* eslint-disable import/no-named-as-default, import/no-named-as-default-member -- qrcode-generator is a CJS callable; stringToBytes is its documented customization hook. */
import qrcode from 'qrcode-generator';

export type QrErrorCorrection = 'L' | 'M' | 'Q' | 'H';

export interface QrMatrix {
  readonly size: number;
  isDark(row: number, col: number): boolean;
}

export class QrEncodingError extends Error {}

// qrcode-generator defaults to a Latin-1 byte mode, which mangles non-ASCII input.
export function utf8Bytes(value: string): number[] {
  const bytes: number[] = [];
  for (let i = 0; i < value.length; i += 1) {
    let code = value.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < value.length) {
      const next = value.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        code = 0x10000 + ((code - 0xd800) << 10) + (next - 0xdc00);
        i += 1;
      }
    }
    if (code < 0x80) {
      bytes.push(code);
    } else if (code < 0x800) {
      bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f));
    } else if (code < 0x10000) {
      bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f));
    } else {
      bytes.push(
        0xf0 | (code >> 18),
        0x80 | ((code >> 12) & 0x3f),
        0x80 | ((code >> 6) & 0x3f),
        0x80 | (code & 0x3f),
      );
    }
  }
  return bytes;
}

qrcode.stringToBytes = utf8Bytes;

export function buildQrMatrix(payload: string, errorCorrection: QrErrorCorrection): QrMatrix {
  const qr = qrcode(0, errorCorrection);
  qr.addData(payload, 'Byte');
  try {
    qr.make();
  } catch {
    throw new QrEncodingError('This content is too large for a QR code.');
  }
  const size = qr.getModuleCount();
  return {
    size,
    isDark: (row, col) => qr.isDark(row, col),
  };
}
