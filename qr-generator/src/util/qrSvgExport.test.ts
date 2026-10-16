import * as FileSystem from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { readLogoDataUri, saveQrSvg, shareQrSvg, svgFileName } from './qrSvgExport';

jest.mock('expo-file-system/legacy', () => ({
  readAsStringAsync: jest.fn(),
  writeAsStringAsync: jest.fn(),
  cacheDirectory: 'file:///cache/',
  StorageAccessFramework: {
    requestDirectoryPermissionsAsync: jest.fn(),
    makeDirectoryAsync: jest.fn(),
    readDirectoryAsync: jest.fn(),
    createFileAsync: jest.fn(),
  },
}));

jest.mock('expo-sharing', () => ({
  isAvailableAsync: jest.fn(),
  shareAsync: jest.fn(),
}));

const fs = FileSystem as unknown as {
  readAsStringAsync: jest.Mock;
  writeAsStringAsync: jest.Mock;
  cacheDirectory: string;
  StorageAccessFramework: {
    requestDirectoryPermissionsAsync: jest.Mock;
    makeDirectoryAsync: jest.Mock;
    readDirectoryAsync: jest.Mock;
    createFileAsync: jest.Mock;
  };
};
const saf = fs.StorageAccessFramework;
const sharing = Sharing as unknown as { isAvailableAsync: jest.Mock; shareAsync: jest.Mock };

const SVG = '<svg xmlns="http://www.w3.org/2000/svg"></svg>';

beforeEach(() => {
  fs.readAsStringAsync.mockResolvedValue('');
  fs.writeAsStringAsync.mockResolvedValue(undefined);
  saf.requestDirectoryPermissionsAsync.mockResolvedValue({ granted: false, directoryUri: '' });
  saf.makeDirectoryAsync.mockResolvedValue('file:///tree/QR%20Generator');
  saf.readDirectoryAsync.mockResolvedValue([]);
  saf.createFileAsync.mockResolvedValue('file:///tree/QR%20Generator/qr-code.svg');
  sharing.isAvailableAsync.mockResolvedValue(true);
  sharing.shareAsync.mockResolvedValue(undefined);
});

describe('svgFileName', () => {
  it('uses the shared timestamp name with an svg extension', () => {
    expect(svgFileName()).toMatch(/^qr-code-\d{8}-\d{6}-\d+\.svg$/);
  });
});

describe('readLogoDataUri', () => {
  it('builds a png data URI from the read bytes', async () => {
    fs.readAsStringAsync.mockResolvedValue('QUJD');
    await expect(readLogoDataUri('file:///picked/logo.png')).resolves.toBe('data:image/png;base64,QUJD');
    expect(fs.readAsStringAsync).toHaveBeenCalledWith('file:///picked/logo.png', { encoding: 'base64' });
  });

  it('detects the mime type from the file extension', async () => {
    fs.readAsStringAsync.mockResolvedValue('QUJD');
    await expect(readLogoDataUri('file:///picked/logo.jpeg')).resolves.toBe('data:image/jpeg;base64,QUJD');
  });

  it('falls back to png for unknown extensions', async () => {
    fs.readAsStringAsync.mockResolvedValue('QUJD');
    await expect(readLogoDataUri('file:///picked/logo.unknownext')).resolves.toBe('data:image/png;base64,QUJD');
  });

  it('returns null when the file cannot be read', async () => {
    fs.readAsStringAsync.mockRejectedValue(new Error('no access'));
    await expect(readLogoDataUri('file:///picked/logo.png')).resolves.toBeNull();
  });

  it('returns null for empty file contents', async () => {
    fs.readAsStringAsync.mockResolvedValue('');
    await expect(readLogoDataUri('file:///picked/logo.png')).resolves.toBeNull();
  });
});

describe('shareQrSvg', () => {
  it('writes a cache file and shares it as SVG', async () => {
    await expect(shareQrSvg(SVG)).resolves.toEqual({ status: 'shared' });
    expect(fs.writeAsStringAsync).toHaveBeenCalledWith(
      expect.stringMatching(/^file:\/\/\/cache\/qr-code-\d{8}-\d{6}-\d+\.svg$/),
      SVG,
      { encoding: 'utf8' },
    );
    expect(sharing.shareAsync).toHaveBeenCalledWith(
      expect.stringMatching(/\.svg$/),
      expect.objectContaining({ mimeType: 'image/svg+xml' }),
    );
  });

  it('reports when sharing is unavailable', async () => {
    sharing.isAvailableAsync.mockResolvedValue(false);
    const result = await shareQrSvg(SVG);
    expect(result.status).toBe('unavailable');
    expect(fs.writeAsStringAsync).not.toHaveBeenCalled();
  });

  it('reports a write failure', async () => {
    fs.writeAsStringAsync.mockRejectedValue(new Error('disk full'));
    const result = await shareQrSvg(SVG);
    expect(result).toEqual({ status: 'error', message: 'disk full' });
  });
});

describe('saveQrSvg', () => {
  it('saves into the chosen directory and reports it', async () => {
    saf.requestDirectoryPermissionsAsync.mockResolvedValue({
      granted: true,
      directoryUri: 'file:///tree/root',
    });
    await expect(saveQrSvg(SVG, null)).resolves.toEqual({
      status: 'saved',
      directoryUri: 'file:///tree/root',
    });
    expect(saf.createFileAsync).toHaveBeenCalledWith(
      'file:///tree/QR%20Generator',
      expect.any(String),
      'image/svg+xml',
    );
    expect(fs.writeAsStringAsync).toHaveBeenCalledWith(
      'file:///tree/QR%20Generator/qr-code.svg',
      SVG,
      { encoding: 'utf8' },
    );
  });

  it('returns cancelled when the user denies access', async () => {
    saf.requestDirectoryPermissionsAsync.mockResolvedValue({ granted: false, directoryUri: '' });
    await expect(saveQrSvg(SVG, 'file:///tree/root')).resolves.toEqual({ status: 'cancelled' });
    expect(saf.createFileAsync).not.toHaveBeenCalled();
  });

  it('reports a save failure', async () => {
    saf.requestDirectoryPermissionsAsync.mockResolvedValue({
      granted: true,
      directoryUri: 'file:///tree/root',
    });
    saf.createFileAsync.mockRejectedValue(new Error('cannot create file'));
    await expect(saveQrSvg(SVG, null)).resolves.toEqual({
      status: 'error',
      message: 'cannot create file',
    });
  });
});
