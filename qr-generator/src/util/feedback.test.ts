import { SUPPORT_EMAIL, buildFeedbackMailto } from './feedback';

describe('buildFeedbackMailto', () => {
  it('targets the support address with an encoded subject', () => {
    expect(buildFeedbackMailto()).toBe(
      `mailto:${SUPPORT_EMAIL}?subject=QR%20Generator%20feedback`,
    );
  });

  it('adds the app version when known', () => {
    expect(buildFeedbackMailto('1.0.0')).toBe(
      `mailto:${SUPPORT_EMAIL}?subject=QR%20Generator%20feedback%20(v1.0.0)`,
    );
  });

  it('matches the contact address in the privacy policy', () => {
    expect(SUPPORT_EMAIL).toBe('qrgenerator.support@proton.me');
  });
});
