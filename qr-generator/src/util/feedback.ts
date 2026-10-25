export const SUPPORT_EMAIL = 'qrgenerator.support@proton.me';

/** mailto: link for the in-app "Send feedback" action. Carries no QR content. */
export function buildFeedbackMailto(appVersion?: string | null): string {
  const subject = appVersion ? `QR Generator feedback (v${appVersion})` : 'QR Generator feedback';
  return `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(subject)}`;
}
