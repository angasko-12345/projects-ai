import { EMPTY_DRAFT, type QrDraft, type WifiFields } from '../types';
import {
  MAX_CONTENT_LENGTH,
  MAX_SSID_BYTES,
  buildWifiPayload,
  contentLabel,
  isHexColor,
  normalizeUrl,
  validateDraft,
} from './content';

function draft(patch: Partial<QrDraft>): QrDraft {
  return { ...EMPTY_DRAFT, ...patch };
}

function wifi(patch: Partial<WifiFields>): WifiFields {
  return { ssid: '', password: '', security: 'wpa', hidden: false, ...patch };
}

describe('validateDraft: text', () => {
  it('is empty before anything is typed', () => {
    expect(validateDraft(draft({ type: 'text', text: '' }))).toEqual({ status: 'empty' });
    expect(validateDraft(draft({ type: 'text', text: '   ' }))).toEqual({ status: 'empty' });
  });

  it('trims the payload', () => {
    expect(validateDraft(draft({ type: 'text', text: '  hello world  ' }))).toEqual({
      status: 'ok',
      payload: 'hello world',
    });
  });

  it('rejects content above the length limit', () => {
    const result = validateDraft(
      draft({ type: 'text', text: 'a'.repeat(MAX_CONTENT_LENGTH + 1) }),
    );
    expect(result.status).toBe('error');
  });

  it('accepts content exactly at the length limit', () => {
    const result = validateDraft(draft({ type: 'text', text: 'a'.repeat(MAX_CONTENT_LENGTH) }));
    expect(result.status).toBe('ok');
  });
});

describe('validateDraft: url', () => {
  it('adds https when the scheme is missing', () => {
    expect(validateDraft(draft({ type: 'url', url: 'example.com/page' }))).toEqual({
      status: 'ok',
      payload: 'https://example.com/page',
    });
  });

  it('keeps an explicit scheme', () => {
    expect(validateDraft(draft({ type: 'url', url: 'http://x.io' }))).toEqual({
      status: 'ok',
      payload: 'http://x.io',
    });
    expect(validateDraft(draft({ type: 'url', url: 'https://example.com/a' }))).toEqual({
      status: 'ok',
      payload: 'https://example.com/a',
    });
  });

  it('treats host:port input as schemeless', () => {
    expect(validateDraft(draft({ type: 'url', url: 'example.com:8080/path' }))).toEqual({
      status: 'ok',
      payload: 'https://example.com:8080/path',
    });
    expect(validateDraft(draft({ type: 'url', url: 'localhost:3000' }))).toEqual({
      status: 'ok',
      payload: 'https://localhost:3000',
    });
  });

  it.each([
    'javascript:alert(1)',
    'javascript://alert(1)',
    'file:///sdcard/secret.txt',
    'file:/sdcard/secret.txt',
    'content://media/external/images',
    'ftp://files.example.com/pub',
    'data:text/plain;base64,aGVsbG8=',
    'intent://scan/#Intent;end',
    'mailto:someone@example.com',
    'tel:+15551234567',
  ])('rejects the %s scheme', (url) => {
    expect(validateDraft(draft({ type: 'url', url }))).toEqual({
      status: 'error',
      message: 'Enter a valid web address.',
    });
  });

  it('rejects whitespace anywhere in the URL', () => {
    expect(validateDraft(draft({ type: 'url', url: 'https://exam ple.com' }))).toEqual({
      status: 'error',
      message: 'Enter a valid web address.',
    });
    expect(validateDraft(draft({ type: 'url', url: 'example.com/a b' }))).toEqual({
      status: 'error',
      message: 'Enter a valid web address.',
    });
  });

  it('rejects a host containing spaces', () => {
    expect(validateDraft(draft({ type: 'url', url: 'my site.com' }))).toEqual({
      status: 'error',
      message: 'Enter a valid web address.',
    });
  });

  it('is empty when untouched', () => {
    expect(validateDraft(draft({ type: 'url', url: '' }))).toEqual({ status: 'empty' });
  });
});

describe('validateDraft: email', () => {
  it('builds a mailto payload', () => {
    expect(validateDraft(draft({ type: 'email', email: 'user@example.com' }))).toEqual({
      status: 'ok',
      payload: 'mailto:user@example.com',
    });
  });

  it('rejects malformed addresses', () => {
    expect(validateDraft(draft({ type: 'email', email: 'not-an-email' }))).toEqual({
      status: 'error',
      message: 'Enter a valid email address.',
    });
    expect(validateDraft(draft({ type: 'email', email: 'a@b' }))).toEqual({
      status: 'error',
      message: 'Enter a valid email address.',
    });
  });
});

describe('validateDraft: phone', () => {
  it('strips formatting down to a tel payload', () => {
    expect(validateDraft(draft({ type: 'phone', phone: '+1 (555) 123-4567' }))).toEqual({
      status: 'ok',
      payload: 'tel:+15551234567',
    });
  });

  it('rejects too few digits', () => {
    expect(validateDraft(draft({ type: 'phone', phone: '12345' }))).toEqual({
      status: 'error',
      message: 'Enter a valid phone number.',
    });
  });

  it('rejects letters', () => {
    expect(validateDraft(draft({ type: 'phone', phone: 'call-me' }))).toEqual({
      status: 'error',
      message: 'Enter a valid phone number.',
    });
  });
});

describe('validateDraft: wifi', () => {
  it('is empty without a network name', () => {
    expect(validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid: ' ' }) }))).toEqual({
      status: 'empty',
    });
  });

  it('rejects an over-long network name', () => {
    const result = validateDraft(
      draft({ type: 'wifi', wifi: wifi({ ssid: 'n'.repeat(MAX_SSID_BYTES + 1) }) }),
    );
    expect(result).toEqual({
      status: 'error',
      message: `Network name must be ${MAX_SSID_BYTES} bytes or fewer.`,
    });
  });

  it('accepts an ASCII network name of exactly 32 bytes', () => {
    const ssid = 'n'.repeat(MAX_SSID_BYTES);
    expect(ssid).toHaveLength(32);
    expect(
      validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid, password: 'secret123' }) })),
    ).toEqual({ status: 'ok', payload: `WIFI:T:WPA;S:${ssid};P:secret123;;` });
  });

  it('rejects 32 characters that exceed 32 UTF-8 bytes', () => {
    const ssid = 'é'.repeat(32);
    expect(ssid).toHaveLength(32);
    expect(new TextEncoder().encode(ssid)).toHaveLength(64);
    expect(
      validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid, password: 'secret123' }) })),
    ).toEqual({
      status: 'error',
      message: `Network name must be ${MAX_SSID_BYTES} bytes or fewer.`,
    });
  });

  it('accepts a multibyte network name of exactly 32 UTF-8 bytes', () => {
    const twoByte = 'é'.repeat(16); // 16 characters, 32 bytes
    const fourByte = '😀'.repeat(8); // 8 characters, 32 bytes
    for (const ssid of [twoByte, fourByte]) {
      expect(new TextEncoder().encode(ssid)).toHaveLength(32);
      expect(
        validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid, password: 'secret123' }) })),
      ).toEqual({ status: 'ok', payload: `WIFI:T:WPA;S:${ssid};P:secret123;;` });
    }
  });

  it('rejects one multibyte character past 32 bytes', () => {
    expect(
      validateDraft(
        draft({ type: 'wifi', wifi: wifi({ ssid: '😀'.repeat(9), password: 'secret123' }) }),
      ),
    ).toEqual({
      status: 'error',
      message: `Network name must be ${MAX_SSID_BYTES} bytes or fewer.`,
    });
  });

  it('asks for a password before building a secured payload', () => {
    expect(
      validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid: 'HomeNet' }) })),
    ).toEqual({ status: 'empty' });
  });

  it('rejects a short password', () => {
    const result = validateDraft(
      draft({ type: 'wifi', wifi: wifi({ ssid: 'HomeNet', password: 'short' }) }),
    );
    expect(result.status).toBe('error');
  });

  it('builds a WPA payload with escaping', () => {
    expect(
      validateDraft(
        draft({
          type: 'wifi',
          wifi: wifi({ ssid: 'My "Net;X\\Y', password: 'secret123', security: 'wpa' }),
        }),
      ),
    ).toEqual({
      status: 'ok',
      payload: 'WIFI:T:WPA;S:My \\"Net\\;X\\\\Y;P:secret123;;',
    });
  });

  it('escapes every reserved character in the SSID', () => {
    expect(buildWifiPayload(wifi({ ssid: 'a\\b;c,d:e"f', password: 'secret123' }))).toBe(
      'WIFI:T:WPA;S:a\\\\b\\;c\\,d\\:e\\"f;P:secret123;;',
    );
  });

  it('escapes the same characters in the password', () => {
    expect(buildWifiPayload(wifi({ ssid: 'HomeNet', password: 'p\\a;s,s:w"d' }))).toBe(
      'WIFI:T:WPA;S:HomeNet;P:p\\\\a\\;s\\,s\\:w\\"d;;',
    );
  });

  it('leaves ordinary characters untouched', () => {
    expect(buildWifiPayload(wifi({ ssid: 'Café Ω', password: 'secret123' }))).toBe(
      'WIFI:T:WPA;S:Café Ω;P:secret123;;',
    );
  });

  it('omits the password for open networks', () => {
    expect(buildWifiPayload(wifi({ ssid: 'Cafe', security: 'none', hidden: true }))).toBe(
      'WIFI:T:nopass;S:Cafe;H:true;;',
    );
  });

  it('uses WEP for wep networks', () => {
    expect(
      buildWifiPayload(wifi({ ssid: 'Old', password: '12345678', security: 'wep' })),
    ).toBe('WIFI:T:WEP;S:Old;P:12345678;;');
  });
});

describe('helpers', () => {
  it('normalizes urls', () => {
    expect(normalizeUrl('example.com')).toBe('https://example.com');
    expect(normalizeUrl('HTTPS://x.io')).toBe('HTTPS://x.io');
  });

  it('validates hex colors', () => {
    expect(isHexColor('#0B6E5F')).toBe(true);
    expect(isHexColor('#0b6e5f')).toBe(true);
    expect(isHexColor('#FFF')).toBe(false);
    expect(isHexColor('0B6E5F')).toBe(false);
    expect(isHexColor('#GGHHII')).toBe(false);
  });

  it('keeps SSID whitespace in the payload so it targets the right network', () => {
    expect(buildWifiPayload(wifi({ ssid: '  Home  ', password: 'secret123' }))).toBe(
      'WIFI:T:WPA;S:  Home  ;P:secret123;;',
    );
    expect(
      validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid: '  Home  ', password: 'secret123' }) })),
    ).toEqual({ status: 'ok', payload: 'WIFI:T:WPA;S:  Home  ;P:secret123;;' });
  });

  it('still treats a whitespace-only SSID as empty', () => {
    expect(
      validateDraft(draft({ type: 'wifi', wifi: wifi({ ssid: '   ', password: 'secret123' }) })),
    ).toEqual({ status: 'empty' });
  });

  it('counts SSID whitespace toward the 32-byte limit', () => {
    expect(
      validateDraft(
        draft({ type: 'wifi', wifi: wifi({ ssid: ` ${'a'.repeat(32)}`, password: 'secret123' }) }),
      ),
    ).toEqual({
      status: 'error',
      message: `Network name must be ${MAX_SSID_BYTES} bytes or fewer.`,
    });
  });

  it('keeps password whitespace verbatim', () => {
    expect(buildWifiPayload(wifi({ ssid: 'HomeNet', password: ' secret123 ' }))).toBe(
      'WIFI:T:WPA;S:HomeNet;P: secret123 ;;',
    );
  });

  it('never splits a surrogate pair when truncating labels', () => {
    const loneSurrogate = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
    const text = `${'a'.repeat(47)}😀b`;
    const textLabel = contentLabel(draft({ type: 'text', text }));
    expect(textLabel).toBe(`${'a'.repeat(47)}😀`);
    expect(loneSurrogate.test(textLabel)).toBe(false);

    const ssidLabel = contentLabel(draft({ type: 'wifi', wifi: wifi({ ssid: '😀'.repeat(60) }) }));
    expect(Array.from(ssidLabel)).toHaveLength(48);
    expect(ssidLabel).toBe('😀'.repeat(48));
    expect(loneSurrogate.test(ssidLabel)).toBe(false);
  });

  it('truncates ASCII labels at 48 characters', () => {
    expect(contentLabel(draft({ type: 'text', text: 'x'.repeat(60) }))).toBe('x'.repeat(48));
  });

  it('labels content for lists', () => {
    expect(contentLabel(draft({ type: 'text', text: '  spaced \n  out  ' }))).toBe(
      'spaced out',
    );
    expect(contentLabel(draft({ type: 'wifi', wifi: wifi({ ssid: '' }) }))).toBe('Wi-Fi');
    expect(contentLabel(draft({ type: 'url', url: 'x.io' }))).toBe('https://x.io');
  });
});
