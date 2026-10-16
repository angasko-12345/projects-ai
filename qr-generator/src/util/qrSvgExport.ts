import * as FileSystem from 'expo-file-system/legacy';
import { StorageAccessFramework } from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';

import { resolveTargetDirectory, timestampName, type SaveOutcome, type ShareOutcome } from './qrExport';

export const SVG_MIME_TYPE = 'image/svg+xml';

const LOGO_MIME_TYPES: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  svg: 'image/svg+xml',
};

export function svgFileName(): string {
  return `${timestampName()}.svg`;
}

function mimeTypeForUri(uri: string): string {
  const withoutQuery = uri.split('?')[0];
  const extension = withoutQuery.slice(withoutQuery.lastIndexOf('.') + 1).toLowerCase();
  return LOGO_MIME_TYPES[extension] ?? 'image/png';
}

// Reads a picked image into a self-contained data URI so the exported SVG has
// no external references. Returns null when the file cannot be read.
export async function readLogoDataUri(uri: string): Promise<string | null> {
  try {
    const base64 = await FileSystem.readAsStringAsync(uri, { encoding: 'base64' });
    if (!base64) {
      return null;
    }
    return `data:${mimeTypeForUri(uri)};base64,${base64}`;
  } catch {
    return null;
  }
}

export async function shareQrSvg(svg: string): Promise<ShareOutcome> {
  try {
    if (!(await Sharing.isAvailableAsync())) {
      return { status: 'unavailable', message: 'Sharing is not available on this device.' };
    }
    const directory = FileSystem.cacheDirectory;
    if (!directory) {
      return { status: 'error', message: 'Sharing is not available in this environment.' };
    }
    const uri = `${directory}${svgFileName()}`;
    await FileSystem.writeAsStringAsync(uri, svg, { encoding: 'utf8' });
    await Sharing.shareAsync(uri, {
      mimeType: SVG_MIME_TYPE,
      dialogTitle: 'Share QR code (SVG)',
      UTI: 'public.svg-image',
    });
    return { status: 'shared' };
  } catch (error) {
    return { status: 'error', message: error instanceof Error ? error.message : 'Sharing failed.' };
  }
}

export async function saveQrSvg(
  svg: string,
  initialDirectoryUri: string | null,
): Promise<SaveOutcome> {
  try {
    const permission = await StorageAccessFramework.requestDirectoryPermissionsAsync(
      initialDirectoryUri,
    );
    if (!permission.granted) {
      return { status: 'cancelled' };
    }
    const directoryUri = await resolveTargetDirectory(permission.directoryUri);
    const fileUri = await StorageAccessFramework.createFileAsync(
      directoryUri,
      timestampName(),
      SVG_MIME_TYPE,
    );
    await FileSystem.writeAsStringAsync(fileUri, svg, { encoding: 'utf8' });
    return { status: 'saved', directoryUri: permission.directoryUri };
  } catch (error) {
    return {
      status: 'error',
      message: error instanceof Error ? error.message : 'Saving the SVG failed.',
    };
  }
}
