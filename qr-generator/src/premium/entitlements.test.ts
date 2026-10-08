import {
  FREE_SAVED_CODE_LIMIT,
  PREMIUM_FEATURES,
  canSaveCode,
  type PremiumFeatureId,
} from './entitlements';

describe('canSaveCode', () => {
  it('lets free users save up to the limit', () => {
    expect(canSaveCode(false, 0)).toBe(true);
    expect(canSaveCode(false, 2)).toBe(true);
  });

  it('blocks free users once the limit is reached', () => {
    expect(canSaveCode(false, FREE_SAVED_CODE_LIMIT)).toBe(false);
    expect(canSaveCode(false, 50)).toBe(false);
  });

  it('never blocks premium users', () => {
    expect(canSaveCode(true, 0)).toBe(true);
    expect(canSaveCode(true, FREE_SAVED_CODE_LIMIT)).toBe(true);
    expect(canSaveCode(true, 1000)).toBe(true);
  });
});

describe('premium feature list', () => {
  it('covers every advertised feature exactly once', () => {
    const ids = PREMIUM_FEATURES.map((feature) => feature.id);
    const expected: PremiumFeatureId[] = [
      'colors',
      'styles',
      'logo',
      'saved-codes',
      'history',
      'no-ads',
    ];
    expect([...ids].sort()).toEqual([...expected].sort());
    expect(ids).toHaveLength(expected.length);
  });
});
