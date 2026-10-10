import { buildCaptureOptions, timestampName } from './qrExport';

describe('buildCaptureOptions', () => {
  it('requests a fixed 1024x1024 png data-uri capture', () => {
    expect(buildCaptureOptions()).toEqual({
      format: 'png',
      quality: 1,
      result: 'data-uri',
      width: 1024,
      height: 1024,
    });
  });
});

describe('timestampName', () => {
  afterEach(() => {
    jest.useRealTimers();
  });

  it('formats the name from the current time', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2026, 0, 2, 3, 4, 5));
    expect(timestampName()).toBe('qr-code-20260102-030405-0');
  });

  it('gives two saves in the same second distinct names', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2026, 0, 3, 6, 7, 8));
    const first = timestampName();
    const second = timestampName();
    expect(first).toBe('qr-code-20260103-060708-0');
    expect(second).toBe('qr-code-20260103-060708-1');
  });

  it('starts a new counter for a later second', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date(2026, 0, 4, 9, 10, 11));
    expect(timestampName()).toBe('qr-code-20260104-091011-0');
  });
});
