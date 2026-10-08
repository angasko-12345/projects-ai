export const FREE_SAVED_CODE_LIMIT = 3;

export type PremiumFeatureId = 'colors' | 'styles' | 'logo' | 'saved-codes' | 'history' | 'no-ads';

export interface PremiumFeature {
  id: PremiumFeatureId;
  title: string;
  detail: string;
}

export const PREMIUM_FEATURES: PremiumFeature[] = [
  { id: 'colors', title: 'Custom colors', detail: 'Pick your own code and background colors.' },
  { id: 'styles', title: 'QR styles', detail: 'Switch between square, rounded and dot styles.' },
  { id: 'logo', title: 'Logo in the code', detail: 'Place one of your images in the center.' },
  { id: 'saved-codes', title: 'Unlimited saved codes', detail: `Free saves up to ${FREE_SAVED_CODE_LIMIT} codes. Premium removes the limit.` },
  { id: 'history', title: 'QR history', detail: 'Reopen codes you made earlier.' },
  { id: 'no-ads', title: 'No ads', detail: 'Premium never shows ads.' },
];

export function canSaveCode(isPremium: boolean, currentCount: number): boolean {
  return isPremium || currentCount < FREE_SAVED_CODE_LIMIT;
}
