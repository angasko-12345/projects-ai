import * as Clipboard from 'expo-clipboard';
import * as FileSystem from 'expo-file-system/legacy';
import { StorageAccessFramework } from 'expo-file-system/legacy';
import * as Sharing from 'expo-sharing';
import type { RefObject } from 'react';
import type { View } from 'react-native';
import { captureRef } from 'react-native-view-shot';

export interface QrPngImage {
  base64: string;
}

export type ShareOutcome =
  | { status: 'shared' }
  | { status: 'unavailable'; message: string }
  | { status: 'error'; message: string };

export type SaveOutcome =
  | { status: 'saved'; directoryUri: string }
  | { status: 'cancelled' }
  | { status: 'error'; message: string };

export async function captureQrPng(viewRef: RefObject<View | null>): Promise<QrPngImage> {
  // data-uri capture decodes in JS: no temp file and no file reads, so it
  // behaves the same on Android and on the web preview.
  const dataUri = await captureRef(viewRef, { format: 'png', quality: 1, result: 'data-uri' });
  const separator = dataUri.indexOf(',');
  return { base64: separator >= 0 ? dataUri.slice(separator + 1) : dataUri };
}

export async function shareQrPng(image: QrPngImage): Promise<ShareOutcome> {
  try {
    if (!(await Sharing.isAvailableAsync())) {
      return { status: 'unavailable', message: 'Sharing is not available on this device.' };
    }
    const directory = FileSystem.cacheDirectory;
    if (!directory) {
      return { status: 'error', message: 'Sharing is not available in this environment.' };
    }
    const uri = `${directory}${timestampName()}.png`;
    await FileSystem.writeAsStringAsync(uri, image.base64, { encoding: 'base64' });
    await Sharing.shareAsync(uri, {
      mimeType: 'image/png',
      dialogTitle: 'Share QR code',
      UTI: 'public.png',
    });
    return { status: 'shared' };
  } catch (error) {
    return { status: 'error', message: error instanceof Error ? error.message : 'Sharing failed.' };
  }
}

export async function copyQrPng(image: QrPngImage): Promise<void> {
  await Clipboard.setImageAsync(image.base64);
}

function timestampName(): string {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, '0');
  return `qr-code-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(
    now.getHours(),
  )}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
}

async function resolveTargetDirectory(rootUri: string): Promise<string> {
  try {
    return await StorageAccessFramework.makeDirectoryAsync(rootUri, 'QR Generator');
  } catch {
    // The folder already exists: reuse it instead of failing the save.
    try {
      const children = await StorageAccessFramework.readDirectoryAsync(rootUri);
      const existing = children.find((child) => decodeURIComponent(child).endsWith('/QR Generator'));
      if (existing) {
        return existing;
      }
    } catch {
      // Fall through to the granted root directory.
    }
    return rootUri;
  }
}

export async function saveQrPng(
  image: QrPngImage,
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
      'image/png',
    );
    await FileSystem.writeAsStringAsync(fileUri, image.base64, { encoding: 'base64' });
    return { status: 'saved', directoryUri: permission.directoryUri };
  } catch (error) {
    return {
      status: 'error',
      message: error instanceof Error ? error.message : 'Saving the PNG failed.',
    };
  }
}
